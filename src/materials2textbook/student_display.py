"""Student-visible title selection at the rendering boundary.

Planning and semantic artifacts may carry internal context labels.  This module
does not rewrite those labels into nicer prose: it selects an independently
identified student display title or a neutral fallback instead.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_INTERNAL_LABEL_PATTERNS = (
    re.compile(r"\b(?:INTRO|TEACH|RECALL|APPLY|EXTEND)\s*:", re.IGNORECASE),
    re.compile(r"\bduplicate\s+teach\b", re.IGNORECASE),
    re.compile(r"\bbridge\s*task\b", re.IGNORECASE),
    re.compile(r"\bphase\s*\d+(?:[a-z]|\.\d+)*\b", re.IGNORECASE),
    re.compile(r"\b(?:fixture|test|debug|evaluation)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class DisplayTitleDecision:
    internal_context_title: str
    display_title: str
    source: str  # explicit_display_title | knowledge_title | original_title | fallback


@dataclass(frozen=True)
class DisplayMetadata:
    """Presentation-only title data associated with an immutable outline node."""

    outline_node_id: str
    title: DisplayTitleDecision


def internal_label_matches(text: str) -> tuple[str, ...]:
    value = str(text or "")
    return tuple(match.group(0) for pattern in _INTERNAL_LABEL_PATTERNS for match in pattern.finditer(value))


def is_internal_context_label(text: str) -> bool:
    return bool(internal_label_matches(text))


def decide_display_title(
    internal_context_title: str,
    *,
    explicit_display_title: str = "",
    knowledge_titles: list[str] | tuple[str, ...] = (),
    fallback: str = "本任务",
) -> DisplayTitleDecision:
    """Choose a student title without exposing an internal planning label."""
    internal = _normalise(internal_context_title)
    explicit = _normalise(explicit_display_title)
    if explicit and not is_internal_context_label(explicit):
        return DisplayTitleDecision(internal, explicit, "explicit_display_title")
    if internal and not is_internal_context_label(internal):
        return DisplayTitleDecision(internal, internal, "original_title")
    for title in knowledge_titles:
        candidate = _normalise(title)
        if candidate and not is_internal_context_label(candidate):
            return DisplayTitleDecision(internal, candidate, "knowledge_title")
    return DisplayTitleDecision(internal, _normalise(fallback) or "本任务", "fallback")


def student_display_title(
    internal_context_title: str,
    *,
    explicit_display_title: str = "",
    knowledge_titles: list[str] | tuple[str, ...] = (),
    fallback: str = "本任务",
) -> str:
    return decide_display_title(
        internal_context_title,
        explicit_display_title=explicit_display_title,
        knowledge_titles=knowledge_titles,
        fallback=fallback,
    ).display_title


def display_metadata_for_outline_node(
    outline_node_id: str,
    original_title: str,
    *,
    knowledge_titles: list[str] | tuple[str, ...] = (),
    explicit_display_title: str = "",
    fallback: str = "学习内容",
) -> DisplayMetadata:
    """Create a rendering overlay without writing presentation values into BookPlan."""
    return DisplayMetadata(
        outline_node_id=outline_node_id,
        title=decide_display_title(
            original_title,
            explicit_display_title=explicit_display_title,
            knowledge_titles=knowledge_titles,
            fallback=fallback,
        ),
    )


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ：:;；，。-_")
