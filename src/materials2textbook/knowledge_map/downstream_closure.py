"""Read-only teaching-support closure analysis.

This module answers a narrow question: when a student-facing requirement is
encountered, which instructional facets have already been verified at that
position?  It deliberately does not change plans, roles, content, or
requirements.  The analyzer accepts both production dataclasses and the JSON
shape emitted by the semantic execution audit so the same rules can be used
for live workflow checks and post-run diagnosis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import re
from typing import Any, Iterable

from materials2textbook.knowledge_map.models import MasteryFacet, PlannedOccurrence


_FACETS = {
    MasteryFacet.ORIENTED,
    MasteryFacet.EXPLAIN,
    MasteryFacet.PERFORM,
    MasteryFacet.ANALYZE,
}


CLOSED = "CLOSED"
UNDER_SUPPORTED = "UNDER_SUPPORTED"
UNSUPPORTED = "UNSUPPORTED"
BLOCKED_BY_PRIOR_FAILURE = "BLOCKED_BY_PRIOR_FAILURE"
TARGET_NOT_DELIVERED = "TARGET_NOT_DELIVERED"
UNMAPPED_REQUIREMENT = "UNMAPPED_REQUIREMENT"
AMBIGUOUS_REQUIREMENT = "AMBIGUOUS_REQUIREMENT"

# Closure is deliberately classified in three buckets for publication.  This
# keeps the final gate fail-closed without treating every non-CLOSED result as
# the same kind of defect.
HARD_BLOCKER_STATUSES = frozenset({
    UNSUPPORTED,
    BLOCKED_BY_PRIOR_FAILURE,
    TARGET_NOT_DELIVERED,
})
REVIEW_REQUIRED_STATUSES = frozenset({
    UNDER_SUPPORTED,
    UNMAPPED_REQUIREMENT,
    AMBIGUOUS_REQUIREMENT,
})
NON_BLOCKING_STATUSES = frozenset({CLOSED})

MODULE_NAVIGATION = "navigation"
MODULE_ASSESSMENT = "assessment"
MODULE_EXERCISE = "exercise"
MODULE_PROJECT_GOAL = "project_learning_goal"

EVAL_BEFORE_LOCATION = "BEFORE_REQUIREMENT"
EVAL_SECTION_END = "SECTION_END"
EVAL_TASK_END = "TASK_END"
EVAL_PROJECT_END = "PROJECT_END"


@dataclass(frozen=True)
class RequirementSemantic:
    """Semantic facts attached to a student-facing requirement.

    This is an internal representation only.  It never contains a closure
    decision; that decision remains deterministic and location-aware.
    """

    requirement_type: str = "CAPABILITY_REQUIREMENT"
    target_knowledge_ids: tuple[str, ...] = ()
    target_knowledge_titles: tuple[str, ...] = ()
    extracted_action: str = ""
    candidate_required_facets: tuple[str, ...] = ()
    facet_relation: str = "ALL"
    mapping_source: str = "UNMAPPED"
    mapping_confidence: float = 0.0
    extraction_provenance: str = ""
    evaluation_point: str = ""


@dataclass(frozen=True)
class StudentRequirement:
    requirement_id: str
    source_module: str
    visible_text: str
    requirement_type: str = "CAPABILITY_REQUIREMENT"
    project_id: str = ""
    task_id: str = ""
    section_id: str = ""
    requirement_position: dict[str, int] = field(default_factory=dict)
    evaluation_point: str = EVAL_TASK_END
    required_facets: tuple[str, ...] = ()
    facet_relation: str = "ALL"
    extracted_action: str = ""
    mapping_source: str = "UNMAPPED"
    mapping_confidence: float = 0.0
    extraction_provenance: str = ""
    target_occurrence_ids: tuple[str, ...] = ()
    target_knowledge_ids: tuple[str, ...] = ()
    mapping_reason: str = ""


@dataclass(frozen=True)
class TeachingSupportClosure:
    requirement: StudentRequirement
    supporting_occurrence_ids: tuple[str, ...] = ()
    verified_facets: tuple[str, ...] = ()
    verified_extension_keys: tuple[str, ...] = ()
    support_provenance: dict[str, str] = field(default_factory=dict)
    relevant_blocked_occurrence_ids: tuple[str, ...] = ()
    status: str = UNMAPPED_REQUIREMENT
    reason: str = ""


@dataclass
class DownstreamClosureReport:
    results: list[TeachingSupportClosure] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    module_status_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    blocked_impact: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [
                {
                    "requirement": asdict(item.requirement),
                    "supporting_occurrence_ids": list(item.supporting_occurrence_ids),
                    "verified_facets": list(item.verified_facets),
                    "verified_extension_keys": list(item.verified_extension_keys),
                    "support_provenance": dict(item.support_provenance),
                    "relevant_blocked_occurrence_ids": list(item.relevant_blocked_occurrence_ids),
                    "status": item.status,
                    "reason": item.reason,
                }
                for item in self.results
            ],
            "status_counts": dict(self.status_counts),
            "module_status_counts": {
                module: dict(counts) for module, counts in self.module_status_counts.items()
            },
            "blocked_impact": list(self.blocked_impact),
            "publication_impact": summarize_downstream_closure(self),
        }


def summarize_downstream_closure(report: DownstreamClosureReport | None) -> dict[str, Any]:
    """Classify closure results for the final publication gate.

    ``UNMAPPED_REQUIREMENT`` and ``UNDER_SUPPORTED`` remain explicit review
    items; they are never silently promoted to ``CLOSED``.  Unknown statuses
    are also fail-closed as review items so a future analyzer status cannot
    accidentally bypass publication policy.
    """
    if report is None:
        return {
            "provided": False,
            "hard_blocker_count": 0,
            "review_required_count": 0,
            "non_blocking_count": 0,
            "unknown_status_count": 0,
            "hard_blocker_statuses": [],
            "review_required_statuses": [],
            "non_blocking_statuses": [],
            "publishable": False,
        }

    counts: dict[str, int] = {}
    for item in report.results:
        status = str(item.status or "")
        counts[status] = counts.get(status, 0) + 1
    hard = {status: count for status, count in counts.items() if status in HARD_BLOCKER_STATUSES}
    review = {
        status: count for status, count in counts.items()
        if status in REVIEW_REQUIRED_STATUSES or status not in NON_BLOCKING_STATUSES | HARD_BLOCKER_STATUSES
    }
    non_blocking = {status: count for status, count in counts.items() if status in NON_BLOCKING_STATUSES}
    unknown_count = sum(review.values()) - sum(counts.get(status, 0) for status in REVIEW_REQUIRED_STATUSES)
    return {
        "provided": True,
        "hard_blocker_count": sum(hard.values()),
        "review_required_count": sum(review.values()),
        "non_blocking_count": sum(non_blocking.values()),
        "unknown_status_count": max(0, unknown_count),
        "hard_blocker_statuses": sorted(hard),
        "review_required_statuses": sorted(review),
        "non_blocking_statuses": sorted(non_blocking),
        "status_counts": counts,
        "publishable": not hard and not review,
    }


def analyze_downstream_closure(
    *,
    digital_book: Any,
    planned_occurrences: list[PlannedOccurrence],
    semantic_execution: Any,
    source_knowledge_points: Iterable[Any] | None = None,
) -> DownstreamClosureReport:
    """Extract requirements and evaluate them against a location-aware state.

    The function is intentionally conservative.  A task title or knowledge
    point name alone is never converted into a capability requirement; only
    visible navigation/assessment/exercise/goal text is mapped.
    """
    occurrences = _occurrence_records(planned_occurrences, source_knowledge_points or ())
    requirements = _extract_requirements(digital_book, occurrences)
    timeline = _timeline(semantic_execution, occurrences)
    blocked = _blocked_records(semantic_execution)
    results: list[TeachingSupportClosure] = []
    for requirement in requirements:
        state = _state_at(requirement, timeline)
        support = _support_for_requirement(requirement, state, occurrences)
        relevant_blocked = _relevant_blocked(requirement, blocked, occurrences)
        status, reason = _classify(requirement, support, relevant_blocked)
        results.append(
            TeachingSupportClosure(
                requirement=requirement,
                supporting_occurrence_ids=tuple(support["occurrence_ids"]),
                verified_facets=tuple(support["facets"]),
                verified_extension_keys=tuple(support["extensions"]),
                support_provenance=dict(support["provenance"]),
                relevant_blocked_occurrence_ids=tuple(relevant_blocked),
                status=status,
                reason=reason,
            )
        )
    report = DownstreamClosureReport(results=results)
    for item in results:
        report.status_counts[item.status] = report.status_counts.get(item.status, 0) + 1
        module_counts = report.module_status_counts.setdefault(item.requirement.source_module, {})
        module_counts[item.status] = module_counts.get(item.status, 0) + 1
    report.blocked_impact = _blocked_impact(results, blocked)
    return report


def deterministic_requirement_semantic(text: str) -> dict[str, Any]:
    """Return conservative action/facet facts without making a closure decision."""
    facets, _ = _map_requirement_facets(text)
    return {
        "candidate_required_facets": list(facets),
        "facet_relation": "ALL",
        "extracted_action": _infer_requirement_action(text),
        "mapping_source": "DETERMINISTIC_RULE" if facets else "UNMAPPED",
        "mapping_confidence": 0.65 if facets else 0.0,
        "extraction_provenance": "deterministic_requirement_rule",
    }


def normalize_requirement_semantic_proposal(
    proposal: Any,
    *,
    allowed_knowledge_ids: set[str],
    model_version: str = "",
) -> dict[str, Any]:
    """Validate an optional model proposal without deciding closure.

    Invalid or unknown target IDs remain explicitly unmapped.  The normalizer
    never substitutes a nearby knowledge point and never accepts a model's
    claimed ``CLOSED`` result.
    """
    if not isinstance(proposal, dict):
        return {
            "mapping_source": "UNMAPPED",
            "mapping_confidence": 0.0,
            "extraction_provenance": "invalid_model_proposal",
        }
    raw_targets = [str(item) for item in proposal.get("target_knowledge_ids", []) if str(item).strip()]
    valid_targets = [item for item in raw_targets if item in allowed_knowledge_ids]
    invalid_targets = [item for item in raw_targets if item not in allowed_knowledge_ids]
    facets = [str(item) for item in proposal.get("candidate_required_facets", []) if str(item) in _FACETS]
    relation = str(proposal.get("facet_relation") or "ALL").upper()
    if relation not in {"ALL", "ANY"}:
        relation = "ALL"
    confidence = proposal.get("mapping_confidence", proposal.get("confidence", 0.0))
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    normalized = {
        "requirement_type": str(proposal.get("requirement_type") or "CAPABILITY_REQUIREMENT"),
        "target_knowledge_ids": valid_targets,
        "target_knowledge_titles": [str(item) for item in proposal.get("target_knowledge_titles", []) if str(item).strip()],
        "extracted_action": str(proposal.get("extracted_action") or ""),
        "candidate_required_facets": list(dict.fromkeys(facets)),
        "facet_relation": relation,
        "mapping_source": "MODEL_EXTRACTED" if valid_targets and facets and not invalid_targets else "UNMAPPED",
        "mapping_confidence": confidence if not invalid_targets else 0.0,
        "extraction_provenance": f"qwen:{model_version}" if model_version else "model_requirement_semantic_extraction",
    }
    if invalid_targets:
        normalized["invalid_target_knowledge_ids"] = invalid_targets
    return normalized


def render_downstream_closure_markdown(report: DownstreamClosureReport) -> str:
    lines = ["# Downstream Teaching-Support Closure", "", "## Summary", ""]
    lines.append("| Module | Status | Count |")
    lines.append("|---|---|---:|")
    for module, counts in sorted(report.module_status_counts.items()):
        for status, count in sorted(counts.items()):
            lines.append(f"| {module} | {status} | {count} |")
    lines.extend(["", "## Requirement matrix", ""])
    for item in report.results:
        req = item.requirement
        lines.extend([
            f"### {req.requirement_id}",
            f"- module/type: `{req.source_module}` / `{req.requirement_type}`; evaluation: `{req.evaluation_point}`",
            f"- location: `{req.project_id}/{req.task_id}/{req.section_id}`",
            f"- requirement: {req.visible_text}",
            f"- required facets: `{', '.join(req.required_facets) or 'UNMAPPED'}`",
            f"- action / relation: `{req.extracted_action or 'UNMAPPED'}` / `{req.facet_relation}`",
            f"- mapping: `{req.mapping_source}` (confidence {req.mapping_confidence:.2f})",
            f"- extraction provenance: `{req.extraction_provenance or 'none'}`",
            f"- supporting occurrences: `{', '.join(item.supporting_occurrence_ids) or 'none'}`",
            f"- verified facets: `{', '.join(item.verified_facets) or 'none'}`",
            f"- relevant blocked: `{', '.join(item.relevant_blocked_occurrence_ids) or 'none'}`",
            f"- status: **{item.status}** — {item.reason}",
            "",
        ])
    return "\n".join(lines)


def _extract_requirements(digital_book: Any, occurrences: list[dict[str, Any]]) -> list[StudentRequirement]:
    requirements: list[StudentRequirement] = []
    projects = _items(digital_book, "projects")
    for project_index, project in enumerate(projects):
        project_id = str(_value(project, "project_id", f"project_{project_index + 1}"))
        for goal_index, text in enumerate(_items(project, "learning_goals")):
            visible = str(text).strip()
            req = _build_requirement(
                requirement_id=f"{project_id}:goal:{goal_index + 1}",
                source_module=MODULE_PROJECT_GOAL,
                visible_text=visible,
                project_id=project_id,
                evaluation_point=EVAL_PROJECT_END,
                position={"project_ordinal": project_index + 1},
                occurrences=occurrences,
            )
            requirements.append(req)
        for task_index, task in enumerate(_items(project, "tasks")):
            task_id = str(_value(task, "task_id", f"{project_id}:task:{task_index + 1}"))
            section_id = str(_value(task, "section_id", _value(task, "metadata", {}).get("section_id", "")))
            position = {"project_ordinal": project_index + 1, "task_ordinal": task_index + 1}
            for block in _items(task, "blocks"):
                block_type = str(_value(block, "type", ""))
                module = {
                    "learning_nav": MODULE_NAVIGATION,
                    "assessment": MODULE_ASSESSMENT,
                    "exercises": MODULE_EXERCISE,
                }.get(block_type)
                if module is None:
                    continue
                for item_index, text in enumerate(_items(block, "items")):
                    visible = str(text).strip()
                    if not visible:
                        continue
                    forward = module == MODULE_NAVIGATION and _is_forward_navigation(visible)
                    prior = module == MODULE_NAVIGATION and _is_prior_invocation(visible)
                    evaluation = (
                        EVAL_SECTION_END if forward else EVAL_BEFORE_LOCATION if prior else EVAL_TASK_END
                    )
                    metadata = _requirement_metadata(block, item_index)
                    req = _build_requirement(
                        requirement_id=f"{task_id}:{block_type}:{item_index + 1}",
                        source_module=module,
                        visible_text=visible,
                        project_id=project_id,
                        task_id=task_id,
                        section_id=section_id,
                        evaluation_point=evaluation,
                        position=position,
                        occurrences=occurrences,
                        semantic_metadata=metadata,
                    )
                    requirements.append(req)
    return requirements


def _build_requirement(*, requirement_id: str, source_module: str, visible_text: str,
                       project_id: str = "", task_id: str = "", section_id: str = "",
                       evaluation_point: str, position: dict[str, int],
                       occurrences: list[dict[str, Any]], semantic_metadata: dict[str, Any] | None = None) -> StudentRequirement:
    semantic_metadata = dict(semantic_metadata or {})
    candidate_facets, mapping_reason = _map_requirement_facets(visible_text)
    facets = list(semantic_metadata.get("candidate_required_facets") or candidate_facets)
    facet_relation = str(semantic_metadata.get("facet_relation") or "ALL").upper()
    if facet_relation not in {"ALL", "ANY"}:
        facet_relation = "ALL"
    action = str(semantic_metadata.get("extracted_action") or _infer_requirement_action(visible_text))
    requirement_type = str(semantic_metadata.get("requirement_type") or "CAPABILITY_REQUIREMENT")
    mapping_source = str(semantic_metadata.get("mapping_source") or ("DETERMINISTIC_RULE" if facets else "UNMAPPED"))
    mapping_confidence = float(semantic_metadata.get("mapping_confidence") or (0.9 if semantic_metadata else 0.65 if facets else 0.0))
    extraction_provenance = str(semantic_metadata.get("extraction_provenance") or ("surface_text_rule" if facets else "no_semantic_mapping"))
    target_titles = [str(item) for item in semantic_metadata.get("target_knowledge_titles", []) if str(item).strip()]
    target_ids = [str(item) for item in semantic_metadata.get("target_knowledge_ids", []) if str(item).strip()]
    targets = _find_targets(visible_text, occurrences, project_id=project_id, task_id=task_id, section_id=section_id,
                            source_module=source_module, target_titles=target_titles, target_ids=target_ids)
    resolved_ids = tuple(dict.fromkeys(item["knowledge_id"] for item in targets))
    return StudentRequirement(
        requirement_id=requirement_id,
        source_module=source_module,
        visible_text=visible_text,
        requirement_type=requirement_type,
        project_id=project_id,
        task_id=task_id,
        section_id=section_id,
        requirement_position=position,
        evaluation_point=evaluation_point,
        required_facets=tuple(facets),
        facet_relation=facet_relation,
        extracted_action=action,
        mapping_source=mapping_source,
        mapping_confidence=mapping_confidence,
        extraction_provenance=extraction_provenance,
        target_occurrence_ids=tuple(item["occurrence_id"] for item in targets),
        target_knowledge_ids=resolved_ids,
        mapping_reason=mapping_reason,
    )


def _map_requirement_facets(text: str) -> tuple[list[str], str]:
    normalized = text.lower()
    matches: list[str] = []
    if re.search(r"分析|比较|判断|影响|原因|为什么|analy[sz]e|compare|judge|impact|why", normalized):
        matches.append(MasteryFacet.ANALYZE)
    if re.search(r"执行|完成|进行|操作|安装|设置|调节|实施|perform|execute|complete|operate|configure|set up", normalized):
        matches.append(MasteryFacet.PERFORM)
    if re.search(r"解释|说明|原理|阐述|explain|describe|principle|reason", normalized):
        matches.append(MasteryFacet.EXPLAIN)
    if re.search(r"识别|说出|指出|列出|概括|了解|认识|掌握|identify|name|list|recognize|recall|master", normalized):
        matches.append(MasteryFacet.ORIENTED)
    matches = list(dict.fromkeys(matches))
    if not matches:
        return [], "No high-confidence capability verb was found."
    return matches, "Mapped from explicit student-facing capability language."


def _infer_requirement_action(text: str) -> str:
    normalized = text.lower()
    if re.search(r"分析|比较|判断|影响|原因|为什么|analy[sz]e|compare|judge|impact|why", normalized):
        return MasteryFacet.ANALYZE
    if re.search(r"执行|完成|进行|操作|安装|设置|调节|实施|perform|execute|complete|operate|configure|set up", normalized):
        return MasteryFacet.PERFORM
    if re.search(r"解释|说明|原理|阐述|explain|describe|principle|reason", normalized):
        return MasteryFacet.EXPLAIN
    if re.search(r"识别|说出|指出|列出|概括|了解|认识|掌握|identify|name|list|recognize|recall|master", normalized):
        return MasteryFacet.ORIENTED
    return ""


def _find_targets(text: str, occurrences: list[dict[str, Any]], *, project_id: str, task_id: str,
                  section_id: str, source_module: str, target_titles: list[str] | None = None,
                  target_ids: list[str] | None = None) -> list[dict[str, Any]]:
    normalized = _normalize(text)
    normalized_titles = [_normalize(item) for item in (target_titles or []) if len(_normalize(item)) >= 2]
    candidates = [item for item in occurrences if (not project_id or item["project_id"] == project_id)]
    if task_id:
        task_candidates = [item for item in candidates if item["task_id"] == task_id]
        if task_candidates:
            candidates = task_candidates
    if section_id:
        section_candidates = [item for item in candidates if item["section_id"] == section_id]
        if section_candidates:
            candidates = section_candidates
    matches = []
    for item in candidates:
        if target_ids and item["knowledge_id"] not in set(target_ids):
            continue
        if target_ids and item["knowledge_id"] in set(target_ids):
            matches.append(item)
            continue
        title = _normalize(item["knowledge_title"])
        aliases = [_normalize(alias) for alias in item.get("aliases", [])]
        if normalized_titles:
            title_matches = any(
                candidate_title == title
                or candidate_title in title
                or title in candidate_title
                for candidate_title in normalized_titles
            )
            if not title_matches:
                continue
            matches.append(item)
            continue
        terms = [term for term in [title, *aliases] if len(term) >= 2]
        if any(term and term in normalized for term in terms):
            matches.append(item)
            continue
        # A title such as “熔池状态分析” may be referred to simply as “熔池”.
        for term in re.split(r"[与和及的中：:、,，/（）()\s]+", title):
            if len(term) >= 2 and term in normalized:
                matches.append(item)
                break
    if source_module == MODULE_NAVIGATION and _is_forward_navigation(text) and section_id:
        return [item for item in candidates if item["section_id"] == section_id]
    if source_module == MODULE_PROJECT_GOAL and not matches:
        return candidates
    return matches


def _occurrence_records(planned: list[PlannedOccurrence], source_points: Iterable[Any]) -> list[dict[str, Any]]:
    source_by_id = {_value(item, "source_knowledge_point_id", ""): item for item in source_points}
    records: list[dict[str, Any]] = []
    for item in planned:
        source_id = _value(item, "source_knowledge_point_id", "")
        position = _value(item, "position", {})
        source = source_by_id.get(source_id)
        records.append({
            "occurrence_id": _value(item, "occurrence_id", ""),
            "knowledge_id": _value(item, "knowledge_id", ""),
            "knowledge_title": str(_value(source, "title", _value(item, "knowledge_id", "")) if source else _value(item, "knowledge_id", "")),
            "aliases": [],
            "project_id": _value(item, "chapter_id", ""),
            "task_id": str(_value(position, "task_ordinal", 0)),
            "section_id": _value(item, "section_id", ""),
            "position": _position_tuple(position),
        })
    return records


def _timeline(execution: Any, occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transitions = _items(execution, "transitions")
    rows: list[dict[str, Any]] = []
    for transition in transitions:
        after = _value(transition, "after", None)
        if not isinstance(after, dict):
            continue
        position = _transition_position(transition, occurrences)
        rows.append({"position": position, "state": after, "occurrence_id": _value(transition, "occurrence_id", "")})
    rows.sort(key=lambda item: item["position"])
    return rows


def _state_at(requirement: StudentRequirement, timeline: list[dict[str, Any]]) -> dict[str, Any]:
    limit = _position_limit(requirement)
    eligible = [row for row in timeline if row["position"] <= limit]
    if requirement.evaluation_point == EVAL_BEFORE_LOCATION:
        eligible = [row for row in timeline if row["position"] < limit]
    if not eligible:
        return {}
    return eligible[-1]["state"]


def _support_for_requirement(requirement: StudentRequirement, state: dict[str, Any], occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    by_knowledge = _value(state, "availability_by_knowledge", {}) or {}
    facets: list[str] = []
    extensions: list[str] = []
    provenance: dict[str, str] = {}
    supporting: list[str] = []
    for knowledge_id in requirement.target_knowledge_ids:
        record = by_knowledge.get(knowledge_id, {})
        facets.extend(_value(record, "available_facets", []) or [])
        extensions.extend(_value(record, "available_extension_keys", []) or [])
        for facet, occurrence_id in (_value(record, "facet_source_occurrence_ids", {}) or {}).items():
            provenance[f"facet:{facet}"] = occurrence_id
            supporting.append(occurrence_id)
        for key, occurrence_id in (_value(record, "extension_source_occurrence_ids", {}) or {}).items():
            provenance[f"extension:{key}"] = occurrence_id
            supporting.append(occurrence_id)
    return {
        "facets": list(dict.fromkeys(facets)),
        "extensions": list(dict.fromkeys(extensions)),
        "provenance": provenance,
        "occurrence_ids": list(dict.fromkeys(supporting)),
    }


def _relevant_blocked(requirement: StudentRequirement, blocked: list[dict[str, Any]], occurrences: list[dict[str, Any]]) -> list[str]:
    target_ids = set(requirement.target_occurrence_ids)
    result = []
    for item in blocked:
        occurrence_id = str(_value(item, "occurrence_id", ""))
        if occurrence_id in target_ids:
            result.append(occurrence_id)
    return list(dict.fromkeys(result))


def _classify(requirement: StudentRequirement, support: dict[str, Any], blocked: list[str]) -> tuple[str, str]:
    if requirement.mapping_source == "UNMAPPED" or not requirement.required_facets:
        return UNMAPPED_REQUIREMENT, requirement.mapping_reason
    if requirement.source_module in {MODULE_ASSESSMENT, MODULE_EXERCISE} and not requirement.target_knowledge_ids:
        return UNMAPPED_REQUIREMENT, "Capability is explicit, but no target knowledge point is named in the requirement."
    available = set(support["facets"])
    required = set(requirement.required_facets)
    covered = required.issubset(available) if requirement.facet_relation == "ALL" else bool(required & available)
    if covered:
        relation = "all" if requirement.facet_relation == "ALL" else "one of"
        return CLOSED, f"The {relation} required facet(s) were verified available at the evaluation point."
    if requirement.source_module == MODULE_NAVIGATION and requirement.evaluation_point == EVAL_SECTION_END:
        if blocked:
            return TARGET_NOT_DELIVERED, "Forward navigation target was not delivered by section end."
    if requirement.source_module == MODULE_PROJECT_GOAL:
        return TARGET_NOT_DELIVERED, "Project end did not provide the required capability."
    if blocked:
        return BLOCKED_BY_PRIOR_FAILURE, "A targeted prior occurrence was blocked before verified teaching was available."
    if available:
        return UNDER_SUPPORTED, "Some required facets are available, but the requirement asks for a higher capability."
    return UNSUPPORTED, "No verified support for the required facets exists at this location."


def _blocked_impact(results: list[TeachingSupportClosure], blocked_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    code_by_id = {
        str(_value(item, "occurrence_id", "")): str(_value(item, "issue_code", "UNKNOWN"))
        for item in blocked_records
    }
    impact: dict[str, dict[str, Any]] = {}
    for item in results:
        for occurrence_id in item.relevant_blocked_occurrence_ids:
            row = impact.setdefault(
                occurrence_id,
                {
                    "occurrence_id": occurrence_id,
                    "blocking_codes": [code_by_id.get(occurrence_id, "UNKNOWN")],
                    "requirement_ids": [],
                    "statuses": [],
                },
            )
            row["requirement_ids"].append(item.requirement.requirement_id)
            row["statuses"].append(item.status)
    return list(impact.values())


def _transition_position(transition: Any, occurrences: list[dict[str, Any]]) -> tuple[int, int, int]:
    occurrence_id = _value(transition, "occurrence_id", "")
    for item in occurrences:
        if item["occurrence_id"] == occurrence_id:
            return item["position"]
    return (0, 0, 0)


def _position_limit(requirement: StudentRequirement) -> tuple[int, int, int]:
    return (
        int(requirement.requirement_position.get("project_ordinal", 0)),
        int(requirement.requirement_position.get("task_ordinal", 10**9)),
        int(requirement.requirement_position.get("occurrence_ordinal", 10**9)),
    )


def _position_tuple(position: Any) -> tuple[int, int, int]:
    return (
        int(_value(position, "chapter_ordinal", 0)),
        int(_value(position, "task_ordinal", 0)),
        int(_value(position, "occurrence_ordinal", 0)),
    )


def _blocked_records(execution: Any) -> list[dict[str, Any]]:
    return [*(_items(execution, "blocked_occurrences")), *(_items(_value(execution, "coverage", {}), "execution_blocked_occurrences"))]


def _requirement_metadata(block: Any, index: int) -> dict[str, Any]:
    metadata = _value(block, "metadata", {}) or {}
    items = metadata.get("requirement_semantics", []) if isinstance(metadata, dict) else []
    if not isinstance(items, list) or index >= len(items) or not isinstance(items[index], dict):
        return {}
    return dict(items[index])


def _items(value: Any, key: str) -> list[Any]:
    raw = _value(value, key, [])
    return list(raw or []) if isinstance(raw, (list, tuple)) else []


def _value(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value.lower())


def _is_forward_navigation(text: str) -> bool:
    return bool(re.search(r"重点掌握|本节.*(学习|掌握)|本任务.*(学习|掌握)|学习目标", text))


def _is_prior_invocation(text: str) -> bool:
    return bool(re.search(r"前文|已学|已经学|运用.*(方法|原理|知识)|回顾", text))
