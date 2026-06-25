from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Any

from materials2textbook.domain_config import DomainConfig, parse_json_object
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.book_plan import build_book_plan_messages
from materials2textbook.schemas import BookChapterPlan, BookPlan, BookSectionPlan, EvidenceChunk, ReviewIssue


MIN_CHAPTERS = 3
MAX_CHAPTERS = 12
MIN_SECTIONS_PER_CHAPTER = 3


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
        used.update(material_ids)
        points = _string_list(raw_section.get("knowledge_points")) or [_text(raw_section.get("title")) or f"Topic {section_index}"]
        sections.append(
            BookSectionPlan(
                section_id=f"{chapter_id}_section_{section_index:02d}",
                section_no=_text(raw_section.get("section_no")) or f"{chapter_index}.{section_index}",
                title=_text(raw_section.get("title")) or points[0],
                knowledge_point_ids=points,
                primary_material_ids=material_ids,
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
        used.update(ids)
        result.append(
            BookSectionPlan(
                section_id=f"{chapter_id}_section_{section_index:02d}",
                section_no=f"{chapter_index}.{section_index}",
                title=title,
                knowledge_point_ids=[title],
                primary_material_ids=ids,
            )
        )
    while len(result) < MIN_SECTIONS_PER_CHAPTER:
        section_index = len(result) + 1
        result.append(
            BookSectionPlan(
                section_id=f"{chapter_id}_section_{section_index:02d}",
                section_no=f"{chapter_index}.{section_index}",
                title=f"{chapter_title} evidence gap {section_index}",
                knowledge_point_ids=[f"{chapter_title} evidence gap {section_index}"],
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


def _slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return slug or "digital-textbook"
