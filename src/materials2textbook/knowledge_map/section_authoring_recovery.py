"""Bounded recovery for Phase 5D section authoring failures.

The recovery layer is deliberately local and fail-closed.  It classifies the
already materialized section, proposes a block/claim-sized action, and (when a
caller supplies a constrained patch) re-runs the existing section materializer
and claim auditor.  It never replans a Blueprint, expands an Evidence Packet,
changes an occurrence role, or grants runtime availability itself.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import difflib
from typing import Any, Callable, Iterable, Mapping

from materials2textbook.knowledge_map.evidence_packet import (
    PARTIAL_OBLIGATION,
    SOURCE_GAP,
    SUPPORTED_OBLIGATION,
    SectionEvidencePacket,
)
from materials2textbook.knowledge_map.section_authoring import (
    RENDERED,
    RenderedSection,
    SectionAuthoringBrief,
    SectionAuthoringError,
    materialize_section_draft,
)


# Recovery classifications are intentionally plain strings so reports remain
# stable across the existing Phase 3/4 repair artifacts.
WRITER_UNDERDELIVERY = "WRITER_UNDERDELIVERY"
CLAIM_OVERREACH = "CLAIM_OVERREACH"
PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"
SOURCE_GAP = "SOURCE_GAP"
FORBIDDEN_RETEACH = "FORBIDDEN_RETEACH"
LOCAL_CONFORMANCE_FAILURE = "LOCAL_CONFORMANCE_FAILURE"

PATCH_BLOCK = "PATCH_BLOCK"
# A caller-supplied, block-local replacement whose inputs must remain the
# original Brief/Packet/evidence scope.  It is deliberately distinct from a
# whole-section writer retry and is accepted by the same deterministic gate.
MINIMAL_SUPPORTED_REWRITE = "MINIMAL_SUPPORTED_REWRITE"
CONTRACT_BLOCK = "CONTRACT_BLOCK"
EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
SOURCE_GAP_REVIEW = "SOURCE_GAP_REVIEW"
MANUAL_REVIEW = "MANUAL_REVIEW"

ACCEPTED = "ACCEPTED"
ROLLED_BACK = "ROLLED_BACK"
SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class SectionRecoveryIssue:
    """One auditable failure located at section/block/claim granularity."""

    issue_id: str
    section_id: str
    code: str
    reason: str
    block_id: str = ""
    obligation_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    target_text: str = ""
    evidence_ids: tuple[str, ...] = ()
    evidence_status: str = ""
    retry_allowed: bool = False
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["obligation_ids"] = list(self.obligation_ids)
        data["claim_ids"] = list(self.claim_ids)
        data["evidence_ids"] = list(self.evidence_ids)
        data["provenance"] = deepcopy(dict(self.provenance))
        return data


@dataclass(frozen=True)
class SectionRecoveryProposal:
    """A local action; upstream semantic objects are immutable inputs."""

    proposal_id: str
    issue_id: str
    section_id: str
    action: str
    block_id: str
    obligation_ids: tuple[str, ...]
    allowed_evidence_ids: tuple[str, ...]
    target_text: str = ""
    reason: str = ""
    retry_allowed: bool = False
    immutable_inputs: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["obligation_ids"] = list(self.obligation_ids)
        data["allowed_evidence_ids"] = list(self.allowed_evidence_ids)
        data["immutable_inputs"] = deepcopy(dict(self.immutable_inputs))
        data["provenance"] = deepcopy(dict(self.provenance))
        return data


@dataclass(frozen=True)
class SectionRecoveryAttempt:
    """Complete audit for one local candidate and its revalidation."""

    proposal_id: str
    section_id: str
    action: str
    original_text: str
    candidate_text: str
    diff: str
    targeted_issue_ids: tuple[str, ...]
    pre_rendered: RenderedSection | None
    post_rendered: RenderedSection | None
    pre_occurrence_conformance: Any = None
    post_occurrence_conformance: Any = None
    status: str = ROLLED_BACK
    rollback_reason: str = ""
    revalidation: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "section_id": self.section_id,
            "action": self.action,
            "original_text": self.original_text,
            "candidate_text": self.candidate_text,
            "diff": self.diff,
            "targeted_issue_ids": list(self.targeted_issue_ids),
            "pre_rendered": self.pre_rendered.to_dict() if self.pre_rendered else None,
            "post_rendered": self.post_rendered.to_dict() if self.post_rendered else None,
            "pre_occurrence_conformance": _json_value(self.pre_occurrence_conformance),
            "post_occurrence_conformance": _json_value(self.post_occurrence_conformance),
            "status": self.status,
            "rollback_reason": self.rollback_reason,
            "revalidation": deepcopy(dict(self.revalidation)),
            "provenance": deepcopy(dict(self.provenance)),
        }


def classify_section_recovery(
    rendered: RenderedSection,
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    *,
    conformance_issues: Iterable[Any] = (),
) -> tuple[SectionRecoveryIssue, ...]:
    """Classify failures without retrying the writer or changing content.

    A supported binding plus a missing objective signal is writer
    underdelivery.  A partial/source-gap binding is never relabelled as a
    writer problem.  Claim-level results are taken from the existing Phase 4B
    audit embedded by the section materializer.
    """

    _validate_identity(rendered, brief, packet)
    bindings = {item.obligation_id: item for item in packet.obligation_bindings}
    coverage = rendered.generation_provenance.get("obligation_coverage", {})
    issues: list[SectionRecoveryIssue] = []

    # Source gaps are reported first and never receive a writer retry.
    for obligation in brief.required_obligations:
        binding = bindings.get(obligation.obligation_id)
        if binding is None or binding.status != SOURCE_GAP:
            continue
        issues.append(
            _issue(
                rendered,
                code=SOURCE_GAP,
                reason="required obligation has no authorized evidence; writer retry is forbidden",
                obligation_ids=(obligation.obligation_id,),
                evidence_status=SOURCE_GAP,
                retry_allowed=False,
                provenance={"source": "section evidence packet", "route": "source-gap handling"},
            )
        )

    # Required obligation coverage is deterministic and model associations do
    # not count as completion.  Supported evidence + VIOLATION is precisely
    # the writer-underdelivery case.
    for obligation in brief.required_obligations:
        obligation_id = obligation.obligation_id
        binding = bindings.get(obligation_id)
        if not binding or binding.status == SOURCE_GAP:
            continue
        if str(coverage.get(obligation_id, "VIOLATION")) != "VIOLATION":
            continue
        code = WRITER_UNDERDELIVERY if binding.status == SUPPORTED_OBLIGATION else PARTIAL_EVIDENCE
        target_block_id = _first_block_for_obligation(rendered, obligation_id)
        issues.append(
            _issue(
                rendered,
                code=code,
                reason=(
                    "authorized evidence is sufficient but the required obligation was not delivered"
                    if code == WRITER_UNDERDELIVERY
                    else "the obligation binding is partial; only the supported portion may be authored"
                ),
                block_id=target_block_id,
                obligation_ids=(obligation_id,),
                evidence_ids=tuple(binding.accepted_evidence_ids),
                evidence_status=binding.status,
                retry_allowed=code == WRITER_UNDERDELIVERY,
                provenance={"source": "deterministic obligation coverage", "coverage": coverage.get(obligation_id)},
            )
        )

    # Claim audit records are already occurrence/block-local.  Unsupported or
    # partially supported claims in a fully supported binding are safe
    # contraction candidates; partial packet bindings remain PARTIAL_EVIDENCE.
    for claim in rendered.generation_provenance.get("local_claim_evidence_audit", ()) or ():
        status = str(claim.get("final_status") or "").upper()
        if status not in {"UNSUPPORTED", "PARTIALLY_SUPPORTED"}:
            continue
        block_id = _claim_block_id(str(claim.get("occurrence_id") or ""), rendered.section_id)
        obligation_ids = _block_obligation_ids(rendered, block_id)
        binding_statuses = [bindings[item].status for item in obligation_ids if item in bindings]
        code = PARTIAL_EVIDENCE if PARTIAL_OBLIGATION in binding_statuses else CLAIM_OVERREACH
        target_text = str(claim.get("source_span") or claim.get("claim_text") or "").strip()
        issues.append(
            _issue(
                rendered,
                code=code,
                reason=(
                    "the authorized evidence supports only part of the rendered claim"
                    if code == PARTIAL_EVIDENCE
                    else "rendered claim exceeds the authorized evidence and can only be contracted locally"
                ),
                block_id=block_id,
                obligation_ids=tuple(obligation_ids),
                claim_ids=(str(claim.get("claim_id") or ""),),
                target_text=target_text,
                evidence_ids=tuple(claim.get("authorized_evidence_ids") or ()),
                evidence_status=(binding_statuses[0] if binding_statuses else ""),
                retry_allowed=code == CLAIM_OVERREACH,
                provenance={"source": "Phase 4B local claim audit", "claim": deepcopy(dict(claim))},
            )
        )

    # A claim may be source-supported yet still be outside the approved
    # factual realization plan.  Route it through the same block-local
    # overreach recovery, but preserve the plan-specific reason for audit.
    for plan_claim in rendered.generation_provenance.get("factual_plan_conformance", ()) or ():
        if str(plan_claim.get("status") or "") != "PLAN_OUTSIDE":
            continue
        block_id = str(plan_claim.get("block_id") or "")
        if any(
            issue.block_id == block_id
            and issue.target_text == str(plan_claim.get("claim_text") or "")
            for issue in issues
        ):
            continue
        issues.append(
            _issue(
                rendered,
                code=CLAIM_OVERREACH,
                reason="rendered source fact is outside the approved factual claim plan",
                block_id=block_id,
                target_text=str(plan_claim.get("claim_text") or ""),
                retry_allowed=bool(block_id and plan_claim.get("claim_text")),
                provenance={"source": "factual claim plan conformance", "plan_claim": deepcopy(dict(plan_claim))},
            )
        )

    explicit = list(conformance_issues)
    for item in explicit:
        code, reason = _classify_explicit_issue(item)
        block_id = str(item.get("block_id") or "") if isinstance(item, Mapping) else ""
        target_text = str(item.get("text") or item.get("sentence") or "") if isinstance(item, Mapping) else ""
        issues.append(
            _issue(
                rendered,
                code=code,
                reason=reason,
                block_id=block_id,
                target_text=target_text,
                retry_allowed=code in {FORBIDDEN_RETEACH, LOCAL_CONFORMANCE_FAILURE},
                provenance={"source": "occurrence conformance", "raw": _json_value(item)},
            )
        )

    # A materializer may have a forbidden-reteach reason only when a caller
    # supplied a checker result (the materializer itself rejects it).  Keep the
    # classification available for runtime reporting and tests.
    for reason in rendered.block_reasons:
        upper = str(reason).upper()
        if "FORBIDDEN_RETEACH" in upper or "RETEACH" in upper:
            issues.append(
                _issue(
                    rendered,
                    code=FORBIDDEN_RETEACH,
                    reason="forbidden reteach must be removed/compressed at block scope",
                    retry_allowed=True,
                    provenance={"source": "rendered block reason", "raw": reason},
                )
            )

    return _dedupe_issues(issues)


def plan_section_recovery(
    issues: Iterable[SectionRecoveryIssue],
    *,
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
) -> tuple[SectionRecoveryProposal, ...]:
    """Compile classifications into local actions, never a whole-section rewrite."""

    proposals: list[SectionRecoveryProposal] = []
    for issue in issues:
        if issue.code == WRITER_UNDERDELIVERY:
            action, retry = PATCH_BLOCK, True
        elif issue.code == CLAIM_OVERREACH:
            action, retry = CONTRACT_BLOCK, bool(issue.block_id and issue.target_text)
        elif issue.code == FORBIDDEN_RETEACH:
            action, retry = CONTRACT_BLOCK, bool(issue.block_id and issue.target_text)
        elif issue.code == PARTIAL_EVIDENCE:
            # A partial packet is not permission to invent facts.  When the
            # local audit identifies an exact source-fact span, the safest
            # bounded action is to remove that overreaching sentence and
            # revalidate.  Otherwise retain the evidence-review route.
            claim = issue.provenance.get("claim") if isinstance(issue.provenance, Mapping) else {}
            claim_type = str(claim.get("content_type") or "") if isinstance(claim, Mapping) else ""
            if issue.block_id and issue.target_text and claim_type in {
                "SOURCE_FACT",
                "UNSUPPORTED_DOMAIN_CLAIM",
            }:
                action, retry = CONTRACT_BLOCK, True
            else:
                action, retry = EVIDENCE_REVIEW, False
        elif issue.code == SOURCE_GAP:
            action, retry = SOURCE_GAP_REVIEW, False
        else:
            action, retry = PATCH_BLOCK, True
        allowed = _allowed_evidence_for(issue, brief, packet)
        proposal = SectionRecoveryProposal(
            proposal_id=f"section-recovery:{brief.outline_node_id}:{issue.issue_id}",
            issue_id=issue.issue_id,
            section_id=brief.outline_node_id,
            action=action,
            block_id=issue.block_id,
            obligation_ids=issue.obligation_ids,
            allowed_evidence_ids=allowed,
            target_text=issue.target_text,
            reason=issue.reason,
            retry_allowed=retry,
            immutable_inputs={
                "brief_id": brief.brief_id,
                "packet_id": packet.packet_id,
                "source_book_plan_signature": brief.source_book_plan_signature,
                "occurrence_constraints_immutable": True,
                "role_replanning": False,
                "evidence_scope_expansion": False,
            },
            provenance={
                "generator": "phase5e-section-recovery-planner-v1",
                "recovery_granularity": "block_or_obligation",
                "writer_retry_requires_same_brief_and_packet": True,
                "source_issue": issue.to_dict(),
            },
        )
        proposals.append(proposal)
    return tuple(proposals)


def apply_section_recovery(
    *,
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    original_draft: Mapping[str, Any],
    proposal: SectionRecoveryProposal,
    replacement_text: str | None = None,
    replacement_block: Mapping[str, Any] | None = None,
    claim_judge: Any | None = None,
    occurrence_conformance_checker: Callable[[RenderedSection], Any] | None = None,
    allow_unresolved_other_issues: bool = False,
) -> SectionRecoveryAttempt:
    """Apply one caller-supplied local patch and fully revalidate it.

    No model is called here.  A future bounded writer may produce
    ``replacement_text``/``replacement_block`` under the same brief and
    packet; this function remains the single deterministic acceptance gate.
    """

    if proposal.section_id != brief.outline_node_id or proposal.issue_id == "":
        return _attempt_without_render(
            proposal, brief, original_draft, status=SKIPPED, reason="PROPOSAL_INPUT_MISMATCH"
        )
    if proposal.action in {SOURCE_GAP_REVIEW, EVIDENCE_REVIEW, MANUAL_REVIEW} or not proposal.retry_allowed:
        return _attempt_without_render(
            proposal, brief, original_draft, status=SKIPPED, reason="RECOVERY_REQUIRES_SOURCE_OR_MANUAL_REVIEW"
        )

    pre_rendered, pre_error = _safe_materialize(brief, packet, original_draft, claim_judge=claim_judge)
    original_text = _visible_text(pre_rendered) if pre_rendered else _draft_visible_text(original_draft)
    pre_conformance = _run_checker(occurrence_conformance_checker, pre_rendered)
    candidate_draft = deepcopy(dict(original_draft))
    try:
        _apply_block_patch(
            candidate_draft,
            proposal,
            replacement_text=replacement_text,
            replacement_block=replacement_block,
        )
    except (ValueError, TypeError) as exc:
        return _attempt(
            proposal, original_text, original_text, pre_rendered, None, pre_conformance, None,
            status=ROLLED_BACK, reason=str(exc), targeted=(proposal.issue_id,),
        )

    post_rendered, post_error = _safe_materialize(brief, packet, candidate_draft, claim_judge=claim_judge)
    candidate_text = _visible_text(post_rendered) if post_rendered else _draft_visible_text(candidate_draft)
    post_conformance = _run_checker(occurrence_conformance_checker, post_rendered)
    if post_error:
        return _attempt(
            proposal, original_text, candidate_text, pre_rendered, None, pre_conformance, post_conformance,
            status=ROLLED_BACK, reason=f"MATERIALIZATION_FAILED:{post_error}", targeted=(proposal.issue_id,),
        )
    if occurrence_conformance_checker is None:
        return _attempt(
            proposal, original_text, candidate_text, pre_rendered, post_rendered, pre_conformance, post_conformance,
            status=ROLLED_BACK, reason="OCCURRENCE_CONFORMANCE_CHECKER_REQUIRED", targeted=(proposal.issue_id,),
        )

    # A source-bounded section may remain RENDERED_PARTIAL because an
    # obligation binding is intentionally partial.  A local rewrite can be
    # accepted when the occurrence itself and every remaining claim pass; it
    # must not be promoted to a whole-section completeness result.
    allow_partial_section_recovery = (
        post_rendered is not None
        and post_rendered.render_status == RENDERED
        or post_rendered is not None
        and post_rendered.render_status == "RENDERED_PARTIAL"
    )
    reason = _acceptance_failure(
        post_rendered,
        post_conformance,
        brief,
        packet,
        allow_partial_section_recovery=allow_partial_section_recovery,
        target_block_id=proposal.block_id,
        target_text=proposal.target_text,
        allow_unresolved_other_issues=allow_unresolved_other_issues,
    )
    status = ACCEPTED if reason == "" else ROLLED_BACK
    return _attempt(
        proposal,
        original_text,
        candidate_text,
        pre_rendered,
        post_rendered,
        pre_conformance,
        post_conformance,
        status=status,
        reason=reason,
        targeted=(proposal.issue_id,),
    )


def _acceptance_failure(
    rendered: RenderedSection | None,
    occurrence_conformance: Any,
    brief: SectionAuthoringBrief,
    packet: SectionEvidencePacket,
    *,
    allow_partial_section_recovery: bool = False,
    target_block_id: str = "",
    target_text: str = "",
    allow_unresolved_other_issues: bool = False,
) -> str:
    if rendered is None:
        return "NO_POST_RENDERED_SECTION"
    if not _conformance_passes(occurrence_conformance):
        return "OCCURRENCE_CONFORMANCE_NOT_MATCH"
    # RENDERED_PARTIAL is a legitimate section result when the remaining
    # partial binding is optional/downstream.  It must still have no blocked
    # core content and all accepted claims/occurrence checks must pass before
    # a repair can be accepted.
    if rendered.render_status not in {RENDERED, "RENDERED_PARTIAL"}:
        return "SECTION_CONFORMANCE_NOT_MATCH"
    if rendered.blocked and not allow_partial_section_recovery:
        return "SECTION_CONFORMANCE_NOT_MATCH"
    required_coverage = rendered.generation_provenance.get("obligation_coverage", {})
    required_ids = {item.obligation_id for item in brief.required_obligations}
    if allow_unresolved_other_issues:
        # A section may need several independent local repairs.  Validate the
        # candidate block itself now, then let the caller re-run the complete
        # section gate after all accepted patches have been applied.
        target_obligation_ids = {
            str(obligation_id)
            for obligation_id, spans in rendered.obligation_span_map.items()
            if target_block_id
            and any(str(span.get("block_id") or span.get("span_id") or "") == target_block_id for span in spans)
        }
        if any(
            required_coverage.get(obligation_id, "VIOLATION") != "MATCH"
            for obligation_id in target_obligation_ids
            if obligation_id in required_ids
        ):
            return "REQUIRED_OBLIGATION_NOT_MATCH"
    else:
        if any(
            required_coverage.get(item.obligation_id, "VIOLATION") != "MATCH"
            for item in packet.obligation_bindings
            if item.obligation_id in required_ids
            and item.status not in {SOURCE_GAP, PARTIAL_OBLIGATION}
        ):
            return "REQUIRED_OBLIGATION_NOT_MATCH"
        if any(
            item.status == PARTIAL_OBLIGATION
            and required_coverage.get(item.obligation_id, "VIOLATION") == "VIOLATION"
            and not allow_partial_section_recovery
            for item in packet.obligation_bindings
            if item.obligation_id in required_ids
        ):
            return "REQUIRED_OBLIGATION_NOT_MATCH"
    # Optional/downstream source gaps are retained in the audit but do not
    # invalidate a safe local rewrite for a supported occurrence.  Only a
    # source gap on a REQUIRED obligation may block this recovery gate.
    required_binding_ids = {item.obligation_id for item in brief.required_obligations}
    if any(
        item.status == SOURCE_GAP and item.obligation_id in required_binding_ids
        for item in packet.obligation_bindings
    ):
        return "SOURCE_GAP_REMAINS"
    counts = rendered.generation_provenance.get("local_claim_status_counts", {})
    if allow_unresolved_other_issues:
        target_claims = [
            item
            for item in rendered.generation_provenance.get("local_claim_evidence_audit", ()) or ()
            if _claim_block_id(str(item.get("occurrence_id") or ""), rendered.section_id) == target_block_id
        ]
        # A block can contain several independent offending claims.  One
        # bounded patch should be accepted when it removed/replaced its exact
        # target; the next proposal in the same round handles the remaining
        # targets.  Do not require the whole block to be clean before the
        # first patch, otherwise every multi-claim block rolls back forever.
        normalized_target = " ".join(str(target_text or "").replace("\\n", " ").split())
        if normalized_target and any(
            normalized_target in " ".join(
                str(item.get(field) or "").replace("\\n", " ").split()
            )
            for item in target_claims
            for field in ("claim_text", "source_span")
        ):
            return "LOCAL_CLAIM_EVIDENCE_NOT_SUPPORTED"
    elif int(counts.get("PARTIALLY_SUPPORTED", 0) or 0) or int(counts.get("UNSUPPORTED", 0) or 0):
        return "LOCAL_CLAIM_EVIDENCE_NOT_SUPPORTED"
    if not rendered.generation_provenance.get("verified_availability_eligible", False) and not allow_partial_section_recovery:
        return "VERIFIED_AVAILABILITY_NOT_ELIGIBLE"
    return ""


def apply_recovery_patch_to_draft(
    original_draft: Mapping[str, Any],
    proposal: SectionRecoveryProposal,
    *,
    replacement_text: str | None = None,
    replacement_block: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the same deterministic local patch used by the acceptance gate.

    The execution orchestrator uses this helper only after an ACCEPTED local
    attempt so subsequent block repairs build on the accepted candidate.  It
    does not validate or widen any evidence/obligation scope.
    """
    candidate = deepcopy(dict(original_draft))
    _apply_block_patch(
        candidate,
        proposal,
        replacement_text=replacement_text,
        replacement_block=replacement_block,
    )
    return candidate


