from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from materials2textbook.adapters.document_segments import document_segment_to_evidence_chunk
from materials2textbook.adapters.video_segments import video_segment_to_evidence_chunk
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.resource_analyst import build_resource_analyst_messages
from materials2textbook.schemas import EvidenceChunk


class ResourceAnalystAgent:
    """Convert upstream segment records into normalized evidence chunks."""

    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm

    def run(self, video_segments: list[dict]) -> list[EvidenceChunk]:
        chunks = [video_segment_to_evidence_chunk(record) for record in video_segments]
        return self._filter_and_enhance(chunks)

    def run_document_segments(self, document_segments: list[dict]) -> list[EvidenceChunk]:
        chunks = [document_segment_to_evidence_chunk(record) for record in document_segments]
        return self._filter_and_enhance(chunks)

    def run_mixed(self, video_segments: list[dict], document_segments: list[dict] | None = None) -> list[EvidenceChunk]:
        chunks = [video_segment_to_evidence_chunk(record) for record in video_segments]
        chunks.extend(document_segment_to_evidence_chunk(record) for record in document_segments or [])
        return self._filter_and_enhance(chunks)

    def _filter_and_enhance(self, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
        valid_chunks = [chunk for chunk in chunks if chunk.chunk_id and chunk.asset_id]
        if not self.use_llm:
            return valid_chunks
        if self.llm_provider is None:
            raise RuntimeError("ResourceAnalystAgent was asked to use LLM, but no provider was configured.")
        return [self._enhance_chunk(chunk) for chunk in valid_chunks]

    def _enhance_chunk(self, chunk: EvidenceChunk) -> EvidenceChunk:
        try:
            raw_response = self.llm_provider.generate(build_resource_analyst_messages(chunk))
        except Exception as exc:
            metadata = dict(chunk.metadata)
            metadata["llm_resource_analysis"] = {
                "enabled": True,
                "fallback": True,
                "error": str(exc)[:500],
            }
            return replace(chunk, metadata=metadata)
        try:
            payload = _parse_json_object(raw_response)
        except Exception as exc:
            metadata = dict(chunk.metadata)
            metadata["llm_resource_analysis"] = {
                "enabled": True,
                "fallback": True,
                "error": str(exc)[:500],
            }
            return replace(chunk, metadata=metadata)

        summary = _non_empty_string(payload.get("summary")) or chunk.summary
        normalized_content = _non_empty_string(payload.get("normalized_content")) or chunk.content
        keywords = _string_list(payload.get("keywords")) or chunk.keywords
        quality_notes = _non_empty_string(payload.get("quality_notes"))
        uncertain = bool(payload.get("uncertain", False))

        metadata = dict(chunk.metadata)
        metadata["llm_resource_analysis"] = {
            "enabled": True,
            "quality_notes": quality_notes or "",
            "uncertain": uncertain,
        }
        if normalized_content != chunk.content:
            metadata["raw_evidence_text"] = chunk.content

        return replace(
            chunk,
            content=normalized_content,
            summary=summary,
            keywords=keywords,
            metadata=metadata,
        )


def _parse_json_object(raw_response: str) -> dict[str, Any]:
    text = raw_response.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Resource analysis LLM response was not valid JSON: {raw_response[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Resource analysis LLM response must be a JSON object.")
    return payload


def _non_empty_string(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
