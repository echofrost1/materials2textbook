from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import re
from typing import Any, Callable

from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.knowledge_map.outline import book_plan_fingerprint
from materials2textbook.schemas import BookChapterPlan, BookPlan, BookSectionPlan, EvidenceChunk


REPLANNABLE_PLAN_GAP = "REPLANNABLE_PLAN_GAP"
EVIDENCE_BINDING_GAP = "EVIDENCE_BINDING_GAP"
GENUINE_SOURCE_GAP = "GENUINE_SOURCE_GAP"
MANUAL_REVIEW = "MANUAL_REVIEW"

MAX_REPLAN_ATTEMPTS = 1
MAX_PRIMARY_MATERIALS = 6
MAX_REFERENCE_MATERIALS = 12

_SECTION_FIELDS = (
    "section_purpose",
    "expected_learning_outcome",
    "current_task_action",
    "knowledge_scope",
    "case_purpose",
    "exercise_purpose",
    "assessment_purpose",
    "activity_purpose",
)
_TEXT_FIELDS = (
    "section_purpose",
    "expected_learning_outcome",
    "current_task_action",
    "case_purpose",
    "exercise_purpose",
    "assessment_purpose",
    "activity_purpose",
)
_PLACEHOLDER_MARKERS = (
    "to be planned",
    "core concepts and learning tasks",
    "key knowledge points",
    "placeholder",
    "待规划",
    "待补充",
    "核心概念、材料来源和学习任务",
    "关键知识点",
    "学习目标",
)


@dataclass
class CompletenessIssue:
    issue_type: str
    outline_node_id: str
    severity: str
    expected_requirement: str
    current_state: str
    supporting_evidence_ids: list[str] = field(default_factory=list)
    missing_support: list[str] = field(default_factory=list)
    recommended_action: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletenessReport:
    status: str
    book_id: str
    plan_fingerprint: str
    issues: list[CompletenessIssue] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    replan_attempts: int = 0
    deterministic_repairs: list[str] = field(default_factory=list)
    safe_to_freeze: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompletenessOptimizationResult:
    book_plan: BookPlan
    initial_report: CompletenessReport
    final_report: CompletenessReport
    replan_attempts: int = 0
    replan_history: list[dict[str, Any]] = field(default_factory=list)
    resolved_issue_keys: list[str] = field(default_factory=list)
    book_plan_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_report": self.initial_report.to_dict(),
            "final_report": self.final_report.to_dict(),
            "replan_attempts": self.replan_attempts,
            "replan_history": list(self.replan_history),
            "resolved_issue_keys": list(self.resolved_issue_keys),
            "book_plan_changed": self.book_plan_changed,
            "book_plan_fingerprint": book_plan_fingerprint(self.book_plan),
        }


def diagnose_book_plan_completeness(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    *,
    domain_config: DomainConfig | None = None,
) -> CompletenessReport:
    """Inspect a not-yet-frozen BookPlan without changing it."""

    config = domain_config or default_domain_config()
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    issues: list[CompletenessIssue] = []
    issues.extend(_diagnose_curriculum_scope(book_plan, chunks, config))
    issues.extend(_diagnose_chapter_goals(book_plan, chunk_map))
    issues.extend(_diagnose_primary_ownership(book_plan, chunk_map))

    for chapter in book_plan.chapters:
        for section in chapter.sections:
            candidates = _section_candidates(chapter, section, chunks)
            issues.extend(_diagnose_section_fields(chapter, section, candidates))
            issues.extend(_diagnose_section_evidence(section, candidates, chunk_map))

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.issue_type] = counts.get(issue.issue_type, 0) + 1
    status = _report_status(issues)
    return CompletenessReport(
        status=status,
        book_id=book_plan.book_id,
        plan_fingerprint=book_plan_fingerprint(book_plan),
        issues=issues,
        counts=counts,
        safe_to_freeze=status == "PASS",
    )


