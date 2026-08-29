from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from materials2textbook.knowledge_map.shared_facts import (
    COMPRESSIBLE,
    CONTEXTUAL_RESTATEMENT_REQUIRED,
    DISTINCT,
    INSUFFICIENT_INFORMATION,
    NOT_COMPRESSIBLE,
    RELATED_WITH_SHARED_FACTS,
    SAME_CANONICAL,
    audit_shared_fact_proposals,
    recall_shared_fact_candidates,
)


def _record(
    occurrence_id: str,
    canonical_id: str,
    *,
    body: str = "操作前检查连接状态，确认设备可以安全运行。",
    evidence: tuple[str, ...] = ("ev-1",),
    position: int = 1,
    facets: tuple[str, ...] = ("EXPLAIN",),
    grant: bool = True,
) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "canonical_knowledge_id": canonical_id,
        "source_chunk_ids": list(evidence),
        "body": body,
        "position": {"chapter_ordinal": position, "task_ordinal": 1, "occurrence_ordinal": 1},
        "verified_facets": list(facets),
        "required_facets": list(facets),
        "conformance": "MATCH",
        "evidence": "SUPPORTED",
        "runtime_grant_applied": grant,
    }


def _proposal(*, relation=RELATED_WITH_SHARED_FACTS, disposition=CONTEXTUAL_RESTATEMENT_REQUIRED, **extra):
    return {
        "shared_fact_id": "sf-1",
        "source_occurrence_ids": ["o1", "o2"],
        "relation": relation,
        "fact_statement": "操作前应检查连接状态。",
        "evidence_ids_by_occurrence": {"o1": ["ev-1"], "o2": ["ev-2"]},
        "later_independent_contribution": "当前任务增加现场风险判断。",
        "disposition": disposition,
        "requires_recontextualization": disposition == CONTEXTUAL_RESTATEMENT_REQUIRED,
        "confidence": 0.95,
        **extra,
    }


def test_related_fact_requires_both_sides_and_preserves_contribution():
    records = [_record("o1", "k1", evidence=("ev-1",), position=1), _record("o2", "k2", evidence=("ev-2",), position=2)]
    original = deepcopy(records)
    report = audit_shared_fact_proposals(rendered_occurrences=records, proposals=[_proposal()])
    item = report.proposals[0]
    assert item.relation == RELATED_WITH_SHARED_FACTS
    assert item.disposition == CONTEXTUAL_RESTATEMENT_REQUIRED
    assert item.later_independent_contribution
    assert item.evidence_ids_by_occurrence == {"o1": ("ev-1",), "o2": ("ev-2",)}
    assert records == original


def test_gold_six_represents_five_contextual_and_one_compressible_case():
    records = []
    proposals = []
    expected = []
    for index in range(1, 7):
        left_id, right_id = f"g{index}-a", f"g{index}-b"
        records.extend(
            [
                _record(left_id, f"k{index}-a", evidence=(f"ev-{index}-a",), position=index * 2 - 1),
                _record(right_id, f"k{index}-b", evidence=(f"ev-{index}-b",), position=index * 2),
            ]
        )
        disposition = COMPRESSIBLE if index == 6 else CONTEXTUAL_RESTATEMENT_REQUIRED
        proposals.append(
            {
                "shared_fact_id": f"gold-{index}",
                "source_occurrence_ids": [left_id, right_id],
                "relation": RELATED_WITH_SHARED_FACTS,
                "fact_statement": f"共享教学事实 {index}。",
                "evidence_ids_by_occurrence": {left_id: [f"ev-{index}-a"], right_id: [f"ev-{index}-b"]},
                "later_independent_contribution": f"后一个知识的独立贡献 {index}。",
                "disposition": disposition,
                "requires_recontextualization": disposition == CONTEXTUAL_RESTATEMENT_REQUIRED,
                "confidence": 0.95,
            }
        )
        expected.append(disposition)
    report = audit_shared_fact_proposals(rendered_occurrences=records, proposals=proposals)
    assert [item.disposition for item in report.proposals] == expected
    assert report.relation_counts[RELATED_WITH_SHARED_FACTS] == 6


