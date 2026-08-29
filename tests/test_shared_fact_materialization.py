from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from materials2textbook.knowledge_map.rendered_conformance import ConformanceStatus, RenderedConformanceResult, RenderedOccurrence
from materials2textbook.knowledge_map.shared_fact_compression import COMPRESSIBLE, CONTEXTUAL_RESTATEMENT_REQUIRED, build_shared_fact_compression_plan
from materials2textbook.knowledge_map.shared_fact_materialization import ACCEPTED, ROLLED_BACK, SKIPPED, materialize_compressible_shared_fact
from materials2textbook.knowledge_map.shared_facts import RELATED_WITH_SHARED_FACTS, SharedInstructionalFact
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.schemas import DigitalBook, DigitalBookBlock, DigitalBookProject, DigitalBookTask


def _plan(disposition=COMPRESSIBLE):
    fact = SharedInstructionalFact(
        shared_fact_id="sf-1",
        fact_statement="共享事实 F。",
        source_occurrence_ids=("o1", "o2"),
        source_canonical_knowledge_ids=("k1", "k2"),
        evidence_ids_by_occurrence={"o1": ("ev-1",), "o2": ("ev-2",)},
        rendered_support_by_occurrence={
            "o1": {"status": "verified", "conformance": "MATCH", "evidence": "SUPPORTED", "non_empty_body": True, "runtime_grant_applied": True},
            "o2": {"status": "verified", "conformance": "MATCH", "evidence": "SUPPORTED", "non_empty_body": True, "runtime_grant_applied": True},
        },
        earlier_occurrence_id="o1",
        later_occurrence_id="o2",
        relation=RELATED_WITH_SHARED_FACTS,
        earlier_verified_facets=("EXPLAIN",),
        later_required_facets=(),
        later_independent_contribution="当前任务 B。",
        disposition=disposition,
        rationale="test",
    )
    return build_shared_fact_compression_plan(fact)


def _brief():
    return OccurrenceWritingBrief(
        occurrence_id="o2", source_knowledge_point_id="s2", canonical_knowledge_id="k2",
        source_title="后文", canonical_title="后文", chapter_id="c1", section_id="s1", role="APPLY",
        already_available_facets=["EXPLAIN"], required_facets=[], must_teach_facets=[], must_not_reteach_facets=[],
        extension_keys=[], repeated_aspects_to_avoid=[], prerequisite_context=[], contribution_goal="当前任务 B。",
        source_chunk_ids=["ev-2"], writing_contract="APPLY", max_recap_sentences=2,
    )


def _targets(body="共享事实 F。当前任务 B。"):
    markdown = f'<!-- occurrence:start id="o2" chapter="c1" section="s1" task="c1:task:1" -->\n{body}\n<!-- occurrence:end id="o2" -->\n'
    record = RenderedOccurrence("o2", "c1", "s1", "c1:task:1", body, markdown.index(body), markdown.index(body) + len(body), block_id="b2")
    block = DigitalBookBlock("b2", "text", "后文", markdown=body, metadata={"semantic_occurrence": {"occurrence_id": "o2", "chapter_id": "c1", "section_id": "s1"}})
    book = DigitalBook("book", "测试", {}, [DigitalBookProject("p1", "项目", "", [], [], [DigitalBookTask("t1", "任务", [block])])])
    digital_record = RenderedOccurrence("o2", "c1", "s1", "t1", body, 0, len(body), render_target="digital_book", block_id="b2")
    return markdown, book, record, digital_record


def _conformance(brief, markdown_record, digital_record):
    status = ConformanceStatus.MATCH if "B" in markdown_record.markdown else ConformanceStatus.VIOLATION
    result = RenderedConformanceResult("o2", brief.role, True, status, {}, [], {}, status, status, body_present=bool(markdown_record.markdown.strip()))
    return {"markdown": result, "digital_book": result}


def _evidence(markdown_document, digital_book, brief):
    return {"status": "SUPPORTED", "claims": []}


def _closure(*, after="CLOSED"):
    def check(markdown_document, digital_book):
        return {"results": [{"requirement": {"requirement_id": "r1"}, "status": after, "supporting_occurrence_ids": ["o2"]}], "status_counts": {after: 1}}
    return check


def _run(*, plan=None, body="共享事实 F。当前任务 B。", span="共享事实 F。", **kwargs):
    markdown, book, markdown_record, digital_record = _targets(body)
    brief = kwargs.pop("brief", _brief())
    conformance_checker = kwargs.pop("conformance_checker", _conformance)
    evidence_checker = kwargs.pop("evidence_checker", _evidence)
    downstream_rechecker = kwargs.pop("downstream_rechecker", _closure())
    return materialize_compressible_shared_fact(
        plan=plan or _plan(), brief=brief, markdown_document=markdown, digital_book=book,
        markdown_rendered=markdown_record, digital_book_rendered=digital_record,
        baseline_downstream_closure={"results": [{"requirement": {"requirement_id": "r1"}, "status": "CLOSED", "supporting_occurrence_ids": ["o2"]}]},
        downstream_rechecker=downstream_rechecker, shared_fact_span=span, conformance_checker=conformance_checker,
        evidence_checker=evidence_checker, **kwargs,
    )


