from __future__ import annotations

import json
from pathlib import Path

from materials2textbook.agents.knowledge_semantic_planner import LLMSemanticPlanningAgent
from materials2textbook.knowledge_map.gold import evaluate_gold_predictions, load_gold_fixture
from materials2textbook.knowledge_map.models import KnowledgeKind, KnowledgeMap, KnowledgeMapping, KnowledgePoint, MasteryFacet, SemanticDelta
from materials2textbook.knowledge_map.semantic_evaluation import (
    _apply_accepted_identity_merges,
    _normalize_delta_for_availability,
    derive_learning_role,
)


class StubProvider:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"judgements": []}'
        return '{"deltas": []}'


def test_phase15_model_emits_semantic_delta_not_role_or_contribution() -> None:
    agent = LLMSemanticPlanningAgent(StubProvider())
    assert agent.judge_identity([]) == {"judgements": []}
    assert agent.plan_semantic_deltas({}) == {"deltas": []}
    assert agent.call_counts == {"identity": 1, "semantic_delta": 1}


def test_gold_fixture_covers_required_semantic_cases() -> None:
    fixture = Path(__file__).parent / "fixtures" / "knowledge_map_gold" / "phase15_gold.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    assert {case["id"] for case in cases} == {
        "same_alias", "related_distinct", "decomposed_source", "intro_teach", "teach_apply", "teach_extend",
        "duplicate_teach", "prerequisite_gap", "recall_policy", "uncertain_identity",
    }
    assert len(cases) == 10


def test_gold_evaluation_is_case_level_and_exposes_errors() -> None:
    fixture = Path(__file__).parent / "fixtures" / "knowledge_map_gold" / "phase15_gold.json"
    cases = load_gold_fixture(fixture)
    results = evaluate_gold_predictions(cases, {"same_alias": {"identity": "SAME"}})
    assert results.total == 12
    assert results.matched == 1
    assert any(item.case_id == "teach_apply" and not item.matched for item in results.comparisons)


def test_semantic_delta_deterministically_derives_core_roles() -> None:
    base = dict(
        occurrence_id="o1", repeats_prior_explanation=False, uses_prior_knowledge=False, recall_needed=False,
        required_self_facets=[], required_self_extension_keys=[], cross_prerequisite_uses=[], new_facets=[],
        new_extension_keys=[], new_context="", repeated_aspects=[], contribution_summary="", confidence=0.9,
        rationale="fixture", evidence_chunk_ids=["E1"],
    )
    intro = SemanticDelta(**{**base, "new_facets": [MasteryFacet.ORIENTED]})
    apply = SemanticDelta(**{**base, "uses_prior_knowledge": True})
    extend = SemanticDelta(**{**base, "new_extension_keys": ["condition:thin_plate"]})
    duplicate = SemanticDelta(**{**base, "repeats_prior_explanation": True})
    assert derive_learning_role(intro, has_previous=False) == "INTRO"
    # A structural predecessor alone is not sufficient. APPLY and EXTEND
    # require that the predecessor has actually been made instructionally
    # available by the earlier textbook path.
    assert derive_learning_role(apply, has_previous=True) == "TEACH"
    assert derive_learning_role(extend, has_previous=True) == "TEACH"
    assert derive_learning_role(apply, has_previous=True, prior_available_facets=[MasteryFacet.EXPLAIN]) == "APPLY"
    assert derive_learning_role(extend, has_previous=True, prior_available_facets=[MasteryFacet.EXPLAIN]) == "EXTEND"
    assert derive_learning_role(duplicate, has_previous=True) == "TEACH"