def _apply_block_patch(
    draft: dict[str, Any],
    proposal: SectionRecoveryProposal,
    *,
    replacement_text: str | None,
    replacement_block: Mapping[str, Any] | None,
) -> None:
    raw_blocks = draft.get("blocks")
    if not isinstance(raw_blocks, list):
        raise ValueError("ORIGINAL_DRAFT_BLOCKS_MISSING")
    blocks = deepcopy(raw_blocks)
    target_index = next(
        (index for index, item in enumerate(blocks) if isinstance(item, Mapping) and str(item.get("block_id") or "") == proposal.block_id),
        None,
    )
    if proposal.action == CONTRACT_BLOCK:
        if target_index is None or not proposal.target_text:
            raise ValueError("EXACT_TARGET_BLOCK_OR_TEXT_MISSING")
        current = str(blocks[target_index].get("text") or "")
        # Claim auditors may serialize paragraph boundaries as literal ``\\n``
        # escapes while the deterministic materializer stores real newline
        # characters.  Normalize only that representation detail; the target
        # text itself must still match exactly and no fuzzy replacement is
        # performed.
        target_text = proposal.target_text
        if target_text not in current and "\\n" in target_text:
            target_text = target_text.replace("\\n", "\n")
        if target_text not in current:
            raise ValueError("EXACT_TARGET_TEXT_NOT_FOUND")
        # CONTRACT_BLOCK is normally the deterministic safe deletion path.
        # A claim-plan-constrained recovery writer may instead provide a
        # replacement for the exact offending span.  The target match remains
        # exact and block metadata is never mutable, so this does not widen
        # the recovery contract.
        replacement = str(replacement_text or "").strip()
        candidate = current.replace(target_text, replacement, 1).strip()
        if not candidate:
            blocks.pop(target_index)
        else:
            blocks[target_index] = {**dict(blocks[target_index]), "text": candidate}
    elif proposal.action in {PATCH_BLOCK, MINIMAL_SUPPORTED_REWRITE}:
        if replacement_block is not None:
            patch = dict(replacement_block)
            block_id = str(patch.get("block_id") or proposal.block_id).strip()
            if not block_id:
                raise ValueError("PATCH_BLOCK_ID_MISSING")
            if str(patch.get("text") or "").strip() == "":
                raise ValueError("PATCH_BLOCK_TEXT_EMPTY")
            if target_index is None:
                if proposal.block_id and block_id != proposal.block_id:
                    raise ValueError("PATCH_BLOCK_ID_MISMATCH")
                if not patch.get("intended_obligation_ids"):
                    raise ValueError("PATCH_BLOCK_OBLIGATION_SCOPE_MISSING")
                if not set(str(value) for value in (patch.get("intended_obligation_ids") or ())).issubset(set(proposal.obligation_ids)):
                    raise ValueError("PATCH_BLOCK_OBLIGATION_SCOPE_EXPANDED")
                if not set(str(value) for value in (patch.get("evidence_ids") or ())).issubset(set(proposal.allowed_evidence_ids)):
                    raise ValueError("PATCH_BLOCK_EVIDENCE_SCOPE_EXPANDED")
                order = {"body": 0, "case_activity": 1, "exercise": 2, "assessment": 3, "summary": 4}
                new_order = order.get(str(patch.get("channel") or "body"), 0)
                insert_at = next(
                    (
                        index
                        for index, existing in enumerate(blocks)
                        if order.get(str(existing.get("channel") or "body"), 0) > new_order
                    ),
                    len(blocks),
                )
                blocks.insert(insert_at, patch)
            else:
                if block_id != proposal.block_id:
                    raise ValueError("PATCH_BLOCK_ID_MISMATCH")
                original_block = blocks[target_index]
                for field_name in (
                    "intended_obligation_ids",
                    "required_obligation_ids",
                    "intended_occurrence_ids",
                    "approved_claim_ids",
                    "evidence_ids",
                ):
                    before = tuple(str(value) for value in (original_block.get(field_name) or ()))
                    after = tuple(str(value) for value in (patch.get(field_name) or ()))
                    if before != after:
                        raise ValueError(f"PATCH_BLOCK_{field_name.upper()}_MUTATED")
                blocks[target_index] = patch
        else:
            if target_index is None or not replacement_text or not replacement_text.strip():
                raise ValueError("PATCH_BLOCK_REPLACEMENT_MISSING")
            blocks[target_index] = {**dict(blocks[target_index]), "text": replacement_text.strip()}
    else:
        raise ValueError(f"UNSUPPORTED_RECOVERY_ACTION:{proposal.action}")
    draft["blocks"] = blocks
    provenance = draft.get("generation_provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    provenance.update({
        "recovery_proposal_id": proposal.proposal_id,
        "recovery_action": proposal.action,
        "recovery_granularity": "block_or_claim",
        "recovery_same_brief": True,
        "recovery_same_packet": True,
        "recovery_evidence_scope_expanded": False,
    })
    draft["generation_provenance"] = provenance


