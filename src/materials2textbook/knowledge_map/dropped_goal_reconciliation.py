"""Read-only closure checks for evidence-bounded dropped occurrence goals."""

from __future__ import annotations

import re

from materials2textbook.knowledge_map.publication_quality_models import PublicationSeverity
from materials2textbook.knowledge_map.writing_briefs import DroppedOccurrenceGoal
from materials2textbook.schemas import DigitalBook, DigitalBookTask


class DroppedGoalIssueCode:
    TASK_WITHOUT_TEACHING_SUPPORT = "TASK_WITHOUT_TEACHING_SUPPORT"
    ASSESSMENT_WITHOUT_CONTENT_SUPPORT = "ASSESSMENT_WITHOUT_CONTENT_SUPPORT"
    EXERCISE_WITHOUT_CONTENT_SUPPORT = "EXERCISE_WITHOUT_CONTENT_SUPPORT"
    DROPPED_GOAL_STILL_REFERENCED = "DROPPED_GOAL_STILL_REFERENCED"
    PUBLICATION_TASK_CLOSURE_FAILURE = "PUBLICATION_TASK_CLOSURE_FAILURE"


def inspect_dropped_goal_reconciliation(
    *,
    digital_book: DigitalBook,
    dropped_goals: list[DroppedOccurrenceGoal],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for goal in dropped_goals:
        task = _task_for_goal(digital_book, goal.chapter_id, goal.section_id)
        if task is None:
            findings.append(_finding(
                DroppedGoalIssueCode.PUBLICATION_TASK_CLOSURE_FAILURE,
                goal, "DigitalBook task is missing for a fixed BookPlan section.", "",
            ))
            continue
        reconciliation = task.metadata.get("dropped_goal_reconciliation") if task.metadata else None
        block_types = {block.type for block in task.blocks}
        has_implementation = "implementation" in block_types
        has_assessment = "assessment" in block_types
        has_exercises = "exercises" in block_types
        if isinstance(reconciliation, dict) and reconciliation.get("status") == "DISABLED_NO_REMAINING_GOAL":
            if has_implementation or has_assessment or has_exercises:
                findings.append(_finding(
                    DroppedGoalIssueCode.PUBLICATION_TASK_CLOSURE_FAILURE,
                    goal,
                    "Disabled dropped-goal task still exposes teaching, assessment, or exercise components.",
                    task.title,
                ))
            continue
        if not has_implementation:
            findings.append(_finding(
                DroppedGoalIssueCode.TASK_WITHOUT_TEACHING_SUPPORT,
                goal,
                "Task remains student-visible without active teaching support or deterministic reconciliation.",
                task.title,
            ))
        if has_assessment:
            findings.append(_finding(
                DroppedGoalIssueCode.ASSESSMENT_WITHOUT_CONTENT_SUPPORT,
                goal,
                "Assessment remains after its only occurrence goal was dropped.",
                _block_text(task, "assessment"),
            ))
        if has_exercises:
            findings.append(_finding(
                DroppedGoalIssueCode.EXERCISE_WITHOUT_CONTENT_SUPPORT,
                goal,
                "Exercise remains after its only occurrence goal was dropped.",
                _block_text(task, "exercises"),
            ))
        if goal.canonical_knowledge_id and any(goal.canonical_knowledge_id.split(":")[-1] in value for value in task.knowledge_points):
            findings.append(_finding(
                DroppedGoalIssueCode.DROPPED_GOAL_STILL_REFERENCED,
                goal,
                "Dropped knowledge goal remains in student-visible task knowledge points.",
                "、".join(task.knowledge_points),
            ))
    return findings


def _task_for_goal(book: DigitalBook, chapter_id: str, section_id: str) -> DigitalBookTask | None:
    chapter = next((item for item in book.projects if item.project_id == chapter_id), None)
    if chapter is None:
        return None
    for task in chapter.tasks:
        if task.metadata.get("section_id") == section_id:
            return task
        for block in task.blocks:
            semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
            if isinstance(semantic, dict) and semantic.get("section_id") == section_id:
                return task
    match = re.search(r"_(?:task_)?(\d+)$", section_id)
    if match:
        task_suffix = f"task_{int(match.group(1)):02d}"
        return next((item for item in chapter.tasks if item.task_id.endswith(task_suffix)), None)
    return None


def _block_text(task: DigitalBookTask, block_type: str) -> str:
    block = next((item for item in task.blocks if item.type == block_type), None)
    if block is None:
        return ""
    return block.markdown or "；".join(block.items)


def _finding(code: str, goal: DroppedOccurrenceGoal, message: str, span: str) -> dict[str, object]:
    return {
        "code": code,
        "severity": PublicationSeverity.BLOCKER,
        "occurrence_id": goal.occurrence_id,
        "location": f"digital_book:{goal.chapter_id}:{goal.section_id}",
        "message": message,
        "span": span,
        "classification": "renderer_bug",
    }