def test_role_derivation_refuses_intro_and_lets_verified_use_beat_duplicate_signal() -> None:
    base = dict(
        occurrence_id="o1", repeats_prior_explanation=False, uses_prior_knowledge=False, recall_needed=False,
        required_self_facets=[], required_self_extension_keys=[], cross_prerequisite_uses=[], new_facets=[],
        new_extension_keys=[], new_context="", repeated_aspects=[], contribution_summary="", confidence=0.9,
        rationale="fixture", evidence_chunk_ids=["E1"],
    )
    first_complete_teach = SemanticDelta(**{**base, "new_facets": [MasteryFacet.EXPLAIN], "orientation_only": True})
    recall = SemanticDelta(**{**base, "restores_prior_context": True})
    duplicate_in_apply_context = SemanticDelta(**{
        **base, "repeats_prior_explanation": True, "repeats_complete_teaching": True, "uses_prior_knowledge": True,
    })

    assert derive_learning_role(first_complete_teach, has_previous=False, source_context="INTRO:") == "TEACH"
    assert derive_learning_role(
        recall,
        has_previous=True,
        source_context="RECALL: restore minimum context",
        prior_available_facets=[MasteryFacet.EXPLAIN],
        future_contexts=["APPLY: use it in the task"],
    ) == "RECALL"
    assert derive_learning_role(
        duplicate_in_apply_context,
        has_previous=True,
        source_context="APPLY: repeat the complete definition and method",
        prior_available_facets=[MasteryFacet.EXPLAIN],
    ) == "APPLY"
    assert derive_learning_role(
        duplicate_in_apply_context,
        has_previous=True,
        source_context="APPLY: directly use the existing method in this task",
        prior_available_facets=[MasteryFacet.EXPLAIN],
    ) == "APPLY"


def test_final_availability_compilation_removes_stale_new_grants_before_brief_generation() -> None:
    delta = SemanticDelta(
        occurrence_id="o-stale", repeats_prior_explanation=False, uses_prior_knowledge=True, recall_needed=False,
        required_self_facets=[], required_self_extension_keys=[], cross_prerequisite_uses=[],
        new_facets=[MasteryFacet.PERFORM], new_extension_keys=["constraint:thin_plate"],
        new_context="", repeated_aspects=[], contribution_summary="", confidence=0.9,
        rationale="fixture", evidence_chunk_ids=["E1"],
    )
    normalized, audit = _normalize_delta_for_availability(
        delta=delta,
        available_facets=[MasteryFacet.PERFORM],
        available_extension_keys=["constraint:thin_plate"],
        occurrence_id="o-stale",
    )
    assert normalized.new_facets == []
    assert normalized.new_extension_keys == []
    assert audit[0]["classification"] == "STATE_STALE_CONTRIBUTION_NORMALIZED"


def test_high_confidence_same_identity_merges_canonical_points_and_mappings() -> None:
    left = KnowledgePoint("kp:arc", "高频引弧原理", [], KnowledgeKind.PRINCIPLE, source_chunk_ids=["C1"], extraction_confidence=0.9)
    right = KnowledgePoint("kp:arc-alias", "高频引弧的原理", [], KnowledgeKind.PRINCIPLE, source_chunk_ids=["C2"], extraction_confidence=0.8)
    knowledge_map = KnowledgeMap(
        "fixture", "outline", [left, right], [],
        [
            KnowledgeMapping("source:1", [left.knowledge_id], "EXACT", 0.9, "first", ["C1"]),
            KnowledgeMapping("source:2", [right.knowledge_id], "EXACT", 0.8, "second", ["C2"]),
        ],
        [], [], [], [], [],
    )

    changed = _apply_accepted_identity_merges(knowledge_map, [{
        "left_id": "kp:arc", "right_id": "kp:arc-alias", "relation": "SAME", "confidence": 0.95,
    }])

    assert changed is True
    assert [item.knowledge_id for item in knowledge_map.knowledge_points] == ["kp:arc"]
    assert {"高频引弧原理", "高频引弧的原理"}.issubset(knowledge_map.knowledge_points[0].aliases)
    assert [item.canonical_knowledge_ids for item in knowledge_map.mappings] == [["kp:arc"], ["kp:arc"]]
    assert [item.mapping_type for item in knowledge_map.mappings] == ["EXACT", "ALIAS"]