def _safe_materialize(brief: SectionAuthoringBrief, packet: SectionEvidencePacket, draft: Mapping[str, Any], *, claim_judge: Any | None):
    try:
        return materialize_section_draft(brief, packet, draft, claim_judge=claim_judge), ""
    except (SectionAuthoringError, ValueError, TypeError) as exc:
        return None, f"{type(exc).__name__}:{exc}"


def _validate_identity(rendered: RenderedSection, brief: SectionAuthoringBrief, packet: SectionEvidencePacket) -> None:
    if rendered.outline_node_id != brief.outline_node_id or rendered.blueprint_id != brief.blueprint_id or rendered.packet_id != packet.packet_id:
        raise ValueError("SECTION_RECOVERY_INPUT_IDENTITY_MISMATCH")


def _issue(rendered: RenderedSection, *, code: str, reason: str, block_id: str = "", obligation_ids: tuple[str, ...] = (), claim_ids: tuple[str, ...] = (), target_text: str = "", evidence_ids: tuple[str, ...] = (), evidence_status: str = "", retry_allowed: bool = False, provenance: Mapping[str, Any] | None = None) -> SectionRecoveryIssue:
    suffix = block_id or (obligation_ids[0] if obligation_ids else claim_ids[0] if claim_ids else code)
    issue_id = f"{rendered.section_id}:{code}:{suffix}"
    return SectionRecoveryIssue(
        issue_id=issue_id,
        section_id=rendered.section_id,
        code=code,
        reason=reason,
        block_id=block_id,
        obligation_ids=tuple(dict.fromkeys(obligation_ids)),
        claim_ids=tuple(value for value in dict.fromkeys(claim_ids) if value),
        target_text=target_text,
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        evidence_status=evidence_status,
        retry_allowed=retry_allowed,
        provenance=deepcopy(dict(provenance or {})),
    )


