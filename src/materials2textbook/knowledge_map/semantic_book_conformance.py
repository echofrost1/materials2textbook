from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from materials2textbook.knowledge_map.rendered_conformance import (
    RenderedConformanceReport,
    RenderedOccurrence,
    check_rendered_conformance,
    extract_rendered_occurrences,
)
from materials2textbook.knowledge_map.writing_briefs import WritingBriefCoverage


@dataclass
class SemanticBookConformanceReport:
    total_occurrences: int
    brief_covered_occurrences: int
    fallback_occurrences: list[dict[str, Any]]
    markdown: dict[str, Any]
    digital_book: dict[str, Any]
    markdown_anchor_coverage: float
    digital_book_anchor_coverage: float
    occurrence_alignment: dict[str, Any]
    unresolved_semantic_cases: list[dict[str, Any]] = field(default_factory=list)
    legacy_deduplication: dict[str, Any] = field(default_factory=dict)
    section_discourse: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_semantic_book_conformance_report(
    *,
    coverage: WritingBriefCoverage,
    markdown: str,
    digital_book_metadata: dict[str, Any],
) -> SemanticBookConformanceReport:
    markdown_records = extract_rendered_occurrences(markdown)
    markdown_report = check_rendered_conformance(coverage.briefs, markdown)
    digital_payload = digital_book_metadata.get("semantic_rendered_conformance") or {}
    digital_roles = digital_book_metadata.get("semantic_occurrence_roles") or {}
    digital_report = _report_from_payload(digital_payload)
    expected_roles = {item.occurrence_id: item.role for item in coverage.briefs}
    expected_roles.update({item.occurrence_id: "FALLBACK" for item in coverage.fallback_occurrences})
    zero_render_ids = {item.occurrence_id for item in coverage.zero_render_occurrences}
    digital_zero_render_ids = {
        str(item.get("occurrence_id"))
        for item in digital_book_metadata.get("semantic_zero_render_occurrences", [])
        if isinstance(item, dict) and item.get("occurrence_id")
    }
    markdown_ids = {item.occurrence_id for item in markdown_records}
    digital_ids = set(digital_roles)
    common = sorted(set(expected_roles) & markdown_ids & digital_ids)
    role_mismatches = [
        {
            "occurrence_id": occurrence_id,
            "expected_role": expected_roles[occurrence_id],
            "digital_book_role": digital_roles.get(occurrence_id, ""),
        }
        for occurrence_id in common
        if expected_roles[occurrence_id] != digital_roles.get(occurrence_id)
    ]
    expected_count = len(expected_roles)
    aligned_count = len(common) - len(role_mismatches)
    unexpected_markdown_zero = sorted(zero_render_ids & markdown_ids)
    unexpected_digital_zero = sorted(zero_render_ids & digital_ids)
    zero_audit_missing = sorted(zero_render_ids - digital_zero_render_ids)
    section_discourse = _section_discourse_summary(
        markdown=markdown,
        digital_book_metadata=digital_book_metadata,
    )
    return SemanticBookConformanceReport(
        total_occurrences=coverage.total_occurrences,
        brief_covered_occurrences=len(coverage.briefs),
        fallback_occurrences=[asdict(item) for item in coverage.fallback_occurrences],
        markdown=_summarize_rendered_report(markdown_report),
        digital_book=_summarize_rendered_report(digital_report),
        markdown_anchor_coverage=(len(markdown_ids & set(expected_roles)) / expected_count if expected_count else 1.0),
        digital_book_anchor_coverage=(len(digital_ids & set(expected_roles)) / expected_count if expected_count else 1.0),
        occurrence_alignment={
            "expected_occurrences": expected_count,
            "aligned_occurrences": aligned_count,
            "alignment_rate": aligned_count / expected_count if expected_count else 1.0,
            "missing_markdown_occurrences": sorted(set(expected_roles) - markdown_ids),
            "missing_digital_book_occurrences": sorted(set(expected_roles) - digital_ids),
            "role_mismatches": role_mismatches,
            "expected_rendered_occurrences": expected_count,
            "explicit_zero_render_occurrences": len(zero_render_ids),
            "zero_render_audit_missing_from_digital_book": zero_audit_missing,
            "zero_render_unexpected_markdown_bodies": unexpected_markdown_zero,
            "zero_render_unexpected_digital_book_bodies": unexpected_digital_zero,
        },
        unresolved_semantic_cases=[asdict(item) for item in coverage.fallback_occurrences],
        legacy_deduplication={
            "mode": "semantic_book_mode",
            "called": False,
            "exporter_record": digital_book_metadata.get("content_deduplication", []),
        },
        section_discourse=section_discourse,
    )


