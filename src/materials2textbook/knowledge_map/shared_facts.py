"""Read-only shared instructional fact audit for Phase 3B-1.

This module deliberately stops before rewriting.  Similarity is used only to
recall candidate pairs; a final proposal must provide a fact statement,
evidence on both sides, rendered support on both sides, and an independent
contribution for the later occurrence.  No proposal grants availability or
changes an occurrence role.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any, Iterable, Mapping


SAME_CANONICAL = "SAME_CANONICAL"
RELATED_WITH_SHARED_FACTS = "RELATED_WITH_SHARED_FACTS"
DISTINCT = "DISTINCT"
INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"

COMPRESSIBLE = "COMPRESSIBLE"
CONTEXTUAL_RESTATEMENT_REQUIRED = "CONTEXTUAL_RESTATEMENT_REQUIRED"
NOT_COMPRESSIBLE = "NOT_COMPRESSIBLE"

_MATCH = {"MATCH", "PASS"}
_SUPPORTED = {"SUPPORTED", "MATCH", "PASS"}


@dataclass(frozen=True)
class SharedInstructionalFact:
    """One auditable fact shared by two distinct canonical occurrences."""

    shared_fact_id: str
    fact_statement: str
    source_occurrence_ids: tuple[str, ...]
    source_canonical_knowledge_ids: tuple[str, ...]
    evidence_ids_by_occurrence: dict[str, tuple[str, ...]]
    rendered_support_by_occurrence: dict[str, Any]
    earlier_occurrence_id: str
    later_occurrence_id: str
    relation: str
    earlier_verified_facets: tuple[str, ...]
    later_required_facets: tuple[str, ...]
    later_independent_contribution: str
    disposition: str
    rationale: str
    downstream_closure_impact: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    earlier_position: dict[str, int] = field(default_factory=dict)
    later_position: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_ids_by_occurrence"] = {
            key: list(ids) for key, ids in self.evidence_ids_by_occurrence.items()
        }
        value["source_occurrence_ids"] = list(self.source_occurrence_ids)
        value["source_canonical_knowledge_ids"] = list(self.source_canonical_knowledge_ids)
        value["earlier_verified_facets"] = list(self.earlier_verified_facets)
        value["later_required_facets"] = list(self.later_required_facets)
        return value


@dataclass(frozen=True)
class SharedFactCandidate:
    """Deterministic candidate-recall output; not a semantic conclusion."""

    occurrence_a_id: str
    occurrence_b_id: str
    canonical_a_id: str
    canonical_b_id: str
    lexical_score: float
    shared_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"shared_terms": list(self.shared_terms)}


@dataclass
class SharedFactAuditReport:
    proposals: list[SharedInstructionalFact] = field(default_factory=list)
    candidate_pairs: list[SharedFactCandidate] = field(default_factory=list)
    candidate_pair_count: int = 0
    relation_counts: dict[str, int] = field(default_factory=dict)
    disposition_counts: dict[str, int] = field(default_factory=dict)
    rejected_proposals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposals": [item.to_dict() for item in self.proposals],
            "candidate_pairs": [item.to_dict() for item in self.candidate_pairs],
            "candidate_pair_count": self.candidate_pair_count,
            "relation_counts": dict(self.relation_counts),
            "disposition_counts": dict(self.disposition_counts),
            "rejected_proposals": list(self.rejected_proposals),
        }


def recall_shared_fact_candidates(
    rendered_occurrences: Iterable[Any],
    *,
    minimum_score: float = 0.08,
    max_candidates: int | None = None,
) -> list[SharedFactCandidate]:
    """Recall cross-canonical candidates using text only.

    The returned objects are explicitly candidates.  They must not be treated
    as RELATED_WITH_SHARED_FACTS without a semantic proposal and the gates in
    :func:`audit_shared_fact_proposals`.
    """

    records = [_record(item) for item in rendered_occurrences]
    candidates: list[SharedFactCandidate] = []
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            if not left["body"] or not right["body"]:
                continue
            if left["canonical_id"] == right["canonical_id"]:
                continue
            left_terms = _terms(left["body"])
            right_terms = _terms(right["body"])
            union = left_terms | right_terms
            jaccard = len(left_terms & right_terms) / len(union) if union else 0.0
            sequence = SequenceMatcher(None, left["body"], right["body"]).ratio()
            score = 0.6 * jaccard + 0.4 * sequence
            if score < minimum_score:
                continue
            candidates.append(
                SharedFactCandidate(
                    occurrence_a_id=left["occurrence_id"],
                    occurrence_b_id=right["occurrence_id"],
                    canonical_a_id=left["canonical_id"],
                    canonical_b_id=right["canonical_id"],
                    lexical_score=round(score, 6),
                    shared_terms=tuple(sorted(left_terms & right_terms)),
                )
            )
    candidates.sort(key=lambda item: item.lexical_score, reverse=True)
    return candidates[:max_candidates] if max_candidates else candidates


def audit_shared_fact_proposals(
    *,
    rendered_occurrences: Iterable[Any],
    proposals: Iterable[Mapping[str, Any]],
    blocked_occurrence_ids: Iterable[str] = (),
    downstream_closure: Any | None = None,
    candidate_pair_count: int | None = None,
) -> SharedFactAuditReport:
    """Validate model/manual proposals with deterministic safety gates.

    A proposal may describe a fact, but this function owns the final relation
    and disposition checks.  It never mutates the input records and never
    grants a facet, satisfies a prerequisite, or changes a role.
    """

    records = {_record(item)["occurrence_id"]: _record(item) for item in rendered_occurrences}
    blocked = {str(item) for item in blocked_occurrence_ids}
    closure = _closure_records(downstream_closure)
    report = SharedFactAuditReport(candidate_pair_count=candidate_pair_count or 0)
    for proposal_index, raw in enumerate(proposals, start=1):
        result = _audit_one(
            raw=raw,
            records=records,
            blocked=blocked,
            closure=closure,
            proposal_index=proposal_index,
        )
        if isinstance(result, SharedInstructionalFact):
            report.proposals.append(result)
            report.relation_counts[result.relation] = report.relation_counts.get(result.relation, 0) + 1
            report.disposition_counts[result.disposition] = report.disposition_counts.get(result.disposition, 0) + 1
        else:
            report.rejected_proposals.append(result)
    return report


def render_shared_fact_audit_markdown(report: SharedFactAuditReport) -> str:
    lines = [
        "# Shared Instructional Fact Proposal Audit",
        "",
        "This is a read-only proposal audit. It does not rewrite text, grant availability, or change roles.",
        f"- Candidate cross-canonical pairs: {report.candidate_pair_count}",
        f"- Accepted/audited proposals: {len(report.proposals)}",
        f"- Rejected proposals: {len(report.rejected_proposals)}",
        "",
        "## Relation counts",
        "",
    ]
    for key, value in sorted(report.relation_counts.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Disposition counts", ""])
    for key, value in sorted(report.disposition_counts.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Proposals", ""])
    for item in report.proposals:
        lines.extend(
            [
                f"### {item.shared_fact_id}",
                f"- relation: {item.relation}",
                f"- fact: {item.fact_statement}",
                f"- occurrences: {', '.join(item.source_occurrence_ids)}",
                f"- canonical IDs: {', '.join(item.source_canonical_knowledge_ids)}",
                f"- earlier/later: {item.earlier_occurrence_id} → {item.later_occurrence_id}",
                f"- evidence: {item.evidence_ids_by_occurrence}",
                f"- rendered support: {item.rendered_support_by_occurrence}",
                f"- earlier verified facets: {', '.join(item.earlier_verified_facets) or 'none'}",
                f"- later required facets: {', '.join(item.later_required_facets) or 'none'}",
                f"- later independent contribution: {item.later_independent_contribution or 'none'}",
                f"- disposition: {item.disposition}",
                f"- rationale: {item.rationale}",
                f"- downstream closure impact: {item.downstream_closure_impact}",
                "",
            ]
        )
    if report.candidate_pairs:
        lines.extend(["## Candidate recall (not a semantic conclusion)", ""])
        for candidate in report.candidate_pairs:
            lines.append(
                f"- {candidate.occurrence_a_id} ({candidate.canonical_a_id}) / "
                f"{candidate.occurrence_b_id} ({candidate.canonical_b_id}); "
                f"score={candidate.lexical_score:.3f}; shared_terms={', '.join(candidate.shared_terms) or 'none'}"
            )
    if report.rejected_proposals:
        lines.extend(["## Rejected proposals", ""])
        for item in report.rejected_proposals:
            lines.append(f"- {item.get('proposal_id', '?')}: {item.get('reason', 'rejected')}")
    return "\n".join(lines).rstrip() + "\n"


def _audit_one(
    *,
    raw: Mapping[str, Any],
    records: dict[str, dict[str, Any]],
    blocked: set[str],
    closure: list[dict[str, Any]],
    proposal_index: int,
) -> SharedInstructionalFact | dict[str, Any]:
    proposal_id = str(raw.get("shared_fact_id") or f"shared-fact:{proposal_index:04d}")
    ids = tuple(str(item) for item in raw.get("source_occurrence_ids", ()) if str(item))
    if len(ids) != 2 or ids[0] == ids[1]:
        return {"proposal_id": proposal_id, "reason": "exactly two distinct source_occurrence_ids are required"}
    if any(item not in records for item in ids):
        return {"proposal_id": proposal_id, "reason": "both occurrences must be rendered and supplied"}
    if any(item in blocked for item in ids):
        return {"proposal_id": proposal_id, "reason": "blocked occurrence cannot establish prior teaching support"}
    left, right = records[ids[0]], records[ids[1]]
    relation = str(raw.get("relation") or INSUFFICIENT_INFORMATION)
    if left["canonical_id"] == right["canonical_id"]:
        relation = SAME_CANONICAL
    elif relation not in {RELATED_WITH_SHARED_FACTS, DISTINCT, INSUFFICIENT_INFORMATION}:
        relation = INSUFFICIENT_INFORMATION
    if relation == SAME_CANONICAL:
        return _build_non_related(raw, proposal_id, relation, left, right, closure)
    if relation != RELATED_WITH_SHARED_FACTS:
        return _build_non_related(raw, proposal_id, relation, left, right, closure)

    fact = str(raw.get("fact_statement") or "").strip()
    evidence_by = _evidence_by_occurrence(raw, ids)
    evidence_error = _validate_evidence(evidence_by, left, right)
    support_error = _validate_rendered_support(left, right)
    contribution = str(raw.get("later_independent_contribution") or "").strip()
    if not fact or evidence_error or support_error or not contribution:
        reason = "; ".join(item for item in [evidence_error, support_error, "later independent contribution is required" if not contribution else ""] if item)
        return _build_insufficient(
            raw=raw,
            proposal_id=proposal_id,
            left=left,
            right=right,
            closure=closure,
            reason=reason or "shared fact statement is required",
        )
    earlier, later = _ordered(left, right)
    disposition = _deterministic_disposition(raw, earlier, later, closure)
    impact = _downstream_impact(later["occurrence_id"], closure)
    rationale = str(raw.get("rationale") or "")
    if not rationale:
        rationale = "Both sides supplied authorized evidence and rendered support; later independent contribution is preserved."
    return SharedInstructionalFact(
        shared_fact_id=proposal_id,
        fact_statement=fact,
        source_occurrence_ids=ids,
        source_canonical_knowledge_ids=(left["canonical_id"], right["canonical_id"]),
        evidence_ids_by_occurrence=evidence_by,
        rendered_support_by_occurrence={item["occurrence_id"]: _support_payload(item) for item in (left, right)},
        earlier_occurrence_id=earlier["occurrence_id"],
        later_occurrence_id=later["occurrence_id"],
        relation=RELATED_WITH_SHARED_FACTS,
        earlier_verified_facets=tuple(earlier.get("verified_facets", ())),
        later_required_facets=tuple(later.get("required_facets", ())),
        later_independent_contribution=contribution,
        disposition=disposition,
        rationale=rationale,
        downstream_closure_impact=impact,
        confidence=_confidence(raw.get("confidence")),
        earlier_position=_position_payload(earlier),
        later_position=_position_payload(later),
    )


def _build_non_related(raw: Mapping[str, Any], proposal_id: str, relation: str, left: dict[str, Any], right: dict[str, Any], closure: list[dict[str, Any]]) -> SharedInstructionalFact:
    earlier, later = _ordered(left, right)
    return SharedInstructionalFact(
        shared_fact_id=proposal_id,
        fact_statement=str(raw.get("fact_statement") or ""),
        source_occurrence_ids=(left["occurrence_id"], right["occurrence_id"]),
        source_canonical_knowledge_ids=(left["canonical_id"], right["canonical_id"]),
        evidence_ids_by_occurrence=_evidence_by_occurrence(raw, (left["occurrence_id"], right["occurrence_id"])),
        rendered_support_by_occurrence={item["occurrence_id"]: _support_payload(item) for item in (left, right)},
        earlier_occurrence_id=earlier["occurrence_id"],
        later_occurrence_id=later["occurrence_id"],
        relation=relation,
        earlier_verified_facets=tuple(earlier.get("verified_facets", ())),
        later_required_facets=tuple(later.get("required_facets", ())),
        later_independent_contribution=str(raw.get("later_independent_contribution") or ""),
        disposition=INSUFFICIENT_INFORMATION if relation == INSUFFICIENT_INFORMATION else NOT_COMPRESSIBLE,
        rationale=str(raw.get("rationale") or "Relation is retained as an audit result; no compression action is authorized."),
        downstream_closure_impact=_downstream_impact(later["occurrence_id"], closure),
        confidence=_confidence(raw.get("confidence")),
        earlier_position=_position_payload(earlier),
        later_position=_position_payload(later),
    )


def _build_insufficient(*, raw: Mapping[str, Any], proposal_id: str, left: dict[str, Any], right: dict[str, Any], closure: list[dict[str, Any]], reason: str) -> SharedInstructionalFact:
    earlier, later = _ordered(left, right)
    ids = (left["occurrence_id"], right["occurrence_id"])
    return SharedInstructionalFact(
        shared_fact_id=proposal_id,
        fact_statement=str(raw.get("fact_statement") or ""),
        source_occurrence_ids=ids,
        source_canonical_knowledge_ids=(left["canonical_id"], right["canonical_id"]),
        evidence_ids_by_occurrence=_evidence_by_occurrence(raw, ids),
        rendered_support_by_occurrence={item["occurrence_id"]: _support_payload(item) for item in (left, right)},
        earlier_occurrence_id=earlier["occurrence_id"],
        later_occurrence_id=later["occurrence_id"],
        relation=INSUFFICIENT_INFORMATION,
        earlier_verified_facets=tuple(earlier.get("verified_facets", ())),
        later_required_facets=tuple(later.get("required_facets", ())),
        later_independent_contribution=str(raw.get("later_independent_contribution") or ""),
        disposition=INSUFFICIENT_INFORMATION,
        rationale=reason,
        downstream_closure_impact=_downstream_impact(later["occurrence_id"], closure),
        confidence=_confidence(raw.get("confidence")),
        earlier_position=_position_payload(earlier),
        later_position=_position_payload(later),
    )


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        get = value.get
    else:
        get = lambda key, default=None: getattr(value, key, default)
    occurrence_id = str(get("occurrence_id", ""))
    body = str(get("body", get("rendered_body", get("markdown", ""))) or "").strip()
    canonical_id = str(get("canonical_knowledge_id", get("knowledge_id", "")))
    source_ids = tuple(str(item) for item in (get("source_chunk_ids", get("evidence_ids", ())) or ()) if str(item))
    verified_facets = tuple(str(item) for item in (get("verified_facets", get("conformance_verified_facets", ())) or ()) if str(item))
    required_facets = tuple(str(item) for item in (get("required_facets", get("must_teach_facets", ())) or ()) if str(item))
    conformance = str(get("conformance", get("conformance_status", get("overall", ""))) or "")
    evidence = str(get("evidence", get("evidence_status", "")) or "")
    runtime_grant_applied = bool(get("runtime_grant_applied", True))
    if isinstance(get("conformance_result", None), Mapping):
        result = get("conformance_result")
        conformance = str(result.get("overall") or conformance)
        verified_facets = tuple(result.get("verified_facets") or verified_facets)
    support = "verified" if conformance in _MATCH and evidence in _SUPPORTED and body else "not_verified"
    position = get("position", {}) or {}
    if not isinstance(position, Mapping):
        position = {}
    return {
        "occurrence_id": occurrence_id,
        "canonical_id": canonical_id,
        "body": body,
        "source_chunk_ids": source_ids,
        "verified_facets": verified_facets,
        "required_facets": required_facets,
        "conformance": conformance,
        "evidence": evidence,
        "support": support,
        "runtime_grant_applied": runtime_grant_applied,
        "position": tuple(int(position.get(key, 0) or 0) for key in ("chapter_ordinal", "task_ordinal", "occurrence_ordinal", "section_ordinal", "source_point_ordinal")),
    }


def _ordered(left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return (left, right) if left["position"] <= right["position"] else (right, left)


def _position_payload(item: dict[str, Any]) -> dict[str, int]:
    keys = ("chapter_ordinal", "task_ordinal", "occurrence_ordinal", "section_ordinal", "source_point_ordinal")
    return {key: int(value) for key, value in zip(keys, item.get("position", (0,) * len(keys)))}


def _evidence_by_occurrence(raw: Mapping[str, Any], ids: tuple[str, str]) -> dict[str, tuple[str, ...]]:
    value = raw.get("evidence_ids_by_occurrence") or {}
    return {item: tuple(str(evidence_id) for evidence_id in (value.get(item, ()) or ()) if str(evidence_id)) for item in ids}


def _validate_evidence(evidence_by: dict[str, tuple[str, ...]], left: dict[str, Any], right: dict[str, Any]) -> str:
    if any(not evidence_by.get(item["occurrence_id"]) for item in (left, right)):
        return "both occurrences need at least one bound evidence ID"
    for item in (left, right):
        unknown = set(evidence_by[item["occurrence_id"]]) - set(item["source_chunk_ids"])
        if unknown:
            return f"evidence is not authorized for {item['occurrence_id']}: {sorted(unknown)}"
    return ""


def _validate_rendered_support(left: dict[str, Any], right: dict[str, Any]) -> str:
    if left["support"] != "verified" or right["support"] != "verified":
        return "both occurrences need non-empty rendered body, conformance MATCH, and evidence SUPPORTED"
    return ""


def _support_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": item["support"],
        "conformance": item["conformance"],
        "evidence": item["evidence"],
        "non_empty_body": bool(item["body"]),
        "rendered_span_id": item.get("rendered_span_id", ""),
        "runtime_grant_applied": bool(item.get("runtime_grant_applied", True)),
    }


def _deterministic_disposition(
    raw: Mapping[str, Any],
    earlier: dict[str, Any],
    later: dict[str, Any],
    closure: list[dict[str, Any]],
) -> str:
    if not earlier.get("runtime_grant_applied", True):
        return NOT_COMPRESSIBLE
    if bool(raw.get("shared_fact_is_core_responsibility")) or bool(raw.get("later_requires_explicit_teaching")):
        return NOT_COMPRESSIBLE
    if bool(raw.get("downstream_requires_full_teach")):
        return NOT_COMPRESSIBLE
    if _downstream_uses_full_teach(later["occurrence_id"], closure):
        return NOT_COMPRESSIBLE
    if bool(raw.get("requires_recontextualization")):
        return CONTEXTUAL_RESTATEMENT_REQUIRED
    if str(raw.get("disposition") or "") == COMPRESSIBLE:
        return COMPRESSIBLE
    if str(raw.get("disposition") or "") == NOT_COMPRESSIBLE:
        return NOT_COMPRESSIBLE
    return CONTEXTUAL_RESTATEMENT_REQUIRED


def _closure_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return list(value.get("results", ()))
    return list(getattr(value, "results", ()) or ())


def _downstream_impact(occurrence_id: str, closure: list[dict[str, Any]]) -> dict[str, Any]:
    references = []
    for item in closure:
        requirement = item.get("requirement", {}) if isinstance(item, Mapping) else {}
        supporting = item.get("supporting_occurrence_ids", ()) if isinstance(item, Mapping) else ()
        if occurrence_id in supporting or occurrence_id in (requirement.get("target_occurrence_ids", ()) or ()):
            references.append({"requirement_id": requirement.get("requirement_id"), "status": item.get("status")})
    return {"referencing_requirements": references, "count": len(references)}


def _downstream_uses_full_teach(occurrence_id: str, closure: list[dict[str, Any]]) -> bool:
    for item in closure:
        requirement = item.get("requirement", {}) if isinstance(item, Mapping) else {}
        if occurrence_id not in (item.get("supporting_occurrence_ids", ()) or ()):
            continue
        required = set(requirement.get("required_facets", ()) or ())
        if required.intersection({"EXPLAIN", "PERFORM", "ANALYZE"}):
            return True
    return False


def _terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    terms = {normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))}
    terms.update(re.findall(r"[a-z]{2,}|\d+(?:\.\d+)?", normalized))
    return {item for item in terms if item.strip()}


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0