def _allowed_evidence_for(issue: SectionRecoveryIssue, brief: SectionAuthoringBrief, packet: SectionEvidencePacket) -> tuple[str, ...]:
    values: list[str] = []
    for obligation_id in issue.obligation_ids:
        values.extend(brief.authorized_evidence_per_obligation.get(obligation_id, ()))
    if not values:
        values.extend(issue.evidence_ids)
    # A single-occurrence body/summary block may be deterministically mapped
    # to its occurrence even when the model omitted obligation associations.
    # Keep any local rewrite packet-bounded by deriving IDs only from
    # obligations linked to that sole occurrence; never use this for a
    # multi-occurrence section.
    occurrence_ids = tuple(
        str(item.get("occurrence_id") or "")
        for item in brief.occurrence_constraints
        if str(item.get("occurrence_id") or "")
    )
    if not values and len(set(occurrence_ids)) == 1:
        sole_occurrence_id = occurrence_ids[0]
        for obligation in brief.all_obligations:
            if sole_occurrence_id in tuple(obligation.linked_occurrence_ids):
                values.extend(brief.authorized_evidence_per_obligation.get(obligation.obligation_id, ()))
    authorized = set(packet.authorized_primary_evidence_ids) | set(packet.authorized_reference_evidence_ids)
    return tuple(sorted(set(values) & authorized))


