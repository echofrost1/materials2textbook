from __future__ import annotations

from dataclasses import replace

from materials2textbook.knowledge_map.publication_quality import (
    PublicationQualityCode,
    integrate_rendered_claim_semantic_audit,
)
from materials2textbook.knowledge_map.publication_quality_models import PublicationQualityReport
from materials2textbook.knowledge_map.rendered_claim_semantic_audit import (
    ClaimEvidenceSpan,
    ClaimResolution,
    ClaimStatus,
    DeterministicStatus,
    RenderedClaimAuditRecord,
    RenderedClaimSemanticEvidenceAudit,
)


def _audit(status: str) -> RenderedClaimSemanticEvidenceAudit:
    record = RenderedClaimAuditRecord(
        claim_id="occ:1:claim:1",
        occurrence_id="occ:1",
        claim_text="当前 claim",
        claim_type="SOURCE_FACT",
        authorized_evidence_ids=("C1",),
        evidence_spans=(ClaimEvidenceSpan("C1", "evidence"),),
        supporting_span="evidence",
        deterministic_result=DeterministicStatus.UNCERTAIN,
        semantic_result=status,
        final_status=status,
        resolution=ClaimResolution.SEMANTIC,
        unsupported_part="部分不受支持" if status != ClaimStatus.SUPPORTED else "",
        rationale="fixture",
        confidence=0.9,
    )
    return RenderedClaimSemanticEvidenceAudit(
        artifact_root="fixture",
        records=[record],
        qwen_call_count=1,
        qwen_model="Qwen3-32B-AWQ",
    )


def test_partial_claim_is_review_blocker_and_supported_claim_passes():
    report = PublicationQualityReport()
    report.semantic_closed_loop_status = "PASS"
    integrated = integrate_rendered_claim_semantic_audit(
        report=report,
        rendered_claim_audit=_audit(ClaimStatus.PARTIALLY_SUPPORTED),
    )
    assert integrated.publication_quality_status == "FAIL"
    assert integrated.final_publication_status == "FAIL"
    assert any(item.code == PublicationQualityCode.PARTIALLY_SUPPORTED_RENDERED_SEMANTIC_CLAIM for item in integrated.blockers)

    clean = PublicationQualityReport()
    clean.semantic_closed_loop_status = "PASS"
    integrated_clean = integrate_rendered_claim_semantic_audit(
        report=clean,
        rendered_claim_audit=_audit(ClaimStatus.SUPPORTED),
    )
    assert integrated_clean.publication_quality_status == "PASS"
    assert integrated_clean.final_publication_status == "PASS"


def test_invalid_claim_audit_is_fail_closed():
    audit = _audit(ClaimStatus.SUPPORTED)
    record = audit.records[0]
    audit.records[0] = replace(record, evidence_provenance_valid=False)
    report = PublicationQualityReport()
    report.semantic_closed_loop_status = "PASS"
    integrated = integrate_rendered_claim_semantic_audit(report=report, rendered_claim_audit=audit)
    assert integrated.publication_quality_status == "FAIL"
    assert any(item.code == PublicationQualityCode.INVALID_RENDERED_CLAIM_SEMANTIC_AUDIT for item in integrated.blockers)
