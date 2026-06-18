from __future__ import annotations

import json
import re
from typing import Any

from materials2textbook.agents.fact_support import analyze_claim_support, analyze_paragraph_support, detect_claim_consistency_issues
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.reviewers import build_evidence_review_messages, build_pedagogy_review_messages
from materials2textbook.schemas import ChapterPlan, EvidenceChunk, ReviewIssue, ReviewReport


class EvidenceReviewerAgent:
    """Check whether chapter evidence is usable enough for drafting."""

    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm

    def run(
        self,
        plans: list[ChapterPlan],
        chunks: list[EvidenceChunk],
        draft_markdown: str = "",
    ) -> dict[str, list[ReviewIssue]]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        cited_chunk_ids = _extract_known_chunk_ids(draft_markdown, set(chunk_map))
        unknown_citations = _extract_unknown_evidence_citations(draft_markdown, set(chunk_map))
        issues_by_chapter: dict[str, list[ReviewIssue]] = {}
        for plan in plans:
            issues: list[ReviewIssue] = []
            for chunk_id in plan.evidence_chunk_ids:
                chunk = chunk_map.get(chunk_id)
                if not chunk:
                    issues.append(
                        ReviewIssue("high", chunk_id, "章节计划引用了不存在的证据片段。", "重新生成章节计划或补齐证据库。")
                    )
                    continue
                if not chunk.content.strip():
                    issues.append(
                        ReviewIssue("high", chunk_id, "证据片段缺少正文或转写文本。", "补充转写文本后再进入教材写作。")
                    )
                if "pending" in chunk.review_status.lower():
                    issues.append(
                        ReviewIssue("medium", chunk_id, "证据片段仍处于待人工复核状态。", "人工确认时间码和片段边界。")
                    )
                if chunk.score.teaching_value < 0.5:
                    issues.append(
                        ReviewIssue("medium", chunk_id, "片段教学价值评分偏低或缺失。", "重新评分或从教材草稿中降级为补充材料。")
                    )
            if draft_markdown:
                paragraph_support = analyze_paragraph_support(draft_markdown, chunks)
                claim_support = analyze_claim_support(draft_markdown, chunks)
                for paragraph in paragraph_support:
                    if paragraph.support_status == "unsupported":
                        issues.append(
                            ReviewIssue(
                                "medium",
                                paragraph.paragraph_id,
                                "教材段落缺少可回溯证据引用。",
                                "为该段落补充有效 chunk_id，或删改为非事实性导学说明。",
                            )
                        )
                    elif paragraph.support_status == "unknown_citation":
                        issues.append(
                            ReviewIssue(
                                "high",
                                paragraph.paragraph_id,
                                "教材段落引用了证据库中不存在的 chunk_id。",
                                "修正引用，或由上游补齐对应证据片段。",
                            )
                        )
                    elif paragraph.support_status == "pending_evidence" and not _marks_pending_review(paragraph.text):
                        issues.append(
                            ReviewIssue(
                                "medium",
                                paragraph.paragraph_id,
                                "段落使用待复核证据但没有明确标注待人工复核。",
                                "补充待人工复核提示，正式教材前确认时间码和片段边界。",
                            )
                        )
                for claim in claim_support:
                    if claim.support_status == "unsupported":
                        issues.append(
                            ReviewIssue(
                                "medium",
                                claim.claim_id,
                                "教材事实断言缺少可回溯证据引用。",
                                "为该断言补充有效 chunk_id，或删改为非事实性说明。",
                            )
                        )
                    elif claim.support_status == "unknown_citation":
                        issues.append(
                            ReviewIssue(
                                "high",
                                claim.claim_id,
                                "教材事实断言引用了证据库中不存在的 chunk_id。",
                                "修正断言引用，或由上游补齐对应证据片段。",
                            )
                        )
                    elif claim.support_status == "pending_evidence" and not _marks_pending_review(claim.text):
                        issues.append(
                            ReviewIssue(
                                "medium",
                                claim.claim_id,
                                "事实断言使用待复核证据但没有明确标注待人工复核。",
                                "补充待人工复核提示，正式教材前确认时间码和片段边界。",
                            )
                        )
                for consistency_issue in detect_claim_consistency_issues(draft_markdown, chunks, claim_support):
                    issues.append(
                        ReviewIssue(
                            "high",
                            consistency_issue.issue_id,
                            consistency_issue.message,
                            "复核相关断言，统一要求性/禁止性表述，并确认所引用证据是否支持该结论。",
                        )
                    )
                for point in plan.knowledge_points:
                    for chunk_id in point.chunk_ids:
                        if chunk_id not in cited_chunk_ids:
                            issues.append(
                                ReviewIssue(
                                    "high",
                                    chunk_id,
                                    "教材草稿未保留该知识点的证据引用。",
                                    "重新生成或修订正文，确保每个知识点至少保留一个可追溯 chunk_id。",
                                )
                            )
                for unknown_id in sorted(unknown_citations):
                    issues.append(
                        ReviewIssue(
                            "high",
                            unknown_id,
                            "教材草稿引用了证据库中不存在的 chunk_id。",
                            "删除该引用，或由上游补齐对应证据片段后再生成教材。",
                        )
                    )
            if self.use_llm:
                issues.extend(self._run_llm_review(plan, chunks, draft_markdown))
            issues_by_chapter[plan.chapter_id] = issues
        return issues_by_chapter

    def _run_llm_review(
        self,
        plan: ChapterPlan,
        chunks: list[EvidenceChunk],
        draft_markdown: str,
    ) -> list[ReviewIssue]:
        if self.llm_provider is None:
            raise RuntimeError("EvidenceReviewerAgent was asked to use LLM, but no provider was configured.")
        messages = build_evidence_review_messages(plan, chunks, draft_markdown)
        return _parse_review_issues(self.llm_provider.generate(messages), default_location=plan.chapter_id)


