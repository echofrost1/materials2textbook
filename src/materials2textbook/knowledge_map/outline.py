from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from materials2textbook.schemas import BookPlan, EvidenceChunk
from materials2textbook.knowledge_map.models import SourceKnowledgePoint


class FrozenBookPlanValidationError(ValueError):
    """Raised when a supposedly frozen BookPlan cannot be trusted."""

    code = "FROZEN_BOOKPLAN_VALIDATION_FAILED"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


def snapshot_source_book_plan(book_plan: BookPlan) -> BookPlan:
    """Capture the fixed input plan before semantic analysis begins."""
    return deepcopy(book_plan)


def book_plan_deep_equal(left: BookPlan, right: BookPlan) -> bool:
    """Compare every BookPlan field, not only its identity/order signature."""
    return _book_plan_payload(left) == _book_plan_payload(right)


def book_plan_fingerprint(book_plan: BookPlan) -> str:
    """Stable full-plan digest for immutable-plan audit artifacts."""
    encoded = json.dumps(_book_plan_payload(book_plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def book_plan_snapshot_payload(book_plan: BookPlan) -> dict[str, Any]:
    """Return a serializable full snapshot for audit output."""
    return _book_plan_payload(book_plan)


def validate_frozen_book_plan(
    book_plan: BookPlan,
    *,
    book_plan_input: str | Path,
    calibration_input: str | Path | None,
    chapter_token_budget: int = 12000,
) -> dict[str, Any]:
    """Validate the calibration contract for a pre-freeze BookPlan.

    A ``--book-plan-is-frozen`` flag is only a caller assertion.  This
    function requires the separately produced calibration report and a
    matching immutable snapshot/digest before the plan can enter semantic
    execution.  It deliberately does not run any completeness planner or
    mutate the supplied plan.
    """

    if calibration_input is None:
        raise FrozenBookPlanValidationError("a freeze calibration artifact is required")
    calibration_path = Path(calibration_input)
    if not calibration_path.is_file():
        raise FrozenBookPlanValidationError(f"calibration artifact not found: {calibration_path}")
    try:
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - exact parser errors vary by Python version
        raise FrozenBookPlanValidationError(f"invalid calibration artifact: {exc}") from exc
    if not isinstance(calibration, dict):
        raise FrozenBookPlanValidationError("calibration artifact must contain a JSON object")

    freeze = calibration.get("freeze") if isinstance(calibration.get("freeze"), dict) else calibration
    if freeze.get("safe_to_freeze") is not True:
        raise FrozenBookPlanValidationError("calibration safe_to_freeze is not true")
    manual_review_count = _freeze_count(freeze.get("remaining_manual_review"))
    source_gap_count = _freeze_count(freeze.get("true_source_gaps"))
    if manual_review_count is None:
        raise FrozenBookPlanValidationError("calibration does not report remaining manual reviews")
    if source_gap_count is None:
        raise FrozenBookPlanValidationError("calibration does not report true source gaps")
    if manual_review_count != 0:
        raise FrozenBookPlanValidationError(f"calibration has {manual_review_count} remaining manual reviews")
    if source_gap_count != 0:
        raise FrozenBookPlanValidationError(f"calibration has {source_gap_count} true source gaps")

    snapshot_path = _resolve_calibration_snapshot(calibration, calibration_path.parent)
    if snapshot_path is None:
        raise FrozenBookPlanValidationError("calibration has no frozen BookPlan snapshot")
    try:
        snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - exact parser errors vary by Python version
        raise FrozenBookPlanValidationError(f"invalid frozen BookPlan snapshot: {exc}") from exc
    if not isinstance(snapshot_payload, dict) or not isinstance(snapshot_payload.get("chapters"), list):
        raise FrozenBookPlanValidationError("frozen BookPlan snapshot is not a BookPlan payload")

    # Compare the source JSON itself as well as the typed BookPlan projection.
    # Some historical calibration artifacts carry audit-only section metadata
    # that the current BookPlan schema deliberately does not own.  Those
    # fields must still match byte-for-byte at the source boundary, while the
    # semantic pipeline validates the schema-owned projection below.
    input_path = Path(book_plan_input)
    try:
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - exact parser errors vary by Python version
        raise FrozenBookPlanValidationError(f"invalid BookPlan input: {exc}") from exc
    if not isinstance(input_payload, dict) or _canonical_json(input_payload) != _canonical_json(snapshot_payload):
        raise FrozenBookPlanValidationError("BookPlan input differs from the calibrated snapshot")

    from materials2textbook.agents.book_plan_llm import book_plan_from_dict

    expected_plan = book_plan_from_dict(
        snapshot_payload,
        title=str(snapshot_payload.get("title") or book_plan.title),
        chapter_token_budget=chapter_token_budget,
    )
    current_payload = book_plan_snapshot_payload(book_plan)
    expected_typed_payload = book_plan_snapshot_payload(expected_plan)
    deep_equal = _canonical_json(current_payload) == _canonical_json(expected_typed_payload)
    if not deep_equal:
        raise FrozenBookPlanValidationError("current BookPlan differs from the calibrated snapshot projection")

    current_fingerprint = book_plan_fingerprint(book_plan)
    snapshot_fingerprint = _payload_fingerprint(snapshot_payload)
    typed_snapshot_fingerprint = book_plan_fingerprint(expected_plan)
    expected_fingerprint, fingerprint_source = _resolve_calibration_digest(
        calibration,
        calibration_path.parent,
        names=("source_book_plan_fingerprint", "book_plan_fingerprint", "fingerprint"),
        files=("final_book_plan_fingerprint.txt", "frozen_book_plan_fingerprint.txt", "source_book_plan_fingerprint.txt"),
    )
    if not expected_fingerprint:
        raise FrozenBookPlanValidationError("calibration has no expected BookPlan fingerprint")
    if expected_fingerprint not in {current_fingerprint, snapshot_fingerprint, typed_snapshot_fingerprint}:
        raise FrozenBookPlanValidationError(
            f"BookPlan fingerprint mismatch (expected {expected_fingerprint}, current {current_fingerprint})"
        )

    current_outline_signature = outline_signature(book_plan)
    snapshot_outline_signature = outline_signature(expected_plan)
    expected_signature, signature_source = _resolve_calibration_digest(
        calibration,
        calibration_path.parent,
        names=("source_outline_signature", "outline_signature", "book_plan_signature", "signature"),
        files=("source_outline_signature.txt", "final_book_plan_signature.txt", "frozen_book_plan_signature.txt"),
    )
    if not expected_signature:
        raise FrozenBookPlanValidationError("calibration has no expected outline/signature digest")
    # Historical calibration artifacts called the full-plan fingerprint a
    # "signature".  Accept that representation only when it also matches the
    # validated snapshot; otherwise require the actual outline signature.
    if expected_signature not in {
        current_outline_signature,
        current_fingerprint,
        snapshot_fingerprint,
        typed_snapshot_fingerprint,
    }:
        raise FrozenBookPlanValidationError("BookPlan outline/signature mismatch")

    return {
        "state": "FROZEN_VALIDATED",
        "validation_passed": True,
        "freeze_validation_passed": True,
        "calibration_source": str(calibration_path.resolve()),
        "calibration_schema_version": calibration.get("schema_version") or calibration.get("version") or "legacy-freeze-calibration-v1",
        "calibration_run_id": calibration.get("run_id") or calibration.get("run") or "",
        "calibration_timestamp": calibration.get("timestamp") or calibration.get("generated_at") or "",
        "safe_to_freeze": True,
        "remaining_manual_review_count": manual_review_count or 0,
        "true_source_gap_count": source_gap_count or 0,
        "book_plan_input": str(Path(book_plan_input).resolve()),
        "calibration_source_book_plan": _calibration_value(
            calibration,
            ("source_book_plan_path", "pre_freeze_input", "book_plan_path"),
        ) or "",
        "curriculum_resolution_summary": _curriculum_summary(calibration.get("curriculum")),
        "source_book_plan_snapshot": str(snapshot_path.resolve()),
        "source_outline_signature": snapshot_outline_signature,
        "current_outline_signature": current_outline_signature,
        "expected_signature": expected_signature,
        "signature_source": signature_source,
        "source_book_plan_fingerprint": expected_fingerprint,
        "current_book_plan_fingerprint": current_fingerprint,
        "typed_snapshot_fingerprint": typed_snapshot_fingerprint,
        "source_snapshot_fingerprint": snapshot_fingerprint,
        "source_input_fingerprint": _payload_fingerprint(input_payload),
        "expected_book_plan_fingerprint": expected_fingerprint,
        "fingerprint_source": fingerprint_source,
        "deep_equal": True,
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _freeze_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return None


def _curriculum_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    status_counts: dict[str, int] = {}
    for item in value.values():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {"requirement_count": len(value), "status_counts": status_counts}


def _calibration_value(calibration: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = calibration.get(name)
        if value not in (None, ""):
            return value
        freeze = calibration.get("freeze")
        if isinstance(freeze, dict) and freeze.get(name) not in (None, ""):
            return freeze[name]
        source = calibration.get("source")
        if isinstance(source, dict) and source.get(name) not in (None, ""):
            return source[name]
    return None


def _resolve_calibration_digest(
    calibration: dict[str, Any],
    base_dir: Path,
    *,
    names: tuple[str, ...],
    files: tuple[str, ...],
) -> tuple[str, str]:
    value = _calibration_value(calibration, names)
    if value not in (None, ""):
        return str(value).strip(), "calibration"
    for filename in files:
        path = base_dir / filename
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value, str(path.resolve())
    return "", ""


def _resolve_calibration_snapshot(calibration: dict[str, Any], base_dir: Path) -> Path | None:
    raw = _calibration_value(
        calibration,
        ("source_book_plan_snapshot", "frozen_book_plan_snapshot", "snapshot_path", "book_plan_snapshot_path"),
    )
    candidates: list[Path] = []
    if raw not in (None, ""):
        candidate = Path(str(raw))
        candidates.append(candidate if candidate.is_absolute() else base_dir / candidate)
    candidates.extend(
        base_dir / filename
        for filename in ("final_book_plan_snapshot.json", "frozen_book_plan.json", "source_book_plan_snapshot.json")
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _book_plan_payload(book_plan: BookPlan) -> dict[str, Any]:
    return asdict(book_plan)


def outline_signature(book_plan: BookPlan) -> str:
    """Hash only immutable outline identity and order, never instructional analysis."""
    payload = [
        {
            "chapter_id": chapter.chapter_id,
            "chapter_no": chapter.chapter_no,
            "sections": [
                {
                    "section_id": section.section_id,
                    "section_no": section.section_no,
                    "knowledge_point_ids": section.knowledge_point_ids,
                }
                for section in chapter.sections
            ],
        }
        for chapter in book_plan.chapters
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def extract_source_knowledge_points(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
) -> list[SourceKnowledgePoint]:
    chunk_ids = {chunk.chunk_id for chunk in chunks if chunk.chunk_id}
    points: list[SourceKnowledgePoint] = []
    task_ordinal = 0
    for chapter_ordinal, chapter in enumerate(book_plan.chapters, start=1):
        for section_ordinal, section in enumerate(chapter.sections, start=1):
            task_ordinal += 1
            source_chunk_ids = _unique(
                [
                    chunk_id
                    for chunk_id in [*section.primary_material_ids, *section.reference_material_ids]
                    if chunk_id in chunk_ids
                ]
            )
            for source_point_ordinal, title in enumerate(section.knowledge_point_ids, start=1):
                normalized_title = str(title or "").strip()
                source_id = f"{section.section_id or chapter.chapter_id}:kp:{source_point_ordinal:02d}"
                points.append(
                    SourceKnowledgePoint(
                        source_knowledge_point_id=source_id,
                        title=normalized_title,
                        chapter_id=chapter.chapter_id,
                        section_id=section.section_id,
                        chapter_ordinal=chapter_ordinal,
                        section_ordinal=section_ordinal,
                        task_ordinal=task_ordinal,
                        source_point_ordinal=source_point_ordinal,
                        context_title=section.title,
                        source_chunk_ids=source_chunk_ids,
                    )
                )
    return points


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
