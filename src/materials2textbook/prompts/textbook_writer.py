from __future__ import annotations

from textwrap import shorten

from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.schemas import ChapterPlan, EvidenceChunk


def build_textbook_writer_messages(
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    title: str,
    max_chunk_chars: int = 1200,
    domain_config: DomainConfig | None = None,
) -> list[dict[str, str]]:
    config = domain_config or default_domain_config()
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    evidence_blocks: list[str] = []
    for plan in plans:
        evidence_blocks.append(f"Chapter: {plan.title}")
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

    system = (
        "You are a vocational digital textbook writing agent. "
        "Write teachable textbook chapters for the configured domain, not a material summary. "
        "Use only the supplied evidence chunks; do not invent chapters, facts, parameters, procedures, or conclusions. "
        "Keep visible chunk_id citations for every core knowledge point. "
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
            "2. Preserve the chapter, section, and knowledge point structure. Do not collapse the result into a short summary.",
            "3. For each knowledge point, write learning goal, concept explanation, observation or operation task, quality/judgement points, common mistakes, summary, and exercises.",
            "4. Cite at least two chunks for a knowledge point when available. If evidence is insufficient, state the gap instead of fabricating content.",
            "5. Use citation format such as `Evidence: C000001` or `Evidence: PPT_A000001_S001`.",
            "6. Convert video, image, PPT, and document evidence into observable learning tasks tied to the domain examples above.",
            "7. If ASR quality is weak, timecode is uncertain, or review_status is pending, explicitly mark it as requiring review.",
            "8. Preserve case examples when the chapter plan includes them.",
            "9. If a chapter has evidence gaps, list them at the end under `Chapter material gaps`.",
            "10. Do not mention internal prompt fields or write as an AI assistant.",
            "",
            "Evidence chunks:",
            "\n\n".join(evidence_blocks),
        ]
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