def _block_obligation_ids(rendered: RenderedSection, block_id: str) -> tuple[str, ...]:
    values: list[str] = []
    for obligation_id, spans in rendered.obligation_span_map.items():
        if any(str(span.get("block_id") or span.get("span_id") or "") == block_id for span in spans):
            values.append(obligation_id)
    return tuple(values)


def _first_block_for_obligation(rendered: RenderedSection, obligation_id: str) -> str:
    spans = rendered.obligation_span_map.get(obligation_id, ())
    if not spans:
        return ""
    return str(spans[0].get("block_id") or spans[0].get("span_id") or "")


def _claim_block_id(occurrence_id: str, section_id: str) -> str:
    prefix = occurrence_id.rsplit(":claim:", 1)[0]
    marker = f"{section_id}:"
    return prefix[len(marker):] if prefix.startswith(marker) else prefix.rsplit(":", 1)[-1]


def _classify_explicit_issue(item: Any) -> tuple[str, str]:
    text = str(item.get("rule") or item.get("code") or item.get("reason") or item).upper() if isinstance(item, Mapping) else str(item).upper()
    if "RETEACH" in text:
        return FORBIDDEN_RETEACH, "forbidden reteach should be removed or compressed locally"
    if "SOURCE_GAP" in text or "EVIDENCE" in text:
        return PARTIAL_EVIDENCE, "local conformance reported an evidence-bound failure"
    return LOCAL_CONFORMANCE_FAILURE, "local conformance failure requires a bounded block repair"