def optimize_book_plan_completeness(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    *,
    domain_config: DomainConfig | None = None,
    replanner: Callable[[BookPlan, list[CompletenessIssue]], BookPlan | None] | None = None,
    max_replan_attempts: int = MAX_REPLAN_ATTEMPTS,
    allow_replan: bool = True,
) -> CompletenessOptimizationResult:
    """Apply bounded, pre-freeze repairs and optionally ask the original planner once.

    Existing outline identity/order and knowledge ownership are preserved. A
    replanner may append a supported chapter/section, but it cannot rename,
    delete, reorder, or move an existing node.
    """

    initial = diagnose_book_plan_completeness(book_plan, chunks, domain_config=domain_config)
    current = book_plan
    history: list[dict[str, Any]] = []
    deterministic_repairs: list[str] = []
    attempts = 0

    if allow_replan:
        current, deterministic_repairs = _apply_deterministic_repairs(current, chunks)
        if deterministic_repairs:
            history.append(
                {
                    "kind": "DETERMINISTIC_BOUNDED_REPLAN",
                    "attempt": 0,
                    "changed": True,
                    "repaired_fields": list(deterministic_repairs),
                }
            )

    current_report = diagnose_book_plan_completeness(current, chunks, domain_config=domain_config)
    retry_limit = max(0, min(int(max_replan_attempts), MAX_REPLAN_ATTEMPTS))
    while allow_replan and replanner and attempts < retry_limit and _needs_replan(current_report):
        attempts += 1
        candidate = replanner(current, current_report.issues)
        if candidate is None:
            history.append(
                {
                    "kind": "ORIGINAL_PLANNER_REPLAN",
                    "attempt": attempts,
                    "changed": False,
                    "reason": "planner_returned_no_candidate",
                }
            )
            break
        merged = _merge_replan_candidate(current, candidate, chunks)
        if merged is None or book_plan_fingerprint(merged) == book_plan_fingerprint(current):
            history.append(
                {
                    "kind": "ORIGINAL_PLANNER_REPLAN",
                    "attempt": attempts,
                    "changed": False,
                    "reason": "candidate_rejected_or_no_safe_change",
                }
            )
            break
        current = merged
        current, repairs = _apply_deterministic_repairs(current, chunks)
        deterministic_repairs.extend(repairs)
        history.append(
            {
                "kind": "ORIGINAL_PLANNER_REPLAN",
                "attempt": attempts,
                "changed": True,
                "candidate_fingerprint": book_plan_fingerprint(candidate),
                "merged_fingerprint": book_plan_fingerprint(current),
                "repaired_fields": list(repairs),
            }
        )
        current_report = diagnose_book_plan_completeness(current, chunks, domain_config=domain_config)

    final_report = diagnose_book_plan_completeness(current, chunks, domain_config=domain_config)
    final_report.replan_attempts = attempts
    final_report.deterministic_repairs = list(deterministic_repairs)
    initial_keys = {_issue_key(issue) for issue in initial.issues}
    final_keys = {_issue_key(issue) for issue in final_report.issues}
    return CompletenessOptimizationResult(
        book_plan=current,
        initial_report=initial,
        final_report=final_report,
        replan_attempts=attempts,
        replan_history=history,
        resolved_issue_keys=sorted(initial_keys - final_keys),
        book_plan_changed=book_plan_fingerprint(current) != book_plan_fingerprint(book_plan),
    )


def render_completeness_report_markdown(result: CompletenessOptimizationResult) -> str:
    initial = result.initial_report
    final = result.final_report
    lines = [
        "# BookPlan Completeness Report",
        "",
        f"- initial_status: {initial.status}",
        f"- final_status: {final.status}",
        f"- safe_to_freeze: {final.safe_to_freeze}",
        f"- replan_attempts: {result.replan_attempts}",
        f"- book_plan_changed: {result.book_plan_changed}",
        f"- resolved_issue_count: {len(result.resolved_issue_keys)}",
        "",
        "## Final issue counts",
        "",
    ]
    if final.counts:
        lines.extend(f"- {key}: {value}" for key, value in sorted(final.counts.items()))
    else:
        lines.append("- none")
    lines.extend(["", "## Issues", ""])
    if not final.issues:
        lines.append("No unresolved completeness issues.")
    for issue in final.issues:
        evidence = ", ".join(issue.supporting_evidence_ids) or "none"
        missing = ", ".join(issue.missing_support) or "none"
        lines.extend(
            [
                f"### {issue.issue_type} — {issue.outline_node_id}",
                "",
                f"- severity: {issue.severity}",
                f"- expected_requirement: {issue.expected_requirement}",
                f"- current_state: {issue.current_state}",
                f"- supporting_evidence_ids: {evidence}",
                f"- missing_support: {missing}",
                f"- recommended_action: {issue.recommended_action}",
                f"- provenance: {issue.provenance}",
                "",
            ]
        )
    return "\n".join(lines)


