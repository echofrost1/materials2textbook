from __future__ import annotations

import json
from pathlib import Path

import pytest

import materials2textbook.workflow.orchestrator as orchestrator_module
from materials2textbook.knowledge_map.outline import (
    FrozenBookPlanValidationError,
    book_plan_fingerprint,
    book_plan_snapshot_payload,
    outline_signature,
    validate_frozen_book_plan,
)
from materials2textbook.io_utils import write_jsonl
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.orchestrator import TextbookWorkflow

from test_completeness_first_execution import _fixture


def _write_contract(tmp_path: Path, *, plan, safe: bool = True, mutate=None) -> tuple[Path, Path]:
    snapshot = book_plan_snapshot_payload(plan)
    if mutate is not None:
        mutate(snapshot)
    snapshot_path = tmp_path / "final_book_plan_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    # The official entry loads this separate input path; valid fixtures use
    # the exact same frozen payload rather than relying on a flag alone.
    (tmp_path / "book_plan.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    calibration = {
        "schema_version": "freeze-calibration/v1",
        "source_book_plan_snapshot": snapshot_path.name,
        "source_outline_signature": outline_signature(plan),
        "source_book_plan_fingerprint": book_plan_fingerprint(plan),
        "freeze": {
            "safe_to_freeze": safe,
            "remaining_manual_review": [],
            "true_source_gaps": [],
        },
    }
    calibration_path = tmp_path / "final_freeze_readiness_calibration.json"
    calibration_path.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot_path, calibration_path


def test_matching_frozen_snapshot_and_calibration_is_accepted(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)

    result = validate_frozen_book_plan(
        plan,
        book_plan_input=tmp_path / "book_plan.json",
        calibration_input=calibration,
    )

    assert result["state"] == "FROZEN_VALIDATED"
    assert result["validation_passed"] is True
    assert result["deep_equal"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["chapters"][0]["sections"][0].__setitem__("title", "Changed title"),
        lambda payload: payload["chapters"][0]["sections"][0].__setitem__("primary_material_ids", ["unauthorized"]),
    ],
    ids=["changed-title", "changed-evidence-ownership"],
)
def test_snapshot_mutation_is_rejected(tmp_path: Path, mutate) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan, mutate=mutate)

    with pytest.raises(FrozenBookPlanValidationError, match="FROZEN_BOOKPLAN_VALIDATION_FAILED"):
        validate_frozen_book_plan(
            plan,
            book_plan_input=tmp_path / "book_plan.json",
            calibration_input=calibration,
        )


def test_mismatched_fingerprint_is_rejected(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["source_book_plan_fingerprint"] = "0" * 64
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenBookPlanValidationError, match="fingerprint mismatch"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def test_mismatched_outline_signature_is_rejected(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["source_outline_signature"] = "0" * 64
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenBookPlanValidationError, match="outline/signature mismatch"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def test_unsafe_calibration_is_rejected(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan, safe=False)

    with pytest.raises(FrozenBookPlanValidationError, match="safe_to_freeze"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def test_missing_calibration_is_rejected(tmp_path: Path) -> None:
    plan, *_ = _fixture()

    with pytest.raises(FrozenBookPlanValidationError, match="calibration artifact"):
        validate_frozen_book_plan(
            plan,
            book_plan_input=tmp_path / "book_plan.json",
            calibration_input=tmp_path / "missing.json",
        )


def test_official_frozen_entry_skips_prefreeze_completeness(monkeypatch, tmp_path: Path) -> None:
    plan, chunk, *_ = _fixture()
    book_plan_path = tmp_path / "book_plan.json"
    book_plan_path.write_text(json.dumps(book_plan_snapshot_payload(plan), ensure_ascii=False), encoding="utf-8")
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    video_path = tmp_path / "videos.jsonl"
    document_path = tmp_path / "documents.jsonl"
    write_jsonl(video_path, [])
    write_jsonl(document_path, [])

    def unexpected_prefreeze_call(*_args, **_kwargs):
        raise AssertionError("frozen entry must not rerun pre-freeze completeness")

    monkeypatch.setattr(orchestrator_module, "diagnose_book_plan_completeness", unexpected_prefreeze_call)
    monkeypatch.setattr(orchestrator_module, "optimize_book_plan_completeness", unexpected_prefreeze_call)
    workflow = TextbookWorkflow(use_llm=False)
    workflow.resource_analyst.run_mixed = lambda *_args, **_kwargs: [chunk]

    outputs = workflow.run(
        video_segments_path=video_path,
        document_segments_path=document_path,
        output_dir=tmp_path / "run",
        title=plan.title,
        config=WorkflowConfig(copy_media_assets=False, review_rounds=0, enforce_completeness_gate=True),
        book_mode=True,
        semantic_book_mode=True,
        book_plan_input=book_plan_path,
        book_plan_is_frozen=True,
        freeze_calibration_input=calibration,
        chapter_output_root=tmp_path / "run" / "chapters",
    )

    manifest = json.loads(Path(outputs.manifest_path).read_text(encoding="utf-8"))
    assert manifest["book_plan_state"] == "FROZEN_VALIDATED"
    assert manifest["freeze_validation_passed"] is True
    assert manifest["prefreeze_completeness_rerun"] is False
    assert manifest["input"]["freeze_calibration_input"]
    assert manifest["outputs"]["freeze_validation"]