class PedagogyReviewerAgent:
    """Check chapter structure and teaching completeness."""

    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm

    def run(self, plans: list[ChapterPlan], draft_markdown: str = "") -> dict[str, list[ReviewIssue]]:
        issues_by_chapter: dict[str, list[ReviewIssue]] = {}
        for plan in plans:
            issues: list[ReviewIssue] = []
            if len(plan.knowledge_points) < 2:
                issues.append(
                    ReviewIssue("low", plan.chapter_id, "章节知识点数量较少。", "确认是否需要补充原理、操作、注意事项或案例。")
                )
            if not plan.activities:
                issues.append(
                    ReviewIssue("medium", plan.chapter_id, "章节缺少学习活动。", "至少补充观察任务、思考题或操作练习。")
                )
            if not plan.activity_items:
                issues.append(
                    ReviewIssue(
                        "medium",
                        plan.chapter_id,
                        "章节缺少结构化学习活动。", 
                        "由 ActivityDesignerAgent 生成包含类型、难度、证据和评价量规的活动。",
                    )
                )
            if not plan.learning_goals:
                issues.append(
                    ReviewIssue("medium", plan.chapter_id, "章节缺少学习目标。", "补充可评价的学习目标。")
                )
            if len(plan.learning_path) != len(plan.knowledge_points):
                issues.append(
                    ReviewIssue(
                        "medium",
                        plan.chapter_id,
                        "章节缺少完整的学习路径。", 
                        "由知识组织 Agent 补齐 learning_path，并确认每个知识点都有顺序编号。",
                    )
                )
            for point in plan.knowledge_points:
                if not point.difficulty_level or not point.cluster_id:
                    issues.append(
                        ReviewIssue(
                            "low",
                            point.knowledge_point_id,
                            "知识点缺少难度或聚类标记。",
                            "补齐 difficulty_level 和 cluster_id，便于审核难度梯度。",
                        )
                    )
                if point.difficulty_level in {"practice", "advanced"} and not point.prerequisite_ids:
                    issues.append(
                        ReviewIssue(
                            "low",
                            point.knowledge_point_id,
                            "实践或拓展知识点缺少先修关系。",
                            "补充 prerequisite_ids，或确认该知识点确实可以独立学习。",
                        )
                    )
            issues.extend(_review_activity_quality(plan))
            issues.extend(_review_case_quality(plan))
            if self.use_llm:
                issues.extend(self._run_llm_review(plan, draft_markdown))
            issues_by_chapter[plan.chapter_id] = issues
        return issues_by_chapter

    def _run_llm_review(self, plan: ChapterPlan, draft_markdown: str) -> list[ReviewIssue]:
        if self.llm_provider is None:
            raise RuntimeError("PedagogyReviewerAgent was asked to use LLM, but no provider was configured.")
        messages = build_pedagogy_review_messages(plan, draft_markdown)
        return _parse_review_issues(self.llm_provider.generate(messages), default_location=plan.chapter_id)


