from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from dataclasses import dataclass, field
from typing import Any

from materials2textbook.agents.knowledge_semantic_planner import LLMSemanticPlanningAgent
from materials2textbook.knowledge_map.availability import (
    cross_requirements_available,
    self_requirements_available,
    simulate_planned_instructional_availability,
)
from materials2textbook.knowledge_map.occurrences import plan_occurrences
from materials2textbook.knowledge_map.models import (
    InstructionalAvailabilityState,
    KnowledgeMap,
    LearningRole,
    MasteryFacet,
    PlannedOccurrence,
    PrerequisiteUse,
    RuntimeOccurrenceCompilation,
    SemanticDelta,
)
from materials2textbook.knowledge_map.semantic import (
    HeuristicSemanticPlanner,
    MIN_TRUSTED_CONFIDENCE,
    prerequisite_has_runtime_basis,
)
from materials2textbook.knowledge_map.validator import validate_planned_trajectory
from materials2textbook.schemas import EvidenceChunk


_FACETS = {MasteryFacet.ORIENTED, MasteryFacet.EXPLAIN, MasteryFacet.PERFORM, MasteryFacet.ANALYZE}
_IDENTITIES = {"SAME", "RELATED", "DECOMPOSE", "DISTINCT", "UNCERTAIN"}


@dataclass
class SemanticPlanningEvaluation:
    """Read-only semantic-delta proposal and deterministic re-validation."""

    knowledge_map: KnowledgeMap
    identity_judgements: list[dict[str, Any]] = field(default_factory=list)
    semantic_deltas: list[SemanticDelta] = field(default_factory=list)
    rejected_proposals: list[dict[str, Any]] = field(default_factory=list)
    normalizations: list[dict[str, Any]] = field(default_factory=list)
    prerequisite_audit: list[dict[str, Any]] = field(default_factory=list)
    call_counts: dict[str, int] = field(default_factory=dict)


def evaluate_semantic_planning(
    *, knowledge_map: KnowledgeMap, chunks: list[EvidenceChunk], agent: LLMSemanticPlanningAgent, recall_after_tasks: int = 3,
) -> SemanticPlanningEvaluation:
    """Use the LLM for semantic facts only; derive every LearningRole locally."""
    evaluated = deepcopy(knowledge_map)
    chunk_lookup = {item.chunk_id: item for item in chunks}
    sources = {item.source_knowledge_point_id: item for item in evaluated.source_knowledge_points}
    points = {item.knowledge_id: item for item in evaluated.knowledge_points}
    occurrences = {item.occurrence_id: item for item in evaluated.planned_occurrences}
    rejected: list[dict[str, Any]] = []
    normalizations: list[dict[str, Any]] = []
    deltas: list[SemanticDelta] = []

    candidates = _identity_merge_candidates(evaluated, chunk_lookup)
    identity_response = _safe_call(lambda: agent.judge_identity(candidates), rejected, "identity") if candidates else {"judgements": []}
    identity = [_normalise_identity(item, rejected) for item in _list_response(identity_response, "judgements", rejected, "identity")]
    if _apply_accepted_identity_merges(evaluated, identity):
        # A merge changes canonical identity. Rebuild occurrences and their
        # trajectories before any role delta is planned; otherwise an alias
        # could receive a separate, contradictory learning history.
        evaluated.planned_occurrences = plan_occurrences(
            source_points=evaluated.source_knowledge_points,
            knowledge_points=evaluated.knowledge_points,
            mappings=evaluated.mappings,
            prerequisites=evaluated.prerequisites,
            semantic_planner=HeuristicSemanticPlanner(),
        )
        occurrences = {item.occurrence_id: item for item in evaluated.planned_occurrences}
        evaluated.trajectories = _rebuild_trajectories(evaluated)
        points = {item.knowledge_id: item for item in evaluated.knowledge_points}

    canonical_whitelist = [{"knowledge_id": item.knowledge_id, "title": item.title} for item in evaluated.knowledge_points]
    for trajectory in evaluated.trajectories:
        current = [occurrences[item] for item in trajectory.occurrence_ids]
        if not current:
            continue
        payload = _trajectory_payload(trajectory.knowledge_id, points[trajectory.knowledge_id].title, current, sources, chunk_lookup, canonical_whitelist)
        response = _safe_call(lambda: agent.plan_semantic_deltas(payload), rejected, "semantic_delta")
        delta_by_id = {item.get("occurrence_id"): item for item in _list_response(response, "deltas", rejected, "semantic_delta") if isinstance(item, dict)}
        for index, occurrence in enumerate(current):
            delta = _parse_delta(
                delta_by_id.get(occurrence.occurrence_id), occurrence, points, rejected,
                normalizations, has_previous=bool(index),
            )
            if delta is None:
                occurrence.trusted_for_state = False
                continue
            deltas.append(delta)
    compiled_occurrences, final_deltas, prerequisite_audit = _compile_final_occurrences(
        knowledge_map=evaluated,
        deltas=deltas,
        sources=sources,
    )
    evaluated.planned_occurrences = compiled_occurrences

    # This is deliberately a planning diagnostic, not runtime proof that a
    # rendered occurrence taught its intended grants.  Runtime compilation is
    # performed against verified execution state by
    # ``compile_occurrence_for_verified_availability``.
    evaluated.availability_snapshots = simulate_planned_instructional_availability(evaluated.planned_occurrences)
    evaluated.validation_issues = validate_planned_trajectory(
        occurrences=evaluated.planned_occurrences,
        mappings=evaluated.mappings,
        snapshots=evaluated.availability_snapshots,
        semantic_planner=HeuristicSemanticPlanner(),
        recall_after_tasks=recall_after_tasks,
    )
    issue_ids_by_knowledge: dict[str, list[str]] = {}
    for issue in evaluated.validation_issues:
        issue_ids_by_knowledge.setdefault(issue.knowledge_id, []).append(issue.issue_id)
    for trajectory in evaluated.trajectories:
        trajectory.planned_conflict_ids = issue_ids_by_knowledge.get(trajectory.knowledge_id, [])
    evaluated.analysis_version = "knowledge-map.phase-1.5.semantic-delta.v2"
    return SemanticPlanningEvaluation(
        knowledge_map=evaluated,
        identity_judgements=identity,
        semantic_deltas=final_deltas,
        rejected_proposals=rejected,
        normalizations=normalizations,
        prerequisite_audit=prerequisite_audit,
        call_counts=dict(agent.call_counts),
    )


