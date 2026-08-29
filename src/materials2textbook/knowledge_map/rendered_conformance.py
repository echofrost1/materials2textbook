from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import escape
import re

from materials2textbook.knowledge_map.models import LearningRole
from materials2textbook.knowledge_map.writing_briefs import (
    FallbackOccurrence,
    OccurrenceWritingBrief,
    RenderDecision,
    ZeroRenderOccurrence,
)


class ConformanceStatus:
    MATCH = "MATCH"
    PARTIAL = "PARTIAL"
    VIOLATION = "VIOLATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class RenderedOccurrence:
    """A code-owned Markdown span for exactly one planned occurrence."""

    occurrence_id: str
    chapter_id: str
    section_id: str
    task_id: str
    markdown: str
    start_offset: int
    end_offset: int
    render_target: str = "markdown"
    block_id: str = ""
    generation_provenance: str = "unknown"


@dataclass(frozen=True)
class ConformanceViolation:
    rule: str
    sentence: str


@dataclass
class RenderedConformanceResult:
    occurrence_id: str
    role: str
    anchor_present: bool
    role_conformance: str
    must_teach_coverage: dict[str, str]
    forbidden_reteach_violation: list[ConformanceViolation]
    extension_coverage: dict[str, str]
    contribution_goal_coverage: str
    overall: str
    notes: list[str] = field(default_factory=list)
    render_decision: str = RenderDecision.RENDER
    body_present: bool = False


@dataclass
class RenderedConformanceReport:
    results: list[RenderedConformanceResult]
    anchor_coverage: float
    expected_rendered_occurrences: int = 0
    explicit_zero_render_occurrences: int = 0

    def to_dict(self) -> dict:
        return {"anchor_coverage": self.anchor_coverage, "results": [asdict(item) for item in self.results]}


_START = re.compile(
    r'<!-- occurrence:start id="(?P<id>[^"]+)" chapter="(?P<chapter>[^"]*)" '
    r'section="(?P<section>[^"]*)" task="(?P<task>[^"]*)"'
    r'(?: provenance="(?P<provenance>[^"]*)")? -->'
)
_END = re.compile(r'<!-- occurrence:end id="(?P<id>[^"]+)" -->')


def occurrence_task_id(brief: OccurrenceWritingBrief | FallbackOccurrence) -> str:
    """Stable task identity even when the source plan has only an ordinal."""
    ordinal = brief.task_ordinal or brief.occurrence_ordinal or 1
    return f"{brief.chapter_id}:task:{ordinal}"


def wrap_rendered_occurrence(
    brief: OccurrenceWritingBrief | FallbackOccurrence,
    markdown: str,
    *,
    generation_provenance: str = "unknown",
) -> str:
    """Put code-generated anchors around an LLM body; the LLM never owns them."""
    start = (
        f'<!-- occurrence:start id="{escape(brief.occurrence_id, quote=True)}" '
        f'chapter="{escape(brief.chapter_id, quote=True)}" '
        f'section="{escape(brief.section_id, quote=True)}" '
        f'task="{escape(occurrence_task_id(brief), quote=True)}" '
        f'provenance="{escape(generation_provenance or "unknown", quote=True)}" -->'
    )
    end = f'<!-- occurrence:end id="{escape(brief.occurrence_id, quote=True)}" -->'
    return f"{start}\n{markdown.strip()}\n{end}\n"


def extract_rendered_occurrences(markdown: str) -> list[RenderedOccurrence]:
    """Parse only deterministic anchors; unmarked LLM text has no occurrence span."""
    rendered: list[RenderedOccurrence] = []
    cursor = 0
    while start_match := _START.search(markdown, cursor):
        end_match = _END.search(markdown, start_match.end())
        if not end_match or end_match.group("id") != start_match.group("id"):
            cursor = start_match.end()
            continue
        body_start = start_match.end()
        if markdown[body_start:body_start + 1] == "\n":
            body_start += 1
        body_end = end_match.start()
        while body_end > body_start and markdown[body_end - 1] == "\n":
            body_end -= 1
        rendered.append(
            RenderedOccurrence(
                occurrence_id=start_match.group("id"),
                chapter_id=start_match.group("chapter"),
                section_id=start_match.group("section"),
                task_id=start_match.group("task"),
                markdown=markdown[body_start:body_end],
                start_offset=body_start,
                end_offset=body_end,
                generation_provenance=start_match.group("provenance") or "unknown",
            )
        )
        cursor = end_match.end()
    return rendered