def test_m1_safe_compression_is_accepted_and_both_targets_return_candidates():
    result = _run()
    assert result.attempt.final_decision == ACCEPTED
    assert result.markdown_candidate is not None
    assert result.digital_book_candidate is not None
    assert "共享事实 F。" not in result.attempt.candidate_text
    assert "当前任务 B。" in result.attempt.candidate_text
    assert result.attempt.markdown_materialization["alignment"] is True


def test_m2_unique_contribution_loss_rolls_back():
    result = _run(body="共享事实 F。当前任务 B。", span="共享事实 F。当前任务 B。")
    assert result.attempt.final_decision == ROLLED_BACK
    assert result.attempt.rollback_reason == "UNIQUE_CONTRIBUTION_NOT_RETAINED"
    assert result.attempt.unique_contribution_retention["status"] == "FAIL"
    assert result.markdown_candidate is None


def test_m3_new_unsupported_claim_rolls_back():
    result = _run(
        patch_generator=type("Generator", (), {"generate": lambda self, **kwargs: "unsupported new claim。当前任务 B。"})(),
        evidence_checker=lambda markdown, *args: {"status": "UNSUPPORTED" if "unsupported" in markdown else "SUPPORTED", "claims": []},
    )
    assert result.attempt.final_decision == ROLLED_BACK
    assert "POST_EVIDENCE_NOT_SUPPORTED" in result.attempt.rollback_reason


def test_m4_downstream_closure_regression_rolls_back():
    result = _run(downstream_rechecker=_closure(after="UNDER_SUPPORTED"))
    assert result.attempt.final_decision == ROLLED_BACK
    assert result.attempt.rollback_reason == "DOWNSTREAM_CLOSURE_REGRESSED"


def test_m5_contextual_case_is_not_materialization_eligible():
    result = _run(plan=_plan(CONTEXTUAL_RESTATEMENT_REQUIRED))
    assert result.attempt.final_decision == SKIPPED
    assert result.attempt.rollback_reason == "ONLY_COMPRESSIBLE_DISPOSITION_IS_ELIGIBLE"


def test_m6_baseline_mismatch_does_not_write():
    markdown, book, markdown_record, digital_record = _targets()
    digital_record = RenderedOccurrence("o2", "c1", "s1", "t1", "different", 0, 9, render_target="digital_book", block_id="b2")
    original_book = deepcopy(book)
    result = materialize_compressible_shared_fact(
        plan=_plan(), brief=_brief(), markdown_document=markdown, digital_book=book,
        markdown_rendered=markdown_record, digital_book_rendered=digital_record,
        baseline_downstream_closure={"results": []}, downstream_rechecker=_closure(),
        shared_fact_span="共享事实 F。", conformance_checker=_conformance, evidence_checker=_evidence,
    )
    assert result.attempt.final_decision == SKIPPED
    assert result.attempt.rollback_reason == "BASELINE_TEXT_MISMATCH"
    assert book == original_book


def test_m7_one_end_write_failure_rolls_back_both_outputs():
    result = _run(materialization_writer=lambda markdown, book: (True, False))
    assert result.attempt.final_decision == ROLLED_BACK
    assert result.attempt.rollback_reason == "DUAL_TARGET_MATERIALIZATION_FAILED"
    assert result.markdown_candidate is None
    assert result.digital_book_candidate is None


def test_semantic_summary_need_not_be_literal_when_contract_matches():
    brief = replace(
        _brief(),
        contribution_goal="将已掌握的方法用于当前场景的独立教学责任摘要",
        must_include_points=["独立教学责任摘要"],
    )
    result = _run(brief=brief)
    assert result.attempt.final_decision == ACCEPTED
    assert result.attempt.unique_contribution_retention["status"] == "PASS"
    assert result.attempt.unique_contribution_retention["semantic_summary_not_used_as_literal_requirement"] is True


def test_retention_without_structured_contract_fails_closed():
    def no_contract(brief, markdown_record, digital_record):
        result = RenderedConformanceResult(
            "o2", brief.role, True, ConformanceStatus.MATCH, {}, [], {},
            ConformanceStatus.NOT_APPLICABLE, ConformanceStatus.MATCH,
            body_present=bool(markdown_record.markdown.strip()),
        )
        return {"markdown": result, "digital_book": result}

    result = _run(conformance_checker=no_contract)
    assert result.attempt.final_decision == ROLLED_BACK
    assert result.attempt.rollback_reason == "UNIQUE_CONTRIBUTION_RETENTION_UNRESOLVED"
    assert result.attempt.unique_contribution_retention["status"] == "UNRESOLVED"


def test_shared_fact_removed_but_unique_facet_contract_remains():
    def facet_conformance(brief, markdown_record, digital_record):
        status = ConformanceStatus.MATCH if "B" in markdown_record.markdown else ConformanceStatus.VIOLATION
        result = RenderedConformanceResult(
            "o2", brief.role, True, status, {"EXPLAIN": status}, [], {},
            status, status, body_present=bool(markdown_record.markdown.strip()),
        )
        return {"markdown": result, "digital_book": result}

    result = _run(
        brief=replace(_brief(), role="TEACH", must_teach_facets=["EXPLAIN"]),
        conformance_checker=facet_conformance,
    )
    assert result.attempt.final_decision == ACCEPTED
    assert result.attempt.unique_contribution_retention["required_contribution_keys"] == ["facet:EXPLAIN"]