def _compile_final_occurrences(
    *,
    knowledge_map: KnowledgeMap,
    deltas: list[SemanticDelta],
    sources: dict[str, Any],
) -> tuple[list[Any], list[SemanticDelta], list[dict[str, Any]]]:
    """Compile the final normalized delta into occurrences in book order.

    This is the single write boundary for *planned* ``PlannedOccurrence``
    semantic fields.  It consumes assumed planned availability only to make
    static planning diagnostics internally coherent.  It must not be confused
    with production runtime availability, which is established only from
    verified rendered execution.
    """
    delta_by_id = {item.occurrence_id: item for item in deltas}
    ordered = sorted(knowledge_map.planned_occurrences, key=lambda item: item.position)
    trajectories: dict[str, list[Any]] = {}
    for occurrence in ordered:
        trajectories.setdefault(occurrence.knowledge_id, []).append(occurrence)
    first_position = {knowledge_id: items[0].position for knowledge_id, items in trajectories.items()}
    compiled: list[Any] = []
    final_deltas: list[SemanticDelta] = []
    prerequisite_audit: list[dict[str, Any]] = []

    for seed in ordered:
        delta = delta_by_id.get(seed.occurrence_id)
        if delta is None:
            compiled.append(replace(seed, trusted_for_state=False))
            continue
        snapshots = simulate_planned_instructional_availability(compiled)
        before = snapshots[-1].after if snapshots else InstructionalAvailabilityState()
        prior = [item for item in compiled if item.knowledge_id == seed.knowledge_id]
        record = before.availability_by_knowledge.get(seed.knowledge_id)
        available = list(record.available_facets) if record else []
        available_extensions = list(record.available_extension_keys) if record else []
        # A semantic proposal is made without access to the final state of all
        # preceding compiled occurrences.  It may therefore call an already
        # available facet/extension "new".  Normalise that stale claim before
        # deriving role or building a WritingBrief; otherwise the brief could
        # simultaneously require a complete procedure and prohibit reteaching
        # the same procedure.  This is a deterministic state-compilation
        # correction, not a change to the upstream semantic taxonomy.
        delta, availability_normalizations = _normalize_delta_for_availability(
            delta=delta,
            available_facets=available,
            available_extension_keys=available_extensions,
            occurrence_id=seed.occurrence_id,
        )
        prerequisite_audit.extend(availability_normalizations)
        effective_cross, cross_audit = _compile_cross_prerequisites(
            occurrence=seed,
            delta=delta,
            before=before,
            first_position=first_position,
        )
        prerequisite_audit.extend(cross_audit)
        final_delta = replace(delta, cross_prerequisite_uses=effective_cross)
        compiled_occurrence = replace(seed)
        future_contexts = [
            sources[item.source_knowledge_point_id].context_title
            for item in trajectories[seed.knowledge_id]
            if item.position > seed.position
        ]
        _apply_delta(
            compiled_occurrence,
            final_delta,
            has_previous=bool(prior),
            source_context=sources[seed.source_knowledge_point_id].context_title,
            prior_available_facets=available,
            prior_available_extension_keys=available_extensions,
            future_contexts=future_contexts,
        )
        # Regression invariant: no pre-normalization requirement may survive
        # the compiler boundary.
        if compiled_occurrence.required_self_facets != final_delta.required_self_facets:
            raise RuntimeError(f"Compiled self-facet requirements diverged for {seed.occurrence_id!r}.")
        if compiled_occurrence.required_self_extension_keys != final_delta.required_self_extension_keys:
            raise RuntimeError(f"Compiled self-extension requirements diverged for {seed.occurrence_id!r}.")
        compiled.append(compiled_occurrence)
        final_deltas.append(final_delta)
    return compiled, final_deltas, prerequisite_audit