def _dedupe_issues(issues: Iterable[SectionRecoveryIssue]) -> tuple[SectionRecoveryIssue, ...]:
    result: list[SectionRecoveryIssue] = []
    seen: set[str] = set()
    for issue in issues:
        if issue.issue_id in seen:
            continue
        seen.add(issue.issue_id)
        result.append(issue)
    return tuple(result)


def _visible_text(rendered: RenderedSection | None) -> str:
    if rendered is None:
        return ""
    values = [rendered.body, rendered.case_activity, *rendered.exercises, rendered.assessment, rendered.summary]
    return "\n\n".join(value.strip() for value in values if str(value or "").strip()).strip()


def _draft_visible_text(draft: Mapping[str, Any]) -> str:
    blocks = draft.get("blocks") if isinstance(draft.get("blocks"), (list, tuple)) else ()
    ordered = sorted(
        [item for item in blocks if isinstance(item, Mapping)],
        key=lambda item: {"body": 0, "case_activity": 1, "exercise": 2, "assessment": 3, "summary": 4}.get(str(item.get("channel") or "body"), 0),
    )
    return "\n\n".join(str(item.get("text") or "").strip() for item in ordered if str(item.get("text") or "").strip()).strip()


def _run_checker(checker: Callable[[RenderedSection], Any] | None, rendered: RenderedSection | None) -> Any:
    if checker is None or rendered is None:
        return None
    try:
        return checker(rendered)
    except Exception as exc:  # the attempt must fail closed, not hide checker errors
        return {"status": "ERROR", "error": f"{type(exc).__name__}:{exc}"}


