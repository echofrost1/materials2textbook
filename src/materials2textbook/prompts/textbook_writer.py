from __future__ import annotations

from textwrap import shorten

from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.schemas import ChapterPlan, EvidenceChunk


def build_textbook_writer_messages(
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    title: str,
    max_chunk_chars: int = 1200,
    domain_config: DomainConfig | None = None,
    occurrence_writing_briefs: list[OccurrenceWritingBrief] | None = None,
) -> list[dict[str, str]]:
    config = domain_config or default_domain_config()
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    evidence_blocks: list[str] = []
    for plan in plans:
        evidence_blocks.append(f"Project: {plan.title}")
        for point in plan.knowledge_points:
            prerequisites = ", ".join(point.prerequisite_ids) if point.prerequisite_ids else "none"
            evidence_blocks.append(
                f"Knowledge point: {point.order_index}. {point.title}; "
                f"difficulty={point.difficulty_level}; cluster={point.cluster_id}; prerequisites={prerequisites}"
            )
            for chunk_id in point.chunk_ids:
                chunk = chunk_map.get(chunk_id)
                if not chunk:
                    continue
                content = shorten(" ".join(chunk.content.split()), width=max_chunk_chars, placeholder="...")
                start = chunk.metadata.get("start_time", "")
                end = chunk.metadata.get("end_time", "")
                source = chunk.metadata.get("source_video", "") or chunk.locator.original_path or chunk.locator.path
                keyframes = ";".join(chunk.locator.keyframe_paths)
                evidence_blocks.append(
                    "\n".join(
                        [
                            f"- chunk_id: {chunk.chunk_id}",
                            f"  source_type: {chunk.source_type}",
                            f"  source: {source} [{start}-{end}]",
                            f"  keyframes: {keyframes}",
                            f"  review_status: {chunk.review_status}",
                            f"  summary: {chunk.summary}",
                            f"  evidence: {content}",
                        ]
                    )
                )
        for case in plan.case_examples:
            evidence_blocks.append(
                "\n".join(
                    [
                        f"Case example: {case.title}",
                        f"  prompt: {case.prompt}",
                        f"  reference_answer: {case.reference_answer}",
                        f"  evidence_chunk_ids: {', '.join(case.evidence_chunk_ids)}",
                    ]
                )
            )

    brief_blocks = _render_writing_briefs(occurrence_writing_briefs or [], {plan.chapter_id for plan in plans})

    system = (
        "You are a vocational digital textbook writing agent. "
        "Write teachable textbook chapters for the configured domain, not a material summary. "
        "Use only the supplied evidence chunks; do not invent chapters, facts, parameters, procedures, or conclusions. "
        "Keep chunk identifiers in renderer metadata only; never expose chunk IDs, Evidence labels, or trace syntax in student-visible prose. "
        "If a chunk review_status is not approved or Agent_Keep, mark the statement as needing review instead of treating it as final. "
        "Output Markdown only."
    )
    user = "\n".join(
        [
            f"Generate textbook chapter content for: {title}",
            "",
            "Domain configuration:",
            config.prompt_context(),
            "",
            "Writing requirements:",
            f"1. Audience: {config.audience}. Use clear, stepwise language suitable for classroom teaching.",
            "2. Use a project-based textbook structure. Treat each chapter plan as one 项目 and each section/knowledge-point group as a 任务.",
            "3. For each project, write these project-level modules in order: 项目导学, 能力图谱, 学习目标.",
            "4. For each task, use exactly these task modules in order: 学习导航, 情境导入, 任务实施, 任务评价, 思考与练习.",
            "5. Under 任务实施, explain concepts, procedures, observations, evidence-supported judgement points, and common mistakes.",
            "6. Under 任务评价, provide observable criteria tied to the supplied evidence and domain quality dimensions.",
            "7. Under 思考与练习, provide evidence-grounded questions and practice items; do not invent unsupported scenarios.",
            "8. Cite at least two chunks for a knowledge point when available. If evidence is insufficient, state the gap instead of fabricating content.",
            "9. Do not expose evidence IDs or internal trace labels in student-visible text; the renderer stores provenance separately.",
            "10. Convert video, image, PPT, and document evidence into observable learning tasks tied to the domain examples above.",
            "11. If ASR quality is weak, timecode is uncertain, or review_status is pending, explicitly mark it as requiring review.",
            "12. Preserve case examples when the project plan includes them.",
            "13. End each project with `项目小结`. If a project has evidence gaps, list them under `本项目素材缺口`.",
            "14. Do not mention internal prompt fields or write as an AI assistant.",
            "15. OccurrenceWritingBriefs are immutable instructional constraints. Do not reclassify their role, add facets, "
            "or override SemanticDelta. Follow each writing_contract and do not re-teach must_not_reteach_facets.",
            "",
            "OccurrenceWritingBriefs (authoritative):",
            brief_blocks or "- none supplied; use the normal evidence-writing contract.",
            "",
            "Evidence chunks:",
            "\n\n".join(evidence_blocks),
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _render_writing_briefs(briefs: list[OccurrenceWritingBrief], chapter_ids: set[str]) -> str:
    lines: list[str] = []
    for brief in briefs:
        if brief.chapter_id not in chapter_ids:
            continue
        lines.extend(
            [
                f"- occurrence_id: {brief.occurrence_id}",
                f"  source/canonical: {brief.source_title} / {brief.canonical_title}",
                f"  role: {brief.role}",
                f"  already_available_facets: {', '.join(brief.already_available_facets) or 'none'}",
                f"  required_facets: {', '.join(brief.required_facets) or 'none'}",
                f"  must_teach_facets: {', '.join(brief.must_teach_facets) or 'none'}",
                f"  must_not_reteach_facets: {', '.join(brief.must_not_reteach_facets) or 'none'}",
                f"  extension_keys: {', '.join(brief.extension_keys) or 'none'}",
                f"  repeated_aspects_to_avoid: {', '.join(brief.repeated_aspects_to_avoid) or 'none'}",
                f"  prerequisite_context: {'; '.join(brief.prerequisite_context) or 'none'}",
                f"  contribution_goal: {brief.contribution_goal or 'none'}",
                f"  source_chunk_ids: {', '.join(brief.source_chunk_ids) or 'none'}",
                f"  writing_contract: {brief.writing_contract}",
                f"  allowed_content: {'; '.join(brief.allowed_content) or 'none'}",
                f"  forbidden_content: {'; '.join(brief.forbidden_content) or 'none'}",
                f"  max_recap_sentences: {brief.max_recap_sentences}",
                f"  must_include_points: {'; '.join(brief.must_include_points) or 'none'}",
                f"  must_avoid_patterns: {'; '.join(brief.must_avoid_patterns) or 'none'}",
            ]
        )
    return "\n".join(lines)


def build_occurrence_writer_messages(
    brief: OccurrenceWritingBrief,
    chunks: list[EvidenceChunk],
    title: str,
    domain_config: DomainConfig | None = None,
) -> list[dict[str, str]]:
    """Prompt one occurrence body; anchors are added by code after generation."""
    config = domain_config or default_domain_config()
    selected = [item for item in chunks if item.chunk_id in set(brief.source_chunk_ids)]
    selected = selected or chunks
    evidence = []
    for chunk in selected:
        content = shorten(" ".join(chunk.content.split()), width=1200, placeholder="...")
        evidence.append(
            "\n".join([
                f"- chunk_id: {chunk.chunk_id}",
                f"  review_status: {chunk.review_status}",
                f"  summary: {chunk.summary}",
                f"  evidence: {content}",
            ])
        )
    system = (
        "You write one evidence-grounded vocational textbook occurrence. "
        "The occurrence brief is an immutable execution contract. Do not change its role, infer a new facet, "
        "or replace its allowed/forbidden text behaviours. Output Markdown body only: no document title, "
        "no occurrence markers, and no explanation of these instructions."
    )
    user = "\n".join([
        f"Textbook: {title}",
        f"Audience: {config.audience}",
        "",
        "Authoritative occurrence brief:",
        f"- occurrence_id: {brief.occurrence_id}",
        f"- source title: {brief.source_title}",
        f"- canonical title: {brief.canonical_title}",
        f"- role: {brief.role}",
        f"- already available facets: {', '.join(brief.already_available_facets) or 'none'}",
        f"- required facets: {', '.join(brief.required_facets) or 'none'}",
        f"- must teach facets: {', '.join(brief.must_teach_facets) or 'none'}",
        f"- must not reteach facets: {', '.join(brief.must_not_reteach_facets) or 'none'}",
        f"- extension keys: {', '.join(brief.extension_keys) or 'none'}",
        f"- repeated aspects to avoid: {', '.join(brief.repeated_aspects_to_avoid) or 'none'}",
        f"- allowed content: {'; '.join(brief.allowed_content)}",
        f"- forbidden content: {'; '.join(brief.forbidden_content) or 'none'}",
        f"- maximum recap sentences: {brief.max_recap_sentences}",
        f"- must include: {'; '.join(brief.must_include_points) or 'none'}",
        f"- must avoid patterns: {'; '.join(brief.must_avoid_patterns) or 'none'}",
        f"- contribution goal: {brief.contribution_goal}",
        f"- writing contract: {brief.writing_contract}",
        "",
        "Execution requirements:",
        "1. Use only the supplied evidence, but do not print chunk IDs, `Evidence:` labels, or any internal provenance syntax. Provenance is attached by the renderer.",
        "2. Do not emit a definition, principle explanation, full procedure, or parameter/method rule if it is forbidden.",
        "3. If there is no new facet and no extension, write only the allowed transition/task context and at most the stated recap limit.",
        "4. Do not create a complete project chapter. Render only this occurrence body.",
        "5. Name the source or canonical subject explicitly and use the supplied evidence; never replace it with generic vocational-education commentary.",
        "6. Write in the same language as the source title.",
        "7. Role-specific hard limits:",
        _role_execution_requirements(brief),
        "",
        "Evidence:",
        "\n\n".join(evidence),
    ])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _role_execution_requirements(brief: OccurrenceWritingBrief) -> str:
    """Executable constraints, derived from the immutable brief rather than model judgement."""
    if brief.role == "INTRO":
        return (
            "INTRO: start with `学习方向：` and write no more than two short sentences that establish only an initial observation. "
            "Do not define the subject, explain its mechanism, describe a procedure, or state effects/parameters."
        )
    if brief.role == "RECALL":
        return (
            f"RECALL: write exactly one substantive sentence (never more than {brief.max_recap_sentences}). Restore only the named prior "
            "context; do not introduce an alternative method, steps, parameter values, or new explanation."
        )
    if brief.role == "APPLY":
        return (
            "APPLY: explicitly name the already learned canonical knowledge, name the concrete current-task action or observation, "
            "and state the application relation between them. Do not define, explain, or teach the known method again."
        )
    if brief.role == "EXTEND":
        return (
            "EXTEND: state the already-known bridge in one sentence, then teach only the new condition/constraint. "
            "Include the word `分析` and, when the brief contains an abnormal-condition key, write the exact bilingual label "
            "`异常条件（abnormal condition）`. "
            "Do not provide numeric parameter ranges, say how to adjust/control a parameter, use `根据材料厚度`, give adjustment rules, "
            "a full procedure, or a second complete method explanation."
        )
    if not brief.must_teach_facets and not brief.extension_keys:
        return "Duplicate-TEACH risk: write exactly one transition/task-context sentence; do not repeat teaching content."
    return "TEACH: explicitly use `原理` or `作用` (or an English definition/principle/effect statement) for every must-teach facet."