def _diagnose_curriculum_scope(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    config: DomainConfig,
) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    covered_text = [
        value
        for chapter in book_plan.chapters
        for value in [
            chapter.title,
            *chapter.learning_goals,
            *(section.title for section in chapter.sections),
            *(point for section in chapter.sections for point in section.knowledge_point_ids),
        ]
    ]
    for expected in config.chapter_order:
        if not expected.strip() or any(_text_matches(expected, actual) for actual in covered_text):
            continue
        candidates = [chunk.chunk_id for chunk in chunks if _text_matches(expected, _chunk_search_text(chunk))]
        if candidates:
            issue_type = REPLANNABLE_PLAN_GAP
            action = "让原 BookPlan planner 在一次 bounded replan 中补齐有授权素材支持的课程范围。"
            missing = []
        else:
            issue_type = GENUINE_SOURCE_GAP
            action = "补充授权素材或人工确认该课程范围不在本书范围内；不得使用模型常识补齐。"
            missing = [expected]
        issues.append(
            CompletenessIssue(
                issue_type=issue_type,
                outline_node_id="book",
                severity="high",
                expected_requirement=f"curriculum scope covers {expected}",
                current_state="expected scope is not represented by chapter, section, goal, or knowledge point",
                supporting_evidence_ids=_dedupe(candidates),
                missing_support=missing,
                recommended_action=action,
                provenance={
                    "source": "DomainConfig.chapter_order + BookPlan projection",
                    "rule": "semantic text/topic match with authorized EvidenceChunk candidate recall",
                },
            )
        )
    return issues


def _diagnose_chapter_goals(book_plan: BookPlan, chunk_map: dict[str, EvidenceChunk]) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    for chapter in book_plan.chapters:
        if not chapter.learning_goals or any(_is_placeholder(goal) for goal in chapter.learning_goals):
            evidence_ids = _chapter_evidence_ids(chapter, chunk_map)
            issue_type = REPLANNABLE_PLAN_GAP if evidence_ids else GENUINE_SOURCE_GAP
            issues.append(
                CompletenessIssue(
                    issue_type=issue_type,
                    outline_node_id=chapter.chapter_id,
                    severity="high",
                    expected_requirement="chapter learning goals are specific and non-placeholder",
                    current_state=str(chapter.learning_goals),
                    supporting_evidence_ids=evidence_ids,
                    missing_support=[] if evidence_ids else ["chapter-scoped evidence"],
                    recommended_action=(
                        "derive bounded chapter goals from the chapter title, section scope, and authorized evidence"
                        if evidence_ids
                        else "provide chapter-scoped evidence before freezing"
                    ),
                    provenance={"source": "BookChapterPlan.learning_goals", "rule": "placeholder/fallback detection"},
                )
            )
    return issues


def _diagnose_primary_ownership(book_plan: BookPlan, chunk_map: dict[str, EvidenceChunk]) -> list[CompletenessIssue]:
    # A source chunk may legitimately support more than one section.  The
    # completeness gate constrains each section's authorized pool, but does
    # not invent a global one-owner rule that would turn shared source support
    # into a false planning gap.  Unknown IDs are diagnosed at section scope.
    return []