def _section_discourse_summary(*, markdown: str, digital_book_metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate section assembly without re-planning semantic meaning."""
    assemblies = digital_book_metadata.get("semantic_section_assemblies") or []
    sections: list[dict[str, Any]] = []
    for item in assemblies:
        if not isinstance(item, dict):
            continue
        sections.append({
            "chapter_id": str(item.get("chapter_id") or ""),
            "section_id": str(item.get("section_id") or ""),
            "title": str(item.get("title") or ""),
            "visible_title_count": int(item.get("visible_title_count") or 0),
            "rendered_occurrence_ids": list(item.get("rendered_occurrence_ids") or []),
            "blocked_occurrence_ids": list(item.get("blocked_occurrence_ids") or []),
            "zero_render_occurrence_ids": list(item.get("zero_render_occurrence_ids") or []),
            "order_preserved": bool(item.get("order_preserved", True)),
            "passage_id": str(item.get("passage_id") or ""),
            "visible_passage_count": int(item.get("visible_passage_count") or 0),
            "transition_count": len(item.get("transitions") or []),
        })

    header_counts: dict[str, int] = {}
    for line in markdown.splitlines():
        if line.startswith("### "):
            title = line[4:].strip()
            header_counts[title] = header_counts.get(title, 0) + 1
    title_mismatches = [
        {
            "section_id": item["section_id"],
            "title": item["title"],
            "expected": 1,
            "actual": header_counts.get(item["title"], 0),
        }
        for item in sections
        if item["title"] and header_counts.get(item["title"], 0) != 1
    ]
    order_violations = [item["section_id"] for item in sections if not item["order_preserved"]]
    passage_violations = [
        item["section_id"]
        for item in sections
        if item["rendered_occurrence_ids"]
        and (not item["passage_id"] or item["visible_passage_count"] != 1)
    ]
    return {
        "status": "MATCH" if not title_mismatches and not order_violations and not passage_violations else "VIOLATION",
        "section_count": len(sections),
        "visible_title_mismatches": title_mismatches,
        "order_violations": order_violations,
        "passage_violations": passage_violations,
        "sections": sections,
    }


def _report_from_payload(payload: dict[str, Any]) -> RenderedConformanceReport:
    # The report object is only used for aggregation. Keep the stored
    # structure intact so reruns remain deterministic and JSON-reproducible.
    from materials2textbook.knowledge_map.rendered_conformance import (
        ConformanceViolation,
        RenderedConformanceResult,
    )

    results = []
    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        violations = [ConformanceViolation(**value) for value in item.get("forbidden_reteach_violation", []) if isinstance(value, dict)]
        results.append(
            RenderedConformanceResult(
                occurrence_id=str(item.get("occurrence_id") or ""),
                role=str(item.get("role") or ""),
                anchor_present=bool(item.get("anchor_present")),
                role_conformance=str(item.get("role_conformance") or "VIOLATION"),
                must_teach_coverage=dict(item.get("must_teach_coverage") or {}),
                forbidden_reteach_violation=violations,
                extension_coverage=dict(item.get("extension_coverage") or {}),
                contribution_goal_coverage=str(item.get("contribution_goal_coverage") or "VIOLATION"),
                overall=str(item.get("overall") or "VIOLATION"),
                notes=list(item.get("notes") or []),
                render_decision=str(item.get("render_decision") or "RENDER"),
                body_present=bool(item.get("body_present")),
            )
        )
    return RenderedConformanceReport(
        results=results,
        anchor_coverage=float(payload.get("anchor_coverage") or 0.0),
        expected_rendered_occurrences=int(payload.get("expected_rendered_occurrences") or 0),
        explicit_zero_render_occurrences=int(payload.get("explicit_zero_render_occurrences") or 0),
    )


def _summarize_rendered_report(report: RenderedConformanceReport) -> dict[str, Any]:
    counts = {"MATCH": 0, "PARTIAL": 0, "VIOLATION": 0, "NOT_APPLICABLE": 0}
    forbidden = []
    missing_facets = []
    missing_extensions = []
    missing_contribution = []
    for item in report.results:
        if item.overall in counts:
            counts[item.overall] += 1
        forbidden.extend(
            {"occurrence_id": item.occurrence_id, "rule": value.rule, "sentence": value.sentence}
            for value in item.forbidden_reteach_violation
        )
        missing_facets.extend(
            {"occurrence_id": item.occurrence_id, "facet": key, "status": value}
            for key, value in item.must_teach_coverage.items()
            if value != "MATCH"
        )
        missing_extensions.extend(
            {"occurrence_id": item.occurrence_id, "extension_key": key, "status": value}
            for key, value in item.extension_coverage.items()
            if value != "MATCH"
        )
        if item.contribution_goal_coverage != "MATCH":
            missing_contribution.append({"occurrence_id": item.occurrence_id, "status": item.contribution_goal_coverage})
    return {
        "anchor_coverage": report.anchor_coverage,
        "status_counts": counts,
        "forbidden_reteach_violations": forbidden,
        "missing_must_teach_facets": missing_facets,
        "missing_extension_coverage": missing_extensions,
        "missing_contribution_goal_coverage": missing_contribution,
        "results": [asdict(item) for item in report.results],
    }


def render_semantic_book_conformance_markdown(report: SemanticBookConformanceReport) -> str:
    lines = [
        "# Semantic Book Conformance Report",
        "",
        f"- total occurrences: {report.total_occurrences}",
        f"- brief-covered occurrences: {report.brief_covered_occurrences}",
        f"- fallback occurrences: {len(report.fallback_occurrences)}",
        f"- Markdown anchor coverage: {report.markdown_anchor_coverage:.0%}",
        f"- DigitalBook anchor coverage: {report.digital_book_anchor_coverage:.0%}",
        f"- Markdown/DigitalBook alignment: {report.occurrence_alignment['alignment_rate']:.0%}",
        "",
    ]
    for name, summary in (("Markdown", report.markdown), ("DigitalBook", report.digital_book)):
        lines.extend([
            f"## {name}",
            f"- MATCH / PARTIAL / VIOLATION: {summary['status_counts']}",
            f"- forbidden reteach violations: {len(summary['forbidden_reteach_violations'])}",
            f"- missing must-teach facets: {len(summary['missing_must_teach_facets'])}",
            f"- missing extension coverage: {len(summary['missing_extension_coverage'])}",
            f"- missing contribution goal: {len(summary['missing_contribution_goal_coverage'])}",
            "",
        ])
    lines.extend(["## Fallback / unresolved semantic cases", ""])
    if report.fallback_occurrences:
        lines.extend(f"- `{item['occurrence_id']}`: {item['reason']}" for item in report.fallback_occurrences)
    else:
        lines.append("- none")
    lines.extend(["", "## Renderer alignment", ""])
    if report.occurrence_alignment["role_mismatches"]:
        lines.extend(
            f"- `{item['occurrence_id']}`: expected {item['expected_role']}, DigitalBook {item['digital_book_role']}"
            for item in report.occurrence_alignment["role_mismatches"]
        )
    else:
        lines.append("- no role mismatches")
    lines.extend(["", "## Section discourse", ""])
    lines.append(f"- status: {report.section_discourse.get('status', 'NOT_REPORTED')}")
    lines.append(f"- sections: {report.section_discourse.get('section_count', 0)}")
    lines.append(f"- visible title mismatches: {len(report.section_discourse.get('visible_title_mismatches', []))}")
    lines.append(f"- order violations: {len(report.section_discourse.get('order_violations', []))}")
    lines.append(
        f"- coherent visible passages: "
        f"{sum(1 for item in report.section_discourse.get('sections', []) if item.get('visible_passage_count') == 1)}"
    )
    lines.extend(["", "## Legacy cross-reference de-duplication", "", f"- called: {report.legacy_deduplication['called']}"])
    return "\n".join(lines).rstrip() + "\n"
