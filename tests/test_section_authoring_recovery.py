from __future__ import annotations

from copy import deepcopy

from tests.test_section_authoring import _chunk, _draft, _inputs

from materials2textbook.knowledge_map.section_authoring import materialize_section_draft
from materials2textbook.knowledge_map.section_authoring_recovery import (
    ACCEPTED,
    CLAIM_OVERREACH,
    CONTRACT_BLOCK,
    PARTIAL_EVIDENCE,
    PATCH_BLOCK,
    ROLLED_BACK,
    SKIPPED,
    SOURCE_GAP_REVIEW,
    SOURCE_GAP,
    FORBIDDEN_RETEACH,
    LOCAL_CONFORMANCE_FAILURE,
    WRITER_UNDERDELIVERY,
    apply_section_recovery,
    classify_section_recovery,
    plan_section_recovery,
)


RICH_EVIDENCE = (
    "Explain the basic operation and its quality conditions. "
    "Complete the basic operation and verify the result. "
    "Make the declared parameters, conditions, constraints, or rules usable in this section. "
    "Observe or judge the quality result conditions named by the section. "
    "Assess the observable operation and quality judgment. "
    "Observe the operation and its result. "
    "Summarize the achieved learning outcome: explain the basic operation and identify quality conditions."
)


class _AcceptingJudge:
    model = "fixture-judge"
    call_count = 0

    def judge(self, *, claim, evidence, context):
        self.call_count += 1
        if "always guarantees" in claim.lower():
            return {
                "status": "UNSUPPORTED",
                "supporting_evidence_ids": [],
                "rationale": "The authorized evidence does not support the absolute claim.",
                "confidence": 1.0,
                "unsupported_part": claim,
            }
        return {
            "status": "SUPPORTED",
            "supporting_evidence_ids": [item["evidence_id"] for item in evidence],
            "rationale": "The fixture evidence directly supports the claim.",
            "confidence": 1.0,
        }


def _ready_inputs():
    return _inputs(chunks=[_chunk("chunk-1", RICH_EVIDENCE)], constraints=[{"occurrence_id": "occ-1", "role": "TEACH"}])


def _all_obligation_block(brief, text: str):
    ids = [item.obligation_id for item in brief.required_obligations]
    return {
        "block_id": "teaching-block",
        "text": text,
        "intended_obligation_ids": ids,
        "intended_occurrence_ids": ["occ-1"],
        "evidence_ids": ["E1"],
    }


def _draft_with_body(brief, text: str):
    return {
        "blocks": [_all_obligation_block(brief, text)],
        "generation_provenance": {"writer": "fixture-writer", "model": "fixture"},
    }


def test_classifies_supported_evidence_missing_obligation_as_writer_underdelivery():
    _, _, packet, brief = _ready_inputs()
    draft = _draft_with_body(brief, "A short note.")
    rendered = materialize_section_draft(brief, packet, draft, claim_judge=_AcceptingJudge())
    issues = classify_section_recovery(rendered, brief, packet)
    assert any(item.code == WRITER_UNDERDELIVERY for item in issues)
    proposals = plan_section_recovery(issues, brief=brief, packet=packet)
    assert any(item.action == PATCH_BLOCK and item.retry_allowed for item in proposals)


def test_targeted_writer_patch_accepts_only_after_full_revalidation():
    _, _, packet, brief = _ready_inputs()
    original = _draft_with_body(brief, "A short note.")
    judge = _AcceptingJudge()
    rendered = materialize_section_draft(brief, packet, original, claim_judge=judge)
    issue = next(item for item in classify_section_recovery(rendered, brief, packet) if item.code == WRITER_UNDERDELIVERY)
    proposal = next(item for item in plan_section_recovery([issue], brief=brief, packet=packet) if item.action == PATCH_BLOCK)
    complete = RICH_EVIDENCE
    attempt = apply_section_recovery(
        brief=brief,
        packet=packet,
        original_draft=original,
        proposal=proposal,
        replacement_text=complete,
        claim_judge=judge,
        occurrence_conformance_checker=lambda section: True,
    )
    assert attempt.status == ACCEPTED
    assert attempt.post_rendered is not None
    assert attempt.post_rendered.generation_provenance["verified_availability_eligible"] is True
    assert attempt.revalidation["availability_granted_by_repair"] is False
    assert attempt.post_rendered.packet_id == packet.packet_id