class ReviewComposer:
    def run(
        self,
        plans: list[ChapterPlan],
        fact_issues: dict[str, list[ReviewIssue]],
        pedagogy_issues: dict[str, list[ReviewIssue]],
    ) -> list[ReviewReport]:
        reports: list[ReviewReport] = []
        for plan in plans:
            chapter_fact_issues = fact_issues.get(plan.chapter_id, [])
            chapter_pedagogy_issues = pedagogy_issues.get(plan.chapter_id, [])
            suggestions = [issue.suggestion for issue in chapter_fact_issues + chapter_pedagogy_issues]
            reports.append(
                ReviewReport(
                    chapter_id=plan.chapter_id,
                    chapter_title=plan.title,
                    fact_issues=chapter_fact_issues,
                    pedagogy_issues=chapter_pedagogy_issues,
                    revision_suggestions=suggestions,
                )
            )
        return reports


def _extract_known_chunk_ids(markdown: str, known_chunk_ids: set[str]) -> set[str]:
    if not markdown:
        return set()
    return {chunk_id for chunk_id in known_chunk_ids if chunk_id and chunk_id in markdown}


def _marks_pending_review(text: str) -> bool:
    normalized = text.lower()
    return "待人工复核" in text or "待复核" in text or "pending" in normalized


def _extract_unknown_evidence_citations(markdown: str, known_chunk_ids: set[str]) -> set[str]:
    if not markdown:
        return set()
    candidates: set[str] = set()
    patterns = [
        r"证据\s*[`：:\s]*`?([A-Za-z][A-Za-z0-9_-]{1,})`?",
        r"chunk_id\s*[：:\s]+`?([A-Za-z][A-Za-z0-9_-]{1,})`?",
    ]
    for pattern in patterns:
        candidates.update(re.findall(pattern, markdown, flags=re.IGNORECASE))
    return {candidate for candidate in candidates if candidate not in known_chunk_ids}


def _review_activity_quality(plan: ChapterPlan) -> list[ReviewIssue]:
    if not plan.activity_items:
        return []

    issues: list[ReviewIssue] = []
    levels = [activity.difficulty_level for activity in plan.activity_items]
    required_levels = {"basic", "practice"}
    if len(plan.knowledge_points) >= 2:
        required_levels.add("advanced")
    missing_levels = sorted(required_levels.difference(levels))
    if missing_levels:
        issues.append(
            ReviewIssue(
                "medium",
                plan.chapter_id,
                f"学习活动难度梯度不完整，缺少：{', '.join(missing_levels)}。",
                "补充从观察定位到实践解释再到分析迁移的分层活动。",
            )
        )

    known_point_ids = {point.knowledge_point_id for point in plan.knowledge_points}
    known_chunk_ids = set(plan.evidence_chunk_ids)
    covered_point_ids: set[str] = set()
    for activity in plan.activity_items:
        covered_point_ids.update(point_id for point_id in activity.target_knowledge_point_ids if point_id in known_point_ids)
        if not activity.prompt.strip():
            issues.append(
                ReviewIssue("medium", activity.activity_id, "学习活动缺少任务说明。", "补充可执行的活动 prompt。")
            )
        if not activity.rubric:
            issues.append(
                ReviewIssue("low", activity.activity_id, "学习活动缺少评价量规。", "补充 2-3 条可观察的评价要点。")
            )
        if not set(activity.evidence_chunk_ids).intersection(known_chunk_ids):
            issues.append(
                ReviewIssue(
                    "medium",
                    activity.activity_id,
                    "学习活动没有绑定本章证据片段。",
                    "为活动绑定相关 evidence_chunk_ids，保证练习可追溯。",
                )
            )
    if known_point_ids and len(covered_point_ids) / len(known_point_ids) < 0.5:
        issues.append(
            ReviewIssue(
                "medium",
                plan.chapter_id,
                "学习活动覆盖的知识点不足。",
                "让活动至少覆盖一半以上的知识点，重点覆盖实践和拓展知识点。",
            )
        )
    return issues


