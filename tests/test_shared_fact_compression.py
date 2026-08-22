from __future__ import annotations

from copy import deepcopy

from materials2textbook.knowledge_map.models import MasteryFacet
from materials2textbook.knowledge_map.shared_fact_compression import (
    build_shared_fact_compression_plan,
    build_shared_fact_compression_plans,
    compile_shared_fact_constraints_into_brief,
)
from materials2textbook.knowledge_map.shared_facts import (
    COMPRESSIBLE,
    CONTEXTUAL_RESTATEMENT_REQUIRED,
    INSUFFICIENT_INFORMATION,
    NOT_COMPRESSIBLE,
    RELATED_WITH_SHARED_FACTS,
    SharedInstructionalFact,
)
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief


def _support(grant: bool = True) -> dict:
    return {
        "status": "verified",
        "conformance": "MATCH",
        "evidence": "SUPPORTED",
        "non_empty_body": True,
        "runtime_grant_applied": grant,
    }


def _fact(disposition: str = COMPRESSIBLE, *, grant: bool = True) -> SharedInstructionalFact:
    return SharedInstructionalFact(
        shared_fact_id="sf-1",
        fact_statement="连接状态应在操作前得到确认。",
        source_occurrence_ids=("o1", "o2"),
        source_canonical_knowledge_ids=("k1", "k2"),
        evidence_ids_by_occurrence={"o1": ("ev-1",), "o2": ("ev-2",)},
        rendered_support_by_occurrence={"o1": _support(grant), "o2": _support()},
        earlier_occurrence_id="o1",
        later_occurrence_id="o2",
        relation=RELATED_WITH_SHARED_FACTS,
        earlier_verified_facets=(MasteryFacet.EXPLAIN,),
        later_required_facets=(MasteryFacet.EXPLAIN,),
        later_independent_contribution="当前任务增加风险判断。",
        disposition=disposition,
        rationale="gold",
    )


def _brief() -> OccurrenceWritingBrief:
    return OccurrenceWritingBrief(
        occurrence_id="o2",
        source_knowledge_point_id="s2",
        canonical_knowledge_id="k2",
        source_title="后文",
        canonical_title="后文",
        chapter_id="c1",
        section_id="s1",
        role="TEACH",
        already_available_facets=[MasteryFacet.ORIENTED],
        required_facets=[MasteryFacet.EXPLAIN],
        must_teach_facets=[MasteryFacet.EXPLAIN],
        must_not_reteach_facets=[],
        extension_keys=[],
        repeated_aspects_to_avoid=[],
        prerequisite_context=[],
        contribution_goal="当前任务增加风险判断。",
        source_chunk_ids=["ev-2"],
        writing_contract="TEACH",
    )


def test_compressible_plan_is_fact_level_and_never_materializable():
    plan = build_shared_fact_compression_plan(_fact())
    assert plan is not None
    assert plan.disposition == COMPRESSIBLE
    assert plan.auto_materialization_eligible is False
    assert "FULL_RETEACH_SHARED_FACT" in plan.forbidden_actions
    assert "当前任务增加风险判断。" in plan.compiled_brief_constraints["must_include_points"]
    assert plan.expected_post_compression_conformance["later_role_unchanged"] is True


def test_contextual_plan_requires_minimal_bridge_and_preserves_unique_contribution():
    plan = build_shared_fact_compression_plan(_fact(CONTEXTUAL_RESTATEMENT_REQUIRED))
    assert plan.disposition == CONTEXTUAL_RESTATEMENT_REQUIRED
    assert plan.required_restatement == "连接状态应在操作前得到确认。"
    assert plan.compiled_brief_constraints["max_recap_sentences"] == 2
    assert plan.later_unique_contribution in plan.compiled_brief_constraints["must_include_points"]
    assert "FULL_RETEACH_SHARED_FACT" in plan.forbidden_actions


def test_not_compressible_is_no_change():
    plan = build_shared_fact_compression_plan(_fact(NOT_COMPRESSIBLE))
    assert plan.disposition == NOT_COMPRESSIBLE
    assert plan.allowed_actions == ("NO_CHANGE",)
    assert plan.compiled_brief_constraints["max_recap_sentences"] == 0


def test_insufficient_information_is_manual_review_and_no_auto_action():
    plan = build_shared_fact_compression_plan(_fact(INSUFFICIENT_INFORMATION))
    assert plan.disposition == INSUFFICIENT_INFORMATION
    assert plan.allowed_actions == ("NO_AUTO_ACTION",)
    assert plan.manual_review_reason


def test_earlier_blocked_support_cannot_become_compressible():
    plan = build_shared_fact_compression_plan(_fact(grant=False))
    assert plan.disposition == INSUFFICIENT_INFORMATION
    assert plan.auto_materialization_eligible is False
    assert plan.downstream_safety_constraints["earlier_support_verified"] is False


def test_downstream_explicit_explanation_tightens_compressible_plan():
    closure = {
        "results": [
            {
                "requirement": {"requirement_id": "r1", "required_facets": ["EXPLAIN"]},
                "supporting_occurrence_ids": ["o2"],
                "status": "CLOSED",
            }
        ]
    }
    plan = build_shared_fact_compression_plan(_fact(), downstream_closure=closure)
    assert plan.disposition == NOT_COMPRESSIBLE
    assert plan.downstream_safety_constraints["requires_explicit_teaching"] is True


def test_compilation_merges_fact_constraints_without_changing_role_or_original_brief():
    brief = _brief()
    original = deepcopy(brief)
    plan = build_shared_fact_compression_plan(_fact(CONTEXTUAL_RESTATEMENT_REQUIRED))
    compiled = compile_shared_fact_constraints_into_brief(brief, plan)
    assert compiled.role == brief.role
    assert compiled.required_facets == brief.required_facets
    assert compiled.must_teach_facets == brief.must_teach_facets
    assert brief == original
    assert "FULL_RETEACH_SHARED_FACT" not in compiled.must_avoid_patterns
    assert any("shared fact" in item for item in compiled.forbidden_content)
    assert plan.later_unique_contribution in compiled.must_include_points


def test_report_can_compile_constraints_against_existing_briefs_without_replanning():
    brief = _brief()
    report = build_shared_fact_compression_plans(
        [_fact(CONTEXTUAL_RESTATEMENT_REQUIRED)],
        briefs_by_occurrence={"o2": brief},
    )
    compiled = report.compiled_brief_constraints_by_occurrence["o2"]
    assert compiled["role"] == brief.role
    assert compiled["required_facets"] == brief.required_facets
    assert any("shared fact" in item for item in compiled["forbidden_content"])


def test_report_contains_six_gold_plan_shapes_and_materialization_is_disabled():
    facts = [_fact(CONTEXTUAL_RESTATEMENT_REQUIRED) for _ in range(5)] + [_fact(COMPRESSIBLE)]
    for index, fact in enumerate(facts):
        facts[index] = SharedInstructionalFact(**{**fact.__dict__, "shared_fact_id": f"gold-{index + 1}"})
    report = build_shared_fact_compression_plans(facts)
    assert len(report.plans) == 6
    assert report.disposition_counts[CONTEXTUAL_RESTATEMENT_REQUIRED] == 5
    assert report.disposition_counts[COMPRESSIBLE] == 1
    assert report.materialization_eligible_count == 0
    assert report.to_dict()["materialization_eligible"] is False