def test_real_phase3b_gold_fixture_has_the_six_audited_cases():
    fixture_path = Path(__file__).parent / "fixtures" / "knowledge_map_gold" / "phase3b_shared_facts.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert len(fixture) == 6
    assert [item["disposition"] for item in fixture].count(CONTEXTUAL_RESTATEMENT_REQUIRED) == 5
    assert [item["disposition"] for item in fixture].count(COMPRESSIBLE) == 1
    assert all(item["relation"] == RELATED_WITH_SHARED_FACTS for item in fixture)


def test_same_canonical_is_never_cross_knowledge_related():
    records = [_record("o1", "k1"), _record("o2", "k1", evidence=("ev-2",), position=2)]
    report = audit_shared_fact_proposals(
        rendered_occurrences=records,
        proposals=[_proposal(relation=RELATED_WITH_SHARED_FACTS)],
    )
    assert report.proposals[0].relation == SAME_CANONICAL
    assert report.proposals[0].disposition == NOT_COMPRESSIBLE


def test_distinct_relation_is_retained_without_compression():
    records = [_record("o1", "k1"), _record("o2", "k2", evidence=("ev-2",), position=2)]
    report = audit_shared_fact_proposals(
        rendered_occurrences=records,
        proposals=[_proposal(relation=DISTINCT)],
    )
    assert report.proposals[0].relation == DISTINCT
    assert report.proposals[0].disposition == NOT_COMPRESSIBLE


def test_missing_evidence_on_one_side_is_insufficient_information():
    records = [_record("o1", "k1"), _record("o2", "k2", evidence=("ev-2",), position=2)]
    raw = _proposal()
    raw["evidence_ids_by_occurrence"] = {"o1": ["ev-1"]}
    report = audit_shared_fact_proposals(rendered_occurrences=records, proposals=[raw])
    item = report.proposals[0]
    assert item.relation == INSUFFICIENT_INFORMATION
    assert item.disposition == INSUFFICIENT_INFORMATION


def test_blocked_earlier_occurrence_cannot_be_compressible():
    records = [_record("o1", "k1"), _record("o2", "k2", evidence=("ev-2",), position=2)]
    report = audit_shared_fact_proposals(
        rendered_occurrences=records,
        proposals=[_proposal(disposition=COMPRESSIBLE, requires_recontextualization=False)],
        blocked_occurrence_ids=["o1"],
    )
    assert not report.proposals
    assert "blocked occurrence" in report.rejected_proposals[0]["reason"]


def test_downstream_full_explanation_blocks_compression():
    records = [_record("o1", "k1"), _record("o2", "k2", evidence=("ev-2",), position=2)]
    closure = {
        "results": [
            {
                "requirement": {"requirement_id": "r1", "required_facets": ["EXPLAIN"]},
                "supporting_occurrence_ids": ["o2"],
                "status": "CLOSED",
            }
        ]
    }
    report = audit_shared_fact_proposals(
        rendered_occurrences=records,
        proposals=[_proposal(disposition=COMPRESSIBLE, requires_recontextualization=False)],
        downstream_closure=closure,
    )
    assert report.proposals[0].disposition == NOT_COMPRESSIBLE


def test_candidate_recall_excludes_same_canonical_and_does_not_conclude_relation():
    records = [_record("o1", "k1"), _record("o2", "k1", evidence=("ev-2",), position=2), _record("o3", "k2", evidence=("ev-3",), position=3)]
    original = deepcopy(records)
    candidates = recall_shared_fact_candidates(records, minimum_score=0.0)
    assert all(item.canonical_a_id != item.canonical_b_id for item in candidates)
    assert any({item.occurrence_a_id, item.occurrence_b_id} == {"o1", "o3"} for item in candidates)
    assert records == original