def _diagnose_section_fields(
    chapter: BookChapterPlan,
    section: BookSectionPlan,
    candidates: list[EvidenceChunk],
) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    evidence_ids = [chunk.chunk_id for chunk in candidates]
    required_fields = list(_TEXT_FIELDS)
    if not section.needs_case:
        required_fields.remove("case_purpose")
    if not section.needs_exercises:
        required_fields.remove("exercise_purpose")
    if not section.knowledge_scope:
        required_fields.append("knowledge_scope")
    for field_name in required_fields:
        value = getattr(section, field_name)
        missing = not value or (isinstance(value, str) and _is_placeholder(value))
        if not missing:
            continue
        issue_type = REPLANNABLE_PLAN_GAP if evidence_ids else GENUINE_SOURCE_GAP
        expected = {
            "section_purpose": "specific section purpose",
            "expected_learning_outcome": "observable non-placeholder learning outcome",
            "current_task_action": "current task action",
            "knowledge_scope": "knowledge point scope",
            "case_purpose": "case module intent",
            "exercise_purpose": "exercise module intent",
            "assessment_purpose": "assessment module intent",
            "activity_purpose": "activity module intent",
        }[field_name]
        issues.append(
            CompletenessIssue(
                issue_type=issue_type,
                outline_node_id=section.section_id,
                severity="high" if field_name in {"section_purpose", "expected_learning_outcome", "knowledge_scope"} else "medium",
                expected_requirement=expected,
                current_state=str(value or "empty"),
                supporting_evidence_ids=_dedupe(evidence_ids),
                missing_support=[] if evidence_ids else [section.title or "section-scoped evidence"],
                recommended_action=(
                    f"fill {field_name} from section title, knowledge scope, task context, and authorized evidence"
                    if evidence_ids
                    else "provide section-scoped evidence before planning this responsibility"
                ),
                provenance={
                    "source": f"BookSectionPlan.{field_name}",
                    "chapter_id": chapter.chapter_id,
                    "rule": "required section/module intent presence and placeholder detection",
                },
            )
        )
    return issues


def _diagnose_section_evidence(
    section: BookSectionPlan,
    candidates: list[EvidenceChunk],
    chunk_map: dict[str, EvidenceChunk],
) -> list[CompletenessIssue]:
    issues: list[CompletenessIssue] = []
    candidate_ids = [chunk.chunk_id for chunk in candidates]
    primary = [material_id for material_id in section.primary_material_ids if material_id in chunk_map]
    references = [material_id for material_id in section.reference_material_ids if material_id in chunk_map]
    invalid = [
        material_id
        for material_id in [*section.primary_material_ids, *section.reference_material_ids]
        if material_id not in chunk_map
    ]
    if invalid:
        issues.append(
            CompletenessIssue(
                issue_type=EVIDENCE_BINDING_GAP if candidate_ids else GENUINE_SOURCE_GAP,
                outline_node_id=section.section_id,
                severity="high",
                expected_requirement="all section evidence IDs resolve to authorized EvidenceChunk records",
                current_state=f"unresolved evidence IDs: {invalid}",
                supporting_evidence_ids=_dedupe(candidate_ids),
                missing_support=invalid if not candidate_ids else [],
                recommended_action="replace invalid IDs only with bounded candidates from the same section/task evidence scope",
                provenance={"source": "BookSectionPlan primary/reference material IDs", "rule": "canonical chunk ID whitelist"},
            )
        )
    if not primary:
        issues.append(
            CompletenessIssue(
                issue_type=EVIDENCE_BINDING_GAP if candidate_ids else GENUINE_SOURCE_GAP,
                outline_node_id=section.section_id,
                severity="high",
                expected_requirement="section has at least one primary evidence chunk",
                current_state="primary_material_ids is empty or unresolved",
                supporting_evidence_ids=_dedupe(candidate_ids),
                missing_support=[] if candidate_ids else [section.title or "section evidence"],
                recommended_action="bind bounded section candidates as primary evidence before freeze",
                provenance={"source": "BookSectionPlan.primary_material_ids", "rule": "minimum primary ownership"},
            )
        )
    elif len(candidate_ids) > len(primary) and len(primary) < min(2, len(candidate_ids)):
        issues.append(
            CompletenessIssue(
                issue_type=EVIDENCE_BINDING_GAP,
                outline_node_id=section.section_id,
                severity="medium",
                expected_requirement="section evidence pool is not narrower than available bounded support",
                current_state=f"primary={len(primary)}, references={len(references)}, candidates={len(candidate_ids)}",
                supporting_evidence_ids=_dedupe(candidate_ids),
                recommended_action="add bounded primary/reference evidence without changing knowledge ownership",
                provenance={"source": "section candidate retrieval", "rule": "bounded evidence pool adequacy"},
            )
        )
    if len(candidate_ids) > len(primary) + len(references):
        issues.append(
            CompletenessIssue(
                issue_type=EVIDENCE_BINDING_GAP,
                outline_node_id=section.section_id,
                severity="medium",
                expected_requirement="additional relevant evidence is retained as reference material",
                current_state=f"bound={len(primary) + len(references)}, candidates={len(candidate_ids)}",
                supporting_evidence_ids=_dedupe(candidate_ids),
                recommended_action="retain relevant non-primary candidates as reference_material_ids",
                provenance={"source": "section candidate retrieval", "rule": "primary/reference ownership completeness"},
            )
        )
    return issues


