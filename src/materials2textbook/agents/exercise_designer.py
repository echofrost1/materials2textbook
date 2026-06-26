from __future__ import annotations

import json
import re
from typing import Any

from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.exercises import build_exercises_messages
from materials2textbook.schemas import EvidenceChunk, KnowledgePoint


class ExerciseDesignerAgent:
    """Generate evidence-grounded fill-in-the-blank and thinking exercises.

    Mirrors the ``ResourceAnalystAgent`` shape: constructed with an optional
    LLM provider and a ``use_llm`` flag, and silently falls back to an empty
    list (so the caller can substitute template items) when the LLM is not
    available or returns unparsable output.
    """

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        use_llm: bool = False,
        domain_config: DomainConfig | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm
        self.domain_config = domain_config or default_domain_config()

    def design_items(
        self,
        *,
        points: list[KnowledgePoint],
        chunk_map: dict[str, EvidenceChunk],
        task_title: str = "",
        fill_blank_count: int = 5,
        thinking_count: int = 2,
    ) -> list[str]:
        """Return numbered exercise strings, or an empty list on any failure."""
        if not self.use_llm or self.llm_provider is None:
            return []
        if not points:
            return []

        evidence_summary = _collect_evidence_text(points, chunk_map)
        if not evidence_summary.strip():
            return []

        knowledge_points = [
            {"title": point.title, "summary": point.summary}
            for point in points
            if point.title
        ]
        if not knowledge_points:
            return []

        try:
            raw = self.llm_provider.generate(
                build_exercises_messages(
                    task_title=task_title,
                    knowledge_points=knowledge_points,
                    evidence_summary=evidence_summary,
                    fill_blank_count=fill_blank_count,
                    thinking_count=thinking_count,
                    domain_config=self.domain_config,
                )
            )
        except Exception:
            return []

        try:
            payload = _parse_json_object(raw)
        except Exception:
            return []

        return _format_exercise_items(payload, fill_blank_count, thinking_count)


def _collect_evidence_text(
    points: list[KnowledgePoint],
    chunk_map: dict[str, EvidenceChunk],
    *,
    max_chars: int = 4000,
) -> str:
    """Concatenate point-grouped evidence content, deduped and clipped."""
    seen: set[str] = set()
    blocks: list[str] = []
    for point in points:
        for chunk_id in point.chunk_ids:
            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                continue
            text = (chunk.summary or chunk.content or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            blocks.append(f"【{point.title}】{text}")
    return "\n".join(blocks)[:max_chars]


def _parse_json_object(raw_response: str) -> dict[str, Any]:
    """Parse a JSON object from an LLM response, tolerating ```json fences."""
    text = raw_response.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # Some models wrap the whole response in braces but trailing prose; keep up to
    # the last balanced closing brace as a last resort.
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(f"Exercises LLM response was not valid JSON: {raw_response[:500]}")
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise RuntimeError("Exercises LLM response must be a JSON object.")
    return payload


def _format_exercise_items(
    payload: dict[str, Any],
    fill_blank_count: int,
    thinking_count: int,
) -> list[str]:
    """Turn the parsed payload into numbered, tagged strings.

    Output shape matches the manually-curated digital book:
    ``"1. 【填空】...（答案：...）"`` then ``"6. 【思考】...？"``.
    """
    fill_blank = _string_list(payload.get("fill_blank"))[:fill_blank_count]
    thinking = _string_list(payload.get("thinking"))[:thinking_count]

    items: list[str] = []
    for raw in fill_blank:
        cleaned = _clean_item(raw)
        if cleaned:
            items.append(cleaned)

    numbered: list[str] = []
    for index, question in enumerate(items, start=1):
        numbered.append(f"{index}. 【填空】{question}")

    fill_len = len(numbered)
    think_index = fill_len
    for raw in thinking:
        cleaned = _clean_item(raw)
        if not cleaned:
            continue
        think_index += 1
        numbered.append(f"{think_index}. 【思考】{cleaned}")

    return numbered


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _clean_item(text: str) -> str:
    """Strip model-added numbering, tags, and surrounding whitespace/quotes."""
    cleaned = str(text).strip().strip("\"'“”‘’")
    # Drop a leading "1." / "1、" / "1)" numbering the model may have added.
    cleaned = re.sub(r"^\d+[.、)]\s*", "", cleaned)
    # Drop a leading 【填空】 / 【思考】 tag the model may have added.
    cleaned = re.sub(r"^【(填空|思考)】\s*", "", cleaned)
    return cleaned.strip()
