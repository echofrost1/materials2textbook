from __future__ import annotations

import math
from typing import Any

from materials2textbook.adapters.video_segments import as_float, split_semicolon_list
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def document_segment_to_evidence_chunk(record: dict[str, Any]) -> EvidenceChunk:
    """Convert document/PPT/table evidence rows into the common EvidenceChunk shape."""

    segment_id = _first_text(
        record,
        "segment_id",
        "chunk_id",
        "paragraph_id",
        "ppt_asset_id",
        "assessment_id",
        "table_asset_id",
        "asset_unit_id",
    )
    asset_id = _first_text(record, "source_asset_id", "asset_id", "document_id")
    title = _first_text(record, "knowledge_point", "heading", "slide_title", "title") or segment_id or "document segment"
    content = _first_text(record, "evidence_text", "text", "content", "slide_text")
    summary = _first_text(record, "summary", "clip_summary", "abstract")
    tags = split_semicolon_list(record.get("tags") or record.get("keywords"))
    document_path = _first_text(record, "document_path", "path", "source_document", "original_path")
    source_type = _first_text(record, "source_type", "document_type")
    if not source_type:
        source_type = "ppt_slide" if record.get("ppt_asset_id") else "document_segment"

    return EvidenceChunk(
        chunk_id=segment_id,
        asset_id=asset_id,
        title=title,
        content=content,
        summary=summary,
        keywords=tags or [title],
        subject=_first_text(record, "subject", "subject_cn"),
        material_block=_first_text(record, "material_block", "module", "material_block_cn"),
        material_block_code=_first_text(record, "material_block_code", "module_code"),
        recommended_chapter=_first_text(record, "recommended_chapter", "chapter", "chapter_title") or "待规划章节",
        locator=EvidenceLocator(
            path=document_path,
            original_path=_first_text(record, "original_path") or document_path,
            page=as_int(
                record.get("page")
                or record.get("page_no")
                or record.get("slide")
                or record.get("slide_no")
                or record.get("slide_index")
            ),
            keyframe_paths=split_semicolon_list(record.get("image_paths") or record.get("preview_image_paths")),
        ),
        score=EvidenceScore(
            relevance=as_float(record.get("relevance_score") or record.get("score") or record.get("usefulness_score"), 0.0),
            teaching_value=as_float(record.get("teaching_value") or record.get("usefulness_score") or record.get("score"), 0.0),
            confidence=as_float(record.get("confidence") or record.get("quality_score") or record.get("asset_review_score"), 0.0),
        ),
        source_type=source_type,
        review_status=_first_text(record, "review_status", "asset_review_status"),
        metadata={
            "source_document": _first_text(record, "source_document"),
            "source_ppt": _first_text(record, "source_ppt"),
            "document_title": _first_text(record, "document_title"),
            "heading": _first_text(record, "heading"),
            "page": record.get("page", ""),
            "slide": record.get("slide") or record.get("slide_index") or "",
            "section": _first_text(record, "section", "section_title"),
            "review_comment": _first_text(record, "review_comment", "asset_review_reason"),
        },
    )


def _first_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if _is_missing(value):
            continue
        return str(value)
    return ""


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))
