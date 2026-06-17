from __future__ import annotations

import re
from dataclasses import replace

from materials2textbook.schemas import CaseExample, ChapterPlan, EvidenceChunk


class CaseDesignerAgent:
    """Create evidence-grounded worked examples for textbook chapters."""

    def run(self, plans: list[ChapterPlan], chunks: list[EvidenceChunk]) -> list[ChapterPlan]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        return [self._enrich_plan(plan, chunk_map) for plan in plans]

    def _enrich_plan(self, plan: ChapterPlan, chunk_map: dict[str, EvidenceChunk]) -> ChapterPlan:
        if plan.case_examples:
            return plan

        examples: list[CaseExample] = []
        practice_points = [point for point in plan.knowledge_points if point.difficulty_level in {"practice", "advanced"}]
        selected_points = (practice_points or plan.knowledge_points)[:2]
        for index, point in enumerate(selected_points, start=1):
            evidence_ids = [chunk_id for chunk_id in point.chunk_ids if chunk_id in chunk_map]
            if not evidence_ids:
                continue
            evidence_summaries = _summarize_evidence(evidence_ids, chunk_map)
            examples.append(
                CaseExample(
                    case_id=f"{plan.chapter_id}_case_{index:02d}",
                    title=f"{point.title}课堂应用示例",
                    prompt=_build_case_prompt(point.title, evidence_summaries),
                    reference_answer=_build_reference_answer(point.title, evidence_summaries),
                    target_knowledge_point_ids=[point.knowledge_point_id],
                    evidence_chunk_ids=evidence_ids,
                )
            )

        return replace(plan, case_examples=examples)


def _summarize_evidence(evidence_ids: list[str], chunk_map: dict[str, EvidenceChunk]) -> str:
    parts: list[str] = []
    for chunk_id in evidence_ids[:3]:
        chunk = chunk_map[chunk_id]
        summary = chunk.summary or chunk.content
        if _looks_like_internal_review_text(_clean_student_text(summary)):
            summary = chunk.content
        summary = _best_student_sentence(summary)
        if not summary or _looks_like_internal_review_text(summary) or _looks_like_low_quality_asr(summary):
            continue
        parts.append(summary)
    return "；".join(dict.fromkeys(parts[:3])) or "观察示范视频中的关键动作和工件状态"


def _build_case_prompt(title: str, evidence_summary: str) -> str:
    if any(term in title for term in ("收弧", "送丝", "引弧", "操作")):
        return (
            f"课堂实训中，一名新手学生正在练习“{title}”。请结合示范视频判断："
            f"应重点观察哪些动作或工件状态？如果操作不到位，可能出现什么质量问题？同类现场任务中应如何迁移判断？"
        )
    return f"结合“{title}”的学习内容，说明其关键概念、适用场景和现场判断要点。"


def _build_reference_answer(title: str, evidence_summary: str) -> str:
    if any(term in title for term in ("收弧", "送丝", "引弧", "操作")):
        return (
            f"可先围绕“{evidence_summary}”观察示范视频中的动作，再判断动作是否连续、位置是否稳定、熔池或电弧状态是否正常。"
            "迁移到同类任务时，应结合课堂示范，把动作顺序、工件状态和质量缺陷联系起来分析。"
        )
    return (
        f"可从“{evidence_summary}”入手，先说明基本概念，再结合材料、设备或工艺条件判断其适用范围。"
        "回答时应避免脱离素材扩展没有依据的参数。"
    )


def _best_student_sentence(text: str) -> str:
    cleaned = _clean_student_text(text)
    parts = [
        part.strip(" ：:-，。；;")
        for part in re.split(r"[。；;]\s*|\s{2,}", cleaned)
        if part.strip(" ：:-，。；;")
    ]
    candidates = []
    for part in parts:
        if _looks_like_internal_review_text(part) or _looks_like_low_quality_asr(part):
            continue
        if len(part) < 8:
            continue
        if len(part) > 90:
            part = part[:87].rstrip() + "…"
        candidates.append(part)
    return candidates[0] if candidates else ""


def _clean_student_text(text: str) -> str:
    normalized = _normalize_asr_terms(str(text))
    normalized = re.sub(r"\[\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\s*-->\s*\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\]", "。", normalized)
    normalized = " ".join(normalized.split())
    normalized = re.sub(r"`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?", "", normalized)
    normalized = re.sub(r"[\w\u4e00-\u9fff（）()、.-]+\s*\.\s*(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)", "", normalized, flags=re.IGNORECASE)
    return normalized.strip(" ：:-，。")


def _normalize_asr_terms(text: str) -> str:
    replacements = {
        "採用": "采用",
        "送司法": "送丝法",
        "第二回饭，": "",
        "第二回饭": "",
        "焊接带由": "焊机带有",
        "焊接没有": "焊机没有",
        "焊接電燃": "焊接电缆",
        "焊接电燃": "焊接电缆",
        "漢阶": "焊接",
        "漢階": "焊接",
        "汉阶": "焊接",
        "汉階": "焊接",
        "汗階": "焊接",
        "漢师": "焊丝",
        "漢師": "焊丝",
        "漢絲": "焊丝",
        "龙磁": "熔池",
        "龍池": "熔池",
        "電湖": "电弧",
        "电湖": "电弧",
        "電骨": "电弧",
        "收骨": "收弧",
        "骨坑": "弧坑",
        "练纹": "裂纹",
        "确线": "缺陷",
        "電流衰竭": "电流衰减",
        "电流衰竭": "电流衰减",
        "框框制": "控制",
        "新疆熔池铁碼": "填满熔池铁水",
        "铁碼": "铁水",
        "乳急": "钨极",
        "隱糊": "引弧",
        "隐糊": "引弧",
        "漢": "焊",
        "為": "为",
        "與": "与",
        "這": "这",
        "時": "时",
        "後": "后",
        "應": "应",
        "長": "长",
        "開": "开",
        "準": "准",
        "確": "确",
        "種": "种",
        "質": "质",
        "態": "态",
        "處": "处",
        "高頻": "高频",
        "長度為": "长度为",
        "電流為": "电流为",
        "處於": "处于",
        "狀態": "状态",
        "開關": "开关",
        "起伏": "起弧",
        "電燃": "电源",
        "电燃": "电缆",
        "漢腔": "焊枪",
        "焊腔": "焊枪",
        "式板": "试板",
        "大母指": "拇指",
        "十指": "食指",
        "吴明指": "无名指",
        "一座": "夹住",
        "送开": "松开",
        "善用": "采用",
        "替个汉的": "",
        "替个汉": "",
    }
    normalized = str(text)
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized


def _looks_like_internal_review_text(text: str) -> bool:
    internal_terms = ("候选片段", "待人工", "待 agent", "处理队列", "人工复核", "确认边界", "时间码")
    normalized = text.lower()
    return any(term.lower() in normalized for term in internal_terms)


def _looks_like_low_quality_asr(text: str) -> bool:
    bad_terms = ("無幾", "壓護", "夫妻娘", "罕", "隱", "鈉", "漢师", "龙磁")
    return any(term in text for term in bad_terms)


def _is_unapproved_video_transcript(chunk: EvidenceChunk) -> bool:
    is_video = chunk.source_type in {"video_segment", "video", "audio_segment"}
    return is_video and "approved" not in chunk.review_status.lower()
