from __future__ import annotations

from collections import Counter
from dataclasses import replace

from materials2textbook.schemas import ChapterPlan, EvidenceChunk, KnowledgePoint, OutlineSection, OutlineTopic, TextbookOutline


class TitlePolisherAgent:
    """Conservatively polish chapter and knowledge-point titles without changing scope."""

    GENERIC_CHAPTER_TITLES = ("基本操作", "基础操作", "操作", "原理", "概述", "项目", "任务", "待规划章节")
    BASIC_SUFFIXES = ("原理", "概念", "认知", "基础", "准备", "安全")
    PRACTICE_SUFFIXES = ("操作", "操作要点", "实施", "检查", "训练")
    ADVANCED_SUFFIXES = ("分析", "应用", "评价", "适用范围", "质量控制", "案例")

    def run(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk]) -> list[ChapterPlan]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        return [self._polish_plan(plan, chunk_map) for plan in plans]

    def run_outlines(self, outlines: list[TextbookOutline], chunks: list[EvidenceChunk]) -> list[TextbookOutline]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        return [self._polish_outline(outline, chunk_map) for outline in outlines]

    def _polish_plan(self, plan: ChapterPlan, chunk_map: dict[str, EvidenceChunk]) -> ChapterPlan:
        plan_chunks = [chunk_map[chunk_id] for chunk_id in plan.evidence_chunk_ids if chunk_id in chunk_map]
        chapter_title = _polish_chapter_title(plan.title, plan_chunks, self.GENERIC_CHAPTER_TITLES)
        points = [self._polish_point(point) for point in plan.knowledge_points]
        learning_goals = [_rewrite_goal_title(goal, plan.title, chapter_title) for goal in plan.learning_goals]
        return replace(plan, title=chapter_title, learning_goals=learning_goals, knowledge_points=points)

    def _polish_point(self, point: KnowledgePoint) -> KnowledgePoint:
        title = _normalize_title(point.title)
        if point.difficulty_level == "advanced":
            title = _append_suffix_once(title, "应用分析", self.ADVANCED_SUFFIXES)
        elif point.difficulty_level == "practice":
            title = _append_suffix_once(title, "操作要点", self.PRACTICE_SUFFIXES)
        else:
            title = _append_suffix_once(title, "认知", self.BASIC_SUFFIXES)
        return replace(point, title=title)

    def _polish_outline(self, outline: TextbookOutline, chunk_map: dict[str, EvidenceChunk]) -> TextbookOutline:
        outline_chunk_ids = [chunk_id for section in outline.sections for topic in section.topics for chunk_id in topic.chunk_ids]
        outline_chunks = [chunk_map[chunk_id] for chunk_id in outline_chunk_ids if chunk_id in chunk_map]
        chapter_title = _polish_chapter_title(outline.title, outline_chunks, self.GENERIC_CHAPTER_TITLES)
        sections: list[OutlineSection] = []
        for section in outline.sections:
            section_chunks = [
                chunk_map[chunk_id]
                for topic in section.topics
                for chunk_id in topic.chunk_ids
                if chunk_id in chunk_map
            ]
            section_title = _polish_section_title(section.title, section_chunks)
            topics = [self._polish_topic(topic, chunk_map) for topic in section.topics]
            sections.append(replace(section, title=section_title, topics=topics))
        return replace(outline, title=chapter_title, sections=sections)

    def _polish_topic(self, topic: OutlineTopic, chunk_map: dict[str, EvidenceChunk]) -> OutlineTopic:
        topic_chunks = [chunk_map[chunk_id] for chunk_id in topic.chunk_ids if chunk_id in chunk_map]
        difficulty = _infer_topic_difficulty(topic.title, topic_chunks)
        point = KnowledgePoint(
            knowledge_point_id=topic.topic_id,
            title=topic.title,
            chunk_ids=topic.chunk_ids,
            difficulty_level=difficulty,
        )
        return replace(topic, title=self._polish_point(point).title)


def _polish_chapter_title(title: str, chunks: list[EvidenceChunk], generic_titles: tuple[str, ...]) -> str:
    normalized = _normalize_title(title)
    material_block = _dominant_material_block(chunks)
    if material_block and (normalized in generic_titles or normalized.startswith("待规划")):
        return f"{material_block}{normalized}" if normalized not in material_block else material_block
    return normalized


def _polish_section_title(title: str, chunks: list[EvidenceChunk]) -> str:
    normalized = _normalize_title(title)
    if normalized in {"未分类素材", "素材", "资料"}:
        return _dominant_material_block(chunks) or normalized
    return normalized


def _normalize_title(title: str) -> str:
    return " ".join(str(title or "").replace("_", " ").split()).strip() or "未命名主题"


def _dominant_material_block(chunks: list[EvidenceChunk]) -> str:
    values = [chunk.material_block.strip() for chunk in chunks if chunk.material_block and chunk.material_block.strip()]
    if not values:
        return ""
    return Counter(values).most_common(1)[0][0]


def _append_suffix_once(title: str, suffix: str, existing_suffixes: tuple[str, ...]) -> str:
    if any(title.endswith(existing) for existing in existing_suffixes):
        return title
    return f"{title}{suffix}"


def _rewrite_goal_title(goal: str, old_title: str, new_title: str) -> str:
    if old_title and new_title and old_title != new_title:
        return goal.replace(old_title, new_title)
    return goal


def _infer_topic_difficulty(title: str, chunks: list[EvidenceChunk]) -> str:
    text = " ".join([title, *(keyword for chunk in chunks for keyword in chunk.keywords), *(chunk.summary for chunk in chunks)])
    if any(term in title for term in ("原理", "概念", "认知", "基础", "安全", "准备")):
        return "basic"
    if any(term in text for term in ("适用", "范围", "特点", "质量", "评价", "案例", "综合")):
        return "advanced"
    if any(term in text for term in ("操作", "引弧", "送丝", "收弧", "焊接", "检查")):
        return "practice"
    return "basic"
