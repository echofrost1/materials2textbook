from __future__ import annotations

from dataclasses import dataclass
import json

from materials2textbook.knowledge_map.rendered_claim_semantic_audit import (
    CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
    ClaimResolution,
    ClaimStatus,
    DeterministicStatus,
    audit_rendered_claims,
    calibrate_lexically_supported_claims,
)
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore


def _evidence(chunk_id: str, content: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        asset_id="asset",
        title="设备检查",
        content=content,
        summary=content,
        keywords=[],
        subject="",
        material_block="",
        material_block_code="",
        recommended_chapter="",
        locator=EvidenceLocator(),
        score=EvidenceScore(),
    )


def _brief() -> list[dict]:
    return [{
        "occurrence_id": "occ:1",
        "source_chunk_ids": ["C1"],
        "semantic_delta_evidence_ids": [],
        "role": "TEACH",
        "section_id": "section:1",
    }]


def _markdown(body: str) -> str:
    return '<!-- occurrence:start id="occ:1" chapter="chapter:1" section="section:1" task="task:1" -->\n' + body + '\n<!-- occurrence:end id="occ:1" -->\n'


@dataclass
class FakeJudge:
    payload: dict
    calls: int = 0

    @property
    def call_count(self) -> int:
        return self.calls

    def judge(self, *, claim: str, evidence: list[dict[str, str]], context: dict[str, str]) -> dict:
        self.calls += 1
        return dict(self.payload)


def test_direct_lexical_support_does_not_call_semantic_judge():
    report = audit_rendered_claims(
        markdown=_markdown("设备连接状态需要检查。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "设备连接状态需要检查。")},
    )
    record = report.records[0]
    assert record.deterministic_result == DeterministicStatus.SUPPORTED
    assert record.final_status == ClaimStatus.SUPPORTED
    assert record.resolution == ClaimResolution.DETERMINISTIC
    assert report.semantic_candidates == 0


def test_strengthening_claim_is_semantic_candidate_and_keeps_partial():
    judge = FakeJudge({
        "status": "PARTIALLY_SUPPORTED",
        "supporting_evidence_ids": ["C1"],
        "rationale": "素材只支持降低风险，不支持完全避免。",
        "confidence": 0.91,
        "unsupported_part": "完全避免",
    })
    report = audit_rendered_claims(
        markdown=_markdown("检查设备可以完全避免故障。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "检查设备通常有助于降低故障风险。")},
        judge=judge,
    )
    record = report.records[0]
    assert record.deterministic_result == DeterministicStatus.UNCERTAIN
    assert record.final_status == ClaimStatus.PARTIALLY_SUPPORTED
    assert record.strengthening_signals
    assert judge.calls == 1


def test_semantic_support_does_not_require_summary_substring():
    judge = FakeJudge({
        "status": "SUPPORTED",
        "supporting_evidence_ids": ["C1"],
        "rationale": "改写后的实质含义与素材一致。",
        "confidence": 0.88,
    })
    report = audit_rendered_claims(
        markdown=_markdown("作业前应确认设备处于可用状态。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "开始操作前检查连接和电源。")},
        judge=judge,
    )
    record = report.records[0]
    assert record.final_status == ClaimStatus.SUPPORTED
    assert record.resolution == ClaimResolution.SEMANTIC
    assert report.semantic_supported == 1


def test_unauthorized_model_evidence_is_fail_closed():
    judge = FakeJudge({
        "status": "SUPPORTED",
        "supporting_evidence_ids": ["C999"],
        "rationale": "bad provenance",
        "confidence": 0.99,
    })
    report = audit_rendered_claims(
        markdown=_markdown("作业前应确认设备处于可用状态。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "开始操作前检查连接和电源。")},
        judge=judge,
    )
    record = report.records[0]
    assert record.final_status == ClaimStatus.UNSUPPORTED
    assert record.resolution == ClaimResolution.MODEL_ERROR
    assert not record.evidence_provenance_valid


def test_ambiguous_without_judge_is_not_counted_as_confirmed_unsupported():
    report = audit_rendered_claims(
        markdown=_markdown("作业前应确认设备处于可用状态。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "开始操作前检查连接和电源。")},
    )
    record = report.records[0]
    assert record.final_status == ClaimStatus.UNSUPPORTED
    assert record.resolution == ClaimResolution.SEMANTIC_UNRESOLVED
    assert report.truly_unsupported == 0
    assert report.unresolved_semantic == 1


def test_lexical_calibration_is_blind_and_marks_false_positive():
    class RecordingJudge(FakeJudge):
        def __init__(self, payload):
            super().__init__(payload)
            self.inputs = []

        def judge(self, *, claim, evidence, context):
            self.inputs.append({"claim": claim, "evidence": evidence, "context": context})
            return super().judge(claim=claim, evidence=evidence, context=context)

    judge = RecordingJudge({
        "status": "PARTIALLY_SUPPORTED",
        "supporting_evidence_ids": ["C1"],
        "rationale": "仅部分支持",
        "confidence": 0.8,
    })
    baseline = audit_rendered_claims(
        markdown=_markdown("设备连接状态需要检查。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "设备连接状态需要检查。")},
    )
    report = calibrate_lexically_supported_claims(
        audit=baseline,
        briefs=_brief(),
        judge=judge,
        sample_size=1,
        seed=1,
    )
    assert report.sample_size == 1
    assert report.false_positive_count == 1
    assert report.records[0].lexical_result == "SUPPORTED"
    assert "lexical" not in json.dumps(judge.inputs, ensure_ascii=False).lower()


def test_calibrated_routing_category_can_override_lexical_shortcut():
    judge = FakeJudge({
        "status": "PARTIALLY_SUPPORTED",
        "supporting_evidence_ids": ["C1"],
        "rationale": "范围扩大",
        "confidence": 0.8,
    })
    report = audit_rendered_claims(
        markdown=_markdown("通常需要检查设备。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "通常需要检查设备。")},
        judge=judge,
        semantic_routing_categories=("procedural",),
    )
    assert report.records[0].deterministic_result == DeterministicStatus.SUPPORTED
    assert report.records[0].resolution == ClaimResolution.SEMANTIC
    assert report.records[0].final_status == ClaimStatus.PARTIALLY_SUPPORTED


def test_calibrated_routing_policy_is_recorded_in_audit_summary():
    report = audit_rendered_claims(
        markdown=_markdown("设备连接状态需要检查。"),
        briefs=_brief(),
        evidence_by_id={"C1": _evidence("C1", "设备连接状态需要检查。")},
        semantic_routing_categories=CALIBRATED_SEMANTIC_ROUTING_CATEGORIES,
    )
    summary = report.to_dict()["summary"]
    assert summary["semantic_routing_categories"] == sorted(CALIBRATED_SEMANTIC_ROUTING_CATEGORIES)
