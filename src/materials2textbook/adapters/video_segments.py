from __future__ import annotations

import math
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
    if _is_missing(value):
        return []
    if isinstance(value, list):
        return [_text(item).strip() for item in value if _text(item).strip()]
    return [part.strip() for part in str(value).split(";") if part.strip()]


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if _is_missing(value):
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def video_segment_to_evidence_chunk(record: dict[str, Any]) -> EvidenceChunk:
    clip_id = _first_text(record, "clip_id", "segment_id")
    asset_id = _first_text(record, "source_asset_id", "asset_id")
    knowledge_point = _text(record.get("knowledge_point")) or clip_id or "未命名片段"
    evidence_text = _text(record.get("evidence_text") or record.get("transcript_text"))
    summary = _text(record.get("clip_summary"))
    tags = split_semicolon_list(record.get("tags"))

    return EvidenceChunk(
        chunk_id=clip_id,
        asset_id=asset_id,
        title=knowledge_point,
        content=evidence_text,
        summary=summary,
        keywords=tags or [knowledge_point],
        subject=_text(record.get("subject")),
        material_block=_text(record.get("material_block")),
        material_block_code=_text(record.get("material_block_code")),
        recommended_chapter=_text(record.get("recommended_chapter")) or "待规划章节",
        locator=EvidenceLocator(
            path=_first_text(record, "clip_output_path", "source_video"),
            original_path=_text(record.get("original_path")),
            start_ms=parse_time_to_ms(_text(record.get("start_time"))),
            end_ms=parse_time_to_ms(_text(record.get("end_time"))),
            keyframe_paths=split_semicolon_list(record.get("keyframe_paths")),
        ),
        score=EvidenceScore(
            relevance=as_float(record.get("usefulness_score"), 0.0),
            teaching_value=as_float(record.get("usefulness_score"), 0.0),
            confidence=as_float(record.get("quality_score"), 0.0),
        ),
        review_status=_text(record.get("review_status")),
        metadata={
            "source_video": _text(record.get("source_video")),
            "start_time": _text(record.get("start_time")),
            "end_time": _text(record.get("end_time")),
            "transcript_status": _text(record.get("transcript_status")),
            "boundary_reason": _text(record.get("boundary_reason")),
            "review_comment": _text(record.get("review_comment")),
        },
    )


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _text(value: Any) -> str:
    if _is_missing(value):
        return ""
    return str(value)


def _first_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if not _is_missing(value):
            return str(value)
    return ""