def compile_occurrence_for_verified_availability(
    *,
    seed: PlannedOccurrence,
    delta: SemanticDelta,
    verified_before: InstructionalAvailabilityState,
    has_previous: bool,
    source_context: str = "",
    future_contexts: list[str] | None = None,
    first_position: dict[str, Any] | None = None,
) -> RuntimeOccurrenceCompilation:
    """Compile one occurrence immediately before writing against verified state.

    Whole-book semantic planning remains read-only and may still establish a
    useful planned trajectory.  This function is the separate runtime boundary:
    it never grants from ``intended_grants`` and refuses to turn an occurrence
    that relies on prior teaching into APPLY/RECALL unless that teaching has
    already rendered and passed local conformance and evidence verification.
    """
    before = deepcopy(verified_before)
    record = before.availability_by_knowledge.get(seed.knowledge_id)
    available_facets = list(record.available_facets) if record else []
    available_extensions = list(record.available_extension_keys) if record else []
    normalized_delta, audit = _normalize_delta_for_availability(
        delta=delta,
        available_facets=available_facets,
        available_extension_keys=available_extensions,
        occurrence_id=seed.occurrence_id,
    )
    effective_cross, cross_audit = _compile_cross_prerequisites(
        occurrence=seed,
        delta=normalized_delta,
        before=before,
        first_position=first_position or {},
    )
    audit.extend(cross_audit)
    final_delta = replace(normalized_delta, cross_prerequisite_uses=effective_cross)
    candidate = replace(
        seed,
        required_self_facets=list(final_delta.required_self_facets),
        required_self_extension_keys=list(final_delta.required_self_extension_keys),
        required_prerequisites=list(effective_cross),
    )
    self_available = self_requirements_available(before, candidate)
    cross_available = cross_requirements_available(before, candidate)

    # These semantic facts mean this occurrence is explicitly trying to rely on
    # preceding teaching.  A structural predecessor or an intended grant is not
    # a substitute for a verified source record.  Do not silently derive TEACH
    # in this circumstance: the execution path must surface the broken chain.
    relies_on_prior_teaching = has_previous and (
        final_delta.uses_prior_knowledge
        or final_delta.recall_needed
        or final_delta.restores_prior_context
    )
    has_verified_prior_support = bool(available_facets or available_extensions)
    blockers: list[tuple[str, str]] = []
    if not seed.trusted_for_state or delta.confidence < MIN_TRUSTED_CONFIDENCE:
        blockers.append(("UNTRUSTED_SEMANTIC_PLAN", "The semantic plan is not trusted for runtime execution."))
    if relies_on_prior_teaching and not has_verified_prior_support:
        blockers.append((
            "PRIOR_TEACHING_NOT_VERIFIED",
            "This occurrence relies on prior teaching, but no verified rendered teaching is available for it.",
        ))
    if not self_available:
        blockers.append((
            "SELF_REQUIREMENT_NOT_VERIFIED",
            "Required facets or extensions of this knowledge are not verified available before writing.",
        ))
    if not cross_available:
        blockers.append((
            "CROSS_PREREQUISITE_NOT_VERIFIED",
            "A HARD/DIRECT cross-knowledge prerequisite is not verified available before writing.",
        ))
    if blockers:
        issue_code, issue_details = blockers[0]
        audit.append({
            "occurrence_id": seed.occurrence_id,
            "classification": issue_code,
            "reason": issue_details,
            "prior_verified_facets": available_facets,
            "prior_verified_extension_keys": available_extensions,
        })
        return RuntimeOccurrenceCompilation(
            occurrence_id=seed.occurrence_id,
            before=before,
            compiled_occurrence=None,
            effective_delta=final_delta,
            self_requirements_available=self_available,
            cross_requirements_available=cross_available,
            executable=False,
            issue_code=issue_code,
            issue_details=issue_details,
            audit=audit,
        )

    compiled = replace(seed)
    _apply_delta(
        compiled,
        final_delta,
        has_previous=has_previous,
        source_context=source_context or seed.context_title,
        prior_available_facets=available_facets,
        prior_available_extension_keys=available_extensions,
        future_contexts=future_contexts,
    )
    if compiled.required_self_facets != final_delta.required_self_facets:
        raise RuntimeError(f"Compiled self-facet requirements diverged for {seed.occurrence_id!r}.")
    if compiled.required_self_extension_keys != final_delta.required_self_extension_keys:
        raise RuntimeError(f"Compiled self-extension requirements diverged for {seed.occurrence_id!r}.")
    return RuntimeOccurrenceCompilation(
        occurrence_id=seed.occurrence_id,
        before=before,
        compiled_occurrence=compiled,
        effective_delta=final_delta,
        self_requirements_available=self_available,
        cross_requirements_available=cross_available,
        executable=True,
        audit=audit,
    )


