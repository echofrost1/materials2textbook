from __future__ import annotations

import pytest

from materials2textbook.agents.knowledge_semantic_planner import (
    LLMSemanticPlanningAgent,
    SemanticPlannerContextOverflow,
    prompt_component_audit,
)


class BudgetProvider:
    class Config:
        context_window = 1200
        max_tokens = 800

    def __init__(self) -> None:
        self.config = self.Config()
        self.calls: list[int | None] = []

    def generate(self, messages, *, max_tokens=None):
        self.calls.append(max_tokens)
        return '{"deltas": []}'


def test_semantic_planner_uses_dynamic_remaining_budget() -> None:
    provider = BudgetProvider()
    agent = LLMSemanticPlanningAgent(provider, safety_margin=100, min_output_tokens=32)

    assert agent.plan_semantic_deltas({"knowledge_id": "k", "occurrences": []}) == {"deltas": []}
    assert provider.calls and provider.calls[0] is not None
    audit = agent.budget_audit[0]
    assert audit["effective_output_budget"] < audit["requested_output_tokens"]
    assert audit["effective_output_budget"] == min(
        audit["max_output_cap"],
        audit["context_window"] - audit["input_tokens"] - audit["safety_margin"],
    )
    assert "prompt_components" in audit


def test_semantic_planner_fails_closed_when_context_cannot_fit() -> None:
    provider = BudgetProvider()
    provider.config.context_window = 100
    agent = LLMSemanticPlanningAgent(provider, safety_margin=20, min_output_tokens=32)

    with pytest.raises(SemanticPlannerContextOverflow):
        agent.plan_semantic_deltas({"knowledge_id": "k", "occurrences": [{"evidence": [{"excerpt": "x" * 1000}]}]})
    assert agent.budget_audit[0]["status"] == "OVERFLOW"
    assert agent.budget_audit[0]["effective_output_budget"] == 0
    assert provider.calls == []


def test_prompt_component_audit_keeps_required_context_categories() -> None:
    components = prompt_component_audit([
        {"role": "system", "content": "contract"},
        {
            "role": "user",
            "content": 'instruction\n\nINPUT:\n{"knowledge_id":"k","canonical_title":"K",'
            '"canonical_id_whitelist":[],"occurrences":[{"occurrence_id":"o",'
            '"position":{},"context_title":"task","evidence":[{"id":"e","excerpt":"fact"}]}]}',
        },
    ])
    assert components["system_contract"] > 0
    assert components["book_plan_context"] > 0
    assert components["occurrence_context"] > 0
    assert components["evidence"] > 0
