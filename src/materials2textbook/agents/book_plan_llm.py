from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any

from materials2textbook.domain_config import DomainConfig, parse_json_object
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.book_plan import build_book_plan_messages
from materials2textbook.schemas import BookChapterPlan, BookPlan, BookSectionPlan, EvidenceChunk, ReviewIssue


MIN_CHAPTERS = 3
MAX_CHAPTERS = 12
MIN_SECTIONS_PER_CHAPTER = 3
MAX_SECTIONS_PER_CHAPTER = 8
MIN_MAJOR_BLOCK_CHUNKS = 5
TEXTBOOK_STRUCTURE = "project_task"
TASK_MODULES = ["学习导航", "情境导入", "任务实施", "任务评价", "思考与练习"]
PROJECT_MODULES = ["项目导学", "能力图谱", "学习目标", "项目小结"]


class BookPlanLLMAgent:
    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm
        self.last_mode = "disabled"
        self.last_warning = ""

    def run(
        self,
        *,
        title: str,
        chunks: list[EvidenceChunk],
        domain_config: DomainConfig,
        max_chapters: int = 0,
        chapter_token_budget: int = 12000,
    ) -> tuple[BookPlan | None, list[ReviewIssue]]:
        if not self.use_llm or self.llm_provider is None:
            self.last_mode = "disabled"
            return None, []
        try:
            raw = self.llm_provider.generate(
                build_book_plan_messages(
                    title=title,
                    chunks=chunks,
                    domain_config=domain_config,
                    max_chapters=max_chapters or MAX_CHAPTERS,
                )
            )
            payload = parse_json_object(raw)
            plan, issues = book_plan_from_llm_payload(
                payload,
                title=title,
                chunks=chunks,
                domain_config=domain_config,
                max_chapters=max_chapters or MAX_CHAPTERS,
                chapter_token_budget=chapter_token_budget,
            )
            self.last_mode = "llm"
            self.last_warning = ""
            return plan, issues
        except Exception as exc:  # pragma: no cover - exact provider failures vary.
            self.last_mode = "failed"
            self.last_warning = f"LLM book planning failed: {exc}"
            return None, [ReviewIssue("medium", "book_plan", self.last_warning, "Use rule fallback planning.")]


