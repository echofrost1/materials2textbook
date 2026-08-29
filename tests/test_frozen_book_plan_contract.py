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
from materials2textbook.workflow.orchestrator import (
    FrozenCalibrationReferenceError,
    TextbookWorkflow,
    _load_source_bounded_obligation_calibration,
)

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


def test_canonical_count_schema_is_accepted_without_mutating_plan(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    before = book_plan_snapshot_payload(plan)
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["freeze"] = {
        "safe_to_freeze": True,
        "remaining_manual_review_count": 0,
        "true_source_gap_count": 0,
    }
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_frozen_book_plan(
        plan,
        book_plan_input=tmp_path / "book_plan.json",
        calibration_input=calibration,
    )

    assert result["state"] == "FROZEN_VALIDATED"
    assert book_plan_snapshot_payload(plan) == before


def test_legacy_scalar_count_schema_remains_compatible(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["freeze"]["remaining_manual_review"] = 0
    payload["freeze"]["true_source_gaps"] = 0
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_frozen_book_plan(
        plan,
        book_plan_input=tmp_path / "book_plan.json",
        calibration_input=calibration,
    )

    assert result["validation_passed"] is True


def test_canonical_manual_review_count_still_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["freeze"]["remaining_manual_review_count"] = 1
    payload["freeze"].pop("remaining_manual_review", None)
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenBookPlanValidationError, match="remaining manual reviews"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def test_canonical_source_gap_count_still_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["freeze"]["true_source_gap_count"] = 1
    payload["freeze"].pop("true_source_gaps", None)
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenBookPlanValidationError, match="true source gaps"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def test_missing_canonical_and_legacy_counts_fail_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["freeze"].pop("remaining_manual_review", None)
    payload["freeze"].pop("true_source_gaps", None)
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenBookPlanValidationError, match="does not report remaining manual reviews"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def test_malformed_count_value_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["freeze"]["remaining_manual_review_count"] = "zero"
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenBookPlanValidationError, match="not a valid count"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def test_conflicting_count_generations_fail_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    _snapshot, calibration = _write_contract(tmp_path, plan=plan)
    payload = json.loads(calibration.read_text(encoding="utf-8"))
    payload["freeze"]["remaining_manual_review_count"] = 1
    calibration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FrozenBookPlanValidationError, match="calibration count mismatch"):
        validate_frozen_book_plan(plan, book_plan_input=tmp_path / "book_plan.json", calibration_input=calibration)


def _calibration_row(obligation_id: str, *, status: str = "SOURCE_SUPPORTED_REQUIRED") -> dict[str, object]:
    return {
        "obligation_id": obligation_id,
        "original_required": "REQUIRED",
        "calibrated_status": status,
        "calibrated_scope": "Use only the supported scope.",
        "rationale": "fixture calibration",
        "evidence_result": "SUPPORTED",
    }


def _write_referenced_calibration(
    tmp_path: Path,
    plan,
    *,
    source_name: str = "rich.json",
    rows: list[dict[str, object]] | None = None,
    **overrides,
) -> Path:
    section_id = plan.chapters[0].sections[0].section_id
    if rows is None:
        rows = [_calibration_row(f"{section_id}:obligation:concept_principle:01")]
    payload = {
        "schema_version": "source-bounded-freeze-v1",
        "safe_to_freeze": True,
        "source_bounded_bookplan_valid": True,
        "source_outline_signature": outline_signature(plan),
        "typed_fingerprint": {"kind": "BookPlan", "value": book_plan_fingerprint(plan)},
        "obligation_calibrations": rows,
        **overrides,
    }
    path = tmp_path / source_name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_calibration_wrapper(tmp_path: Path, *, reference: str = "rich.json", **overrides) -> Path:
    payload = {
        "schema_version": "source-bounded-freeze-v1",
        "safe_to_freeze": True,
        "remaining_manual_review_count": 0,
        "true_source_gap_count": 0,
        "calibration_source": reference,
        **overrides,
    }
    path = tmp_path / "wrapper.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_calibration_source_reference_matches_inline_overlay_without_mutating_plan(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    before = book_plan_snapshot_payload(plan)
    rich = _write_referenced_calibration(tmp_path, plan)
    wrapper = _write_calibration_wrapper(tmp_path)

    direct = _load_source_bounded_obligation_calibration(
        json.loads(rich.read_text(encoding="utf-8")), rich, book_plan=plan
    )
    referenced = _load_source_bounded_obligation_calibration(
        json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
    )

    assert referenced == direct
    assert book_plan_snapshot_payload(plan) == before


def test_nested_calibration_source_chain_resolves_to_row_sidecar(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    section_id = plan.chapters[0].sections[0].section_id
    row = _calibration_row(f"{section_id}:obligation:concept_principle:01")
    row_path = tmp_path / "rows.json"
    row_path.write_text(
        json.dumps({"schema_version": "source-bounded-bookplan-calibration-v1", "obligation_calibrations": [row]}),
        encoding="utf-8",
    )
    rich = _write_referenced_calibration(
        tmp_path,
        plan,
        rows=[],
        source_calibration=row_path.name,
    )
    rich_payload = json.loads(rich.read_text(encoding="utf-8"))
    rich_payload.pop("obligation_calibrations", None)
    rich.write_text(json.dumps(rich_payload, ensure_ascii=False), encoding="utf-8")
    wrapper = _write_calibration_wrapper(tmp_path, reference=rich.name)

    resolved = _load_source_bounded_obligation_calibration(
        json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
    )

    assert resolved == {row["obligation_id"]: row}


def test_calibration_source_missing_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    wrapper = _write_calibration_wrapper(tmp_path, reference="missing.json")

    with pytest.raises(FrozenCalibrationReferenceError, match="FROZEN_CALIBRATION_REFERENCE_INVALID"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


def test_calibration_source_malformed_artifact_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    wrapper = _write_calibration_wrapper(tmp_path, reference=bad.name)

    with pytest.raises(FrozenCalibrationReferenceError, match="FROZEN_CALIBRATION_REFERENCE_INVALID"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


def test_calibration_source_missing_schema_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    rich = tmp_path / "rich.json"
    rich.write_text(json.dumps({"obligation_calibrations": []}), encoding="utf-8")
    wrapper = _write_calibration_wrapper(tmp_path, reference=rich.name)

    with pytest.raises(FrozenCalibrationReferenceError, match="schema version"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


def test_calibration_source_relative_traversal_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    outside = tmp_path.parent / "outside-calibration.json"
    outside.write_text("{}", encoding="utf-8")
    wrapper = _write_calibration_wrapper(tmp_path, reference="../outside-calibration.json")

    with pytest.raises(FrozenCalibrationReferenceError, match="escapes"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


def test_calibration_source_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    rich = _write_referenced_calibration(tmp_path, plan, source_outline_signature="0" * 64)
    wrapper = _write_calibration_wrapper(tmp_path)

    with pytest.raises(FrozenCalibrationReferenceError, match="signature mismatch"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


def test_calibration_source_fingerprint_mismatch_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    rich = _write_referenced_calibration(
        tmp_path,
        plan,
        typed_fingerprint={"kind": "BookPlan", "value": "0" * 64},
    )
    wrapper = _write_calibration_wrapper(tmp_path)

    with pytest.raises(FrozenCalibrationReferenceError, match="fingerprint mismatch"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


def test_calibration_rows_must_belong_to_current_frozen_plan(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    rich = _write_referenced_calibration(
        tmp_path,
        plan,
        rows=[_calibration_row("other-section:obligation:concept_principle:01")],
    )
    wrapper = _write_calibration_wrapper(tmp_path)

    with pytest.raises(FrozenCalibrationReferenceError, match="not owned by frozen BookPlan"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


def test_calibration_reference_unsafe_artifact_fails_closed(tmp_path: Path) -> None:
    plan, *_ = _fixture()
    rich = _write_referenced_calibration(tmp_path, plan, safe_to_freeze=False)
    wrapper = _write_calibration_wrapper(tmp_path)

    with pytest.raises(FrozenCalibrationReferenceError, match="not safe to freeze"):
        _load_source_bounded_obligation_calibration(
            json.loads(wrapper.read_text(encoding="utf-8")), wrapper, book_plan=plan
        )


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
