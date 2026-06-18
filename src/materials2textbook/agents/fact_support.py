from __future__ import annotations

import re

from materials2textbook.schemas import ClaimConsistencyIssue, ClaimSupport, EvidenceChunk, ParagraphSupport


_CHUNK_ID_PATTERN = re.compile(r"\b[A-Za-z]\d{1,}\b")
_CLAIM_SPLIT_PATTERN = re.compile(r"(?<=[。！？!?；;])|\n+")
_NEGATIVE_TERMS = ("不能", "不应", "不得", "禁止", "避免", "不需要")
_REQUIRED_TERMS = ("需要", "应", "应该", "必须", "要", "保持", "可以")


def analyze_paragraph_support(markdown: str, chunks: list[EvidenceChunk]) -> list[ParagraphSupport]:
    """Score whether factual-looking Markdown blocks keep traceable evidence IDs."""

    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    paragraphs: list[ParagraphSupport] = []
    for index, block in enumerate(_iter_markdown_blocks(markdown), start=1):
        text = _normalize_block(block)
        if not _is_fact_like_block(text):
            continue

        candidates = _extract_chunk_ids(text)
        cited = [chunk_id for chunk_id in candidates if chunk_id in chunk_map]
        unknown = [chunk_id for chunk_id in candidates if chunk_id not in chunk_map]
        paragraph_id = f"paragraph_{len(paragraphs) + 1:03d}"

        if unknown:
            paragraphs.append(
                ParagraphSupport(
                    paragraph_id=paragraph_id,
                    text=text,
                    cited_chunk_ids=cited,
                    unknown_chunk_ids=unknown,
                    support_status="unknown_citation",
                    score=0.0,
                    notes="段落引用了证据库中不存在的 chunk_id。",
                )
            )
            continue

        if not cited:
            paragraphs.append(
                ParagraphSupport(
                    paragraph_id=paragraph_id,
                    text=text,
                    cited_chunk_ids=[],
                    unknown_chunk_ids=[],
                    support_status="unsupported",
                    score=0.0,
                    notes="段落没有可回溯的 chunk_id。",
                )
            )
            continue

        statuses = [chunk_map[chunk_id].review_status.lower() for chunk_id in cited]
        if any("pending" in status for status in statuses):
            paragraphs.append(
                ParagraphSupport(
                    paragraph_id=paragraph_id,
                    text=text,
                    cited_chunk_ids=cited,
                    unknown_chunk_ids=[],
                    support_status="pending_evidence",
                    score=0.6,
                    notes="段落有证据引用，但至少一个证据片段仍待人工复核。",
                )
            )
            continue

        paragraphs.append(
            ParagraphSupport(
                paragraph_id=paragraph_id,
                text=text,
                cited_chunk_ids=cited,
                unknown_chunk_ids=[],
                support_status="supported",
                score=1.0,
                notes="段落保留了可回溯证据引用。",
            )
        )
    return paragraphs


def paragraph_support_rate(paragraphs: list[ParagraphSupport]) -> float:
    if not paragraphs:
        return 0.0
    return round(sum(paragraph.score for paragraph in paragraphs) / len(paragraphs), 4)


def analyze_claim_support(markdown: str, chunks: list[EvidenceChunk]) -> list[ClaimSupport]:
    """Score factual claims inside Markdown blocks with traceable evidence IDs."""

    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    claims: list[ClaimSupport] = []
    for paragraph_index, block in enumerate(_iter_markdown_blocks(markdown), start=1):
        text = _normalize_block(block)
        if not _is_fact_like_block(text):
            continue

        paragraph_id = f"paragraph_{paragraph_index:03d}"
        block_candidates = _extract_chunk_ids(text)
        block_cited = [chunk_id for chunk_id in block_candidates if chunk_id in chunk_map]
        for claim_text in _iter_claims(text):
            claim_candidates = _extract_chunk_ids(claim_text)
            inherited_candidates = block_candidates if not claim_candidates and len(block_cited) == 1 else []
            candidates = claim_candidates or inherited_candidates
            cited = [chunk_id for chunk_id in candidates if chunk_id in chunk_map]
            unknown = [chunk_id for chunk_id in candidates if chunk_id not in chunk_map]
            claim_id = f"claim_{len(claims) + 1:03d}"

            if unknown:
                claims.append(
                    ClaimSupport(
                        claim_id=claim_id,
                        paragraph_id=paragraph_id,
                        text=claim_text,
                        cited_chunk_ids=cited,
                        unknown_chunk_ids=unknown,
                        support_status="unknown_citation",
                        score=0.0,
                        notes="断言引用了证据库中不存在的 chunk_id。",
                    )
                )
                continue
            if not cited:
                claims.append(
                    ClaimSupport(
                        claim_id=claim_id,
                        paragraph_id=paragraph_id,
                        text=claim_text,
                        cited_chunk_ids=[],
                        unknown_chunk_ids=[],
                        support_status="unsupported",
                        score=0.0,
                        notes="断言没有可回溯的 chunk_id。",
                    )
                )
                continue

            statuses = [chunk_map[chunk_id].review_status.lower() for chunk_id in cited]
            if any("pending" in status for status in statuses):
                claims.append(
                    ClaimSupport(
                        claim_id=claim_id,
                        paragraph_id=paragraph_id,
                        text=claim_text,
                        cited_chunk_ids=cited,
                        unknown_chunk_ids=[],
                        support_status="pending_evidence",
                        score=0.6,
                        notes="断言有证据引用，但至少一个证据片段仍待人工复核。",
                    )
                )
                continue

            inherited_note = "断言直接保留了可回溯证据引用。"
            if not claim_candidates and block_cited:
                inherited_note = "断言继承同一段落中的可回溯证据引用。"
            claims.append(
                ClaimSupport(
                    claim_id=claim_id,
                    paragraph_id=paragraph_id,
                    text=claim_text,
                    cited_chunk_ids=cited,
                    unknown_chunk_ids=[],
                    support_status="supported",
                    score=1.0,
                    notes=inherited_note,
                )
            )
    return claims


