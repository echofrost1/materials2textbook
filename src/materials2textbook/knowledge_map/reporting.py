from __future__ import annotations

from materials2textbook.knowledge_map.models import KnowledgeMap
from materials2textbook.knowledge_map.semantic_evaluation import SemanticPlanningEvaluation


def render_learning_trajectory_report(knowledge_map: KnowledgeMap) -> str:
    points = {point.knowledge_id: point for point in knowledge_map.knowledge_points}
    occurrences = {item.occurrence_id: item for item in knowledge_map.planned_occurrences}
    snapshots = {item.occurrence_id: item for item in knowledge_map.availability_snapshots}
    issues_by_occurrence: dict[str, list] = {}
    for issue in knowledge_map.validation_issues:
        issues_by_occurrence.setdefault(issue.occurrence_id, []).append(issue)

    lines = [
        f"# {knowledge_map.title} 学习轨迹审计",
        "",
        f"- 分析版本：{knowledge_map.analysis_version}",
        f"- 大纲签名：`{knowledge_map.outline_signature}`",
        f"- Canonical 知识点：{len(knowledge_map.knowledge_points)}",
        f"- 计划出现：{len(knowledge_map.planned_occurrences)}",
        f"- 问题：{len(knowledge_map.validation_issues)}",
        "",
        "## 知识点学习轨迹",
        "",
    ]
    for trajectory in knowledge_map.trajectories:
        point = points[trajectory.knowledge_id]
        lines.extend([f"### {point.title} (`{point.knowledge_id}`)", ""])
        for occurrence_id in trajectory.occurrence_ids:
            occurrence = occurrences[occurrence_id]
            snapshot = snapshots[occurrence_id]
            before = snapshot.before.availability_by_knowledge.get(occurrence.knowledge_id)
            facets = ", ".join(before.available_facets) if before else "无"
            extensions = ", ".join(before.available_extension_keys) if before else "无"
            lines.append(
                f"- C{occurrence.position.chapter_ordinal}/T{occurrence.position.task_ordinal}/O{occurrence.position.occurrence_ordinal} "
                f"`{occurrence.role}`：{occurrence.context_title}"
            )
            lines.append(f"  - 到达前教材已提供 facets：{facets}；extension keys：{extensions}")
            lines.append(f"  - 计划提供 facets：{', '.join(occurrence.intended_grants) or '无'}；新增：{', '.join(occurrence.intended_extension_keys) or '无'}")
            lines.append(f"  - 教学意图：{occurrence.intended_contribution}")
            lines.append(f"  - 新情境：{occurrence.new_context or '无'}；重复部分：{', '.join(occurrence.repeated_aspects) or '无'}")
            lines.append(f"  - 贡献依据：{occurrence.contribution_rationale or '未提供'}（confidence={occurrence.contribution_confidence:.2f}；evidence={', '.join(occurrence.contribution_evidence_chunk_ids) or '无'}）")
            for issue in issues_by_occurrence.get(occurrence_id, []):
                lines.append(f"  - [{issue.severity}] {issue.type}：{issue.diagnosis}")
        lines.append("")
    lines.extend(["## 未决映射与问题", ""])
    if not knowledge_map.validation_issues:
        lines.append("- 未发现计划层的可得性、回忆策略或重复教学问题。")
    else:
        for issue in knowledge_map.validation_issues:
            lines.append(
                f"- [{issue.severity}] `{issue.type}` / `{issue.occurrence_id or issue.knowledge_id}`：{issue.diagnosis}"
            )
    return "\n".join(lines).rstrip() + "\n"


def render_mapping_audit(knowledge_map: KnowledgeMap) -> str:
    source_points = {item.source_knowledge_point_id: item for item in knowledge_map.source_knowledge_points}
    points = {item.knowledge_id: item for item in knowledge_map.knowledge_points}
    lines = [f"# {knowledge_map.title} Canonical Mapping Audit", "", "## 全部映射", ""]
    for mapping in knowledge_map.mappings:
        source = source_points[mapping.source_knowledge_point_id]
        targets = [f"{points[item].title} (`{item}`)" for item in mapping.canonical_knowledge_ids if item in points]
        lines.extend([
            f"### {source.title} (`{source.source_knowledge_point_id}`)",
            f"- canonical：{'; '.join(targets) or '未解析'}",
            f"- mapping_type：{mapping.mapping_type}",
            f"- confidence：{mapping.confidence:.2f}",
            f"- rationale：{mapping.rationale}",
            f"- evidence IDs：{', '.join(mapping.evidence_chunk_ids) or '无'}",
            "",
        ])
    flagged = [item for item in knowledge_map.mappings if item.mapping_type in {"DECOMPOSED", "UNCERTAIN"}]
    lines.extend(["## DECOMPOSED / UNCERTAIN", ""])
    lines.extend([f"- {item.source_knowledge_point_id}: {item.mapping_type}" for item in flagged] or ["- 无"])
    return "\n".join(lines).rstrip() + "\n"


