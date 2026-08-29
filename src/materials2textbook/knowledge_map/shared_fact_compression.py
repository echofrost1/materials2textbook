"""Read-only Phase 3B-2 shared-fact compression planning.

The planner converts an audited :class:`SharedInstructionalFact` into a
fact-level policy for a later occurrence.  It never edits rendered text,
changes a role/facet/prerequisite, grants availability, or authorizes
materialization.  The resulting brief constraints are an overlay on the
existing ``OccurrenceWritingBrief`` rather than a second writer planner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Iterable, Mapping

from materials2textbook.knowledge_map.shared_facts import (
    COMPRESSIBLE,
    CONTEXTUAL_RESTATEMENT_REQUIRED,
    INSUFFICIENT_INFORMATION,
    NOT_COMPRESSIBLE,
    SharedInstructionalFact,
)


NO_CHANGE = "NO_CHANGE"
NO_AUTO_ACTION = "NO_AUTO_ACTION"


@dataclass(frozen=True)
class SharedFactCompressionPlan:
    """An auditable policy, not a text edit."""

    plan_id: str
    shared_fact_id: str
    earlier_occurrence_id: str
    later_occurrence_id: str
    earlier_canonical_id: str
    later_canonical_id: str
    disposition: str
    shared_fact_statement: str
    later_unique_contribution: str
    allowed_actions: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    required_restatement: str
    max_recap_scope: str
    required_evidence_ids: tuple[str, ...]
    prior_verified_support: dict[str, Any]
    downstream_safety_constraints: dict[str, Any]
    expected_post_compression_conformance: dict[str, Any]
    distance: dict[str, int] = field(default_factory=dict)
    chapter_boundary: bool = False
    section_boundary: bool = False
    task_boundary: bool = False
    auto_materialization_eligible: bool = False
    manual_review_reason: str = ""
    compiled_brief_constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("allowed_actions", "forbidden_actions", "required_evidence_ids"):
            value[key] = list(value[key])
        return value


@dataclass
class SharedFactCompressionReport:
    plans: list[SharedFactCompressionPlan] = field(default_factory=list)
    disposition_counts: dict[str, int] = field(default_factory=dict)
    materialization_eligible_count: int = 0
    rejected_shared_fact_ids: list[str] = field(default_factory=list)
    compiled_brief_constraints_by_occurrence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plans": [item.to_dict() for item in self.plans],
            "disposition_counts": dict(self.disposition_counts),
            "materialization_eligible_count": self.materialization_eligible_count,
            "rejected_shared_fact_ids": list(self.rejected_shared_fact_ids),
            "compiled_brief_constraints_by_occurrence": dict(self.compiled_brief_constraints_by_occurrence),
            "materialization_eligible": False,
        }


def build_shared_fact_compression_plans(
    shared_facts: Iterable[SharedInstructionalFact],
    *,
    downstream_closure: Any | None = None,
    briefs_by_occurrence: Mapping[str, Any] | None = None,
) -> SharedFactCompressionReport:
    """Compile audited facts into conservative, non-materializing policies."""
    closure = _closure_records(downstream_closure)
    report = SharedFactCompressionReport()
    for fact in shared_facts:
        plan = build_shared_fact_compression_plan(fact, downstream_closure=closure)
        if plan is None:
            report.rejected_shared_fact_ids.append(fact.shared_fact_id)
            continue
        report.plans.append(plan)
        report.disposition_counts[plan.disposition] = report.disposition_counts.get(plan.disposition, 0) + 1
        if plan.auto_materialization_eligible:
            report.materialization_eligible_count += 1
        if briefs_by_occurrence and plan.later_occurrence_id in briefs_by_occurrence:
            current = briefs_by_occurrence[plan.later_occurrence_id]
            compiled = compile_shared_fact_constraints_into_brief(current, plan)
            if is_dataclass(compiled):
                report.compiled_brief_constraints_by_occurrence[plan.later_occurrence_id] = asdict(compiled)
            elif isinstance(compiled, Mapping):
                report.compiled_brief_constraints_by_occurrence[plan.later_occurrence_id] = dict(compiled)
    return report


def build_shared_fact_compression_plan(
    fact: SharedInstructionalFact,
    *,
    downstream_closure: Any | None = None,
) -> SharedFactCompressionPlan | None:
    """Build one policy after a deterministic downstream counterfactual gate."""
    if fact.relation != "RELATED_WITH_SHARED_FACTS":
        return None
    closure = _closure_records(downstream_closure)
    safety = _counterfactual_safety(fact, closure)
    disposition = fact.disposition
    manual_reason = ""
    if not safety["earlier_support_verified"]:
        disposition = INSUFFICIENT_INFORMATION
        manual_reason = "Earlier occurrence does not have verified rendered support for compression."
    elif disposition == COMPRESSIBLE and safety["requires_explicit_teaching"]:
        disposition = NOT_COMPRESSIBLE
        manual_reason = "Downstream closure requires explicit teaching of the shared fact."
    elif disposition == COMPRESSIBLE and safety["has_blocked_or_unclosed_dependency"]:
        disposition = CONTEXTUAL_RESTATEMENT_REQUIRED
        manual_reason = "Downstream support is not fully closed; preserve a contextual bridge."
    elif disposition == INSUFFICIENT_INFORMATION:
        manual_reason = "Evidence, identity, or teaching support is insufficient for automatic planning."

    policy = _policy_for(disposition, fact)
    canonical_by_occurrence = dict(zip(fact.source_occurrence_ids, fact.source_canonical_knowledge_ids))
    distance = _position_distance(fact.earlier_position, fact.later_position)
    plan = SharedFactCompressionPlan(
        plan_id=f"compression:{fact.shared_fact_id}",
        shared_fact_id=fact.shared_fact_id,
        earlier_occurrence_id=fact.earlier_occurrence_id,
        later_occurrence_id=fact.later_occurrence_id,
        earlier_canonical_id=canonical_by_occurrence.get(fact.earlier_occurrence_id, ""),
        later_canonical_id=canonical_by_occurrence.get(fact.later_occurrence_id, ""),
        disposition=disposition,
        shared_fact_statement=fact.fact_statement,
        later_unique_contribution=fact.later_independent_contribution,
        allowed_actions=policy["allowed_actions"],
        forbidden_actions=policy["forbidden_actions"],
        required_restatement=policy["required_restatement"],
        max_recap_scope=policy["max_recap_scope"],
        required_evidence_ids=tuple(
            evidence_id
            for ids in fact.evidence_ids_by_occurrence.values()
            for evidence_id in ids
        ),
        prior_verified_support={
            "earlier_verified_facets": list(fact.earlier_verified_facets),
            "rendered_support": fact.rendered_support_by_occurrence,
            "earlier_occurrence_id": fact.earlier_occurrence_id,
        },
        downstream_safety_constraints=safety,
        expected_post_compression_conformance={
            "overall": "MATCH",
            "later_role_unchanged": True,
            "required_facets_unchanged": True,
            "required_extensions_unchanged": True,
            "unique_contribution_present": True,
            "evidence_bounded": True,
        },
        distance=distance,
        chapter_boundary=distance["chapter_delta"] != 0,
        section_boundary=distance["section_delta"] != 0,
        task_boundary=distance["task_delta"] != 0,
        auto_materialization_eligible=False,
        manual_review_reason=manual_reason,
        compiled_brief_constraints=_compiled_brief_constraints(fact, disposition, policy),
    )
    return plan


def compile_shared_fact_constraints_into_brief(brief: Any, plan: SharedFactCompressionPlan) -> Any:
    """Return an immutable brief overlay without changing role or facets.

    The returned object is suitable for a future writer invocation.  Phase
    3B-2 does not replace the production brief or render it; callers can use
    this function for audit and regression checks only.
    """
    constraints = plan.compiled_brief_constraints
    if isinstance(brief, Mapping):
        result = dict(brief)
        for key, value in constraints.items():
            if key in {"forbidden_content", "must_include_points", "must_avoid_patterns", "allowed_content"}:
                result[key] = list(dict.fromkeys(list(result.get(key, ()) or ()) + list(value)))
            elif key == "max_recap_sentences":
                result[key] = max(int(result.get(key, 0) or 0), int(value or 0))
        result["shared_fact_compression_plan_id"] = plan.plan_id
        return result
    if not is_dataclass(brief):
        raise TypeError("brief must be a mapping or dataclass instance")
    updates: dict[str, Any] = {"shared_fact_compression_plan_id": plan.plan_id}
    fields = {item.name for item in getattr(brief, "__dataclass_fields__", {}).values()}
    for key, value in constraints.items():
        if key not in fields:
            continue
        current = getattr(brief, key, ())
        if key in {"forbidden_content", "must_include_points", "must_avoid_patterns", "allowed_content"}:
            updates[key] = list(dict.fromkeys(list(current or ()) + list(value)))
        elif key == "max_recap_sentences":
            updates[key] = max(int(current or 0), int(value or 0))
    return replace(brief, **{key: value for key, value in updates.items() if key in fields})


def render_shared_fact_compression_markdown(report: SharedFactCompressionReport) -> str:
    lines = [
        "# Shared Fact Compression Plans",
        "",
        "Read-only Phase 3B-2 policy output. No textbook text was changed and no plan is materialization-eligible.",
        f"- Plans: {len(report.plans)}",
        f"- Materialization eligible: {report.materialization_eligible_count}",
        "",
        "## Disposition counts",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(report.disposition_counts.items()))
    lines.extend(["", "## Plans", ""])
    for plan in report.plans:
        lines.extend(
            [
                f"### {plan.plan_id}",
                f"- shared fact: {plan.shared_fact_statement}",
                f"- occurrences: {plan.earlier_occurrence_id} -> {plan.later_occurrence_id}",
                f"- disposition: {plan.disposition}",
                f"- allowed actions: {', '.join(plan.allowed_actions) or 'none'}",
                f"- forbidden actions: {', '.join(plan.forbidden_actions) or 'none'}",
                f"- required restatement: {plan.required_restatement or 'none'}",
                f"- recap scope: {plan.max_recap_scope}",
                f"- distance: {plan.distance}; chapter/section/task boundary: {plan.chapter_boundary}/{plan.section_boundary}/{plan.task_boundary}",
                f"- unique contribution: {plan.later_unique_contribution}",
                f"- evidence: {', '.join(plan.required_evidence_ids) or 'none'}",
                f"- downstream safety: {plan.downstream_safety_constraints}",
                f"- compiled brief constraints: {plan.compiled_brief_constraints}",
                f"- auto materialization eligible: {plan.auto_materialization_eligible}",
                f"- manual review reason: {plan.manual_review_reason or 'none'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _policy_for(disposition: str, fact: SharedInstructionalFact) -> dict[str, Any]:
    if disposition == COMPRESSIBLE:
        return {
            "allowed_actions": ("COMPRESS_SHARED_FACT_TO_MINIMAL_REFERENCE", "RETAIN_LATER_UNIQUE_CONTRIBUTION"),
            "forbidden_actions": ("FULL_RETEACH_SHARED_FACT", "DROP_LATER_UNIQUE_CONTRIBUTION", "CHANGE_ROLE_OR_FACETS"),
            "required_restatement": "",
            "max_recap_scope": "minimal reference; at most one sentence if needed for discourse continuity",
        }
    if disposition == CONTEXTUAL_RESTATEMENT_REQUIRED:
        return {
            "allowed_actions": ("COMPRESS_SHARED_FACT_TO_CONTEXTUAL_RESTATEMENT", "RETAIN_LATER_UNIQUE_CONTRIBUTION"),
            "forbidden_actions": ("FULL_RETEACH_SHARED_FACT", "DROP_CONTEXTUAL_BRIDGE", "DROP_LATER_UNIQUE_CONTRIBUTION", "CHANGE_ROLE_OR_FACETS"),
            "required_restatement": fact.fact_statement,
            "max_recap_scope": "minimal contextual restatement; no standalone full lesson",
        }
    if disposition == NOT_COMPRESSIBLE:
        return {
            "allowed_actions": (NO_CHANGE,),
            "forbidden_actions": ("COMPRESS_SHARED_FACT", "DROP_LATER_UNIQUE_CONTRIBUTION", "CHANGE_ROLE_OR_FACETS"),
            "required_restatement": "",
            "max_recap_scope": "full current teaching responsibility remains unchanged",
        }
    return {
        "allowed_actions": (NO_AUTO_ACTION,),
        "forbidden_actions": ("COMPRESS_SHARED_FACT", "FULL_RETEACH_BY_GUESS", "CHANGE_ROLE_OR_FACETS"),
        "required_restatement": "",
        "max_recap_scope": "unknown; manual review required",
    }


def _compiled_brief_constraints(
    fact: SharedInstructionalFact,
    disposition: str,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    constraints: dict[str, Any] = {
        "forbidden_content": [f"Do not fully reteach shared fact: {fact.fact_statement}"],
        "must_include_points": [fact.later_independent_contribution],
        "must_avoid_patterns": ["standalone full explanation of the shared fact"],
    }
    if disposition == COMPRESSIBLE:
        constraints["allowed_content"] = ["minimal reference or discourse handoff to the earlier verified fact"]
        constraints["max_recap_sentences"] = 1
    elif disposition == CONTEXTUAL_RESTATEMENT_REQUIRED:
        constraints["allowed_content"] = [f"minimal contextual bridge grounded in: {fact.fact_statement}"]
        constraints["must_include_points"] = [fact.fact_statement, fact.later_independent_contribution]
        constraints["max_recap_sentences"] = 2
    else:
        constraints["allowed_content"] = []
        constraints["max_recap_sentences"] = 0
    return constraints


def _closure_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return list(value.get("results", ()) or ())
    return list(getattr(value, "results", ()) or ())


def _position_distance(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> dict[str, int]:
    def value(source: Mapping[str, Any], key: str) -> int:
        try:
            return int(source.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "chapter_delta": value(later, "chapter_ordinal") - value(earlier, "chapter_ordinal"),
        "section_delta": value(later, "section_ordinal") - value(earlier, "section_ordinal"),
        "task_delta": value(later, "task_ordinal") - value(earlier, "task_ordinal"),
        "occurrence_delta": value(later, "occurrence_ordinal") - value(earlier, "occurrence_ordinal"),
    }


def _counterfactual_safety(fact: SharedInstructionalFact, closure: list[dict[str, Any]]) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    requires_explicit = False
    has_blocked_or_unclosed = False
    unsafe_statuses = {"UNDER_SUPPORTED", "UNSUPPORTED", "BLOCKED_BY_PRIOR_FAILURE", "TARGET_NOT_DELIVERED"}
    for item in closure:
        requirement = item.get("requirement", {}) if isinstance(item, Mapping) else {}
        supporting = set(item.get("supporting_occurrence_ids", ()) or ()) if isinstance(item, Mapping) else set()
        if fact.later_occurrence_id not in supporting:
            continue
        required = set(requirement.get("required_facets", ()) or ())
        status = str(item.get("status") or "")
        full_teach = bool(required.intersection({"EXPLAIN", "PERFORM", "ANALYZE"}))
        requires_explicit = requires_explicit or full_teach
        has_blocked_or_unclosed = has_blocked_or_unclosed or status in unsafe_statuses
        references.append(
            {
                "requirement_id": requirement.get("requirement_id", ""),
                "status": status,
                "required_facets": sorted(required),
                "requires_explicit_teaching": full_teach,
            }
        )
    return {
        "later_occurrence_id": fact.later_occurrence_id,
        "referencing_requirements": references,
        "requires_explicit_teaching": requires_explicit,
        "has_blocked_or_unclosed_dependency": has_blocked_or_unclosed,
        "earlier_support_verified": _earlier_support_verified(fact),
        "verified_availability_unchanged": True,
        "role_unchanged": True,
        "prerequisites_unchanged": True,
    }


def _earlier_support_verified(fact: SharedInstructionalFact) -> bool:
    support = fact.rendered_support_by_occurrence.get(fact.earlier_occurrence_id, {})
    if isinstance(support, Mapping):
        return (
            support.get("status") == "verified"
            and support.get("conformance") in {"MATCH", "PASS"}
            and support.get("evidence") in {"SUPPORTED", "MATCH", "PASS"}
            and bool(support.get("non_empty_body", True))
            and bool(support.get("runtime_grant_applied", True))
        )
    return support == "verified"
