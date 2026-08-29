from __future__ import annotations

from pathlib import Path

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.knowledge_map.availability import simulate_instructional_availability
from materials2textbook.knowledge_map.canonicalization import canonicalize_source_points
from materials2textbook.knowledge_map.models import KnowledgeMap, LearningTrajectory, Prerequisite
from materials2textbook.knowledge_map.occurrences import plan_occurrences
from materials2textbook.knowledge_map.outline import (
    book_plan_deep_equal,
    extract_source_knowledge_points,
    outline_signature,
    snapshot_source_book_plan,
)
from materials2textbook.knowledge_map.reporting import render_learning_trajectory_report, render_mapping_audit, render_semantic_trajectory_report
from materials2textbook.knowledge_map.semantic_evaluation import SemanticPlanningEvaluation
from materials2textbook.knowledge_map.semantic import HeuristicSemanticPlanner, SemanticPlanner
from materials2textbook.knowledge_map.validator import validate_planned_trajectory
from materials2textbook.schemas import BookPlan, EvidenceChunk


def analyze_book_knowledge(
    *,
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    semantic_planner: SemanticPlanner | None = None,
    prerequisites: list[Prerequisite] | None = None,
    recall_after_tasks: int = 3,
) -> KnowledgeMap:
    """Build a read-only Phase 1 instructional-availability audit.

    The function neither mutates ``book_plan`` nor writes textbook content.  A
    custom semantic planner may be LLM-backed, but its low-confidence proposals
    are prevented from affecting the deterministic availability state.
    """
    source_book_plan_snapshot = snapshot_source_book_plan(book_plan)
    planner = semantic_planner or HeuristicSemanticPlanner()
    source_points = extract_source_knowledge_points(book_plan, chunks)
    knowledge_points, mappings = canonicalize_source_points(source_points, semantic_planner=planner)
    prerequisite_edges = [*planner.propose_prerequisites(knowledge_points, source_points), *(prerequisites or [])]
    occurrences = plan_occurrences(
        source_points=source_points,
        knowledge_points=knowledge_points,
        mappings=mappings,
        prerequisites=prerequisite_edges,
        semantic_planner=planner,
    )
    snapshots = simulate_instructional_availability(occurrences)
    issues = validate_planned_trajectory(
        occurrences=occurrences,
        mappings=mappings,
        snapshots=snapshots,
        semantic_planner=planner,
        recall_after_tasks=recall_after_tasks,
    )
    issue_ids_by_knowledge: dict[str, list[str]] = {}
    for issue in issues:
        issue_ids_by_knowledge.setdefault(issue.knowledge_id, []).append(issue.issue_id)
    trajectories = [
        LearningTrajectory(
            knowledge_id=knowledge_id,
            occurrence_ids=[item.occurrence_id for item in occurrences if item.knowledge_id == knowledge_id],
            planned_conflict_ids=issue_ids_by_knowledge.get(knowledge_id, []),
        )
        for knowledge_id in [point.knowledge_id for point in knowledge_points]
    ]
    result = KnowledgeMap(
        title=book_plan.title,
        outline_signature=outline_signature(book_plan),
        knowledge_points=knowledge_points,
        source_knowledge_points=source_points,
        mappings=mappings,
        prerequisites=prerequisite_edges,
        planned_occurrences=occurrences,
        trajectories=trajectories,
        availability_snapshots=snapshots,
        validation_issues=issues,
    )
    if not book_plan_deep_equal(book_plan, source_book_plan_snapshot):
        raise RuntimeError("Knowledge analysis mutated the fixed BookPlan.")
    return result


def write_knowledge_map_artifacts(knowledge_map: KnowledgeMap, output_dir: Path) -> tuple[Path, Path, Path]:
    """Write only Phase 1 audit artifacts; no textbook artifact is changed."""
    output_dir = Path(output_dir)
    json_path = output_dir / "knowledge_map.json"
    markdown_path = output_dir / "learning_trajectory_report.md"
    mapping_path = output_dir / "canonical_mapping_audit.md"
    write_json(json_path, knowledge_map)
    write_text(markdown_path, render_learning_trajectory_report(knowledge_map))
    write_text(mapping_path, render_mapping_audit(knowledge_map))
    return json_path, markdown_path, mapping_path


def write_semantic_evaluation_artifacts(evaluation: SemanticPlanningEvaluation, output_dir: Path) -> tuple[Path, Path]:
    """Persist Phase 1.5 audit records only; textbook content remains untouched."""
    output_dir = Path(output_dir)
    json_path = output_dir / "semantic_planning_evaluation.json"
    report_path = output_dir / "semantic_learning_trajectory_report.md"
    write_json(json_path, evaluation)
    write_text(report_path, render_semantic_trajectory_report(evaluation))
    return json_path, report_path
