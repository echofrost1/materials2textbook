from __future__ import annotations

import json

from materials2textbook.knowledge_map.models import (
    BookPosition,
    KnowledgeKind,
    KnowledgeMap,
    KnowledgeMapping,
    KnowledgePoint,
    LearningTrajectory,
    PlannedOccurrence,
    SemanticDelta,
    SourceKnowledgePoint,
)
from materials2textbook.knowledge_map.semantic_evaluation import evaluate_semantic_planning


def _delta(occurrence_id: str) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "repeats_prior_explanation": False,
        "uses_prior_knowledge": False,
        "recall_needed": False,
        "orientation_only": False,
        "restores_prior_context": False,
        "repeats_complete_teaching": False,
        "required_self_facets": [],
        "required_self_extension_keys": [],
        "cross_prerequisite_uses": [],
        "new_facets": ["EXPLAIN"],
        "new_extension_keys": [],
        "new_context": "",
        "repeated_aspects": [],
        "contribution_summary": f"Teach {occurrence_id}.",
        "confidence": 0.95,
        "rationale": "The authorized source explains the concept.",
        "evidence_ids": ["e1"],
    }


def _fixture() -> KnowledgeMap:
    point = KnowledgePoint("k1", "A", [], KnowledgeKind.CONCEPT, source_chunk_ids=["e1"], extraction_confidence=1.0)
    sources = [
        SourceKnowledgePoint("s1", "A", "ch1", "sec1", 1, 1, 1, 1, "First A", ["e1"]),
        SourceKnowledgePoint("s2", "A", "ch1", "sec2", 1, 2, 2, 1, "Second A", ["e1"]),
    ]
    occurrences = [
        PlannedOccurrence("o1", "k1", "s1", BookPosition(1, 1, 1, 1, 1), "ch1", "sec1", "First A", ["e1"], "TEACH"),
        PlannedOccurrence("o2", "k1", "s2", BookPosition(1, 2, 2, 2, 1), "ch1", "sec2", "Second A", ["e1"], "TEACH"),
    ]
    return KnowledgeMap(
        "fixture", "sig", [point], sources,
        [KnowledgeMapping("s1", ["k1"], "EXACT", 1.0, "fixture", ["e1"]), KnowledgeMapping("s2", ["k1"], "EXACT", 1.0, "fixture", ["e1"])],
        [], occurrences, [LearningTrajectory("k1", ["o1", "o2"])], [], [],
    )


class _RetryAgent:
    def __init__(self, first_response, retry_response):
        self.first_response = first_response
        self.retry_response = retry_response
        self.calls = 0
        self.call_counts = {"identity": 0, "semantic_delta": 0}
        self.budget_audit = []

    def plan_semantic_deltas(self, payload):
        self.calls += 1
        self.call_counts["semantic_delta"] += 1
        return self.first_response if self.calls == 1 else self.retry_response

    def judge_identity(self, candidates):
        return {"judgements": []}


class _CaptureAgent(_RetryAgent):
    def __init__(self):
        super().__init__({"deltas": [_delta("o1")]}, {"deltas": [_delta("o1")]})
        self.payloads = []

    def plan_semantic_deltas(self, payload, **kwargs):
        self.payloads.append(payload)
        return {"deltas": [_delta(payload["occurrences"][0]["occurrence_id"])]}


def test_retry_only_reprocesses_failed_occurrence_and_recovers_it() -> None:
    agent = _RetryAgent({"deltas": [_delta("o1")]}, {"deltas": [_delta("o2")]})
    evaluation = evaluate_semantic_planning(knowledge_map=_fixture(), chunks=[], agent=agent)

    assert {item.occurrence_id for item in evaluation.semantic_deltas} == {"o1", "o2"}
    assert agent.calls == 2
    assert evaluation.semantic_retry_audit[0]["occurrence_ids"] == ["o2"]
    assert evaluation.semantic_retry_audit[0]["recovered_occurrence_ids"] == ["o2"]
    assert evaluation.semantic_retry_audit[0]["retry_context_occurrence_ids"] == ["o1", "o2"]


def test_malformed_response_gets_one_retry_then_fails_closed() -> None:
    class _MalformedAgent(_RetryAgent):
        def plan_semantic_deltas(self, payload):
            self.calls += 1
            self.call_counts["semantic_delta"] += 1
            if self.calls == 1:
                raise ValueError("Unterminated JSON")
            return {"deltas": [_delta("o1")]}

    agent = _MalformedAgent({}, {})
    evaluation = evaluate_semantic_planning(knowledge_map=_fixture(), chunks=[], agent=agent)

    assert agent.calls == 2
    assert {item.occurrence_id for item in evaluation.semantic_deltas} == {"o1"}
    retry = evaluation.semantic_retry_audit[0]
    assert retry["first_response_status"] == "PARSE_FAILED"
    assert retry["retry_response_status"] == "RESPONSE"
    assert retry["remaining_occurrence_ids"] == ["o2"]
    assert any(item.get("reason") == "missing_occurrence_delta" and item.get("occurrence_id") == "o2" for item in evaluation.rejected_proposals)


def test_isolated_semantic_context_excludes_future_occurrences_and_unrelated_canonicals() -> None:
    agent = _CaptureAgent()
    evaluation = evaluate_semantic_planning(
        knowledge_map=_fixture(),
        chunks=[],
        agent=agent,
        isolate_occurrence_context=True,
    )

    assert [payload["occurrences"][0]["occurrence_id"] for payload in agent.payloads] == ["o1", "o2"]
    assert agent.payloads[0]["prior_occurrences"] == []
    assert [item["occurrence_id"] for item in agent.payloads[1]["prior_occurrences"]] == ["o1"]
    assert all(
        set(item["knowledge_id"] for item in payload["canonical_id_whitelist"]) == {"k1"}
        for payload in agent.payloads
    )
    assert all(item["future_occurrence_ids_exposed"] == [] for item in evaluation.semantic_context_audit)
