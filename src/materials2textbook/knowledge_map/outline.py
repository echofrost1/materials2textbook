from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from materials2textbook.schemas import BookPlan, EvidenceChunk
from materials2textbook.knowledge_map.models import SourceKnowledgePoint


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