def book_plan_from_llm_payload(
    payload: dict[str, Any],
    *,
    title: str,
    chunks: list[EvidenceChunk],
    domain_config: DomainConfig,
    max_chapters: int = MAX_CHAPTERS,
    chapter_token_budget: int = 12000,
) -> tuple[BookPlan, list[ReviewIssue]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    used: set[str] = set()
    issues: list[ReviewIssue] = []
    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ValueError("LLM book plan must contain chapters.")

    book_chapters: list[BookChapterPlan] = []
    for index, raw_chapter in enumerate(raw_chapters[:max_chapters], start=1):
        if not isinstance(raw_chapter, dict):
            continue
        chapter_title = _text(raw_chapter.get("title")) or f"Chapter {index}"
        raw_sections = raw_chapter.get("sections")
        sections = _sections_from_raw(
            raw_sections if isinstance(raw_sections, list) else [],
            chapter_index=index,
            chapter_id=f"chapter_{index:02d}",
            chunk_map=chunk_map,
            used=used,
        )
        if len(sections) < MIN_SECTIONS_PER_CHAPTER:
            issues.append(
                ReviewIssue(
                    "medium",
                    f"chapter_{index:02d}",
                    "section_count_below_target",
                    "The planner filled missing sections from available evidence when possible.",
                )
            )
            sections = _fill_sections(
                sections,
                chapter_index=index,
                chapter_id=f"chapter_{index:02d}",
                chapter_title=chapter_title,
                chunks=[chunk for chunk in chunks if chunk.chunk_id not in used],
                used=used,
            )
        primary_ids = _dedupe([mid for section in sections for mid in section.primary_material_ids])
        goals = _string_list(raw_chapter.get("learning_goals"))[:4] or [
            f"Understand the core concepts and learning tasks of {chapter_title}.",
            f"Explain key knowledge points in {chapter_title} using textbook evidence.",
        ]
        book_chapters.append(
            BookChapterPlan(
                chapter_id=f"chapter_{index:02d}",
                chapter_no=index,
                title=chapter_title,
                learning_goals=goals,
                sections=sections,
                primary_material_ids=primary_ids,
                reference_material_ids=[],
                token_budget=chapter_token_budget,
            )
        )

    if len(book_chapters) < MIN_CHAPTERS:
        issues.append(ReviewIssue("high", "book_plan", "chapter_count_below_target", "Rule fallback should rebuild the plan."))
    chapters_below = sum(1 for chapter in book_chapters if len(chapter.sections) < MIN_SECTIONS_PER_CHAPTER)
    if book_chapters and chapters_below / len(book_chapters) > 1 / 3:
        issues.append(ReviewIssue("high", "book_plan", "too_many_chapters_below_section_target", "Rule fallback should rebuild the plan."))

    plan = BookPlan(
        book_id=_slugify(title),
        title=_text(payload.get("title")) or title,
        planning_strategy="llm_auto_plan",
        chapters=book_chapters,
        material_stats={"evidence_chunks": len(chunks), "llm_planned_chapters": len(book_chapters)},
        budget={"chapter_token_budget": chapter_token_budget},
        metadata={
            "domain_config": domain_config.to_dict(),
            "planning_mode": "llm",
            "textbook_structure": TEXTBOOK_STRUCTURE,
            "front_matter": ["总序", "前言"],
            "project_modules": PROJECT_MODULES,
            "task_modules": TASK_MODULES,
            "min_sections_per_chapter": MIN_SECTIONS_PER_CHAPTER,
        },
    )
    return plan, issues


def book_plan_from_dict(
    payload: dict[str, Any],
    *,
    title: str = "",
    chapter_token_budget: int = 12000,
) -> BookPlan:
    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ValueError("Book plan JSON must contain a non-empty chapters list.")
    chapters: list[BookChapterPlan] = []
    for chapter_index, raw_chapter in enumerate(raw_chapters, start=1):
        if not isinstance(raw_chapter, dict):
            continue
        raw_sections = raw_chapter.get("sections")
        sections: list[BookSectionPlan] = []
        if isinstance(raw_sections, list):
            for section_index, raw_section in enumerate(raw_sections, start=1):
                if not isinstance(raw_section, dict):
                    continue
                knowledge_points = _string_list(raw_section.get("knowledge_points"))
                if not knowledge_points:
                    knowledge_points = _string_list(raw_section.get("knowledge_point_ids"))
                section_no = _text(raw_section.get("section_no")) or f"{chapter_index}.{section_index}"
                sections.append(
                    BookSectionPlan(
                        section_id=_text(raw_section.get("section_id")) or f"chapter_{chapter_index:02d}_section_{section_index:02d}",
                        section_no=section_no,
                        title=_text(raw_section.get("title")) or (knowledge_points[0] if knowledge_points else f"Section {section_no}"),
                        knowledge_point_ids=knowledge_points or [_text(raw_section.get("title")) or f"Section {section_no}"],
                        primary_material_ids=_string_list(raw_section.get("primary_material_ids")),
                        reference_material_ids=_string_list(raw_section.get("reference_material_ids")),
                        recommended_video_ids=_string_list(raw_section.get("recommended_video_ids")),
                    )
                )
        chapter_no = int(raw_chapter.get("chapter_no") or chapter_index)
        chapter_id = _text(raw_chapter.get("chapter_id")) or f"chapter_{chapter_no:02d}"
        primary_ids = _string_list(raw_chapter.get("primary_material_ids")) or _dedupe(
            [mid for section in sections for mid in section.primary_material_ids]
        )
        chapters.append(
            BookChapterPlan(
                chapter_id=chapter_id,
                chapter_no=chapter_no,
                title=_text(raw_chapter.get("title")) or f"Chapter {chapter_no}",
                learning_goals=_string_list(raw_chapter.get("learning_goals")) or [
                    f"Understand the core concepts and learning tasks of chapter {chapter_no}."
                ],
                sections=sections,
                primary_material_ids=primary_ids,
                reference_material_ids=_string_list(raw_chapter.get("reference_material_ids")),
                token_budget=int(raw_chapter.get("token_budget") or chapter_token_budget),
                video_budget=int(raw_chapter.get("video_budget") or 3),
                document_budget=int(raw_chapter.get("document_budget") or 20),
            )
        )
    if not chapters:
        raise ValueError("Book plan JSON did not contain usable chapters.")
    return BookPlan(
        book_id=_text(payload.get("book_id")) or _slugify(title or _text(payload.get("title"))),
        title=_text(payload.get("title")) or title or "Digital Textbook",
        planning_strategy=_text(payload.get("planning_strategy")) or "external_book_plan",
        chapters=chapters,
        material_stats=payload.get("material_stats") if isinstance(payload.get("material_stats"), dict) else {},
        budget=payload.get("budget") if isinstance(payload.get("budget"), dict) else {"chapter_token_budget": chapter_token_budget},
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def plan_has_blocking_issues(issues: list[ReviewIssue]) -> bool:
    return any(issue.severity == "high" for issue in issues)


def enforce_minimum_sections(book_plan: BookPlan, chunks: list[EvidenceChunk]) -> tuple[BookPlan, list[ReviewIssue]]:
    issues: list[ReviewIssue] = []
    used = {mid for chapter in book_plan.chapters for mid in chapter.primary_material_ids}
    new_chapters: list[BookChapterPlan] = []
    for chapter in book_plan.chapters:
        sections = list(chapter.sections)
        if len(sections) < MIN_SECTIONS_PER_CHAPTER:
            issues.append(
                ReviewIssue(
                    "medium",
                    chapter.chapter_id,
                    "section_count_below_target",
                    "The chapter has fewer than 3 sections after automatic planning.",
                )
            )
            sections = _fill_sections(
                sections,
                chapter_index=chapter.chapter_no,
                chapter_id=chapter.chapter_id,
                chapter_title=chapter.title,
                chunks=[chunk for chunk in chunks if chunk.chunk_id not in used],
                used=used,
            )
        new_chapters.append(
            replace(
                chapter,
                sections=sections,
                primary_material_ids=_dedupe(
                    chapter.primary_material_ids
                    + [
                        mid
                        for section in sections
                        for mid in section.primary_material_ids
                        if mid not in chapter.reference_material_ids
                    ]
                ),
            )
        )
    return replace(book_plan, chapters=new_chapters), issues


def enforce_material_block_coverage(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    *,
    max_chapters: int = MAX_CHAPTERS,
    chapter_token_budget: int = 12000,
) -> tuple[BookPlan, list[ReviewIssue]]:
    effective_max = max_chapters or MAX_CHAPTERS
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    used_ids = {mid for chapter in book_plan.chapters for mid in chapter.primary_material_ids}
    covered_blocks = _covered_material_blocks(book_plan, chunk_map)
    block_counts = Counter(_block_name(chunk) for chunk in chunks if _block_name(chunk))
    missing_blocks = [
        block
        for block, count in block_counts.most_common()
        if count >= MIN_MAJOR_BLOCK_CHUNKS and block not in covered_blocks
    ]

    if not missing_blocks or len(book_plan.chapters) >= effective_max:
        return book_plan, []

    issues: list[ReviewIssue] = []
    chapters = list(book_plan.chapters)
    for block in missing_blocks:
        if len(chapters) >= effective_max:
            break
        block_chunks = [
            chunk
            for chunk in chunks
            if _block_name(chunk) == block and chunk.chunk_id and chunk.chunk_id not in used_ids
        ]
        if not block_chunks:
            continue
        chapter_no = len(chapters) + 1
        chapter_id = f"chapter_{chapter_no:02d}"
        sections = _fill_sections(
            [],
            chapter_index=chapter_no,
            chapter_id=chapter_id,
            chapter_title=block,
            chunks=block_chunks,
            used=used_ids,
        )
        primary_ids = _dedupe([mid for section in sections for mid in section.primary_material_ids])
        chapters.append(
            BookChapterPlan(
                chapter_id=chapter_id,
                chapter_no=chapter_no,
                title=block,
                learning_goals=[
                    f"Understand the core concepts and learning tasks of {block}.",
                    f"Complete practical tasks in {block} using video and document evidence.",
                ],
                sections=sections,
                primary_material_ids=primary_ids,
                reference_material_ids=[],
                token_budget=chapter_token_budget,
            )
        )
        issues.append(
            ReviewIssue(
                "medium",
                chapter_id,
                f"added_missing_material_block:{block}",
                "The automatic planner omitted a major material block; a project was added from available evidence.",
            )
        )

    return replace(book_plan, chapters=chapters), issues


def expand_tasks_by_material_density(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    *,
    max_sections_per_chapter: int = MAX_SECTIONS_PER_CHAPTER,
) -> tuple[BookPlan, list[ReviewIssue]]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    all_blocks = {_block_name(chunk) for chunk in chunks if _block_name(chunk)}
    issues: list[ReviewIssue] = []
    new_chapters: list[BookChapterPlan] = []
    for chapter in book_plan.chapters:
        chapter_blocks = _chapter_blocks(chapter, chunk_map, all_blocks)
        candidates = [
            chunk
            for chunk in chunks
            if chunk.chunk_id and (not chapter_blocks or _block_name(chunk) in chapter_blocks)
        ]
        workflow_groups = _project_workflow_task_groups(chapter.title, candidates)
        target_sections = min(
            max_sections_per_chapter,
            _target_task_count(len(candidates)),
            max(MIN_SECTIONS_PER_CHAPTER, len(workflow_groups)),
        )
        source_sections = [
            section
            for section in chapter.sections
            if "task evidence gap" not in section.title and not _looks_bad_task_title(section.title)
        ]
        source_ids = [mid for section in source_sections for mid in section.primary_material_ids]
        sections: list[BookSectionPlan] = []
        used_ids: set[str] = set()
        used_titles: set[str] = set()

        for title, grouped_chunks in workflow_groups:
            if len(sections) >= target_sections:
                break
            if _is_bad_task_title(title, chapter_blocks, all_blocks):
                continue
            if _normalize_title(title) in used_titles:
                continue
            section_index = len(sections) + 1
            local_used = set(used_ids)
            section_chunks = _pick_representative_chunks(grouped_chunks, local_used, 6)
            if not section_chunks:
                section_chunks = _pick_representative_chunks(grouped_chunks or candidates, set(), 6)
            if not section_chunks:
                continue
            if not any(chunk.source_type == "video_segment" for chunk in section_chunks):
                video_chunk = _best_video_chunk([*grouped_chunks, *candidates])
                if video_chunk and video_chunk.chunk_id not in {chunk.chunk_id for chunk in section_chunks}:
                    section_chunks = [video_chunk, *section_chunks[:5]]
            fallback_ids = [
                mid
                for mid in source_ids
                if mid in chunk_map and mid not in {chunk.chunk_id for chunk in section_chunks}
            ][: max(0, 6 - len(section_chunks))]
            for mid in fallback_ids:
                if mid in chunk_map:
                    section_chunks.append(chunk_map[mid])
            ids = [chunk.chunk_id for chunk in section_chunks]
            video_ids = [chunk.chunk_id for chunk in section_chunks if chunk.source_type == "video_segment"][:3]
            sections.append(
                BookSectionPlan(
                    section_id=f"{chapter.chapter_id}_section_{section_index:02d}",
                    section_no=f"{chapter.chapter_no}.{section_index}",
                    title=title,
                    knowledge_point_ids=[title],
                    primary_material_ids=ids,
                    recommended_video_ids=video_ids,
                )
            )
            used_ids.update(ids)
            used_titles.add(_normalize_title(title))
            issues.append(
                ReviewIssue(
                    "medium",
                    f"{chapter.chapter_id}_section_{section_index:02d}",
                    f"added_density_task:{title}",
                    "The material block has enough evidence for more tasks; a project-workflow task was added from high-quality evidence.",
                )
            )

        for section in source_sections:
            if len(sections) >= target_sections:
                break
            if _normalize_title(section.title) in used_titles:
                continue
            section_index = len(sections) + 1
            section_ids = [mid for mid in section.primary_material_ids if mid in chunk_map and mid not in used_ids]
            if not section_ids:
                continue
            sections.append(
                replace(
                    section,
                    section_id=f"{chapter.chapter_id}_section_{section_index:02d}",
                    section_no=f"{chapter.chapter_no}.{section_index}",
                    primary_material_ids=section_ids,
                    recommended_video_ids=[
                        mid for mid in section_ids if mid in chunk_map and chunk_map[mid].source_type == "video_segment"
                    ][:3],
                )
            )
            used_ids.update(section_ids)
            used_titles.add(_normalize_title(section.title))

        primary_ids = _dedupe(chapter.primary_material_ids + [mid for section in sections for mid in section.primary_material_ids])
        new_chapters.append(replace(chapter, sections=sections, primary_material_ids=primary_ids))

    return replace(book_plan, chapters=new_chapters), issues


def enrich_chapter_evidence(
    book_plan: BookPlan,
    chunks: list[EvidenceChunk],
    *,
    min_materials_per_chapter: int = 8,
    max_materials_per_section: int = 6,
) -> BookPlan:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id}
    all_blocks = {_block_name(chunk) for chunk in chunks if _block_name(chunk)}
    new_chapters: list[BookChapterPlan] = []
    for chapter in book_plan.chapters:
        chapter_blocks = _chapter_blocks(chapter, chunk_map, all_blocks)
        candidates = [
            chunk
            for chunk in chunks
            if chunk.chunk_id and (not chapter_blocks or _block_name(chunk) in chapter_blocks)
        ]
        sections: list[BookSectionPlan] = []
        used = set(chapter.primary_material_ids)
        for section in chapter.sections:
            section_ids = list(section.primary_material_ids)
            section_video_ids = list(section.recommended_video_ids)
            target_count = max(max_materials_per_section, len(section_ids))
            additions = _pick_representative_chunks(candidates, used | set(section_ids), target_count - len(section_ids))
            section_ids = _dedupe(section_ids + [chunk.chunk_id for chunk in additions])
            section_video_ids = _dedupe(
                section_video_ids
                + [
                    chunk_id
                    for chunk_id in section_ids
                    if chunk_id in chunk_map and chunk_map[chunk_id].source_type == "video_segment"
                ]
            )
            used.update(section_ids)
            sections.append(
                replace(
                    section,
                    primary_material_ids=section_ids,
                    recommended_video_ids=section_video_ids,
                )
            )

        primary_ids = _dedupe([mid for section in sections for mid in section.primary_material_ids])
        if len(primary_ids) < min_materials_per_chapter:
            additions = _pick_representative_chunks(candidates, set(primary_ids), min_materials_per_chapter - len(primary_ids))
            primary_ids = _dedupe(primary_ids + [chunk.chunk_id for chunk in additions])
            if sections:
                first = sections[0]
                first_ids = _dedupe(first.primary_material_ids + [chunk.chunk_id for chunk in additions])
                first_videos = _dedupe(
                    first.recommended_video_ids
                    + [chunk.chunk_id for chunk in additions if chunk.source_type == "video_segment"]
                )
                sections[0] = replace(first, primary_material_ids=first_ids, recommended_video_ids=first_videos)

        new_chapters.append(replace(chapter, sections=sections, primary_material_ids=primary_ids))
    return replace(book_plan, chapters=new_chapters)


def render_auto_book_plan_review(title: str, issues: list[ReviewIssue], *, planning_mode: str, warning: str = "") -> str:
    lines = [f"# {title} automatic book plan review", "", f"- planning_mode: {planning_mode}"]
    if warning:
        lines.append(f"- warning: {warning}")
    if not issues:
        lines.append("- no blocking automatic planning issues found")
    for issue in issues:
        lines.append(f"- [{issue.severity}] {issue.location}: {issue.message}; suggestion: {issue.suggestion}")
    return "\n".join(lines) + "\n"


def _sections_from_raw(
    raw_sections: list[Any],
    *,
    chapter_index: int,
    chapter_id: str,
    chunk_map: dict[str, EvidenceChunk],
    used: set[str],
) -> list[BookSectionPlan]:
    sections: list[BookSectionPlan] = []
    for section_index, raw_section in enumerate(raw_sections, start=1):
        if not isinstance(raw_section, dict):
            continue
        material_ids = [
            chunk_id
            for chunk_id in _string_list(raw_section.get("primary_material_ids"))
            if chunk_id in chunk_map
        ]
        recommended_video_ids = [
            chunk_id
            for chunk_id in _string_list(raw_section.get("recommended_video_ids"))
            if chunk_id in chunk_map and chunk_map[chunk_id].source_type == "video_segment"
        ]
        recommended_video_ids.extend(
            chunk_id for chunk_id in material_ids if chunk_map[chunk_id].source_type == "video_segment"
        )
        used.update(material_ids)
        points = _string_list(raw_section.get("knowledge_points")) or [_text(raw_section.get("title")) or f"Topic {section_index}"]
        sections.append(
            BookSectionPlan(
                section_id=f"{chapter_id}_section_{section_index:02d}",
                section_no=_text(raw_section.get("section_no")) or f"{chapter_index}.{section_index}",
                title=_text(raw_section.get("title")) or points[0],
                knowledge_point_ids=points,
                primary_material_ids=material_ids,
                recommended_video_ids=_dedupe(recommended_video_ids),
            )
        )
    return sections


def _fill_sections(
    sections: list[BookSectionPlan],
    *,
    chapter_index: int,
    chapter_id: str,
    chapter_title: str,
    chunks: list[EvidenceChunk],
    used: set[str],
) -> list[BookSectionPlan]:
    result = list(sections)
    grouped: dict[str, list[EvidenceChunk]] = defaultdict(list)
    for chunk in chunks:
        if not chunk.chunk_id or chunk.chunk_id in used:
            continue
        key = chunk.title or chunk.material_block or chapter_title
        grouped[key].append(chunk)
    for title, grouped_chunks in grouped.items():
        if len(result) >= MIN_SECTIONS_PER_CHAPTER:
            break
        section_index = len(result) + 1
        ids = [chunk.chunk_id for chunk in grouped_chunks[:4] if chunk.chunk_id]
        video_ids = [chunk.chunk_id for chunk in grouped_chunks if chunk.chunk_id and chunk.source_type == "video_segment"][:2]
        used.update(ids)
        result.append(
            BookSectionPlan(
                section_id=f"{chapter_id}_section_{section_index:02d}",
                section_no=f"{chapter_index}.{section_index}",
                title=title,
                knowledge_point_ids=[title],
                primary_material_ids=ids,
                recommended_video_ids=video_ids,
            )
        )
    while len(result) < MIN_SECTIONS_PER_CHAPTER:
        section_index = len(result) + 1
        result.append(
            BookSectionPlan(
                section_id=f"{chapter_id}_section_{section_index:02d}",
                section_no=f"{chapter_index}.{section_index}",
                title=f"{chapter_title} task evidence gap {section_index}",
                knowledge_point_ids=[f"{chapter_title} task evidence gap {section_index}"],
                primary_material_ids=[],
            )
        )
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _pick_representative_chunks(chunks: list[EvidenceChunk], used: set[str], limit: int) -> list[EvidenceChunk]:
    if limit <= 0:
        return []
    selected: list[EvidenceChunk] = []
    ranked_chunks = sorted(chunks, key=_quality_key, reverse=True)
    for source_type in ("video_segment", "ppt_slide", "reference_text", "audio_segment", "structured_asset"):
        for chunk in ranked_chunks:
            if len(selected) >= limit:
                return selected
            if not chunk.chunk_id or chunk.chunk_id in used:
                continue
            if chunk.source_type != source_type:
                continue
            selected.append(chunk)
            used.add(chunk.chunk_id)
            break
    for chunk in ranked_chunks:
        if len(selected) >= limit:
            break
        if chunk.chunk_id and chunk.chunk_id not in used:
            selected.append(chunk)
            used.add(chunk.chunk_id)
    return selected


def _chapter_blocks(chapter: BookChapterPlan, chunk_map: dict[str, EvidenceChunk], all_blocks: set[str]) -> set[str]:
    blocks = {
        _block_name(chunk_map[mid])
        for mid in chapter.primary_material_ids
        if mid in chunk_map and _block_name(chunk_map[mid])
    }
    blocks.update(block for block in all_blocks if block and (block == chapter.title or block in chapter.title or chapter.title in block))
    return blocks


def _target_task_count(candidate_count: int) -> int:
    if candidate_count >= 2500:
        return 8
    if candidate_count >= 1000:
        return 7
    if candidate_count >= 500:
        return 5
    if candidate_count >= 100:
        return 4
    return MIN_SECTIONS_PER_CHAPTER


def _project_workflow_task_groups(chapter_title: str, chunks: list[EvidenceChunk]) -> list[tuple[str, list[EvidenceChunk]]]:
    ranked_chunks = sorted(chunks, key=_quality_key, reverse=True)
    groups: list[tuple[str, list[EvidenceChunk]]] = []
    for title, keywords in _project_workflow_templates(chapter_title):
        matched = [
            chunk
            for chunk in ranked_chunks
            if chunk.chunk_id
            and _chunk_matches_keywords(chunk, keywords)
        ]
        if not matched:
            matched = [
                chunk
                for chunk in ranked_chunks
                if chunk.chunk_id
            ][:12]
        if not matched:
            continue
        groups.append((title, matched))
    return groups


def _best_video_chunk(chunks: list[EvidenceChunk]) -> EvidenceChunk | None:
    videos = [chunk for chunk in chunks if chunk.chunk_id and chunk.source_type == "video_segment"]
    if not videos:
        return None
    return max(videos, key=_quality_key)


def _project_workflow_templates(chapter_title: str) -> list[tuple[str, list[str]]]:
    normalized = _normalize_title(chapter_title)
    if "气焊" in chapter_title or "气割" in chapter_title:
        return [
            ("气焊与气割设备检查与安全准备", ["设备", "检查", "安全", "防护", "氧气", "乙炔"]),
            ("气焊火焰调节与焊前准备", ["火焰", "调节", "点火", "焊前", "准备"]),
            ("气焊操作过程与焊缝成形控制", ["气焊", "操作", "焊缝", "成形", "熔池"]),
            ("气割参数设置与切割操作", ["气割", "切割", "参数", "割炬", "割嘴"]),
            ("气焊气割质量检查与故障处理", ["质量", "缺陷", "检查", "故障", "处理"]),
        ]
    if "钨极" in chapter_title or "氩弧" in chapter_title or "tig" in normalized:
        return [
            ("钨极氩弧焊设备与材料准备", ["设备", "材料", "钨极", "氩气", "准备"]),
            ("钨极氩弧焊工艺参数选择", ["参数", "电流", "电压", "气体", "流量"]),
            ("钨极氩弧焊引弧与焊枪操作", ["引弧", "焊枪", "操作", "角度", "送丝"]),
            ("钨极氩弧焊焊接过程控制", ["熔池", "焊缝", "速度", "控制", "成形"]),
            ("钨极氩弧焊质量检验与缺陷预防", ["质量", "缺陷", "检验", "预防", "气孔"]),
        ]
    if "焊条" in chapter_title or "电弧焊" in chapter_title:
        return [
            ("焊条电弧焊设备检查与焊前准备", ["设备", "检查", "焊前", "准备", "安全"]),
            ("焊条与焊接参数选择", ["焊条", "参数", "电流", "电压", "选择"]),
            ("焊条电弧焊引弧与运条操作", ["引弧", "运条", "操作", "焊接"]),
            ("焊条电弧焊焊道成形与顺序控制", ["焊道", "成形", "顺序", "变形", "控制"]),
            ("焊条电弧焊质量检查与缺陷处理", ["质量", "缺陷", "检查", "处理", "裂纹"]),
        ]
    if "基本操作" in chapter_title:
        return [
            ("焊接操作前准备与安全确认", ["准备", "安全", "防护", "检查"]),
            ("焊接接头装配与定位", ["接头", "装配", "定位", "坡口"]),
            ("焊接参数设置与试焊", ["参数", "电流", "电压", "试焊"]),
            ("焊接过程中的熔池与焊枪控制", ["熔池", "焊枪", "控制", "速度"]),
            ("焊缝外观检查与质量判断", ["焊缝", "外观", "检查", "质量"]),
            ("常见操作问题分析与纠正", ["问题", "缺陷", "纠正", "原因"]),
        ]
    if "设备" in chapter_title or "安全" in chapter_title:
        return [
            ("焊接设备认知与工作场地布置", ["设备", "组成", "场地", "布置"]),
            ("焊接安全防护用品选用", ["安全", "防护", "用品", "面罩"]),
            ("焊接设备安装检查与通电准备", ["安装", "检查", "通电", "准备"]),
            ("焊接作业风险识别与防控", ["风险", "安全", "防控", "规范"]),
            ("焊接设备使用后的断电与维护", ["断电", "维护", "关闭", "保养"]),
        ]
    return [
        (f"{chapter_title}项目准备与安全确认", ["准备", "安全", "检查"]),
        (f"{chapter_title}工艺参数与材料选择", ["参数", "材料", "选择"]),
        (f"{chapter_title}核心操作实施", ["操作", "实施", "过程"]),
        (f"{chapter_title}过程控制与质量检查", ["控制", "质量", "检查"]),
        (f"{chapter_title}问题分析与改进", ["问题", "缺陷", "改进"]),
    ]


def _chunk_matches_keywords(chunk: EvidenceChunk, keywords: list[str]) -> bool:
    text = " ".join(
        [
            chunk.title or "",
            chunk.summary or "",
            chunk.content[:500] if chunk.content else "",
            " ".join(str(keyword) for keyword in chunk.keywords),
        ]
    )
    return any(keyword and keyword in text for keyword in keywords)


def _ranked_task_groups(chunks: list[EvidenceChunk]) -> list[tuple[str, list[EvidenceChunk]]]:
    grouped: dict[str, list[EvidenceChunk]] = defaultdict(list)
    for chunk in chunks:
        title = _task_title(chunk)
        if title:
            grouped[title].append(chunk)
    return sorted(
        grouped.items(),
        key=lambda item: (
            len(item[1]),
            sum(_quality_key(chunk)[0] for chunk in item[1]) / max(len(item[1]), 1),
            sum(_quality_key(chunk)[1] for chunk in item[1]) / max(len(item[1]), 1),
        ),
        reverse=True,
    )


def _task_title(chunk: EvidenceChunk) -> str:
    title = (chunk.title or "").strip()
    if title and not _looks_bad_task_title(title) and title not in {chunk.material_block, chunk.recommended_chapter, chunk.subject}:
        return title[:80]
    summary = (chunk.summary or "").strip()
    if summary:
        for separator in ["。", "；", ";", ".", "\n"]:
            if separator in summary:
                summary = summary.split(separator, 1)[0]
                break
        if 4 <= len(summary) <= 80 and not _looks_bad_task_title(summary):
            return summary
    for keyword in chunk.keywords:
        keyword = str(keyword).strip()
        if keyword and not _looks_bad_task_title(keyword) and keyword not in {chunk.material_block, chunk.recommended_chapter, chunk.subject}:
            return keyword[:80]
    return title or chunk.material_block or chunk.recommended_chapter or chunk.subject


def _is_bad_task_title(title: str, chapter_blocks: set[str], all_blocks: set[str]) -> bool:
    normalized = _normalize_title(title)
    if not normalized:
        return True
    if _looks_bad_task_title(title):
        return True
    return any(title == block and block not in chapter_blocks for block in all_blocks)


def _looks_bad_task_title(value: str) -> bool:
    value = value.strip()
    normalized = _normalize_title(value)
    generic_titles = {"操作演示", "课程资源", "教学资源", "视频片段", "课件", "ppt", "PPT"}
    if normalized in {_normalize_title(item) for item in generic_titles}:
        return True
    bad_fragments = ["候选片段", "批处理自动生成", "批处理MVP", "修正", "术语错误", "错别字", "同音字", "繁简"]
    return any(fragment in value for fragment in bad_fragments)


def _normalize_title(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _covered_material_blocks(book_plan: BookPlan, chunk_map: dict[str, EvidenceChunk]) -> set[str]:
    covered: set[str] = set()
    known_blocks = {_block_name(chunk) for chunk in chunk_map.values() if _block_name(chunk)}
    for chapter in book_plan.chapters:
        for block in known_blocks:
            if block and (block == chapter.title or block in chapter.title or chapter.title in block):
                covered.add(block)
        for mid in chapter.primary_material_ids:
            chunk = chunk_map.get(mid)
            if chunk:
                covered.add(_block_name(chunk))
        for section in chapter.sections:
            for mid in section.primary_material_ids + section.recommended_video_ids:
                chunk = chunk_map.get(mid)
                if chunk:
                    covered.add(_block_name(chunk))
    return covered


def _block_name(chunk: EvidenceChunk) -> str:
    return (chunk.material_block or chunk.recommended_chapter or chunk.subject or "").strip()


def _quality_key(chunk: EvidenceChunk) -> tuple[float, float, float, int, int, int]:
    review_bonus = 1 if chunk.review_status in {"Agent_Keep", "Reviewed_Keep", "keep"} else 0
    source_bonus = {
        "video_segment": 3,
        "ppt_slide": 2,
        "reference_text": 2,
        "audio_segment": 1,
        "structured_asset": 1,
    }.get(chunk.source_type, 0)
    content_len = min(len(chunk.content or "") + len(chunk.summary or ""), 2000)
    title_len = min(len(chunk.title or ""), 80)
    return (
        chunk.score.teaching_value,
        chunk.score.confidence,
        chunk.score.relevance,
        review_bonus,
        source_bonus,
        content_len + title_len,
    )


def _slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return slug or "digital-textbook"