def _review_case_quality(plan: ChapterPlan) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    needs_case = any(point.difficulty_level in {"practice", "advanced"} for point in plan.knowledge_points)
    if needs_case and not plan.case_examples:
        issues.append(
            ReviewIssue(
                "medium",
                plan.chapter_id,
                "章节缺少面向实践或迁移的案例示例。",
                "由 CaseDesignerAgent 生成包含例题、参考分析、学生情境和证据引用的案例示例。",
            )
        )
        return issues

    known_point_ids = {point.knowledge_point_id for point in plan.knowledge_points}
    known_chunk_ids = set(plan.evidence_chunk_ids)
    for example in plan.case_examples:
        if not example.prompt.strip() or not example.reference_answer.strip():
            issues.append(
                ReviewIssue(
                    "medium",
                    example.case_id,
                    "案例示例缺少例题或参考分析。",
                    "补齐可执行的例题 prompt 和基于证据的 reference_answer。",
                )
            )
        if not set(example.evidence_chunk_ids).intersection(known_chunk_ids):
            issues.append(
                ReviewIssue(
                    "medium",
                    example.case_id,
                    "案例示例没有绑定本章证据片段。",
                    "为案例补充 evidence_chunk_ids，确保例题分析可以追溯到原始素材。",
                )
            )
        if not set(example.target_knowledge_point_ids).intersection(known_point_ids):
            issues.append(
                ReviewIssue(
                    "low",
                    example.case_id,
                    "案例示例没有绑定本章知识点。",
                    "为案例补充 target_knowledge_point_ids，便于检查案例覆盖和学习路径位置。",
                )
            )
        combined_text = f"{example.prompt} {example.reference_answer}"
        if not _contains_any(combined_text, ("迁移", "同类", "现场", "项目", "判断", "应用")):
            issues.append(
                ReviewIssue(
                    "low",
                    example.case_id,
                    "案例示例缺少迁移应用或现场判断要求。",
                    "在例题中加入同类任务、现场情境或迁移判断，让学生能把知识点迁移到新问题。",
                )
            )
        if not _contains_any(combined_text, ("学生", "新手", "学员", "课堂", "实训", "岗位")):
            issues.append(
                ReviewIssue(
                    "low",
                    example.case_id,
                    "案例示例缺少学生画像或学习情境。",
                    "补充新手学生、课堂实训或岗位任务情境，便于教学适配。",
                )
            )
    return issues


def _parse_review_issues(raw_response: str, default_location: str) -> list[ReviewIssue]:
    payload = _parse_json_array(raw_response)
    issues: list[ReviewIssue] = []
    for item in payload:
        if not isinstance(item, dict):
            raise RuntimeError("Reviewer LLM response items must be JSON objects.")
        severity = _normalize_severity(item.get("severity"))
        message = _non_empty_string(item.get("message"))
        suggestion = _non_empty_string(item.get("suggestion"))
        if not message or not suggestion:
            raise RuntimeError(f"Reviewer LLM issue is missing message or suggestion: {item}")
        issues.append(
            ReviewIssue(
                severity=severity,
                location=_non_empty_string(item.get("location")) or default_location,
                message=message,
                suggestion=suggestion,
            )
        )
    return issues


def _parse_json_array(raw_response: str) -> list[Any]:
    text = raw_response.strip()
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Reviewer LLM response was not valid JSON: {raw_response[:500]}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Reviewer LLM response must be a JSON array.")
    return payload


def _normalize_severity(value: Any) -> str:
    severity = str(value or "medium").strip().lower()
    if severity not in {"high", "medium", "low"}:
        return "medium"
    return severity


def _non_empty_string(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
