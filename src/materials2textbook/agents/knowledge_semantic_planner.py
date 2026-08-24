from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.knowledge_map import build_identity_messages, build_semantic_delta_messages


class SemanticPlannerContextOverflow(RuntimeError):
    """The semantic planner prompt cannot fit without dropping required context."""

    code = "SEMANTIC_PLANNER_CONTEXT_OVERFLOW"

    def __init__(self, audit: dict[str, Any]) -> None:
        self.audit = audit
        super().__init__(
            f"{self.code}: input_tokens={audit.get('input_tokens')} "
            f"context_window={audit.get('context_window')} "
            f"effective_output_budget={audit.get('effective_output_budget')}"
        )


@dataclass
class LLMSemanticPlanningAgent:
    """Read-only Phase 1.5 boundary: identity and semantic facts, never roles."""

    llm_provider: LLMProvider
    call_counts: dict[str, int] = field(default_factory=lambda: {"identity": 0, "semantic_delta": 0})
    context_window: int | None = None
    safety_margin: int = 256
    min_output_tokens: int = 256
    max_output_cap: int | None = None
    budget_audit: list[dict[str, Any]] = field(default_factory=list)

    def judge_identity(self, candidates: list[dict]) -> dict:
        self.call_counts["identity"] += 1
        messages = build_identity_messages(candidates)
        return _json_object(self._budgeted_generate(messages, "identity"))

    def plan_semantic_deltas(self, trajectory: dict) -> dict:
        self.call_counts["semantic_delta"] += 1
        messages = build_semantic_delta_messages(trajectory)
        occurrence_ids = [
            str(item.get("occurrence_id"))
            for item in trajectory.get("occurrences", [])
            if isinstance(item, dict) and item.get("occurrence_id")
        ]
        return _json_object(self._budgeted_generate(messages, "semantic_delta", occurrence_ids=occurrence_ids))

    def _budgeted_generate(
        self,
        messages: list[dict[str, str]],
        stage: str,
        *,
        occurrence_ids: list[str] | None = None,
    ) -> str:
        config = _provider_config(self.llm_provider)
        context_window = self.context_window or int(getattr(config, "context_window", 8192))
        requested = int(getattr(config, "max_tokens", 4096))
        cap = int(self.max_output_cap or requested)
        cap = max(1, cap)
        input_tokens = estimate_message_tokens(messages)
        effective = min(cap, context_window - input_tokens - max(0, self.safety_margin))
        audit = {
            "stage": stage,
            "input_tokens": input_tokens,
            "context_window": context_window,
            "requested_output_tokens": requested,
            "max_output_cap": cap,
            "min_output_tokens": self.min_output_tokens,
            "safety_margin": self.safety_margin,
            "effective_output_budget": max(0, effective),
            "status": "BUDGETED" if effective >= self.min_output_tokens else "OVERFLOW",
            "truncation": "none",
            "prompt_components": prompt_component_audit(messages),
            "token_estimator": "qwen_heuristic_v1",
        }
        if occurrence_ids:
            audit["occurrence_ids"] = list(occurrence_ids)
        self.budget_audit.append(audit)
        if effective < self.min_output_tokens:
            raise SemanticPlannerContextOverflow(audit)
        try:
            return self.llm_provider.generate(messages, max_tokens=effective)
        except TypeError as exc:
            # Small test doubles and third-party adapters may still expose the
            # original one-argument protocol.  Keep those usable while making
            # the missing enforcement explicit in the audit.
            if "max_tokens" not in str(exc):
                raise
            audit["budget_enforcement"] = "provider_not_budget_aware"
            return self.llm_provider.generate(messages)


def _json_object(raw: str) -> dict:
    cleaned = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    value, _ = json.JSONDecoder().raw_decode(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Semantic planner must return a JSON object.")
    return value


def _provider_config(provider: Any) -> Any:
    current = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        config = getattr(current, "config", None)
        if config is not None:
            return config
        current = getattr(current, "provider", None)
    return None


def estimate_message_tokens(messages: list[dict[str, str]]) -> int:
    """Conservative, dependency-free Qwen input estimate for budget decisions."""
    total = 0
    for message in messages:
        text = str(message.get("content", ""))
        ascii_chars = sum(1 for char in text if ord(char) < 128)
        non_ascii_chars = len(text) - ascii_chars
        total += math.ceil(ascii_chars / 4) + non_ascii_chars + 4
    return max(1, total)


def prompt_component_audit(messages: list[dict[str, str]]) -> dict[str, int]:
    """Report prompt composition without dropping any semantic contract fields."""
    system = next((str(item.get("content", "")) for item in messages if item.get("role") == "system"), "")
    user = next((str(item.get("content", "")) for item in messages if item.get("role") == "user"), "")
    instruction, _, payload_text = user.partition("INPUT:\n")
    components = {
        "system_contract": estimate_message_tokens([{"role": "system", "content": system}]),
        "book_plan_context": 0,
        "occurrence_context": 0,
        "prerequisite_context": 0,
        "evidence": 0,
        "previous_trajectory": 0,
        "other": estimate_message_tokens([{"role": "user", "content": instruction}]),
    }
    try:
        payload = json.loads(payload_text) if payload_text else {}
    except json.JSONDecodeError:
        components["other"] += estimate_message_tokens([{"role": "user", "content": payload_text}])
        return components
    if not isinstance(payload, dict):
        components["other"] += estimate_message_tokens([{"role": "user", "content": payload_text}])
        return components
    top = {key: value for key, value in payload.items() if key != "occurrences"}
    book = {key: top[key] for key in ("knowledge_id", "canonical_title", "canonical_id_whitelist") if key in top}
    components["book_plan_context"] = estimate_message_tokens([{"role": "user", "content": json.dumps(book, ensure_ascii=False)}])
    occurrences = payload.get("occurrences") if isinstance(payload.get("occurrences"), list) else []
    for index, item in enumerate(occurrences):
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        components["evidence"] += estimate_message_tokens([{"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}])
        context = {key: item.get(key) for key in ("occurrence_id", "position", "context_title", "source_title") if key in item}
        bucket = "previous_trajectory" if index < len(occurrences) - 1 else "occurrence_context"
        components[bucket] += estimate_message_tokens([{"role": "user", "content": json.dumps(context, ensure_ascii=False)}])
        for key in ("prerequisite", "prerequisites", "cross_prerequisite_uses"):
            if key in item:
                components["prerequisite_context"] += estimate_message_tokens([{"role": "user", "content": json.dumps(item[key], ensure_ascii=False)}])
    known = {"knowledge_id", "canonical_title", "canonical_id_whitelist", "occurrences"}
    unknown = {key: value for key, value in payload.items() if key not in known}
    components["other"] += estimate_message_tokens([{"role": "user", "content": json.dumps(unknown, ensure_ascii=False)}]) if unknown else 0
    return components
