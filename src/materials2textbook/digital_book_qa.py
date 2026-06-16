from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from materials2textbook.llm.provider import LLMProvider


@dataclass
class BookSearchResult:
    project_title: str
    task_title: str
    block_id: str
    block_title: str
    block_type: str
    text: str
    evidence_chunk_ids: list[str] = field(default_factory=list)
    score: int = 0


@dataclass
class BookAnswer:
    question: str
    answer: str
    sources: list[BookSearchResult]
    used_llm: bool = False


def load_digital_book(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def search_digital_book(book: dict[str, Any], question: str, limit: int = 5) -> list[BookSearchResult]:
    terms = _query_terms(question)
    results: list[BookSearchResult] = []
    for project in book.get("projects", []):
        for task in project.get("tasks", []):
            for block in task.get("blocks", []):
                text = _block_text(block)
                haystack = " ".join(
                    [
                        str(project.get("title", "")),
                        str(task.get("title", "")),
                        str(block.get("title", "")),
                        text,
                        " ".join(block.get("evidence_chunk_ids", [])),
                    ]
                ).lower()
                score = _score_match(haystack, terms)
                if score <= 0:
                    continue
                results.append(
                    BookSearchResult(
                        project_title=str(project.get("title", "")),
                        task_title=str(task.get("title", "")),
                        block_id=str(block.get("block_id", "")),
                        block_title=str(block.get("title", "")),
                        block_type=str(block.get("type", "")),
                        text=_compact_text(text),
                        evidence_chunk_ids=[str(chunk_id) for chunk_id in block.get("evidence_chunk_ids", [])],
                        score=score,
                    )
                )
    return sorted(results, key=lambda item: (-item.score, item.project_title, item.task_title, item.block_id))[:limit]


def answer_digital_book_question(
    book: dict[str, Any],
    question: str,
    *,
    llm_provider: LLMProvider | None = None,
    limit: int = 5,
) -> BookAnswer:
    sources = search_digital_book(book, question, limit=limit)
    if not sources:
        return BookAnswer(
            question=question,
            answer="未在当前数字教材中找到直接相关内容。请换一个知识点、操作词或证据编号再问。",
            sources=[],
            used_llm=False,
        )
    if llm_provider is not None:
        messages = build_book_qa_messages(question, sources)
        return BookAnswer(
            question=question,
            answer=llm_provider.generate(messages).strip(),
            sources=sources,
            used_llm=True,
        )
    return BookAnswer(question=question, answer=_render_local_answer(question, sources), sources=sources, used_llm=False)


def answer_digital_book_payload(payload: dict[str, Any], *, llm_provider: LLMProvider | None = None) -> dict[str, Any]:
    question = str(payload.get("question", "")).strip()
    sources = [_source_from_payload(item) for item in payload.get("sources", []) if isinstance(item, dict)]
    sources = [source for source in sources if source.text]
    if not question:
        return {"answer": "Question is required.", "citations": [], "used_llm": False}
    if not sources:
        return {"answer": "No textbook source fragments were provided.", "citations": [], "used_llm": False}

    if llm_provider is not None:
        answer = llm_provider.generate(build_book_qa_messages(question, sources)).strip()
        used_llm = True
    else:
        answer = _render_local_answer(question, sources)
        used_llm = False
    citations = _dedupe(chunk_id for source in sources for chunk_id in source.evidence_chunk_ids)
    return {"answer": answer, "citations": citations, "used_llm": used_llm}


def build_book_qa_messages(question: str, sources: list[BookSearchResult]) -> list[dict[str, str]]:
    source_blocks = []
    for index, source in enumerate(sources, start=1):
        source_blocks.append(
            "\n".join(
                [
                    f"[{index}] {source.project_title} / {source.task_title} / {source.block_title}",
                    f"type: {source.block_type}",
                    f"evidence_chunk_ids: {', '.join(source.evidence_chunk_ids) or '无'}",
                    f"text: {source.text}",
                ]
            )
        )
    return [
        {
            "role": "system",
            "content": (
                "你是数字教材问书助手。只能依据给定教材片段回答，不得补充片段之外的事实。"
                "回答要简洁，并保留相关 evidence_chunk_ids 或说明未提供证据编号。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{question}\n\n教材片段：\n\n" + "\n\n".join(source_blocks),
        },
    ]


def render_book_answer_markdown(answer: BookAnswer) -> str:
    lines = [
        f"# 问书结果",
        "",
        f"**问题**：{answer.question}",
        "",
        "## 回答",
        "",
        answer.answer,
        "",
        "## 来源",
        "",
    ]
    if not answer.sources:
        lines.append("- 暂无匹配来源。")
    for source in answer.sources:
        evidence = ", ".join(source.evidence_chunk_ids) or "无"
        lines.append(f"- `{source.block_id}` {source.project_title} / {source.task_title} / {source.block_title}；证据：{evidence}")
    return "\n".join(lines).rstrip() + "\n"


def _render_local_answer(question: str, sources: list[BookSearchResult]) -> str:
    lines = [f"围绕“{question}”，当前教材中最相关的内容如下："]
    for index, source in enumerate(sources[:3], start=1):
        evidence = ", ".join(source.evidence_chunk_ids) or "未提供证据编号"
        lines.append(f"{index}. {source.block_title}：{source.text}（证据：{evidence}）")
    return "\n".join(lines)


def _block_text(block: dict[str, Any]) -> str:
    parts = [str(block.get("markdown", ""))]
    parts.extend(str(item) for item in block.get("items", []))
    return "\n".join(part for part in parts if part)


def _query_terms(question: str) -> list[str]:
    normalized = question.lower().strip()
    terms = [term for term in re.split(r"[\s,，。；;：:、？！?]+", normalized) if term]
    for chinese_text in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        for size in (2, 3, 4):
            for index in range(0, max(0, len(chinese_text) - size + 1)):
                terms.append(chinese_text[index : index + size])
    if normalized and normalized not in terms:
        terms.append(normalized)
    seen: set[str] = set()
    unique_terms: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)
    return unique_terms


def _score_match(haystack: str, terms: list[str]) -> int:
    score = 0
    for term in terms:
        if not term:
            continue
        if term in haystack:
            score += 4 if re.fullmatch(r"[a-z]\d+", term) else 2
    return score


def _compact_text(text: str, max_length: int = 260) -> str:
    compact = " ".join(text.split())
    if len(compact) > max_length:
        return compact[: max_length - 3] + "..."
    return compact


def _source_from_payload(item: dict[str, Any]) -> BookSearchResult:
    return BookSearchResult(
        project_title=str(item.get("project_title", "")),
        task_title=str(item.get("task_title", "")),
        block_id=str(item.get("block_id", "")),
        block_title=str(item.get("block_title", "")),
        block_type=str(item.get("block_type", "")),
        text=_compact_text(str(item.get("text", "")), max_length=600),
        evidence_chunk_ids=[str(chunk_id) for chunk_id in item.get("evidence_chunk_ids", [])],
        score=int(item.get("score", 0) or 0),
    )


def _dedupe(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_value in values:
        value = str(raw_value)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