def _normalize_delta_for_availability(
    *,
    delta: SemanticDelta,
    available_facets: list[str],
    available_extension_keys: list[str],
    occurrence_id: str,
) -> tuple[SemanticDelta, list[dict[str, Any]]]:
    """Remove state-stale grants before role/brief compilation.

    The availability simulator is the only authority for what prior textbook
    positions made available.  An LLM proposal cannot re-grant one of those
    facets as a new contribution merely because it planned that occurrence in
    isolation.
    """
    available_facet_set = set(available_facets)
    available_extension_set = set(available_extension_keys)
    stale_facets = [item for item in delta.new_facets if item in available_facet_set]
    stale_extensions = [item for item in delta.new_extension_keys if item in available_extension_set]
    if not stale_facets and not stale_extensions:
        return delta, []
    normalized = replace(
        delta,
        new_facets=[item for item in delta.new_facets if item not in available_facet_set],
        new_extension_keys=[item for item in delta.new_extension_keys if item not in available_extension_set],
    )
    return normalized, [{
        "occurrence_id": occurrence_id,
        "classification": "STATE_STALE_CONTRIBUTION_NORMALIZED",
        "truly_necessary": False,
        "retained": True,
        "reason": "Proposed new facets/extensions were already instructionally available before this occurrence.",
        "removed_new_facets": stale_facets,
        "removed_new_extension_keys": stale_extensions,
        "prior_available_facets": list(available_facets),
        "prior_available_extension_keys": list(available_extension_keys),
    }]


def _compile_cross_prerequisites(
    *,
    occurrence,
    delta: SemanticDelta,
    before: InstructionalAvailabilityState,
    first_position: dict[str, Any],
) -> tuple[list[PrerequisiteUse], list[dict[str, Any]]]:
    """Retain only blocking prerequisites that can precede this occurrence.

    SUPPORTING/BACKGROUND references remain in the semantic record for audit,
    but are explicitly non-blocking.  A future outline occurrence cannot be a
    prerequisite for the current instructional event; retaining it would turn
    a planner overclaim into a fabricated availability gap.
    """
    effective: list[PrerequisiteUse] = []
    audit: list[dict[str, Any]] = []
    for requirement in delta.cross_prerequisite_uses:
        record = before.availability_by_knowledge.get(requirement.knowledge_id)
        prior_position = first_position.get(requirement.knowledge_id)
        trusted_for_runtime = bool(requirement.trusted_for_runtime) and prerequisite_has_runtime_basis(
            knowledge_id=requirement.knowledge_id,
            required_facets=list(requirement.required_facets),
            required_extension_keys=list(requirement.required_extension_keys),
            rationale=requirement.rationale,
            evidence_chunk_ids=list(requirement.evidence_chunk_ids),
            provenance=requirement.provenance,
            supporting_basis=requirement.supporting_basis,
            confidence=requirement.confidence,
        )
        base = {
            "occurrence_id": occurrence.occurrence_id,
            "knowledge_id": occurrence.knowledge_id,
            "required_prerequisite_knowledge_id": requirement.knowledge_id,
            "required_facets": list(requirement.required_facets),
            "required_extension_keys": list(requirement.required_extension_keys),
            # Do not borrow the occurrence-level rationale/evidence for a
            # cross-knowledge prerequisite.  That would make an unsupported
            # prerequisite appear evidence-backed merely because the current
            # occurrence has source material of its own.
            "planner_rationale": requirement.rationale,
            "supporting_evidence_ids": list(requirement.evidence_chunk_ids),
            "prior_available_facets": list(record.available_facets) if record else [],
            "prior_available_extension_keys": list(record.available_extension_keys) if record else [],
            "prerequisite_confidence": requirement.confidence,
            "prerequisite_provenance": requirement.provenance,
            "supporting_basis": requirement.supporting_basis,
            "trusted_for_runtime": trusted_for_runtime,
        }
        if not trusted_for_runtime:
            audit.append({
                **base,
                "classification": "UNTRUSTED_PREREQUISITE_PROPOSAL",
                "truly_necessary": False,
                "retained": False,
                "reason": (
                    "A HARD/DIRECT prerequisite must include a canonical target, required facet or extension, "
                    "explicit rationale, and supporting provenance before it may block runtime."
                ),
            })
            continue
        if requirement.relation != "HARD" or requirement.use_type != "DIRECT":
            audit.append({
                **base,
                "classification": "NON_BLOCKING_CONTEXT",
                "truly_necessary": False,
                "retained": True,
                "reason": "SUPPORTING or BACKGROUND context must not block instructional availability.",
            })
            effective.append(requirement)
            continue
        if prior_position is None or prior_position >= occurrence.position:
            audit.append({
                **base,
                "classification": "OVERCLAIMED_PREREQUISITE",
                "truly_necessary": False,
                "retained": False,
                "reason": "The claimed prerequisite first appears at or after the current fixed outline position.",
            })
            continue
        available = bool(record) and set(requirement.required_facets).issubset(record.available_facets) and set(requirement.required_extension_keys).issubset(record.available_extension_keys)
        audit.append({
            **base,
            "classification": "VALID_PREREQUISITE" if available else "VALID_PREREQUISITE_GAP",
            "truly_necessary": True,
            "retained": True,
            "reason": "The requirement is a HARD/DIRECT dependency with an earlier canonical occurrence.",
        })
        effective.append(requirement)
    return effective, audit