def check_rendered_conformance(
    briefs: list[OccurrenceWritingBrief], markdown: str,
    zero_render_occurrences: list[ZeroRenderOccurrence] | None = None,
) -> RenderedConformanceReport:
    """Read-only check of a rendered body against immutable brief constraints.

    It deliberately does not infer a different role, modify text, or repair a
    violation.  All decisions below are deterministic and auditable.
    """
    return check_rendered_occurrence_records(
        briefs,
        extract_rendered_occurrences(markdown),
        zero_render_occurrences=zero_render_occurrences,
    )


def check_rendered_occurrence_records(
    briefs: list[OccurrenceWritingBrief], records: list[RenderedOccurrence],
    *, zero_render_occurrences: list[ZeroRenderOccurrence] | None = None,
) -> RenderedConformanceReport:
    """Check any renderer's occurrence records against the same immutable brief."""
    rendered_by_id = {item.occurrence_id: item for item in records}
    zero_render_occurrences = zero_render_occurrences or []
    results: list[RenderedConformanceResult] = []
    for brief in briefs:
        rendered = rendered_by_id.get(brief.occurrence_id)
        if not rendered:
            results.append(
                RenderedConformanceResult(
                    occurrence_id=brief.occurrence_id,
                    role=brief.role,
                    anchor_present=False,
                    role_conformance=ConformanceStatus.VIOLATION,
                    must_teach_coverage={item: ConformanceStatus.VIOLATION for item in brief.must_teach_facets},
                    forbidden_reteach_violation=[],
                    extension_coverage={item: ConformanceStatus.VIOLATION for item in brief.extension_keys},
                    contribution_goal_coverage=ConformanceStatus.VIOLATION,
                    overall=ConformanceStatus.VIOLATION,
                    notes=["Missing code-generated occurrence anchor."],
                    render_decision=RenderDecision.RENDER,
                    body_present=False,
                )
            )
            continue
        results.append(_check_one(brief, rendered))
    for zero in zero_render_occurrences:
        rendered = rendered_by_id.get(zero.occurrence_id)
        if rendered is not None:
            results.append(RenderedConformanceResult(
                occurrence_id=zero.occurrence_id,
                role=zero.role,
                anchor_present=True,
                role_conformance=ConformanceStatus.VIOLATION,
                must_teach_coverage={},
                forbidden_reteach_violation=[],
                extension_coverage={},
                contribution_goal_coverage=ConformanceStatus.VIOLATION,
                overall=ConformanceStatus.VIOLATION,
                notes=["Explicit ZERO_RENDER occurrence has a student-visible rendered body."],
                render_decision=RenderDecision.ZERO_RENDER,
                body_present=bool(rendered.markdown.strip()),
            ))
        else:
            results.append(RenderedConformanceResult(
                occurrence_id=zero.occurrence_id,
                role=zero.role,
                anchor_present=False,
                role_conformance=ConformanceStatus.NOT_APPLICABLE,
                must_teach_coverage={},
                forbidden_reteach_violation=[],
                extension_coverage={},
                contribution_goal_coverage=ConformanceStatus.NOT_APPLICABLE,
                overall=ConformanceStatus.NOT_APPLICABLE,
                notes=[f"Explicit ZERO_RENDER: {zero.non_render_reason}"],
                render_decision=RenderDecision.ZERO_RENDER,
                body_present=False,
            ))
    expected_ids = {item.occurrence_id for item in briefs}
    coverage = len(set(rendered_by_id) & expected_ids) / len(briefs) if briefs else 1.0
    return RenderedConformanceReport(
        results=results,
        anchor_coverage=coverage,
        expected_rendered_occurrences=len(briefs),
        explicit_zero_render_occurrences=len(zero_render_occurrences),
    )