def claim_support_rate(claims: list[ClaimSupport]) -> float:
    if not claims:
        return 0.0
    return round(sum(claim.score for claim in claims) / len(claims), 4)


def detect_claim_consistency_issues(
    markdown: str,
    chunks: list[EvidenceChunk],
    claims: list[ClaimSupport] | None = None,
) -> list[ClaimConsistencyIssue]:
    """Detect simple contradictions across claims that cite the same evidence/topic."""

    claim_items = claims if claims is not None else analyze_claim_support(markdown, chunks)
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    by_key: dict[tuple[str, str], dict[str, list[ClaimSupport]]] = {}
    for claim in claim_items:
        polarity = _claim_polarity(claim.text)
        if polarity == "neutral":
            continue
        for chunk_id in claim.cited_chunk_ids:
            chunk = chunk_map.get(chunk_id)
            topic = _claim_topic(claim.text, chunk)
            by_key.setdefault((chunk_id, topic), {"required": [], "negative": []})[polarity].append(claim)

    issues: list[ClaimConsistencyIssue] = []
    for (chunk_id, topic), grouped in sorted(by_key.items()):
        if grouped["required"] and grouped["negative"]:
            related_claims = grouped["required"][:1] + grouped["negative"][:1]
            issues.append(
                ClaimConsistencyIssue(
                    issue_id=f"claim_consistency_{len(issues) + 1:03d}",
                    topic=topic,
                    claim_ids=[claim.claim_id for claim in related_claims],
                    cited_chunk_ids=[chunk_id],
                    message=f"同一证据 {chunk_id} 在“{topic}”相关断言中出现要求性和禁止性表述冲突。",
                )
            )
    return issues


def _iter_markdown_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _normalize_block(block: str) -> str:
    return " ".join(line.strip() for line in block.splitlines() if line.strip())


def _is_fact_like_block(text: str) -> bool:
    if not text:
        return False
    if text.startswith("#") or text.startswith(">"):
        return False
    if text.startswith("|") or text.startswith("```"):
        return False
    if len(re.sub(r"[`\s*_#>\-：:，。,.;；（）()]", "", text)) < 12:
        return False
    return True


def _extract_chunk_ids(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in _CHUNK_ID_PATTERN.findall(text):
        if match not in seen:
            seen.add(match)
            result.append(match)
    return result


def _iter_claims(text: str) -> list[str]:
    claims: list[str] = []
    for part in _CLAIM_SPLIT_PATTERN.split(text):
        claim = part.strip()
        if not claim:
            continue
        if _is_claim_like(claim):
            claims.append(claim)
    return claims or [text]


def _is_claim_like(text: str) -> bool:
    if not _is_fact_like_block(text):
        clean = re.sub(r"[`\s*_#>\-，,。.;；（）()]", "", text)
        return len(clean) >= 8 and not text.startswith(("#", ">", "|", "```"))
    return True


def _claim_polarity(text: str) -> str:
    if any(term in text for term in _NEGATIVE_TERMS):
        return "negative"
    if any(term in text for term in _REQUIRED_TERMS):
        return "required"
    return "neutral"


def _claim_topic(text: str, chunk: EvidenceChunk | None) -> str:
    if chunk:
        candidates = [chunk.title, *chunk.keywords, chunk.material_block]
        for candidate in candidates:
            if candidate and candidate in text:
                return candidate
        return chunk.title or chunk.chunk_id
    return "未知主题"
