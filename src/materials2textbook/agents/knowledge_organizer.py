from __future__ import annotations

from collections import Counter, defaultdict
import re
from statistics import mean

from materials2textbook.schemas import ChapterPlan, EvidenceChunk, KnowledgePoint


class KnowledgeOrganizerAgent:
    """Group evidence chunks and derive a conservative learning path."""

    BASIC_TERMS = ("原理", "概念", "基础", "认识", "安全", "准备", "保护")
    OPERATION_TERMS = ("操作", "引弧", "送丝", "收弧", "焊接", "检查")
    ADVANCED_TERMS = ("适用", "范围", "特点", "缺陷", "质量", "评价", "案例", "综合")

    def run(self, chunks: list[EvidenceChunk], max_chunks_per_knowledge_point: int | None = None) -> list[ChapterPlan]:
        by_chapter: dict[str, list[EvidenceChunk]] = defaultdict(list)
        for chunk in chunks:
            by_chapter[chunk.recommended_chapter or "待规划章节"].append(chunk)

        plans: list[ChapterPlan] = []
        ordered_chapters = sorted(
            by_chapter.items(),
            key=lambda item: (_chapter_sort_key(item[1]), item[0]),
        )
        for index, (chapter_title, chapter_chunks) in enumerate(ordered_chapters, start=1):
            by_point: dict[str, list[EvidenceChunk]] = defaultdict(list)
            for chunk in chapter_chunks:
                by_point[chunk.title].append(chunk)

            dominant_block = _dominant_material_block(chapter_chunks)
            point_groups = sorted(
                [
                    (point_title, point_chunks)
                    for point_title, point_chunks in by_point.items()
                    if _is_on_topic_point(point_title, point_chunks, chapter_title, dominant_block)
                ],
                key=lambda item: (_point_sort_key(item[0], item[1]), item[0]),
            )
            points = [
                KnowledgePoint(
                    knowledge_point_id=f"kp_{index:02d}_{point_index:02d}",
                    title=point_title,
                    chunk_ids=[
                        chunk.chunk_id
                        for chunk in (
                            point_chunks[:max_chunks_per_knowledge_point]
                            if max_chunks_per_knowledge_point
                            else point_chunks
                        )
                    ],
                    summary=point_chunks[0].summary,
                    order_index=point_index,
                    difficulty_level=_difficulty_level(point_title, point_chunks),
                    prerequisite_ids=[],
                    cluster_id=_cluster_id(point_title, point_chunks),
                )
                for point_index, (point_title, point_chunks) in enumerate(point_groups, start=1)
            ]
            _attach_prerequisites(points)

            plans.append(
                ChapterPlan(
                    chapter_id=f"chapter_{index:02d}",
                    title=chapter_title,
                    learning_goals=[
                        f"理解{chapter_title}的核心概念和适用场景",
                        f"能够复述{chapter_title}的关键操作或原理",
                        "能够结合示范视频说明操作注意事项和质量要求",
                    ],
                    knowledge_points=points,
                    evidence_chunk_ids=[chunk.chunk_id for chunk in chapter_chunks],
                    activities=[
                        "结合视频片段观察关键动作或现象。",
                        "根据学习要点回答思考题。",
                    ],
                    learning_path=[point.knowledge_point_id for point in points],
                )
            )
        return plans


def _chapter_sort_key(chunks: list[EvidenceChunk]) -> tuple[int, float]:
    order_values = [
        int(chunk.metadata.get("chapter_order"))
        for chunk in chunks
        if str(chunk.metadata.get("chapter_order", "")).isdigit()
    ]
    if order_values:
        return min(order_values), 0.0
    difficulty_values = [_difficulty_rank(_difficulty_level(chunk.title, [chunk])) for chunk in chunks]
    return 999, mean(difficulty_values) if difficulty_values else 0.0


def _point_sort_key(title: str, chunks: list[EvidenceChunk]) -> tuple[int, int, float]:
    explicit_orders = [
        int(chunk.metadata.get("knowledge_order"))
        for chunk in chunks
        if str(chunk.metadata.get("knowledge_order", "")).isdigit()
    ]
    if explicit_orders:
        return 0, min(explicit_orders), 0.0
    difficulty = _difficulty_level(title, chunks)
    average_start = mean([chunk.locator.start_ms or 0 for chunk in chunks]) if chunks else 0.0
    return 1, _difficulty_rank(difficulty), average_start


def _difficulty_level(title: str, chunks: list[EvidenceChunk]) -> str:
    text = _combined_text(title, chunks)
    if _contains_any(text, KnowledgeOrganizerAgent.ADVANCED_TERMS):
        return "advanced"
    if _contains_any(text, KnowledgeOrganizerAgent.BASIC_TERMS):
        return "basic"
    if _contains_any(text, KnowledgeOrganizerAgent.OPERATION_TERMS):
        return "practice"
    return "basic"


