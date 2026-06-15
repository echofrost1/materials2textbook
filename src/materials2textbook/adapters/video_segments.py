from __future__ import annotations

import re
from typing import Any

from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def parse_time_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?", value.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int((match.group(4) or "0").ljust(3, "0"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def split_semicolon_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(";") if part.strip()]


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def video_segment_to_evidence_chunk(record: dict[str, Any]) -> EvidenceChunk:
    clip_id = str(record.get("clip_id") or record.get("segment_id") or "")
    asset_id = str(record.get("source_asset_id") or record.get("asset_id") or "")
    knowledge_point = str(record.get("knowledge_point") or clip_id or "未命名片段")
    evidence_text = str(record.get("evidence_text") or record.get("transcript_text") or "")
    summary = str(record.get("clip_summary") or "")
    tags = split_semicolon_list(record.get("tags"))

    return EvidenceChunk(
        chunk_id=clip_id,
        asset_id=asset_id,
        title=knowledge_point,
        content=evidence_text,
        summary=summary,
        keywords=tags or [knowledge_point],
        subject=str(record.get("subject") or ""),
        material_block=str(record.get("material_block") or ""),
        material_block_code=str(record.get("material_block_code") or ""),
        recommended_chapter=str(record.get("recommended_chapter") or "待规划章节"),
        locator=EvidenceLocator(
            path=str(record.get("clip_output_path") or record.get("source_video") or ""),
            original_path=str(record.get("original_path") or ""),
            start_ms=parse_time_to_ms(record.get("start_time")),
            end_ms=parse_time_to_ms(record.get("end_time")),
            keyframe_paths=split_semicolon_list(record.get("keyframe_paths")),
        ),
        score=EvidenceScore(
            relevance=as_float(record.get("usefulness_score"), 0.0),
            teaching_value=as_float(record.get("usefulness_score"), 0.0),
            confidence=as_float(record.get("quality_score"), 0.0),
        ),
        review_status=str(record.get("review_status") or ""),
        metadata={
            "source_video": record.get("source_video", ""),
            "start_time": record.get("start_time", ""),
            "end_time": record.get("end_time", ""),
            "transcript_status": record.get("transcript_status", ""),
            "boundary_reason": record.get("boundary_reason", ""),
            "review_comment": record.get("review_comment", ""),
        },
    )