def _check_one(brief: OccurrenceWritingBrief, rendered: RenderedOccurrence) -> RenderedConformanceResult:
    sentences = _sentences(rendered.markdown)
    violations = _forbidden_reteach_violations(brief, sentences)
    facet_coverage = {facet: _facet_coverage(facet, rendered.markdown) for facet in brief.must_teach_facets}
    extension_coverage = {key: _extension_coverage(key, rendered.markdown) for key in brief.extension_keys}
    substantive_count = _substantive_sentence_count(sentences)
    role = _role_conformance(brief, rendered.markdown, violations, facet_coverage, extension_coverage, substantive_count)
    contribution = _contribution_coverage(brief, rendered.markdown, role, facet_coverage, extension_coverage)
    statuses = [role, contribution, *facet_coverage.values(), *extension_coverage.values()]
    overall = (
        ConformanceStatus.VIOLATION if violations or ConformanceStatus.VIOLATION in statuses
        else ConformanceStatus.PARTIAL if ConformanceStatus.PARTIAL in statuses
        else ConformanceStatus.MATCH
    )
    notes: list[str] = []
    if (
        brief.role in {LearningRole.TEACH, LearningRole.RECALL}
        and substantive_count > brief.max_recap_sentences
        and not brief.must_teach_facets
        and not brief.extension_keys
    ):
        notes.append(f"Rendered {substantive_count} substantive sentences; recap limit is {brief.max_recap_sentences}.")
    return RenderedConformanceResult(
        occurrence_id=brief.occurrence_id,
        role=brief.role,
        anchor_present=True,
        role_conformance=role,
        must_teach_coverage=facet_coverage,
        forbidden_reteach_violation=violations,
        extension_coverage=extension_coverage,
        contribution_goal_coverage=contribution,
        overall=overall,
        notes=notes,
        render_decision=RenderDecision.RENDER,
        body_present=bool(rendered.markdown.strip()),
    )


def _role_conformance(
    brief: OccurrenceWritingBrief,
    text: str,
    violations: list[ConformanceViolation],
    facets: dict[str, str],
    extensions: dict[str, str],
    substantive_count: int,
) -> str:
    if violations:
        return ConformanceStatus.VIOLATION
    if not text.strip():
        return ConformanceStatus.VIOLATION
    if (
        brief.role in {LearningRole.TEACH, LearningRole.RECALL}
        and not brief.must_teach_facets
        and not brief.extension_keys
        and substantive_count > brief.max_recap_sentences
    ):
        return ConformanceStatus.VIOLATION
    if brief.role == LearningRole.APPLY and not _contains_any(text, ("apply", "use", "task", "应用", "使用", "任务", "选择", "操作")):
        return ConformanceStatus.PARTIAL
    if brief.role == LearningRole.EXTEND and ConformanceStatus.MATCH not in extensions.values():
        return ConformanceStatus.PARTIAL
    if any(value != ConformanceStatus.MATCH for value in facets.values()):
        return ConformanceStatus.PARTIAL
    return ConformanceStatus.MATCH


def _contribution_coverage(
    brief: OccurrenceWritingBrief,
    text: str,
    role: str,
    facets: dict[str, str],
    extensions: dict[str, str],
) -> str:
    if role == ConformanceStatus.VIOLATION:
        return ConformanceStatus.VIOLATION
    if brief.role == LearningRole.EXTEND:
        return _aggregate_coverage(extensions.values())
    if brief.must_teach_facets:
        return _aggregate_coverage(facets.values())
    if brief.role == LearningRole.APPLY:
        return ConformanceStatus.MATCH if _contains_any(text, ("apply", "use", "应用", "使用", "任务", "操作")) else ConformanceStatus.PARTIAL
    if brief.role in {LearningRole.RECALL, LearningRole.TEACH} and not brief.extension_keys:
        return ConformanceStatus.MATCH if len(_sentences(text)) <= brief.max_recap_sentences else ConformanceStatus.VIOLATION
    return ConformanceStatus.MATCH


def _aggregate_coverage(statuses) -> str:
    values = list(statuses)
    if not values:
        return ConformanceStatus.NOT_APPLICABLE
    if all(item == ConformanceStatus.MATCH for item in values):
        return ConformanceStatus.MATCH
    if any(item == ConformanceStatus.MATCH for item in values):
        return ConformanceStatus.PARTIAL
    return ConformanceStatus.VIOLATION


def _facet_coverage(facet: str, text: str) -> str:
    signals = {
        "ORIENTED": ("direction", "方向", "正接", "反接"),
        "EXPLAIN": ("definition", "defined", "explain", "explanation", "affects", "定义", "是指", "原理", "作用", "影响", "解释"),
        "PERFORM": ("step", "步骤", "操作", "执行", "调整"),
        "ANALYZE": ("analysis", "analyze", "分析", "判断", "原因"),
    }.get(facet, ())
    # Never treat an internal facet label as proof that the facet was taught.
    # Remove labels before applying the legacy explanatory/procedural signals.
    semantic_text = re.sub(r"\b(?:ORIENTED|EXPLAIN|PERFORM|ANALYZE)\b", "", text, flags=re.IGNORECASE)
    if _contains_any(semantic_text, signals):
        return ConformanceStatus.MATCH
    # EXPLAIN is a teaching action, not a requirement that authors literally
    # write the word "definition".  These are intentionally narrow Chinese
    # explanatory constructions, kept deterministic so a checker does not
    # re-plan the occurrence role after rendering.
    if facet == "EXPLAIN" and re.search(
        r"(?:是(?:一[种个])?|指(?:的)?|通过|能够|用于|从而|有助于|体现出?|适合).{2,}",
        text,
    ):
        return ConformanceStatus.MATCH
    return ConformanceStatus.VIOLATION


