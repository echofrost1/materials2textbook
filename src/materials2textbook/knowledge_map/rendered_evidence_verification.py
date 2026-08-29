"""Read-only evidence verification for facts actually rendered to students."""

from __future__ import annotations

import re

from materials2textbook.knowledge_map.publication_quality_models import RenderedClaimVerification
from materials2textbook.knowledge_map.rendered_conformance import extract_rendered_occurrences
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief
from materials2textbook.schemas import DigitalBook, EvidenceChunk


class ClaimType:
    SOURCE_FACT = "SOURCE_FACT"
    TRAJECTORY_FACT = "TRAJECTORY_FACT"
    STRUCTURAL_FACT = "STRUCTURAL_FACT"
    MIXED = "MIXED"


class SupportStatus:
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNCERTAIN = "UNCERTAIN"


# An inline evidence id belongs to the sentence before it; splitting it into a
# separate fragment would silently lose an unauthorized C-id.
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*(?!Evidence\s*:\s*C\d+\b)|\n+", re.IGNORECASE)
_EVIDENCE_ID = re.compile(r"\bEvidence\s*:\s*(C\d+)\b", re.IGNORECASE)
_TRAJECTORY = re.compile(r"之前|前文|已掌握|已学|previously|already\s+taught", re.IGNORECASE)
_STRUCTURAL = re.compile(r"本任务|本节|任务|学习目标|current\s+task|this\s+task", re.IGNORECASE)


def verify_rendered_evidence(
    *,
    markdown: str,
    digital_book: DigitalBook,
    briefs: list[OccurrenceWritingBrief],
    evidence_by_id: dict[str, EvidenceChunk],
) -> list[RenderedClaimVerification]:
    brief_by_id = {item.occurrence_id: item for item in briefs}
    records: list[tuple[str, str, str]] = [("markdown", item.occurrence_id, item.markdown) for item in extract_rendered_occurrences(markdown)]
    records.extend(_digital_records(digital_book))
    claims: list[RenderedClaimVerification] = []
    for target, occurrence_id, body in records:
        brief = brief_by_id.get(occurrence_id)
        if brief is None:
            continue
        allowed = tuple(dict.fromkeys([*brief.source_chunk_ids, *brief.semantic_delta_evidence_ids]))
        for sentence in _sentences(body):
            clean = _EVIDENCE_ID.sub("", sentence).strip()
            if not clean:
                continue
            claim_type = _claim_type(clean)
            cited = tuple(dict.fromkeys(_EVIDENCE_ID.findall(sentence)))
            status, supported, confidence = _support_status(
                clean,
                claim_type=claim_type,
                cited_ids=cited,
                allowed_ids=allowed,
                evidence_by_id=evidence_by_id,
            )
            claims.append(RenderedClaimVerification(
                occurrence_id=occurrence_id,
                target=target,
                claim_text=clean,
                claim_type=claim_type,
                source_span=sentence,
                supporting_evidence_ids=supported,
                support_status=status,
                confidence=confidence,
            ))
    return claims


def _digital_records(book: DigitalBook) -> list[tuple[str, str, str]]:
    records = []
    for project in book.projects:
        for task in project.tasks:
            for block in task.blocks:
                semantic = block.metadata.get("semantic_occurrence") if block.metadata else None
                if isinstance(semantic, dict) and semantic.get("occurrence_id"):
                    records.append(("digital_book", str(semantic["occurrence_id"]), block.markdown))
    return records


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]


def _claim_type(text: str) -> str:
    trajectory = bool(_TRAJECTORY.search(text))
    structural = bool(_STRUCTURAL.search(text))
    if trajectory and structural:
        return ClaimType.MIXED
    if trajectory:
        return ClaimType.TRAJECTORY_FACT
    if structural and len(_content_terms(text)) < 2:
        return ClaimType.STRUCTURAL_FACT
    return ClaimType.SOURCE_FACT


def _support_status(
    text: str,
    *,
    claim_type: str,
    cited_ids: tuple[str, ...],
    allowed_ids: tuple[str, ...],
    evidence_by_id: dict[str, EvidenceChunk],
) -> tuple[str, tuple[str, ...], float]:
    if claim_type in {ClaimType.TRAJECTORY_FACT, ClaimType.STRUCTURAL_FACT}:
        return SupportStatus.SUPPORTED, (), 0.95
    # A mixed sentence often says both “in this task” and a concrete source
    # fact.  Structural/trajectory provenance may support the former, but it
    # never authorizes an uncited source assertion in the latter.
    if cited_ids and not set(cited_ids).issubset(set(allowed_ids)):
        return SupportStatus.UNSUPPORTED, cited_ids, 1.0
    candidates = [evidence_by_id[item] for item in allowed_ids if item in evidence_by_id]
    overlap = _evidence_overlap(text, candidates)
    if cited_ids and set(cited_ids).issubset(set(allowed_ids)):
        return SupportStatus.SUPPORTED, cited_ids, 0.85 if overlap else 0.7
    if overlap:
        return SupportStatus.SUPPORTED, tuple(item.chunk_id for item in candidates if _has_overlap(text, item)), 0.7
    if candidates:
        return SupportStatus.UNCERTAIN, (), 0.35
    return SupportStatus.UNSUPPORTED, (), 1.0


def _evidence_overlap(text: str, chunks: list[EvidenceChunk]) -> bool:
    return any(_has_overlap(text, item) for item in chunks)


def _has_overlap(text: str, chunk: EvidenceChunk) -> bool:
    source = " ".join([chunk.title, chunk.summary, chunk.content])
    if set(_content_terms(text)) & set(_content_terms(source)):
        return True
    # Chinese source excerpts often have no whitespace token boundaries.
    return bool(_chinese_bigrams(text) & _chinese_bigrams(source))


def _content_terms(text: str) -> list[str]:
    return [item.lower() for item in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}|\d+(?:\.\d+)?", text)]


def _chinese_bigrams(text: str) -> set[str]:
    characters = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    return {characters[index:index + 2] for index in range(max(0, len(characters) - 1))}