def _cluster_id(title: str, chunks: list[EvidenceChunk]) -> str:
    metadata_cluster = _metadata_cluster_id(chunks)
    if metadata_cluster:
        return metadata_cluster
    text = _combined_text(title, chunks)
    if _contains_any(text, KnowledgeOrganizerAgent.BASIC_TERMS):
        return "concept"
    if _contains_any(text, KnowledgeOrganizerAgent.OPERATION_TERMS):
        return "operation"
    if _contains_any(text, KnowledgeOrganizerAgent.ADVANCED_TERMS):
        return "extension"
    return "general"


def _metadata_cluster_id(chunks: list[EvidenceChunk]) -> str:
    cluster_values: list[str] = []
    for chunk in chunks:
        for key in ("semantic_cluster", "semantic_cluster_id", "cluster_id", "topic_cluster"):
            value = str(chunk.metadata.get(key, "")).strip()
            if value:
                cluster_values.append(value)
                break
    if not cluster_values:
        return ""
    return Counter(cluster_values).most_common(1)[0][0]


def _attach_prerequisites(points: list[KnowledgePoint]) -> None:
    previous_by_level: dict[str, list[str]] = {"basic": [], "practice": [], "advanced": []}
    for point in points:
        if point.difficulty_level == "practice":
            point.prerequisite_ids = previous_by_level["basic"][-2:]
        elif point.difficulty_level == "advanced":
            point.prerequisite_ids = (previous_by_level["basic"] + previous_by_level["practice"])[-3:]
        previous_by_level.setdefault(point.difficulty_level, []).append(point.knowledge_point_id)


def _difficulty_rank(level: str) -> int:
    return {"basic": 0, "practice": 1, "advanced": 2}.get(level, 1)


def _combined_text(title: str, chunks: list[EvidenceChunk]) -> str:
    terms = [title]
    for chunk in chunks:
        terms.extend(chunk.keywords)
        terms.append(chunk.material_block_code)
        terms.append(chunk.material_block)
    return " ".join(terms).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _dominant_material_block(chunks: list[EvidenceChunk]) -> str:
    values = [chunk.material_block.strip() for chunk in chunks if chunk.material_block and chunk.material_block.strip()]
    if not values:
        return ""
    return Counter(values).most_common(1)[0][0]


def _is_on_topic_point(
    title: str,
    chunks: list[EvidenceChunk],
    chapter_title: str,
    dominant_material_block: str,
) -> bool:
    text = _combined_text(title, chunks)
    scope_text = f"{chapter_title} {dominant_material_block}"
    if "钨极氩弧焊" in scope_text and "焊条电弧焊" in text:
        return False
    if "氩弧焊" in scope_text and "焊条电弧焊" in text:
        return False
    if _looks_like_catalog_point(title, chunks):
        return False
    if not _has_teachable_content(title, chunks):
        return False
    return True


def _looks_like_catalog_point(title: str, chunks: list[EvidenceChunk]) -> bool:
    text = " ".join([title, *(chunk.content[:120] for chunk in chunks[:2])]).lower()
    return "contents" in text or "目录" in text


def _has_teachable_content(title: str, chunks: list[EvidenceChunk]) -> bool:
    if any(chunk.source_type in {"video_segment", "video", "audio_segment"} for chunk in chunks):
        return True
    for chunk in chunks:
        if _looks_like_media_placeholder_chunk(chunk):
            continue
        text = _clean_teachable_text(" ".join([chunk.summary, chunk.content]))
        if len(text) >= 32:
            return True
        title_terms = _clean_teachable_text(title)
        if len(text) >= 16 and title_terms and title_terms not in text:
            return True
    return False


def _looks_like_media_placeholder_chunk(chunk: EvidenceChunk) -> bool:
    raw = " ".join(str(part or "") for part in (chunk.summary, chunk.content))
    has_media_name = bool(re.search(r"\.(?:mp4|flv|mp3|wav)\b", raw, flags=re.IGNORECASE))
    has_course_shell = any(term in raw for term in ("学习单元", "课程", "焊接基础知识", "焊接方法的分类"))
    return has_media_name and has_course_shell


def _clean_teachable_text(text: str) -> str:
    normalized = " ".join(str(text or "").split())
    normalized = re.sub(r"[\w\u4e00-\u9fff（）()、.-]+\s*\.\s*(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)", "", normalized, flags=re.IGNORECASE)
    noise_terms = (
        "学习单元",
        "课程",
        "焊接方法的分类及常用的焊接方法",
        "焊接基础知识",
        "contents",
        "目录",
    )
    for term in noise_terms:
        normalized = normalized.replace(term, "")
    normalized = re.sub(r"[\s\d.、:：一二三四五六七八九十-]+", "", normalized)
    return normalized