def _apply_deterministic_repairs(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
) -> tuple[BookPlan, list[str]]:
    # Keep deterministic repairs limited to facts that are already explicit
    # in the plan or evidence bindings.  Learning goals/module purposes are
    # never synthesized from a title-only fallback.
    return _apply_grounded_repairs(book_plan, chunks)


def _apply_grounded_repairs(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
) -> tuple[BookPlan, list[str]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    changed: list[str] = []
    chapters: list[BookChapterPlan] = []
    for chapter in book_plan.chapters:
        sections: list[BookSectionPlan] = []
        for section in chapter.sections:
            candidates = _section_candidates(chapter, section, chunks)
            candidate_ids = [chunk.chunk_id for chunk in candidates]
            knowledge_scope = list(section.knowledge_scope)
            if not knowledge_scope and section.knowledge_point_ids:
                knowledge_scope = list(section.knowledge_point_ids)
                changed.append(f"{section.section_id}.knowledge_scope")

            primary = [material_id for material_id in section.primary_material_ids if material_id in chunk_map]
            references = [
                material_id
                for material_id in section.reference_material_ids
                if material_id in chunk_map and material_id not in primary
            ]
            target_primary = min(MAX_PRIMARY_MATERIALS, max(1, min(2, len(candidate_ids)))) if candidate_ids else len(primary)
            for material_id in candidate_ids:
                if len(primary) >= target_primary:
                    break
                if material_id not in primary:
                    primary.append(material_id)
            for material_id in candidate_ids:
                if material_id not in primary and material_id not in references and len(references) < MAX_REFERENCE_MATERIALS:
                    references.append(material_id)
            if primary != section.primary_material_ids or references != section.reference_material_ids:
                changed.append(f"{section.section_id}.evidence_ownership")
            sections.append(
                replace(
                    section,
                    primary_material_ids=primary,
                    reference_material_ids=references,
                    knowledge_scope=knowledge_scope,
                )
            )
        chapter_primary = _dedupe(
            [
                material_id
                for material_id in [
                    *chapter.primary_material_ids,
                    *(material_id for section in sections for material_id in section.primary_material_ids),
                ]
                if material_id in chunk_map
            ]
        )
        chapters.append(replace(chapter, sections=sections, primary_material_ids=chapter_primary))
    return replace(book_plan, chapters=chapters), sorted(set(changed))


def _legacy_unreachable_deterministic_repairs(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
) -> tuple[BookPlan, list[str]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    changed: list[str] = []
    chapters: list[BookChapterPlan] = []
    for chapter in book_plan.chapters:
        chapter_evidence = _chapter_evidence_ids(chapter, chunk_map)
        goals = list(chapter.learning_goals)
        if (not goals or any(_is_placeholder(goal) for goal in goals)) and chapter_evidence:
            goals = [
                f"围绕{chapter.title}明确本章知识范围与学习任务。",
                f"能够基于授权素材说明{chapter.title}的关键学习要点并完成相关任务。",
            ]
            changed.append(f"{chapter.chapter_id}.learning_goals")
        sections: list[BookSectionPlan] = []
        for section in chapter.sections:
            candidates = _section_candidates(chapter, section, chunks)
            candidate_ids = [chunk.chunk_id for chunk in candidates]
            values = {
                "section_purpose": section.section_purpose,
                "expected_learning_outcome": section.expected_learning_outcome,
                "current_task_action": section.current_task_action,
                "knowledge_scope": list(section.knowledge_scope),
                "case_purpose": section.case_purpose,
                "exercise_purpose": section.exercise_purpose,
                "assessment_purpose": section.assessment_purpose,
                "activity_purpose": section.activity_purpose,
            }
            if candidates:
                values["section_purpose"] = values["section_purpose"] if not _is_placeholder(values["section_purpose"]) else (
                    f"明确“{section.title}”在当前任务中的教学作用。"
                )
                values["expected_learning_outcome"] = values["expected_learning_outcome"] if not _is_placeholder(values["expected_learning_outcome"]) else (
                    f"能够基于授权素材说明“{section.title}”的关键学习要点。"
                )
                values["current_task_action"] = values["current_task_action"] if not _is_placeholder(values["current_task_action"]) else (
                    f"在当前任务中围绕“{section.title}”完成观察、说明或操作任务。"
                )
                values["knowledge_scope"] = values["knowledge_scope"] or list(section.knowledge_point_ids) or [section.title]
                if section.needs_case and _is_placeholder(values["case_purpose"]):
                    values["case_purpose"] = f"用授权素材构造与“{section.title}”直接相关的应用情境。"
                if section.needs_exercises and _is_placeholder(values["exercise_purpose"]):
                    values["exercise_purpose"] = f"通过“{section.title}”相关的识别、说明或操作练习巩固本节要点。"
                if _is_placeholder(values["assessment_purpose"]):
                    values["assessment_purpose"] = f"评价学生能否基于授权素材完成“{section.title}”对应的学习要求。"
                if _is_placeholder(values["activity_purpose"]):
                    values["activity_purpose"] = f"组织围绕“{section.title}”的观察、讨论或操作活动。"
                for field_name in _SECTION_FIELDS:
                    if getattr(section, field_name) != values[field_name]:
                        changed.append(f"{section.section_id}.{field_name}")

            primary = [material_id for material_id in section.primary_material_ids if material_id in chunk_map]
            references = [
                material_id
                for material_id in section.reference_material_ids
                if material_id in chunk_map and material_id not in primary
            ]
            target_primary = min(MAX_PRIMARY_MATERIALS, max(1, min(2, len(candidate_ids)))) if candidate_ids else len(primary)
            for material_id in candidate_ids:
                if len(primary) >= target_primary:
                    break
                if material_id in primary:
                    continue
                primary.append(material_id)
            for material_id in candidate_ids:
                if material_id not in primary and material_id not in references and len(references) < MAX_REFERENCE_MATERIALS:
                    references.append(material_id)
            if primary != section.primary_material_ids or references != section.reference_material_ids:
                changed.append(f"{section.section_id}.evidence_ownership")
            sections.append(
                replace(
                    section,
                    primary_material_ids=primary,
                    reference_material_ids=references,
                    section_purpose=values["section_purpose"],
                    expected_learning_outcome=values["expected_learning_outcome"],
                    current_task_action=values["current_task_action"],
                    knowledge_scope=values["knowledge_scope"],
                    case_purpose=values["case_purpose"],
                    exercise_purpose=values["exercise_purpose"],
                    assessment_purpose=values["assessment_purpose"],
                    activity_purpose=values["activity_purpose"],
                )
            )
        chapter_primary = _dedupe(
            [
                material_id
                for material_id in [
                    *chapter.primary_material_ids,
                    *(material_id for section in sections for material_id in section.primary_material_ids),
                ]
                if material_id in chunk_map
            ]
        )
        chapters.append(replace(chapter, learning_goals=goals, sections=sections, primary_material_ids=chapter_primary))
    return replace(book_plan, chapters=chapters), sorted(set(changed))


def _merge_replan_candidate(
    current: BookPlan,
    candidate: BookPlan,
    chunks: list[EvidenceChunk],
) -> BookPlan | None:
    if candidate.book_id != current.book_id or candidate.title != current.title:
        return None
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    candidate_chapters = {chapter.chapter_id: chapter for chapter in candidate.chapters}
    current_chapter_ids = {chapter.chapter_id for chapter in current.chapters}
    merged_chapters: list[BookChapterPlan] = []
    for chapter in current.chapters:
        candidate_chapter = candidate_chapters.get(chapter.chapter_id)
        candidate_sections = (
            {section.section_id: section for section in candidate_chapter.sections}
            if candidate_chapter
            else {}
        )
        sections: list[BookSectionPlan] = []
        current_section_ids = {section.section_id for section in chapter.sections}
        for section in chapter.sections:
            sections.append(_merge_section_proposal(section, candidate_sections.get(section.section_id), chunk_map))
        if candidate_chapter:
            for section in candidate_chapter.sections:
                if section.section_id not in current_section_ids and _safe_new_section(section, chunk_map):
                    sections.append(section)
        goals = list(chapter.learning_goals)
        if candidate_chapter and any(_is_placeholder(goal) for goal in goals):
            proposed_goals = [goal for goal in candidate_chapter.learning_goals if not _is_placeholder(goal)]
            if proposed_goals:
                goals = proposed_goals
        primary = _dedupe(
            [
                material_id
                for material_id in [
                    *chapter.primary_material_ids,
                    *(candidate_chapter.primary_material_ids if candidate_chapter else []),
                    *(material_id for section in sections for material_id in section.primary_material_ids),
                ]
                if material_id in chunk_map
            ]
        )
        merged_chapters.append(replace(chapter, learning_goals=goals, sections=sections, primary_material_ids=primary))

    for chapter in candidate.chapters:
        if chapter.chapter_id in current_chapter_ids or not _safe_new_chapter(chapter, chunk_map):
            continue
        merged_chapters.append(chapter)
    return replace(current, chapters=merged_chapters)


def _merge_section_proposal(
    current: BookSectionPlan,
    proposal: BookSectionPlan | None,
    chunk_map: dict[str, EvidenceChunk],
) -> BookSectionPlan:
    if proposal is None:
        return current
    updates: dict[str, Any] = {}
    proposal_evidence = [
        material_id
        for material_id in [*proposal.primary_material_ids, *proposal.reference_material_ids]
        if material_id in chunk_map
    ]
    # Planner-proposed purpose/intent is accepted only when the proposal also
    # carries an authorized evidence binding.  This keeps bounded replanning
    # from turning an ungrounded LLM description into a frozen requirement.
    if proposal_evidence:
        for field_name in _TEXT_FIELDS:
            current_value = getattr(current, field_name)
            proposal_value = getattr(proposal, field_name)
            if _is_placeholder(current_value) and not _is_placeholder(proposal_value):
                updates[field_name] = proposal_value
        if not current.knowledge_scope and proposal.knowledge_scope:
            updates["knowledge_scope"] = list(proposal.knowledge_scope)
    updates["primary_material_ids"] = _dedupe(
        [material_id for material_id in [*current.primary_material_ids, *proposal.primary_material_ids] if material_id in chunk_map]
    )
    updates["reference_material_ids"] = _dedupe(
        [
            material_id
            for material_id in [*current.reference_material_ids, *proposal.reference_material_ids]
            if material_id in chunk_map and material_id not in updates["primary_material_ids"]
        ]
    )
    return replace(current, **updates)


def _safe_new_chapter(chapter: BookChapterPlan, chunk_map: dict[str, EvidenceChunk]) -> bool:
    return bool(
        chapter.title.strip()
        and chapter.sections
        and any(
            material_id in chunk_map
            for section in chapter.sections
            for material_id in [*section.primary_material_ids, *section.reference_material_ids]
        )
    )


def _safe_new_section(section: BookSectionPlan, chunk_map: dict[str, EvidenceChunk]) -> bool:
    return bool(
        section.section_id
        and section.title.strip()
        and section.knowledge_point_ids
        and any(
            material_id in chunk_map
            for material_id in [*section.primary_material_ids, *section.reference_material_ids]
        )
    )


def _section_candidates(
    chapter: BookChapterPlan,
    section: BookSectionPlan,
    chunks: list[EvidenceChunk],
) -> list[EvidenceChunk]:
    section_text = " ".join([chapter.title, section.title, *section.knowledge_point_ids])
    scored: list[tuple[float, EvidenceChunk]] = []
    current_ids = set(section.primary_material_ids) | set(section.reference_material_ids)
    for chunk in chunks:
        score = _text_match_score(section_text, _chunk_search_text(chunk))
        if chunk.chunk_id in current_ids:
            score = max(score, 3.0)
        if score <= 0:
            continue
        scored.append((score, chunk))
    scored.sort(
        key=lambda item: (
            item[0],
            item[1].score.teaching_value,
            item[1].score.relevance,
            item[1].score.confidence,
            len(item[1].content or "") + len(item[1].summary or ""),
        ),
        reverse=True,
    )
    return [chunk for _, chunk in scored]


def _chunk_search_text(chunk: EvidenceChunk) -> str:
    metadata = " ".join(str(value) for value in chunk.metadata.values() if isinstance(value, (str, int, float)))
    return " ".join(
        [
            chunk.title,
            chunk.summary,
            chunk.content[:1000],
            " ".join(chunk.keywords),
            chunk.subject,
            chunk.material_block,
            chunk.recommended_chapter,
            metadata,
        ]
    )


def _chapter_evidence_ids(chapter: BookChapterPlan, chunk_map: dict[str, EvidenceChunk]) -> list[str]:
    return _dedupe(
        [
            material_id
            for material_id in [
                *chapter.primary_material_ids,
                *chapter.reference_material_ids,
                *(
                    material_id
                    for section in chapter.sections
                    for material_id in [*section.primary_material_ids, *section.reference_material_ids]
                ),
            ]
            if material_id in chunk_map
        ]
    )


def _report_status(issues: list[CompletenessIssue]) -> str:
    if not issues:
        return "PASS"
    if any(issue.issue_type in {GENUINE_SOURCE_GAP, MANUAL_REVIEW} for issue in issues):
        return "BLOCKED"
    return "REPLAN_REQUIRED"


def _needs_replan(report: CompletenessReport) -> bool:
    return any(issue.issue_type in {REPLANNABLE_PLAN_GAP, EVIDENCE_BINDING_GAP} for issue in report.issues)


def _issue_key(issue: CompletenessIssue) -> str:
    return f"{issue.issue_type}|{issue.outline_node_id}|{issue.expected_requirement}"


def _is_placeholder(value: Any) -> bool:
    if isinstance(value, list):
        return not value
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return len(text) < 4 or any(marker.lower() in lowered for marker in _PLACEHOLDER_MARKERS)


def _text_matches(expected: str, actual: str) -> bool:
    return _text_match_score(expected, actual) >= 1.0


def _text_match_score(expected: str, actual: str) -> float:
    left_raw = str(expected or "").lower().strip()
    right_raw = str(actual or "").lower().strip()
    left = _normalize_text(left_raw)
    right = _normalize_text(right_raw)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    # Tokenize the original text, not the separator-stripped form.  Keeping
    # word boundaries is important for bounded candidate recall (for example,
    # ``basic operation`` should match ``basic operation conditions``).
    left_terms = _terms(left_raw)
    right_terms = _terms(right_raw)
    if not left_terms or not right_terms:
        return 0.0
    overlap = len(left_terms & right_terms)
    if overlap >= 2:
        return overlap / max(1, min(len(left_terms), len(right_terms)))
    return 0.0


def _normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").lower())


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", value.lower()))


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
