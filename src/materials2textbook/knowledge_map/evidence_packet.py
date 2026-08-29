"""Bounded, obligation-level evidence packets for a section blueprint.

Phase 5C deliberately stops before writing.  Retrieval may inspect supplied
chunks to find possible matches, but accepted evidence is always restricted to
the frozen section's primary/reference ownership (and any narrower ownership
recorded by the blueprint).  Exact semantic binding is a later concern.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

from materials2textbook.knowledge_map.outline import book_plan_fingerprint
from materials2textbook.knowledge_map.teaching_blueprint import (
    REQUIRED,
    SectionTeachingBlueprint,
    TeachingObligation,
)
from materials2textbook.schemas import BookPlan, BookSectionPlan, EvidenceChunk


SUPPORTED_OBLIGATION = "SUPPORTED_OBLIGATION"
PARTIAL_OBLIGATION = "PARTIAL_OBLIGATION"
SOURCE_GAP = "SOURCE_GAP"
OBLIGATION_STATUSES = frozenset({SUPPORTED_OBLIGATION, PARTIAL_OBLIGATION, SOURCE_GAP})

# Some section responsibilities are derived from already accepted content
# rather than requiring a source sentence of their own.  Summary is the first
# production responsibility with this contract: it may synthesize the
# supported section teaching, but it may not introduce a new domain claim.
DERIVABLE_FROM_VERIFIED_CONTENT = "DERIVABLE_FROM_VERIFIED_CONTENT"

MAX_CANDIDATES_PER_OBLIGATION = 12
MAX_REJECTED_CANDIDATES_PER_OBLIGATION = 4
FULL_SUPPORT_SCORE = 0.45
PARTIAL_SUPPORT_SCORE = 0.12


class SectionEvidencePacketError(ValueError):
    """Raised when a packet would violate a frozen plan or blueprint boundary."""


@dataclass(frozen=True)
class ObligationEvidenceBinding:
    obligation_id: str
    obligation_kind: str
    status: str
    accepted_evidence_ids: tuple[str, ...] = ()
    accepted_evidence_spans: tuple[Mapping[str, Any], ...] = ()
    candidate_evidence_ids: tuple[str, ...] = ()
    rejected_candidates: tuple[Mapping[str, Any], ...] = ()
    support_rationale: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "obligation_kind": self.obligation_kind,
            "status": self.status,
            "accepted_evidence_ids": list(self.accepted_evidence_ids),
            "accepted_evidence_spans": [deepcopy(dict(item)) for item in self.accepted_evidence_spans],
            "candidate_evidence_ids": list(self.candidate_evidence_ids),
            "rejected_candidates": [deepcopy(dict(item)) for item in self.rejected_candidates],
            "support_rationale": self.support_rationale,
            "provenance": deepcopy(dict(self.provenance)),
        }


@dataclass(frozen=True)
class SectionEvidencePacket:
    packet_id: str
    outline_node_id: str
    blueprint_id: str
    source_book_plan_signature: str
    authorized_primary_evidence_ids: tuple[str, ...] = ()
    authorized_reference_evidence_ids: tuple[str, ...] = ()
    obligation_bindings: tuple[ObligationEvidenceBinding, ...] = ()
    source_gaps: tuple[Mapping[str, Any], ...] = ()
    retrieval_audit: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "outline_node_id": self.outline_node_id,
            "blueprint_id": self.blueprint_id,
            "source_book_plan_signature": self.source_book_plan_signature,
            "authorized_primary_evidence_ids": list(self.authorized_primary_evidence_ids),
            "authorized_reference_evidence_ids": list(self.authorized_reference_evidence_ids),
            "obligation_bindings": [item.to_dict() for item in self.obligation_bindings],
            "source_gaps": [deepcopy(dict(item)) for item in self.source_gaps],
            "retrieval_audit": deepcopy(dict(self.retrieval_audit)),
            "provenance": deepcopy(dict(self.provenance)),
        }


def build_section_evidence_packet(
    book_plan: BookPlan,
    section_id: str,
    blueprint: SectionTeachingBlueprint,
    evidence_chunks: Iterable[EvidenceChunk],
    *,
    prior_verified_support: Mapping[str, Any] | None = None,
    max_candidates_per_obligation: int = MAX_CANDIDATES_PER_OBLIGATION,
    source_bounded_calibration: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
) -> SectionEvidencePacket:
    """Create one bounded packet without mutating ``book_plan`` or ``blueprint``.

    The supplied chunk list may contain a wider index.  Wider-index records
    can only appear in the audit as rejected/out-of-scope candidates; they can
    never become accepted evidence.  The accepted pool is the intersection of
    the frozen section ownership and the blueprint's (possibly narrower)
    ownership declaration.
    """

    found = _find_section(book_plan, section_id)
    if found is None:
        raise SectionEvidencePacketError(f"unknown outline section: {section_id}")
    chapter, section = found
    expected_signature = book_plan_fingerprint(book_plan)
    if blueprint.outline_node_id != section_id:
        raise SectionEvidencePacketError("blueprint outline_node_id does not match the requested section")
    if blueprint.source_book_plan_signature != expected_signature:
        raise SectionEvidencePacketError("blueprint belongs to a different BookPlan fingerprint")

    primary_ids = _dedupe([*section.primary_material_ids])
    reference_ids = [value for value in _dedupe(section.reference_material_ids) if value not in primary_ids]
    section_owned = set(primary_ids + reference_ids)
    blueprint_owned = set(blueprint.authorized_evidence_ids)
    unauthorized_blueprint_ids = sorted(blueprint_owned - section_owned)
    if unauthorized_blueprint_ids:
        raise SectionEvidencePacketError(
            "blueprint evidence ownership exceeds the frozen section: "
            + ", ".join(unauthorized_blueprint_ids)
        )
    # The blueprint carries the section's explicit ownership snapshot.  An
    # empty tuple is therefore an intentional empty pool, not permission to
    # fall back to the whole section ownership.
    effective_owned = section_owned & blueprint_owned
    effective_primary = [value for value in primary_ids if value in effective_owned]
    effective_reference = [value for value in reference_ids if value in effective_owned]

    chunks = list(evidence_chunks)
    chunk_map, duplicate_chunk_ids = _chunk_index(chunks)
    missing_owned_ids = sorted(value for value in effective_owned if value not in chunk_map)
    ranked_all = {
        obligation.obligation_id: _rank_candidates(obligation, chunks)
        for obligation in blueprint.obligations
    }
    limit = max(1, min(int(max_candidates_per_obligation), MAX_CANDIDATES_PER_OBLIGATION))

    bindings: list[ObligationEvidenceBinding] = []
    source_gaps: list[dict[str, Any]] = []
    out_of_scope_ids: set[str] = set()
    for obligation in blueprint.obligations:
        ranked = ranked_all[obligation.obligation_id]
        owned_ranked = [item for item in ranked if item[1].chunk_id in effective_owned][:limit]
        out_scope_ranked = [item for item in ranked if item[1].chunk_id not in effective_owned]
        out_of_scope_ids.update(item[1].chunk_id for item in out_scope_ranked[:MAX_REJECTED_CANDIDATES_PER_OBLIGATION])
        binding = _build_binding(
            obligation,
            owned_ranked,
            out_scope_ranked,
            effective_owned,
            missing_owned_ids,
        )
        calibrated_binding = _calibrated_evidence_binding(
            obligation,
            source_bounded_calibration,
            effective_owned=effective_owned,
            chunk_map=chunk_map,
        )
        if calibrated_binding is not None:
            binding = calibrated_binding
        bindings.append(binding)
        if binding.status == SOURCE_GAP and obligation.required == REQUIRED:
            source_gaps.append(
                {
                    "gap_id": f"{obligation.obligation_id}:source-gap",
                    "obligation_id": obligation.obligation_id,
                    "obligation_kind": obligation.kind,
                    "issue_type": SOURCE_GAP,
                    "missing_support": obligation.evidence_requirement,
                    "reason": binding.support_rationale,
                    "provenance": "deterministic bounded section evidence packet",
                }
            )

    prior_keys = sorted(str(key) for key in (prior_verified_support or {}).keys())
    retrieval_audit = {
        "retrieval_method": "deterministic obligation-level lexical bounded retrieval",
        "candidate_scope": "frozen section primary/reference ownership intersected with blueprint ownership",
        "input_chunk_count": len(chunks),
        "section_owned_evidence_ids": sorted(section_owned),
        "effective_owned_evidence_ids": sorted(effective_owned),
        "missing_owned_evidence_ids": missing_owned_ids,
        "duplicate_chunk_ids": duplicate_chunk_ids,
        "out_of_scope_candidate_ids": sorted(out_of_scope_ids),
        "unauthorized_accepted_evidence_count": 0,
        "prior_verified_support_keys": prior_keys,
        "qwen_used": False,
        "source_bounded_semantic_support_used": any(
            bool(item.provenance.get("source_bounded_semantic_support")) for item in bindings
        ),
        "obligation_count": len(blueprint.obligations),
        "required_obligation_count": sum(item.required == REQUIRED for item in blueprint.obligations),
        "status_counts": _status_counts(bindings),
    }
    provenance = {
        "generator": "phase5c-deterministic-section-evidence-packet-v1",
        "chapter_id": chapter.chapter_id,
        "section_id": section_id,
        "blueprint_id": blueprint.blueprint_id,
        "source_book_plan_signature": expected_signature,
        "evidence_binding_mode": "bounded deterministic; exact semantic rerank deferred",
        "prior_verified_support": deepcopy(dict(prior_verified_support or {})),
        "book_plan_mutated": False,
        "blueprint_mutated": False,
        "semantic_ownership_changed": False,
    }
    return SectionEvidencePacket(
        packet_id=f"packet:{section_id}:{blueprint.blueprint_id.split(':')[-1]}",
        outline_node_id=section_id,
        blueprint_id=blueprint.blueprint_id,
        source_book_plan_signature=expected_signature,
        authorized_primary_evidence_ids=tuple(effective_primary),
        authorized_reference_evidence_ids=tuple(effective_reference),
        obligation_bindings=tuple(bindings),
        source_gaps=tuple(source_gaps),
        retrieval_audit=retrieval_audit,
        provenance=provenance,
    )


def validate_section_evidence_packet(
    packet: SectionEvidencePacket,
    blueprint: SectionTeachingBlueprint,
    *,
    expected_source_signature: str | None = None,
) -> list[str]:
    """Return deterministic packet violations for tests and audit consumers."""

    issues: list[str] = []
    if expected_source_signature and packet.source_book_plan_signature != expected_source_signature:
        issues.append("SOURCE_BOOK_PLAN_SIGNATURE_MISMATCH")
    if packet.blueprint_id != blueprint.blueprint_id:
        issues.append("BLUEPRINT_ID_MISMATCH")
    expected_ids = {item.obligation_id for item in blueprint.obligations}
    actual_ids = {item.obligation_id for item in packet.obligation_bindings}
    if expected_ids != actual_ids:
        issues.append("OBLIGATION_BINDING_SET_MISMATCH")
    if packet.retrieval_audit.get("unauthorized_accepted_evidence_count", 0) != 0:
        issues.append("UNAUTHORIZED_ACCEPTED_EVIDENCE")
    for binding in packet.obligation_bindings:
        if binding.status not in OBLIGATION_STATUSES:
            issues.append(f"UNKNOWN_OBLIGATION_STATUS:{binding.status}")
        if (
            binding.status == SUPPORTED_OBLIGATION
            and not binding.accepted_evidence_ids
            and not bool(binding.provenance.get("derivable_from_verified_content"))
        ):
            issues.append(f"SUPPORTED_WITHOUT_EVIDENCE:{binding.obligation_id}")
        if any(item not in packet.authorized_primary_evidence_ids + packet.authorized_reference_evidence_ids for item in binding.accepted_evidence_ids):
            issues.append(f"UNAUTHORIZED_EVIDENCE:{binding.obligation_id}")
    return issues


def _build_binding(
    obligation: TeachingObligation,
    owned_ranked: list[tuple[float, EvidenceChunk, tuple[str, ...]]],
    out_scope_ranked: list[tuple[float, EvidenceChunk, tuple[str, ...]]],
    effective_owned: set[str],
    missing_owned_ids: list[str],
    ) -> ObligationEvidenceBinding:
    # A pedagogical summary is not a second factual teaching obligation.  It
    # is synthesized from the section's accepted content after the factual
    # obligations have been bound.  Keeping this as a packet-level provenance
    # flag lets the writer contract allow a discourse-only summary while the
    # claim audit still rejects any new factual assertion in that summary.
    if (
        obligation.kind == "summary"
        and str(obligation.evidence_requirement or "").strip()
        == "summary may be derived from already-supported section content"
    ):
        return ObligationEvidenceBinding(
            obligation_id=obligation.obligation_id,
            obligation_kind=obligation.kind,
            status=SUPPORTED_OBLIGATION,
            accepted_evidence_ids=(),
            accepted_evidence_spans=(),
            candidate_evidence_ids=(),
            rejected_candidates=(),
            support_rationale=(
                "summary is pedagogical synthesis derived from accepted section content; "
                "no summary-specific source sentence is required"
            ),
            provenance={
                "obligation_required": obligation.required,
                "evidence_requirement": DERIVABLE_FROM_VERIFIED_CONTENT,
                "derivable_from_verified_content": True,
                "independent_evidence_required": False,
                "retrieval_scope": "accepted section obligations",
                "semantic_rerank_used": False,
            },
        )
    candidate_ids = tuple(item[1].chunk_id for item in owned_ranked if item[0] >= PARTIAL_SUPPORT_SCORE)
    # Select a minimal sufficient *set*, not merely the single highest lexical
    # hit.  A concept/procedure obligation can be split across several owned
    # chunks; each new chunk is retained only when it contributes new matched
    # propositions.  The writer still receives only this obligation-local set.
    selected: list[tuple[float, EvidenceChunk, tuple[str, ...]]] = []
    covered_terms: set[str] = set()
    for item in owned_ranked:
        if item[0] < PARTIAL_SUPPORT_SCORE:
            continue
        marginal = set(item[2]) - covered_terms
        if not selected or marginal or (item[0] >= FULL_SUPPORT_SCORE and len(selected) < 3):
            selected.append(item)
            covered_terms.update(item[2])
        if len(selected) >= 6:
            break
    objective_terms = _terms(obligation.objective)
    combined_coverage = len(covered_terms & objective_terms) / max(1, len(objective_terms))
    top_score = owned_ranked[0][0] if owned_ranked else 0.0
    if top_score >= FULL_SUPPORT_SCORE or combined_coverage >= 0.72:
        status = SUPPORTED_OBLIGATION
        rationale = (
            "the selected authorized evidence set jointly covers the obligation "
            f"(coverage={combined_coverage:.3f})"
        )
    elif top_score >= PARTIAL_SUPPORT_SCORE:
        status = PARTIAL_OBLIGATION
        rationale = (
            "the selected authorized evidence set provides partial support "
            f"(coverage={combined_coverage:.3f})"
        )
    else:
        status = SOURCE_GAP
        selected = []
        if not effective_owned:
            rationale = "the section has no effective authorized evidence pool"
        elif missing_owned_ids and not owned_ranked:
            rationale = "owned EvidenceChunk IDs are missing from the supplied index"
        else:
            rationale = "no authorized candidate provides a deterministic lexical basis for this obligation"

    accepted_ids = tuple(item[1].chunk_id for item in selected)
    spans = tuple(_span_record(obligation, score, chunk, matched_terms) for score, chunk, matched_terms in selected)
    rejected: list[dict[str, Any]] = []
    for score, chunk, matched_terms in owned_ranked:
        if chunk.chunk_id not in accepted_ids:
            rejected.append(
                {
                    "evidence_id": chunk.chunk_id,
                    "score": round(score, 6),
                    "matched_terms": list(matched_terms),
                    "reason": "below accepted obligation coverage threshold",
                }
            )
    for score, chunk, matched_terms in out_scope_ranked[:MAX_REJECTED_CANDIDATES_PER_OBLIGATION]:
        rejected.append(
            {
                "evidence_id": chunk.chunk_id,
                "score": round(score, 6),
                "matched_terms": list(matched_terms),
                "reason": "OUTSIDE_SECTION_OWNERSHIP",
            }
        )
    return ObligationEvidenceBinding(
        obligation_id=obligation.obligation_id,
        obligation_kind=obligation.kind,
        status=status,
        accepted_evidence_ids=accepted_ids,
        accepted_evidence_spans=spans,
        candidate_evidence_ids=candidate_ids,
        rejected_candidates=tuple(rejected),
        support_rationale=rationale,
        provenance={
            "obligation_required": obligation.required,
            "evidence_requirement": obligation.evidence_requirement,
            "retrieval_scope": "section-owned whitelist",
            "semantic_rerank_used": False,
            "accepted_ids_are_whitelisted": all(item in effective_owned for item in accepted_ids),
            "evidence_set_support": {
                "status": status,
                "combined_matched_terms": sorted(covered_terms),
                "combined_coverage": round(combined_coverage, 6),
                "selected_evidence_ids": list(accepted_ids),
                "selection_strategy": "greedy marginal proposition coverage",
            },
        },
    )


def _calibration_rows_for_obligation(
    calibration: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
    obligation_id: str,
) -> list[Mapping[str, Any]]:
    """Return validated source-bounded calibration rows for one obligation.

    The production freeze loader currently supplies a mapping keyed by
    ``obligation_id`` while archived calibration artifacts store a list under
    ``obligation_calibrations``.  Accept both representations here so the
    evidence packet cannot silently fall back to a book-wide pool merely
    because an artifact was normalized differently.
    """

    if calibration is None:
        return []
    rows: Any = calibration
    if isinstance(rows, Mapping) and "obligation_calibrations" in rows:
        rows = rows.get("obligation_calibrations")
    if isinstance(rows, Mapping):
        direct = rows.get(obligation_id)
        if isinstance(direct, Mapping):
            return [direct]
        result = []
        for key, value in rows.items():
            if not isinstance(value, Mapping):
                continue
            if str(value.get("obligation_id") or key) == obligation_id:
                result.append(value)
        return result
    if isinstance(rows, Iterable) and not isinstance(rows, (str, bytes)):
        return [
            value
            for value in rows
            if isinstance(value, Mapping) and str(value.get("obligation_id") or "") == obligation_id
        ]
    return []


def _calibrated_evidence_binding(
    obligation: TeachingObligation,
    calibration: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
    *,
    effective_owned: set[str],
    chunk_map: Mapping[str, EvidenceChunk],
) -> ObligationEvidenceBinding | None:
    """Apply an already validated semantic evidence result without widening scope.

    Source-bounded calibration is an upstream, frozen artifact.  It may make a
    deterministic lexical binding more precise (including a multi-chunk
    binding), but it is never allowed to authorize an ID outside the current
    section's effective ownership or to invent a support result without
    concrete evidence IDs.  Unknown/invalid rows deliberately return ``None``
    so the ordinary deterministic binding remains fail-closed.
    """

    rows = _calibration_rows_for_obligation(calibration, obligation.obligation_id)
    if not rows:
        return None
    row = rows[-1]
    calibrated_status = str(row.get("calibrated_status") or "").upper()
    if calibrated_status == "OUT_OF_SOURCE_SCOPE":
        # A source-bounded exclusion is an audit record, never writer
        # responsibility, even if a stale row happens to carry an old
        # evidence_result value.
        return _calibration_status_binding(
            obligation,
            row,
            SOURCE_GAP,
            "OUT_OF_SOURCE_SCOPE is excluded from writer responsibility",
            support_used=False,
        )
    decision = str(row.get("evidence_result") or row.get("semantic_result") or row.get("decision") or "").upper()
    if decision in {"PARTIALLY_SUPPORTED", "PARTIAL_OBLIGATION"}:
        decision = "PARTIAL"
    elif decision in {"SUPPORTED_OBLIGATION"}:
        decision = "SUPPORTED"
    elif decision in {"NO_SUPPORT", "SOURCE_GAP"}:
        return _calibration_gap_binding(obligation, row, str(row.get("rationale") or "validated semantic judgement found no support"))
    elif not decision:
        return _calibration_gap_binding(obligation, row, "source-bounded calibration did not provide a support decision")
    if decision not in {"SUPPORTED", "PARTIAL"}:
        return _calibration_gap_binding(obligation, row, "unrecognized source-bounded support decision")

    raw_ids = row.get("supporting_evidence_ids") or row.get("accepted_evidence_ids") or row.get("evidence_ids") or ()
    requested_ids = _dedupe(raw_ids if isinstance(raw_ids, Iterable) and not isinstance(raw_ids, (str, bytes)) else [raw_ids])
    # A calibration row containing an unowned ID is not partially trusted: it
    # indicates an artifact/ownership mismatch and must remain fail-closed.
    if not requested_ids or any(item not in effective_owned or item not in chunk_map for item in requested_ids):
        if decision == "PARTIAL":
            return _calibration_partial_binding(obligation, row, "partial semantic support has no valid authorized evidence set")
        return _calibration_gap_binding(obligation, row, "supported semantic result referenced evidence outside the authorized packet")
    selected_chunks = [chunk_map[item] for item in requested_ids]
    spans = tuple(_span_record(obligation, 1.0, chunk, ()) for chunk in selected_chunks)
    supported_requirements = row.get("supported_requirements") or row.get("supported_propositions") or ()
    unsupported_requirements = row.get("unsupported_requirements") or ()
    evidence_set_status = SUPPORTED_OBLIGATION if decision == "SUPPORTED" else PARTIAL_OBLIGATION
    rationale = str(row.get("rationale") or "source-bounded semantic evidence calibration")
    return ObligationEvidenceBinding(
        obligation_id=obligation.obligation_id,
        obligation_kind=obligation.kind,
        status=evidence_set_status,
        accepted_evidence_ids=tuple(requested_ids),
        accepted_evidence_spans=spans,
        candidate_evidence_ids=tuple(requested_ids),
        rejected_candidates=(),
        support_rationale=rationale,
        provenance={
            "obligation_required": obligation.required,
            "evidence_requirement": obligation.evidence_requirement,
            "retrieval_scope": "validated source-bounded calibration within section whitelist",
            "semantic_rerank_used": True,
            "source_bounded_semantic_support": True,
            "evidence_set_support": {
                "status": evidence_set_status,
                "selected_evidence_ids": list(requested_ids),
                "selection_strategy": "validated semantic calibration multi-evidence set",
                "supported_requirements": list(supported_requirements) if isinstance(supported_requirements, Iterable) and not isinstance(supported_requirements, (str, bytes)) else [str(supported_requirements)] if supported_requirements else [],
                "unsupported_requirements": list(unsupported_requirements) if isinstance(unsupported_requirements, Iterable) and not isinstance(unsupported_requirements, (str, bytes)) else [str(unsupported_requirements)] if unsupported_requirements else [],
            },
            "calibration_provenance": {
                "obligation_id": obligation.obligation_id,
                "evidence_result": decision,
                "rationale": rationale,
                "calibration_row_provenance": row.get("provenance") or row.get("calibration_provenance") or "validated source-bounded calibration",
            },
            "accepted_ids_are_whitelisted": True,
        },
    )


def _calibration_gap_binding(
    obligation: TeachingObligation,
    row: Mapping[str, Any],
    rationale: str,
) -> ObligationEvidenceBinding:
    return _calibration_status_binding(obligation, row, SOURCE_GAP, rationale)


def _calibration_partial_binding(
    obligation: TeachingObligation,
    row: Mapping[str, Any],
    rationale: str,
) -> ObligationEvidenceBinding:
    return _calibration_status_binding(obligation, row, PARTIAL_OBLIGATION, rationale)


def _calibration_status_binding(
    obligation: TeachingObligation,
    row: Mapping[str, Any],
    status: str,
    rationale: str,
    *,
    support_used: bool = True,
) -> ObligationEvidenceBinding:
    return ObligationEvidenceBinding(
        obligation_id=obligation.obligation_id,
        obligation_kind=obligation.kind,
        status=status,
        support_rationale=rationale,
        provenance={
            "obligation_required": obligation.required,
            "retrieval_scope": "validated source-bounded calibration",
            "semantic_rerank_used": True,
            "source_bounded_semantic_support": support_used,
            "evidence_set_support": {"status": status, "selected_evidence_ids": []},
            "calibration_provenance": {
                "obligation_id": obligation.obligation_id,
                "evidence_result": str(row.get("evidence_result") or row.get("semantic_result") or row.get("decision") or ""),
                "rationale": rationale,
            },
        },
    )


def _rank_candidates(
    obligation: TeachingObligation,
    chunks: Iterable[EvidenceChunk],
) -> list[tuple[float, EvidenceChunk, tuple[str, ...]]]:
    ranked: list[tuple[float, EvidenceChunk, tuple[str, ...]]] = []
    for chunk in chunks:
        score, matched_terms = _support_score(obligation.objective, chunk)
        if score > 0:
            ranked.append((score, chunk, matched_terms))
    ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
    return ranked


def _support_score(objective: str, chunk: EvidenceChunk) -> tuple[float, tuple[str, ...]]:
    objective_terms = _terms(objective)
    if not objective_terms:
        return 0.0, ()
    evidence_text = " ".join(
        [
            str(chunk.title or ""),
            str(chunk.summary or ""),
            str(chunk.content or ""),
            str(chunk.subject or ""),
            str(chunk.material_block or ""),
            " ".join(str(item) for item in chunk.keywords),
        ]
    )
    evidence_terms = _terms(evidence_text)
    matched = tuple(sorted(objective_terms & evidence_terms))
    if not matched:
        return 0.0, ()
    coverage = len(matched) / max(1, len(objective_terms))
    evidence_density = len(matched) / max(1, len(evidence_terms))
    return min(1.0, 0.8 * coverage + 0.2 * evidence_density), matched


def _span_record(
    obligation: TeachingObligation,
    score: float,
    chunk: EvidenceChunk,
    matched_terms: tuple[str, ...],
) -> dict[str, Any]:
    text = str(chunk.content or chunk.summary or chunk.title or "").strip()
    excerpt = text[:240]
    lower_text = text.lower()
    first_term = next((term for term in matched_terms if term.lower() in lower_text), "")
    start = lower_text.find(first_term.lower()) if first_term else 0
    if start < 0:
        start = 0
    end = min(len(text), max(start + len(first_term), 240))
    return {
        "evidence_id": chunk.chunk_id,
        "span_type": "deterministic_content_excerpt",
        "start": start,
        "end": end,
        "text": excerpt,
        "matched_terms": list(matched_terms),
        "score": round(score, 6),
        "obligation_id": obligation.obligation_id,
    }


def _chunk_index(chunks: Iterable[EvidenceChunk]) -> tuple[dict[str, EvidenceChunk], list[str]]:
    index: dict[str, EvidenceChunk] = {}
    duplicates: list[str] = []
    for chunk in chunks:
        chunk_id = str(chunk.chunk_id or "").strip()
        if not chunk_id:
            continue
        if chunk_id in index:
            duplicates.append(chunk_id)
            continue
        index[chunk_id] = chunk
    return index, _dedupe(duplicates)


def _find_section(book_plan: BookPlan, section_id: str) -> tuple[Any, BookSectionPlan] | None:
    for chapter in book_plan.chapters:
        for section in chapter.sections:
            if section.section_id == section_id:
                return chapter, section
    return None


def _status_counts(bindings: Iterable[ObligationEvidenceBinding]) -> dict[str, int]:
    result: dict[str, int] = {}
    for binding in bindings:
        result[binding.status] = result.get(binding.status, 0) + 1
    return result


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]*", lowered))
    for segment in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(segment) >= 2:
            terms.add(segment)
            terms.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return {item for item in terms if item not in _STOPWORDS}


def _dedupe(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it",
        "of", "on", "or", "that", "the", "their", "this", "to", "use", "with", "要", "的", "和", "在",
        "本", "该", "对", "中", "将", "与", "及", "并", "以", "为", "所", "需",
    }
)
