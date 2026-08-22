from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.knowledge_map import build_identity_messages, build_semantic_delta_messages


@dataclass
class LLMSemanticPlanningAgent:
    """Read-only Phase 1.5 boundary: identity and semantic facts, never roles."""

    llm_provider: LLMProvider
    call_counts: dict[str, int] = field(default_factory=lambda: {"identity": 0, "semantic_delta": 0})

    def judge_identity(self, candidates: list[dict]) -> dict:
        self.call_counts["identity"] += 1
        return _json_object(self.llm_provider.generate(build_identity_messages(candidates)))

    def plan_semantic_deltas(self, trajectory: dict) -> dict:
        self.call_counts["semantic_delta"] += 1
        return _json_object(self.llm_provider.generate(build_semantic_delta_messages(trajectory)))


def _json_object(raw: str) -> dict:
    cleaned = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    value, _ = json.JSONDecoder().raw_decode(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Semantic planner must return a JSON object.")
    return value