def test_claim_overreach_is_contracted_at_block_scope_and_can_pass():
    _, _, packet, brief = _ready_inputs()
    original = _draft_with_body(
        brief,
        RICH_EVIDENCE + " This operation always guarantees every result.",
    )
    judge = _AcceptingJudge()
    rendered = materialize_section_draft(brief, packet, original, claim_judge=judge)
    issue = next(item for item in classify_section_recovery(rendered, brief, packet) if item.code == CLAIM_OVERREACH)
    assert issue.block_id == "teaching-block"
    proposal = plan_section_recovery([issue], brief=brief, packet=packet)[0]
    assert proposal.action == CONTRACT_BLOCK
    attempt = apply_section_recovery(
        brief=brief,
        packet=packet,
        original_draft=original,
        proposal=proposal,
        claim_judge=judge,
        occurrence_conformance_checker=lambda section: True,
    )
    assert attempt.status == ACCEPTED
    assert "always guarantees" not in attempt.candidate_text
    assert attempt.post_rendered is not None


def test_source_gap_never_triggers_writer_retry():
    _, _, packet, brief = _inputs(chunks=[], authorized=[])
    rendered = materialize_section_draft(brief, packet, {"blocks": []})
    issues = classify_section_recovery(rendered, brief, packet)
    source_issue = next(item for item in issues if item.code == SOURCE_GAP)
    proposal = plan_section_recovery([source_issue], brief=brief, packet=packet)[0]
    assert proposal.action == SOURCE_GAP_REVIEW
    assert proposal.retry_allowed is False
    attempt = apply_section_recovery(
        brief=brief,
        packet=packet,
        original_draft={"blocks": []},
        proposal=proposal,
        replacement_text="This must never be generated from model common sense.",
        occurrence_conformance_checker=lambda section: True,
    )
    assert attempt.status == SKIPPED


def test_partial_evidence_is_not_misclassified_as_writer_underdelivery():
    _, _, packet, brief = _inputs(chunks=[_chunk("chunk-1", "basic operation")])
    rendered = materialize_section_draft(brief, packet, _draft(brief, body="The basic operation is introduced."))
    issues = classify_section_recovery(rendered, brief, packet)
    assert any(item.code == PARTIAL_EVIDENCE for item in issues)
    assert not any(item.code == WRITER_UNDERDELIVERY and item.evidence_status == "PARTIAL_OBLIGATION" for item in issues)


def test_unauthorized_patch_rolls_back_without_expanding_evidence_scope():
    _, _, packet, brief = _ready_inputs()
    original = _draft_with_body(brief, "A short note.")
    judge = _AcceptingJudge()
    rendered = materialize_section_draft(brief, packet, original, claim_judge=judge)
    issue = next(item for item in classify_section_recovery(rendered, brief, packet) if item.code == WRITER_UNDERDELIVERY)
    proposal = next(item for item in plan_section_recovery([issue], brief=brief, packet=packet) if item.action == PATCH_BLOCK)
    unauthorized = deepcopy(original["blocks"][0])
    unauthorized["text"] = RICH_EVIDENCE
    unauthorized["evidence_ids"] = ["not-authorized"]
    attempt = apply_section_recovery(
        brief=brief,
        packet=packet,
        original_draft=original,
        proposal=proposal,
        replacement_block=unauthorized,
        claim_judge=judge,
        occurrence_conformance_checker=lambda section: True,
    )
    assert attempt.status == ROLLED_BACK
    assert (
        "MATERIALIZATION_FAILED" in attempt.rollback_reason
        or "EVIDENCE_IDS_MUTATED" in attempt.rollback_reason
    )
    assert attempt.post_rendered is None


def test_forbidden_reteach_and_local_conformance_have_local_proposals():
    _, _, packet, brief = _ready_inputs()
    rendered = materialize_section_draft(
        brief,
        packet,
        _draft_with_body(brief, RICH_EVIDENCE),
        claim_judge=_AcceptingJudge(),
    )
    issues = classify_section_recovery(
        rendered,
        brief,
        packet,
        conformance_issues=[
            {"rule": "FORBIDDEN_RETEACH", "block_id": "teaching-block", "sentence": "Repeated definition."},
            {"rule": "MISSING_REQUIRED_FACET", "block_id": "teaching-block"},
        ],
    )
    assert any(item.code == FORBIDDEN_RETEACH for item in issues)
    assert any(item.code == LOCAL_CONFORMANCE_FAILURE for item in issues)
    proposals = plan_section_recovery(issues, brief=brief, packet=packet)
    assert any(item.action == CONTRACT_BLOCK for item in proposals)
    assert any(item.action == PATCH_BLOCK for item in proposals)
