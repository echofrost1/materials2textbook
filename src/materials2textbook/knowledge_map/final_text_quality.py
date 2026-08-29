"""Deterministic, read-only checks for student-visible final text."""

from __future__ import annotations

import re

from materials2textbook.knowledge_map.publication_quality_models import (
    PublicationContentFragment,
    PublicationSeverity,
)
from materials2textbook.student_display import internal_label_matches


class FinalTextQualityCode:
    CORRUPTED_TEXT = "CORRUPTED_TEXT"
    BROKEN_SENTENCE = "BROKEN_SENTENCE"
    PLACEHOLDER_LEAKAGE = "PLACEHOLDER_LEAKAGE"
    INTERNAL_LABEL_LEAKAGE = "INTERNAL_LABEL_LEAKAGE"
    ABNORMAL_LANGUAGE_MIX = "ABNORMAL_LANGUAGE_MIX"
    SUSPICIOUS_DOMAIN_TERM = "SUSPICIOUS_DOMAIN_TERM"
    DUPLICATED_SENTENCE = "DUPLICATED_SENTENCE"
    EMPTY_OR_TRIVIAL_SECTION = "EMPTY_OR_TRIVIAL_SECTION"


_CORRUPTION_MARKERS = ("汉箱", "西骨", "示污级", "端步打磨程瑞追醒")
_PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME|PLACEHOLDER)\b|\{\{.+?\}\}|\[\[.+?\]\]", re.IGNORECASE)
_EVIDENCE_TRACE = re.compile(r"\bEvidence\s*:\s*C\d+\b", re.IGNORECASE)
_ENGLISH_LABEL = re.compile(r"\b(?:Implementation|Evidence|Bridge\s*task)\s*:?", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*|\n+")


def inspect_final_text(fragments: list[PublicationContentFragment]) -> list[dict[str, object]]:
    """Return raw findings; publication_quality owns final issue identities."""
    findings: list[dict[str, object]] = []
    seen_sentences: dict[str, PublicationContentFragment] = {}
    for fragment in fragments:
        text = fragment.text.strip()
        # Short titles such as “第一章” are legitimate navigation labels.
        # A publication blocker is reserved for an instructional section that
        # is empty or has no usable instructional content.
        instructional_component = fragment.component in {
            "occurrence", "implementation", "scenario", "assessment", "exercises",
        }
        if not text or (instructional_component and _is_trivial(text)):
            findings.append(_finding(FinalTextQualityCode.EMPTY_OR_TRIVIAL_SECTION, PublicationSeverity.BLOCKER, fragment, text or "(empty)"))
            continue
        for match in internal_label_matches(text):
            findings.append(_finding(FinalTextQualityCode.INTERNAL_LABEL_LEAKAGE, PublicationSeverity.BLOCKER, fragment, match))
        for pattern in (_EVIDENCE_TRACE, _ENGLISH_LABEL):
            for match in pattern.finditer(text):
                findings.append(_finding(FinalTextQualityCode.INTERNAL_LABEL_LEAKAGE, PublicationSeverity.BLOCKER, fragment, match.group(0)))
        for match in _PLACEHOLDER.finditer(text):
            findings.append(_finding(FinalTextQualityCode.PLACEHOLDER_LEAKAGE, PublicationSeverity.BLOCKER, fragment, match.group(0)))
        for marker in _CORRUPTION_MARKERS:
            if marker in text:
                findings.append(_finding(FinalTextQualityCode.CORRUPTED_TEXT, PublicationSeverity.BLOCKER, fragment, marker))
                findings.append(_finding(FinalTextQualityCode.BROKEN_SENTENCE, PublicationSeverity.BLOCKER, fragment, _sentence_with(text, marker)))
                findings.append(_finding(FinalTextQualityCode.SUSPICIOUS_DOMAIN_TERM, PublicationSeverity.WARNING, fragment, marker))
        if _abnormal_language_mix(text):
            findings.append(_finding(FinalTextQualityCode.ABNORMAL_LANGUAGE_MIX, PublicationSeverity.WARNING, fragment, _language_excerpt(text)))
        for sentence in _sentences(text):
            key = _normalise_sentence(sentence)
            if len(key) < 16:
                continue
            previous = seen_sentences.get(key)
            if previous and previous.location != fragment.location:
                findings.append(_finding(FinalTextQualityCode.DUPLICATED_SENTENCE, PublicationSeverity.WARNING, fragment, sentence))
            else:
                seen_sentences[key] = fragment
    return _dedupe_findings(findings)


def _finding(code: str, severity: str, fragment: PublicationContentFragment, span: str) -> dict[str, object]:
    return {"code": code, "severity": severity, "fragment": fragment, "span": span}


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]


def _sentence_with(text: str, marker: str) -> str:
    return next((item for item in _sentences(text) if marker in item), marker)


def _normalise_sentence(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value).lower()


def _is_trivial(text: str) -> bool:
    return len(re.sub(r"\s+", "", text)) < 12 or text in {"-", "—", "暂无内容", "无"}


def _abnormal_language_mix(text: str) -> bool:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = re.findall(r"[A-Za-z]{4,}", text)
    return chinese >= 8 and len(english_words) >= 3


def _language_excerpt(text: str) -> str:
    words = re.findall(r"[A-Za-z]{4,}", text)
    return " ".join(words[:4])


def _dedupe_findings(items: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    result = []
    for item in items:
        fragment = item["fragment"]
        key = (str(item["code"]), fragment.location, str(item["span"]))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