def _conformance_passes(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.upper() == "MATCH"
    if isinstance(value, Mapping):
        return str(value.get("overall") or value.get("status") or "").upper() == "MATCH"
    return str(getattr(value, "overall", getattr(value, "status", ""))).upper() == "MATCH"


def _attempt_without_render(proposal: SectionRecoveryProposal, brief: SectionAuthoringBrief, draft: Mapping[str, Any], *, status: str, reason: str) -> SectionRecoveryAttempt:
    original = _draft_visible_text(draft)
    return _attempt(proposal, original, original, None, None, None, None, status=status, reason=reason, targeted=(proposal.issue_id,))


def _attempt(proposal: SectionRecoveryProposal, original: str, candidate: str, pre: RenderedSection | None, post: RenderedSection | None, pre_conf: Any, post_conf: Any, *, status: str, reason: str, targeted: tuple[str, ...]) -> SectionRecoveryAttempt:
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), candidate.splitlines(keepends=True),
        fromfile="original", tofile="candidate",
    ))
    return SectionRecoveryAttempt(
        proposal_id=proposal.proposal_id,
        section_id=proposal.section_id,
        action=proposal.action,
        original_text=original,
        candidate_text=candidate,
        diff=diff,
        targeted_issue_ids=tuple(targeted),
        pre_rendered=pre,
        post_rendered=post,
        pre_occurrence_conformance=pre_conf,
        post_occurrence_conformance=post_conf,
        status=status,
        rollback_reason=reason,
        revalidation={
            "deterministic_materialization": post is not None,
            "obligation_coverage": post.generation_provenance.get("obligation_coverage", {}) if post else {},
            "local_claim_evidence_audit": post.generation_provenance.get("local_claim_evidence_audit", ()) if post else (),
            "verified_availability_eligible": bool(post and post.generation_provenance.get("verified_availability_eligible", False)),
            "availability_granted_by_repair": False,
        },
        provenance={
            "generator": "phase5e-section-authoring-recovery-v1",
            "same_brief": True,
            "same_packet": True,
            "whole_section_regeneration": False,
            "digital_book_touched": False,
        },
    )


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    return str(value)