def _extension_coverage(extension_key: str, text: str) -> str:
    tokens = _extension_tokens(extension_key)
    return ConformanceStatus.MATCH if tokens and _contains_any(text, tokens) else ConformanceStatus.PARTIAL


def _extension_tokens(key: str) -> tuple[str, ...]:
    lowered = key.lower()
    tokens: list[str] = [piece for piece in re.split(r"[:_\\-]+", lowered) if len(piece) > 2]
    if "thin" in lowered and "plate" in lowered:
        tokens.extend(["thin plate", "薄板"])
    if "burn" in lowered:
        tokens.extend(["burn-through", "烧穿"])
    if "limit" in lowered:
        tokens.extend(["limit", "限制", "限流"])
    return tuple(dict.fromkeys(tokens))


def _forbidden_reteach_violations(
    brief: OccurrenceWritingBrief, sentences: list[str],
) -> list[ConformanceViolation]:
    rules = _forbidden_rules(brief.must_avoid_patterns)
    violations: list[ConformanceViolation] = []
    for sentence in sentences:
        if _is_explicit_non_reteach(sentence):
            continue
        for rule, patterns in rules.items():
            if _contains_any(sentence, patterns):
                violations.append(ConformanceViolation(rule=rule, sentence=sentence))
                break
    return violations


def _forbidden_rules(keys: list[str]) -> dict[str, tuple[str, ...]]:
    rules: dict[str, tuple[str, ...]] = {}
    normalized = " ".join(item.lower() for item in keys)
    if "definition" in normalized:
        rules["definition"] = (" is defined as ", "是指", "定义", "概念讲解", "definition")
    if "principle" in normalized or "effect" in normalized:
        rules["principle_explanation"] = ("基本原理", "作用是", "决定", "直接影响", "principle", "effect on", "affects")
    if "procedure" in normalized:
        rules["complete_procedure"] = ("操作步骤", "步骤如下", "step 1", "第一步", "第二步", "完整步骤")
    if "method" in normalized or "parameter" in normalized or "adjustment" in normalized:
        rules["parameter_or_method_rule"] = (
            "调整方法", "调节方法", "操作要点", "参数设置", "根据材料厚度",
            "adjust current according to", "set the current according to",
        )
    return rules


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？.!?])\s*|\n+", text)
    return [item.strip(" -•\t") for item in parts if item.strip(" -•\t") and not item.strip().startswith("Evidence:")]


def _substantive_sentence_count(sentences: list[str]) -> int:
    return sum(1 for item in sentences if not item.startswith("#") and len(item) > 12)


def _contains_any(text: str, patterns: tuple[str, ...] | list[str]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _is_explicit_non_reteach(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(token in lowered for token in (
        "无需重复", "不重复", "不再重复", "避免重复", "不重新讲授", "do not repeat", "without repeating", "not reteach",
    ))


def render_conformance_report_markdown(report: RenderedConformanceReport) -> str:
    lines = [
        "# Rendered Occurrence Conformance", "",
        f"- expected rendered occurrences: {report.expected_rendered_occurrences}",
        f"- explicit zero-render occurrences: {report.explicit_zero_render_occurrences}",
        f"- anchor coverage (expected rendered only): {report.anchor_coverage:.0%}", "",
    ]
    for item in report.results:
        lines.extend([
            f"## {item.occurrence_id}",
            f"- role: {item.role}",
            f"- anchor present: {item.anchor_present}",
            f"- render decision / body present: {item.render_decision} / {item.body_present}",
            f"- role conformance: {item.role_conformance}",
            f"- must teach coverage: {item.must_teach_coverage or 'not applicable'}",
            f"- extension coverage: {item.extension_coverage or 'not applicable'}",
            f"- contribution goal coverage: {item.contribution_goal_coverage}",
            f"- overall: {item.overall}",
        ])
        if item.forbidden_reteach_violation:
            lines.append("- forbidden reteach violations:")
            lines.extend(f"  - [{violation.rule}] {violation.sentence}" for violation in item.forbidden_reteach_violation)
        if item.notes:
            lines.append(f"- notes: {'; '.join(item.notes)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