def derive_learning_role(
    delta: SemanticDelta,
    *,
    has_previous: bool,
    source_context: str = "",
    prior_available_facets: list[str] | None = None,
    prior_available_extension_keys: list[str] | None = None,
    future_contexts: list[str] | None = None,
) -> str:
    """Derive a role from semantic facts and verified state, never from an LLM role."""
    available = set(prior_available_facets or [])
    available_extensions = set(prior_available_extension_keys or [])
    has_prior_support = bool(available or available_extensions)
    intent = _context_intent(source_context)
    future_supports_application = any(_context_intent(item) in {"APPLY", "EXTEND"} for item in future_contexts or [])
    has_increment = bool(delta.new_facets or delta.new_extension_keys)
    orientation_facets = {MasteryFacet.ORIENTED}
    only_orientation = (
        delta.orientation_only
        and (not delta.new_facets or set(delta.new_facets).issubset(orientation_facets))
    )
    # INTRO is not simply the first occurrence: it may only establish a
    # direction for learning.  A first occurrence that teaches EXPLAIN,
    # PERFORM, or ANALYZE is a TEACH occurrence even when its author labelled
    # the surrounding section "INTRO".
    if not has_prior_support and only_orientation and not delta.repeats_complete_teaching:
        return LearningRole.INTRO
    if not has_previous and delta.new_facets == [MasteryFacet.ORIENTED] and not delta.repeats_prior_explanation:
        return LearningRole.INTRO
    # A complete-repeat signal is a *deduplication input*, not a directive to
    # reteach.  First resolve what the current occurrence actually adds or
    # consumes.  This ordering is intentionally generic: it applies to every
    # canonical knowledge point and cannot be replaced by a domain-specific
    # exception.
    if has_increment:
        # An extension is a new teaching increment layered on any verified
        # prior support (facet or extension).  Do not silently downgrade a
        # valid EXTEND proposal to TEACH merely because the prior support is
        # represented by an extension key rather than a facet.
        if has_prior_support and delta.new_extension_keys:
            return LearningRole.EXTEND
        # A non-orientation facet is an actual teaching increment.  It must
        # not be collapsed into INTRO just because the surrounding section is
        # introductory or the occurrence is the first one in its trajectory.
        if any(facet != MasteryFacet.ORIENTED for facet in delta.new_facets):
            return LearningRole.TEACH
        return LearningRole.TEACH
    if available and (delta.restores_prior_context or delta.recall_needed or intent == "RECALL") and future_supports_application:
        return LearningRole.RECALL
    # APPLY consumes instructionally available prior knowledge.  A merely
    # earlier structural occurrence is not enough: its teaching may have been
    # blocked by a prerequisite gap and therefore cannot be assumed available.
    if has_previous and available and (delta.uses_prior_knowledge or intent == "APPLY"):
        return LearningRole.APPLY
    # With no increment, no verified use, and no recall request, retain the
    # conservative TEACH/issue path.  In particular, do not force ZERO_RENDER
    # merely because a duplicate flag was observed.
    return LearningRole.TEACH


def _identity_merge_candidates(knowledge_map: KnowledgeMap, lookup: dict[str, EvidenceChunk]) -> list[dict[str, object]]:
    """Recall only safe alias candidates; the LLM never proposes arbitrary merges."""
    points = {item.knowledge_id: item for item in knowledge_map.knowledge_points}
    candidates: list[dict[str, object]] = []
    ordered = list(knowledge_map.knowledge_points)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if _identity_key(left.title) != _identity_key(right.title):
                continue
            evidence_ids = list(dict.fromkeys([*left.source_chunk_ids, *right.source_chunk_ids]))
            candidates.append({
                "left_id": left.knowledge_id,
                "right_id": right.knowledge_id,
                "right_ids": [right.knowledge_id],
                "left_title": left.title,
                "right_titles": [right.title],
                "existing_mapping_type": "UNCERTAIN",
                "evidence": _evidence_for(evidence_ids, lookup),
            })
    return candidates


def _identity_key(title: str) -> str:
    # Removing the possessive particle is deliberately narrow: it recalls
    # aliases such as “X 原理” / “X 的原理” without treating merely related
    # concepts as merge candidates.
    return "".join(str(title or "").lower().replace("的", "").split())