def render_semantic_trajectory_report(evaluation: SemanticPlanningEvaluation) -> str:
    """Human-review report for the Phase 1.5 semantic proposal, never a rewrite."""
    knowledge_map = evaluation.knowledge_map
    points = {item.knowledge_id: item for item in knowledge_map.knowledge_points}
    mappings_by_canonical: dict[str, list] = {}
    for mapping in knowledge_map.mappings:
        for knowledge_id in mapping.canonical_knowledge_ids:
            mappings_by_canonical.setdefault(knowledge_id, []).append(mapping)
    sources = {item.source_knowledge_point_id: item for item in knowledge_map.source_knowledge_points}
    occurrences = {item.occurrence_id: item for item in knowledge_map.planned_occurrences}
    snapshots = {item.occurrence_id: item for item in knowledge_map.availability_snapshots}
    issues_by_occurrence: dict[str, list] = {}
    for issue in knowledge_map.validation_issues:
        issues_by_occurrence.setdefault(issue.occurrence_id, []).append(issue)

    lines = [
        f"# {knowledge_map.title} — Phase 1.5 Semantic Trajectory Audit",
        "",
        "This is a read-only semantic proposal. It does not modify the BookPlan, textbook text, writer, reviser, or exporter.",
        f"- Canonical knowledge points: {len(knowledge_map.knowledge_points)}",
        f"- Semantic LLM calls: identity={evaluation.call_counts.get('identity', 0)}, semantic deltas={evaluation.call_counts.get('semantic_delta', 0)}",
        f"- Rejected or untrusted proposals: {len(evaluation.rejected_proposals)}",
        f"- Deterministic issues after semantic planning: {len(knowledge_map.validation_issues)}",
        "",
        "## Identity judgements (proposal only)",
        "",
    ]
    for item in evaluation.identity_judgements:
        lines.append(
            f"- `{item.get('left_id', '?')}` ↔ `{item.get('right_id', '?')}`: **{item.get('relation', 'UNCERTAIN')}** "
            f"(confidence={item.get('confidence', 0):.2f}); evidence: {', '.join(item.get('evidence_ids', [])) or 'none'}"
        )
        lines.append(f"  - {item.get('rationale', 'No rationale returned.')}")
    if not evaluation.identity_judgements:
        lines.append("- No valid identity judgements returned.")

    for trajectory in knowledge_map.trajectories:
        point = points[trajectory.knowledge_id]
        lines.extend(["", f"## {point.title} (`{point.knowledge_id}`)", ""])
        lines.append(f"- aliases: {', '.join(point.aliases) or 'none'}")
        lines.append(f"- canonical confidence: {point.extraction_confidence:.2f}")
        lines.append("- source mappings:")
        for mapping in mappings_by_canonical.get(point.knowledge_id, []):
            source = sources[mapping.source_knowledge_point_id]
            lines.append(
                f"  - `{source.source_knowledge_point_id}` / {source.title}: {mapping.mapping_type}, "
                f"confidence={mapping.confidence:.2f}, evidence={', '.join(mapping.evidence_chunk_ids) or 'none'}"
            )
        for occurrence_id in trajectory.occurrence_ids:
            occurrence = occurrences[occurrence_id]
            snapshot = snapshots[occurrence_id]
            before = snapshot.before.availability_by_knowledge.get(point.knowledge_id)
            available_facets = ", ".join(before.available_facets) if before else "none"
            available_extensions = ", ".join(before.available_extension_keys) if before else "none"
            cross = "; ".join(
                f"{use.knowledge_id}[{','.join(use.required_facets) or '-'}]" for use in occurrence.required_prerequisites
            ) or "none"
            lines.extend([
                "",
                f"### {occurrence.occurrence_id} — C{occurrence.position.chapter_ordinal}/T{occurrence.position.task_ordinal}/O{occurrence.position.occurrence_ordinal}",
                f"- position/context: {occurrence.context_title}",
                f"- role (deterministically derived): {occurrence.role} (confidence={occurrence.planning_confidence:.2f}, trusted_for_state={occurrence.trusted_for_state})",
                f"- before availability: facets={available_facets}; extension keys={available_extensions}",
                f"- requires: self facets={', '.join(occurrence.required_self_facets) or 'none'}; self extensions={', '.join(occurrence.required_self_extension_keys) or 'none'}; cross={cross}",
                f"- grants: facets={', '.join(occurrence.intended_grants) or 'none'}; extension keys={', '.join(occurrence.intended_extension_keys) or 'none'}",
                f"- contribution: {occurrence.intended_contribution or 'none'}",
                f"- semantic delta: repeats_prior_explanation={occurrence.repeats_prior_explanation}; uses_prior_knowledge={occurrence.uses_prior_knowledge}; recall_needed={occurrence.recall_needed}",
                f"- new context: {occurrence.new_context or 'none'}; repeated aspects: {', '.join(occurrence.repeated_aspects) or 'none'}",
                f"- role rationale/evidence: {occurrence.planning_rationale or 'none'} / {', '.join(occurrence.planning_evidence_chunk_ids) or 'none'}",
                f"- contribution rationale/evidence: {occurrence.contribution_rationale or 'none'} / {', '.join(occurrence.contribution_evidence_chunk_ids) or 'none'}",
                f"- deterministic transition: applied={snapshot.transition_applied}; blocked={', '.join(snapshot.blocked_reasons) or 'none'}",
            ])
            for issue in issues_by_occurrence.get(occurrence_id, []):
                lines.append(f"- issue [{issue.severity}] {issue.type}: {issue.diagnosis}")

    lines.extend(["", "## Rejected / untrusted proposals", ""])
    if evaluation.rejected_proposals:
        for item in evaluation.rejected_proposals:
            lines.append(f"- `{item.get('stage', '?')}` / `{item.get('reason', '?')}`: occurrence={item.get('occurrence_id', 'n/a')}")
    else:
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"