def _apply_accepted_identity_merges(knowledge_map: KnowledgeMap, judgements: list[dict[str, object]]) -> bool:
    points_by_id = {item.knowledge_id: item for item in knowledge_map.knowledge_points}
    parent = {item.knowledge_id: item.knowledge_id for item in knowledge_map.knowledge_points}
    confidence_by_root: dict[str, float] = {}

    def root(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    accepted = False
    for judgement in judgements:
        left = judgement.get("left_id")
        right = judgement.get("right_id")
        confidence = float(judgement.get("confidence") or 0.0)
        if (
            judgement.get("relation") != "SAME"
            or confidence < MIN_TRUSTED_CONFIDENCE
            or not isinstance(left, str)
            or not isinstance(right, str)
            or left not in parent
            or right not in parent
        ):
            continue
        left_root, right_root = root(left), root(right)
        if left_root == right_root:
            continue
        # Keep the earlier canonical ID stable; mappings and reports can
        # therefore be diffed without assigning a new synthetic owner.
        if list(parent).index(right_root) < list(parent).index(left_root):
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        confidence_by_root[left_root] = max(confidence_by_root.get(left_root, 0.0), confidence)
        accepted = True
    if not accepted:
        return False

    groups: dict[str, list[str]] = {}
    for knowledge_id in parent:
        groups.setdefault(root(knowledge_id), []).append(knowledge_id)
    merged_points = []
    for knowledge_id in parent:
        if root(knowledge_id) != knowledge_id:
            continue
        members = [points_by_id[item] for item in groups[knowledge_id]]
        base = members[0]
        aliases = list(dict.fromkeys(alias for item in members for alias in [item.title, *item.aliases]))
        evidence_ids = list(dict.fromkeys(chunk_id for item in members for chunk_id in item.source_chunk_ids))
        merged_points.append(replace(base, aliases=aliases, source_chunk_ids=evidence_ids, extraction_confidence=max(item.extraction_confidence for item in members)))
    id_map = {knowledge_id: root(knowledge_id) for knowledge_id in parent}
    knowledge_map.knowledge_points = merged_points
    for mapping in knowledge_map.mappings:
        revised_ids = list(dict.fromkeys(id_map[item] for item in mapping.canonical_knowledge_ids))
        if revised_ids != mapping.canonical_knowledge_ids:
            merge_confidence = max(confidence_by_root.get(item, 0.0) for item in revised_ids)
            mapping.canonical_knowledge_ids = revised_ids
            mapping.mapping_type = "ALIAS" if len(revised_ids) == 1 else mapping.mapping_type
            mapping.confidence = max(mapping.confidence, merge_confidence)
            mapping.rationale = f"{mapping.rationale} Accepted high-confidence SAME identity merge."
    remapped_prerequisites = []
    for edge in knowledge_map.prerequisites:
        source_id, target_id = id_map.get(edge.source_knowledge_id, edge.source_knowledge_id), id_map.get(edge.target_knowledge_id, edge.target_knowledge_id)
        if source_id != target_id:
            remapped_prerequisites.append(replace(edge, source_knowledge_id=source_id, target_knowledge_id=target_id))
    knowledge_map.prerequisites = remapped_prerequisites
    return True


def _rebuild_trajectories(knowledge_map: KnowledgeMap):
    from materials2textbook.knowledge_map.models import LearningTrajectory

    return [
        LearningTrajectory(
            knowledge_id=point.knowledge_id,
            occurrence_ids=[item.occurrence_id for item in knowledge_map.planned_occurrences if item.knowledge_id == point.knowledge_id],
        )
        for point in knowledge_map.knowledge_points
    ]


def _context_intent(context: str) -> str:
    """Read only explicit instructional intent; never infer it from an LLM role."""
    value = str(context or "").lower()
    if any(token in value for token in ("intro:", "initial intuition", "初识", "入门", "概览")):
        return "INTRO"
    if any(token in value for token in ("recall:", "restore minimum", "回顾", "复习", "回忆")):
        return "RECALL"
    if any(token in value for token in ("repeat the complete", "重复完整", "再次完整", "完整讲授")):
        return "DUPLICATE_TEACH" if "repeat" in value or "重复" in value or "再次" in value else "TEACH"
    if any(token in value for token in ("apply:", "directly use", "直接使用", "任务应用", "实训应用")):
        return "APPLY"
    if any(token in value for token in ("extend:", "new abnormal", "新增", "拓展", "异常条件")):
        return "EXTEND"
    return ""


def _role_required_facets(role: str, delta: SemanticDelta, available: list[str]) -> list[str]:
    if role == LearningRole.INTRO:
        return []
    # PlannedOccurrence requirements are compiled only from the final,
    # normalized SemanticDelta. Never infer an additional requirement from a
    # tentative availability record: doing so makes the occurrence stale as
    # soon as a delta is contracted or normalized.
    return list(delta.required_self_facets)


def _role_grants(role: str, delta: SemanticDelta, available: list[str], intent: str) -> list[str]:
    if role == LearningRole.INTRO:
        return [MasteryFacet.ORIENTED]
    if role in {LearningRole.RECALL, LearningRole.APPLY}:
        return []
    if role == LearningRole.TEACH and intent == "DUPLICATE_TEACH":
        return []
    if role == LearningRole.EXTEND:
        return list(delta.new_facets)
    return list(delta.new_facets)


def _apply_delta(
    occurrence,
    delta: SemanticDelta,
    *,
    has_previous: bool,
    source_context: str = "",
    prior_available_facets: list[str] | None = None,
    prior_available_extension_keys: list[str] | None = None,
    future_contexts: list[str] | None = None,
) -> None:
    available = list(dict.fromkeys(prior_available_facets or []))
    intent = _context_intent(source_context)
    role = derive_learning_role(
        delta,
        has_previous=has_previous,
        source_context=source_context,
        prior_available_facets=available,
        prior_available_extension_keys=prior_available_extension_keys,
        future_contexts=future_contexts,
    )
    occurrence.role = role
    # INTRO is a first-contact policy, never a self-prerequisite assertion.
    occurrence.required_self_facets = _role_required_facets(role, delta, available)
    occurrence.required_self_extension_keys = [] if role == LearningRole.INTRO else delta.required_self_extension_keys
    occurrence.required_prerequisites = delta.cross_prerequisite_uses
    # Both teaching availability and contribution are derived from this *same* delta.
    occurrence.intended_grants = _role_grants(role, delta, available, intent)
    occurrence.intended_extension_keys = [] if role in {LearningRole.INTRO, LearningRole.RECALL} else list(delta.new_extension_keys)
    occurrence.repeats_prior_explanation = delta.repeats_prior_explanation or intent == "DUPLICATE_TEACH"
    occurrence.uses_prior_knowledge = delta.uses_prior_knowledge
    occurrence.recall_needed = delta.recall_needed
    occurrence.intended_contribution = delta.contribution_summary
    occurrence.new_context = delta.new_context
    occurrence.repeated_aspects = list(delta.repeated_aspects)
    occurrence.planning_confidence = delta.confidence
    occurrence.planning_rationale = delta.rationale
    occurrence.planning_evidence_chunk_ids = list(delta.evidence_chunk_ids)
    occurrence.contribution_confidence = delta.confidence
    occurrence.contribution_rationale = delta.rationale
    occurrence.contribution_evidence_chunk_ids = list(delta.evidence_chunk_ids)
    occurrence.trusted_for_state = delta.confidence >= MIN_TRUSTED_CONFIDENCE


def _parse_delta(
    value: dict[str, Any] | None,
    occurrence,
    points: dict[str, Any],
    rejected: list[dict[str, Any]],
    normalizations: list[dict[str, Any]],
    *,
    has_previous: bool,
) -> SemanticDelta | None:
    if not value or value.get("occurrence_id") != occurrence.occurrence_id:
        rejected.append({"stage": "semantic_delta", "reason": "missing_occurrence_delta", "occurrence_id": occurrence.occurrence_id})
        return None
    confidence = _confidence(value.get("confidence"))
    if confidence is None or not _audit_ready(value):
        rejected.append({"stage": "semantic_delta", "reason": "invalid_delta", "occurrence_id": occurrence.occurrence_id, "proposal": value})
        return None
    raw_required_self_facets = _strings(value.get("required_self_facets"))
    raw_new_facets = _strings(value.get("new_facets"))
    if not has_previous and (raw_required_self_facets or _strings(value.get("required_self_extension_keys"))):
        rejected.append({
            "stage": "schema", "reason": "invalid_self_requirement_timing", "occurrence_id": occurrence.occurrence_id,
            "required_self_facets": raw_required_self_facets, "new_facets": raw_new_facets,
        })
        return None
    overlapping_self_facets = set(raw_required_self_facets) & set(raw_new_facets)
    if overlapping_self_facets:
        normalizations.append({
            "stage": "schema", "reason": "overlapping_self_requirement_normalized", "occurrence_id": occurrence.occurrence_id,
            "required_self_facets": raw_required_self_facets, "new_facets": raw_new_facets,
        })
        raw_required_self_facets = [item for item in raw_required_self_facets if item not in overlapping_self_facets]
    return SemanticDelta(
        occurrence_id=occurrence.occurrence_id,
        repeats_prior_explanation=bool(value.get("repeats_prior_explanation")),
        uses_prior_knowledge=bool(value.get("uses_prior_knowledge")),
        recall_needed=bool(value.get("recall_needed")),
        required_self_facets=_valid_facets(raw_required_self_facets, rejected, occurrence.occurrence_id, "required_self_facets"),
        required_self_extension_keys=_strings(value.get("required_self_extension_keys")),
        cross_prerequisite_uses=_valid_cross_uses(value.get("cross_prerequisite_uses"), points, occurrence.knowledge_id, rejected, occurrence.occurrence_id),
        new_facets=_valid_facets(value.get("new_facets"), rejected, occurrence.occurrence_id, "new_facets"),
        new_extension_keys=_strings(value.get("new_extension_keys")),
        new_context=str(value.get("new_context", "")),
        repeated_aspects=_strings(value.get("repeated_aspects")),
        contribution_summary=str(value.get("contribution_summary", "")),
        confidence=confidence,
        rationale=value["rationale"],
        evidence_chunk_ids=_strings(value.get("evidence_ids")),
        orientation_only=bool(value.get("orientation_only")),
        restores_prior_context=bool(value.get("restores_prior_context")),
        repeats_complete_teaching=bool(value.get("repeats_complete_teaching")),
    )


def _valid_cross_uses(value: Any, points: dict[str, Any], current_id: str, rejected: list[dict[str, Any]], occurrence_id: str) -> list[PrerequisiteUse]:
    if value is None:
        return []
    if not isinstance(value, list):
        rejected.append({"stage": "schema", "reason": "invalid_cross_prerequisite_uses", "occurrence_id": occurrence_id})
        return []
    result: list[PrerequisiteUse] = []
    for item in value:
        candidate_id = item.get("knowledge_id") if isinstance(item, dict) else None
        if candidate_id not in points or candidate_id == current_id:
            rejected.append({"stage": "schema", "reason": "noncanonical_prerequisite_rejected", "occurrence_id": occurrence_id, "candidate_id": candidate_id})
            continue
        required_facets = _valid_facets(item.get("required_facets"), rejected, occurrence_id, "cross_required_facets")
        required_extension_keys = _strings(item.get("required_extension_keys"))
        rationale = str(item.get("rationale") or "").strip()
        evidence_ids = _strings(item.get("evidence_ids"))
        provenance = str(item.get("provenance") or "").strip()
        supporting_basis = str(item.get("supporting_basis") or "").strip()
        confidence = _confidence(item.get("confidence")) or 0.0
        trusted_for_runtime = prerequisite_has_runtime_basis(
            knowledge_id=str(candidate_id),
            required_facets=required_facets,
            required_extension_keys=required_extension_keys,
            rationale=rationale,
            evidence_chunk_ids=evidence_ids,
            provenance=provenance,
            supporting_basis=supporting_basis,
            confidence=confidence,
        )
        if not trusted_for_runtime:
            rejected.append({
                "stage": "prerequisite_trust",
                "reason": "untrusted_prerequisite_proposal",
                "occurrence_id": occurrence_id,
                "candidate_id": candidate_id,
                "required_facets": required_facets,
                "required_extension_keys": required_extension_keys,
                "rationale": rationale,
                "evidence_ids": evidence_ids,
                "provenance": provenance,
                "supporting_basis": supporting_basis,
                "confidence": confidence,
            })
        result.append(PrerequisiteUse(
            knowledge_id=candidate_id,
            required_facets=required_facets,
            required_extension_keys=required_extension_keys,
            relation=item.get("relation") if item.get("relation") in {"HARD", "SUPPORTING"} else "HARD",
            use_type=item.get("use_type") if item.get("use_type") in {"DIRECT", "BACKGROUND"} else "DIRECT",
            edge_id=str(item.get("edge_id") or ""),
            rationale=rationale,
            evidence_chunk_ids=evidence_ids,
            confidence=confidence,
            provenance=provenance,
            supporting_basis=supporting_basis,
            trusted_for_runtime=trusted_for_runtime,
        ))
    return result


def _trajectory_payload(knowledge_id: str, title: str, occurrences: list, sources: dict[str, Any], lookup: dict[str, EvidenceChunk], canonical_whitelist: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "knowledge_id": knowledge_id,
        "canonical_title": title,
        "canonical_id_whitelist": canonical_whitelist,
        "occurrences": [
            {
                "occurrence_id": item.occurrence_id,
                "position": {"chapter_ordinal": item.position.chapter_ordinal, "task_ordinal": item.position.task_ordinal, "occurrence_ordinal": item.position.occurrence_ordinal},
                "context_title": item.context_title,
                "source_title": sources[item.source_knowledge_point_id].title,
                "evidence": _evidence_for(item.source_chunk_ids, lookup),
            }
            for item in occurrences
        ],
    }


def _evidence_for(ids: list[str], lookup: dict[str, EvidenceChunk]) -> list[dict[str, str]]:
    return [{"id": item, "title": lookup[item].title, "excerpt": (lookup[item].summary or lookup[item].content)[:700]} for item in ids if item in lookup]


def _list_response(response: dict, key: str, rejected: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    value = response.get(key)
    if isinstance(value, list):
        return value
    rejected.append({"stage": stage, "reason": f"missing_or_invalid_{key}"})
    return []


def _safe_call(call, rejected: list[dict[str, Any]], stage: str) -> dict:
    try:
        return call()
    except Exception as exc:
        rejected.append({"stage": stage, "reason": "planner_call_failed", "error": str(exc)[:500]})
        return {}


def _normalise_identity(value: dict[str, Any], rejected: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(value)
    confidence = _confidence(result.get("confidence"))
    if result.get("relation") not in _IDENTITIES or confidence is None or not _audit_ready(result):
        rejected.append({"stage": "identity", "reason": "invalid_identity_judgement", "proposal": value})
        result.update({"relation": "UNCERTAIN", "confidence": 0.0})
    elif result["relation"] == "SAME" and confidence < MIN_TRUSTED_CONFIDENCE:
        result.update({"relation": "UNCERTAIN", "downgraded_reason": "low_confidence_same"})
    return result


def _valid_facets(value: Any, rejected: list[dict[str, Any]], occurrence_id: str, field_name: str) -> list[str]:
    values = _strings(value)
    invalid = sorted(set(values) - _FACETS)
    if invalid:
        rejected.append({"stage": "schema", "reason": "unknown_facet", "occurrence_id": occurrence_id, "field": field_name, "values": invalid})
    return [item for item in values if item in _FACETS]


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _confidence(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and 0 <= float(value) <= 1 else None


def _audit_ready(value: dict[str, Any]) -> bool:
    return isinstance(value.get("rationale"), str) and bool(value["rationale"].strip()) and bool(_strings(value.get("evidence_ids")))
