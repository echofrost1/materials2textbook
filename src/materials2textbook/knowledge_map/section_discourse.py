"""Section-level organization for already-rendered semantic occurrences.

This module is intentionally a presentation-layer operation.  It does not
re-plan an occurrence, change its role, reorder occurrences, or merge their
audit spans.  It removes repeated visible section headings, keeps adjacent
occurrence spans in one student-visible section passage, and adds a small,
deterministic discourse bridge when the immutable role requires one.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Any

from materials2textbook.knowledge_map.models import LearningRole
from materials2textbook.knowledge_map.writing_briefs import OccurrenceWritingBrief


def build_chapter_synthesis(chapter: Any) -> dict[str, str]:
    """Build a short, plan-derived chapter opening and summary.

    The synthesis is discourse only: it uses the frozen section titles and
    purposes to explain the learning path, but it never introduces a domain
    claim or changes section order/ownership.
    """

    sections = list(getattr(chapter, "sections", ()) or ())
    section_rows = []
    for item in sections:
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        purpose = _first_sentence(
            str(getattr(item, "section_purpose", "") or "").strip()
        )
        outcome = _first_sentence(
            str(getattr(item, "expected_learning_outcome", "") or "").strip()
        )
        section_rows.append((title, purpose, outcome))
    if not section_rows:
        return {"opening": "", "summary": ""}
    titles = [item[0] for item in section_rows]
    path = " → ".join(f"“{item}”" for item in titles)
    first_title, first_purpose, _first_outcome = section_rows[0]
    last_title, last_purpose, last_outcome = section_rows[-1]
    if len(section_rows) == 1:
        focus = first_purpose or first_title
        opening = f"本章从“{first_title}”切入，围绕{focus}建立后续学习所需的认识。"
        summary = f"回顾本章时，可从“{first_title}”重新梳理{last_outcome or focus}。"
    else:
        first_focus = first_purpose or first_title
        last_focus = last_purpose or last_title
        opening = (
            f"本章先从“{first_title}”的{first_focus}开始，随后把这份认识带入后续环节，"
            f"最后落到“{last_title}”的{last_focus}。"
        )
        summary = (
            f"回顾本章，可沿着{path}检查学习推进：先把基础问题说清，再逐步连接到后面的任务；"
            f"本章最后应能回到{last_outcome or last_title}。"
        )
    return {"opening": opening, "summary": summary}


def _first_sentence(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    match = re.search(r"[。！？!?]", value)
    return value[: match.end()].rstrip("。！？!?") if match else value


@dataclass(frozen=True)
class SectionDiscourseTransition:
    occurrence_id: str
    previous_occurrence_id: str = ""
    role: str = ""
    kind: str = "NONE"
    text: str = ""
    source_occurrence_ids: tuple[str, ...] = ()
    evidence_scope: str = "STRUCTURAL_OR_TRAJECTORY_ONLY"


@dataclass(frozen=True)
class SectionAssemblyAudit:
    chapter_id: str
    section_id: str
    title: str
    occurrence_ids: tuple[str, ...]
    rendered_occurrence_ids: tuple[str, ...]
    blocked_occurrence_ids: tuple[str, ...] = ()
    zero_render_occurrence_ids: tuple[str, ...] = ()
    visible_title_count: int = 1
    order_preserved: bool = True
    passage_id: str = ""
    visible_passage_count: int = 1
    transitions: tuple[SectionDiscourseTransition, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["transitions"] = [asdict(item) for item in self.transitions]
        return value


def build_section_discourse_bodies(
    rows: list[dict[str, Any]],
    briefs: list[OccurrenceWritingBrief],
) -> tuple[dict[str, str], list[SectionAssemblyAudit]]:
    """Add bounded bridges to rendered bodies while preserving span identity.

    ``rows`` must already be in semantic execution order.  The returned body
    map contains one body per rendered occurrence; callers still wrap each
    body with its own code-generated occurrence anchor.
    """
    brief_by_id = {item.occurrence_id: item for item in briefs}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    group_order: list[tuple[str, str]] = []
    for row in rows:
        occurrence_id = str(row.get("occurrence_id") or "")
        brief = brief_by_id.get(occurrence_id)
        if not brief:
            continue
        key = (brief.chapter_id, brief.section_id)
        if key not in grouped:
            grouped[key] = []
            group_order.append(key)
        grouped[key].append(row)

    bodies: dict[str, str] = {}
    audits: list[SectionAssemblyAudit] = []
    for chapter_id, section_id in group_order:
        section_rows = grouped[(chapter_id, section_id)]
        transitions: list[SectionDiscourseTransition] = []
        rendered_ids: list[str] = []
        occurrence_ids: list[str] = []
        previous: dict[str, Any] | None = None
        rendered_body_keys: dict[str, str] = {}
        for row in section_rows:
            occurrence_id = str(row.get("occurrence_id") or "")
            brief = brief_by_id[occurrence_id]
            occurrence_ids.append(occurrence_id)
            rendered_ids.append(occurrence_id)
            transition = _transition_for(brief, previous, brief_by_id)
            transitions.append(transition)
            body = _clean_visible_body(str(row.get("body") or ""))
            body_key = _visible_body_key(body)
            if body_key and body_key in rendered_body_keys:
                # A section-level passage already taught this exact content
                # for an earlier occurrence.  Keep the later occurrence
                # auditable, but replace the repeated lesson with a short
                # reader-facing bridge so the book does not read like copied
                # answers.  This presentation-only operation never changes
                # the occurrence spans used by runtime availability.
                prior_id = rendered_body_keys[body_key]
                prior_brief = brief_by_id.get(prior_id)
                body = _duplicate_body_bridge(brief, prior_brief)
            elif body_key:
                rendered_body_keys[body_key] = occurrence_id
            if transition.text and not _body_already_has_transition(body, brief.role):
                body = f"{transition.text}\n\n{body}" if body else transition.text
            bodies[occurrence_id] = body
            previous = row

        title = str(section_rows[0].get("source_title") or brief_by_id[occurrence_ids[0]].source_title)
        audits.append(
            SectionAssemblyAudit(
                chapter_id=chapter_id,
                section_id=section_id,
                title=title,
                occurrence_ids=tuple(occurrence_ids),
                rendered_occurrence_ids=tuple(rendered_ids),
                passage_id=f"{chapter_id}:{section_id}:passage",
                visible_passage_count=1 if rendered_ids else 0,
                transitions=tuple(transitions),
            )
        )
    return bodies, audits


def _visible_body_key(text: str) -> str:
    """Normalize a visible body for conservative same-section deduplication."""

    return re.sub(r"\s+", " ", str(text or "")).strip()


def _clean_visible_body(text: str) -> str:
    """Remove only exact blank list items and repeated visible paragraphs."""

    value = str(text or "").replace("\r\n", "\n").strip()
    if not value:
        return ""
    # Empty numbered items are generation debris, not teaching content.  A
    # non-empty item is preserved verbatim for the evidence/reader audit.
    value = re.sub(r"(?m)^[ \t]*\d+[.)、:：]?[ \t]*\r?\n?", "", value)
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", value) if part.strip()]
    seen: set[str] = set()
    kept: list[str] = []
    for paragraph in paragraphs:
        key = _visible_body_key(paragraph)
        if key in seen:
            continue
        seen.add(key)
        kept.append(paragraph)
    return "\n\n".join(kept).strip()


def _duplicate_body_bridge(
    brief: OccurrenceWritingBrief,
    prior_brief: OccurrenceWritingBrief | None,
) -> str:
    """Keep a later occurrence readable without repeating a full passage."""

    prior_title = ""
    if prior_brief is not None:
        prior_title = prior_brief.canonical_title or prior_brief.source_title
    current_title = brief.canonical_title or brief.source_title
    if prior_title and current_title and prior_title != current_title:
        return (
            f"前文已经介绍“{prior_title}”的基础内容。本处把这些要点连接到“{current_title}”的学习任务。"
        )
    if current_title:
        return f"关于“{current_title}”的基础内容已在本节前文说明。本处继续围绕当前任务展开。"
    return "前文已经说明相关基础，本处继续围绕当前任务展开。"


def complete_section_discourse_audits(
    audits: list[SectionAssemblyAudit],
    *,
    blocked_occurrence_ids: set[str] | None = None,
    zero_render_occurrence_ids: set[str] | None = None,
    section_catalog: list[dict[str, str]] | None = None,
) -> list[SectionAssemblyAudit]:
    """Attach non-rendered outcomes and formal empty sections without prose."""
    blocked = blocked_occurrence_ids or set()
    zero_render = zero_render_occurrence_ids or set()
    by_key = {(item.chapter_id, item.section_id): item for item in audits}
    catalog = section_catalog or [
        {"chapter_id": item.chapter_id, "section_id": item.section_id, "title": item.title}
        for item in audits
    ]
    catalog_keys = {(item.get("chapter_id", ""), item.get("section_id", "")) for item in catalog}

    def section_key(occurrence_id: str) -> tuple[str, str]:
        parts = str(occurrence_id).split(":")
        section_id = parts[1] if len(parts) > 1 and parts[0] == "occ" else ""
        chapter_id = section_id.rsplit("_section_", 1)[0] if "_section_" in section_id else ""
        return chapter_id, section_id

    blocked_by_section: dict[tuple[str, str], list[str]] = {}
    for occurrence_id in blocked:
        blocked_by_section.setdefault(section_key(occurrence_id), []).append(occurrence_id)
    zero_by_section: dict[tuple[str, str], list[str]] = {}
    for occurrence_id in zero_render:
        zero_by_section.setdefault(section_key(occurrence_id), []).append(occurrence_id)

    completed: list[SectionAssemblyAudit] = []
    ordered_keys = [
        (item.get("chapter_id", ""), item.get("section_id", ""))
        for item in catalog
    ]
    ordered_keys.extend(key for key in by_key if key not in catalog_keys)
    for key in ordered_keys:
        audit = by_key.get(key)
        catalog_item = next((item for item in catalog if (item.get("chapter_id", ""), item.get("section_id", "")) == key), {})
        rendered_ids = audit.rendered_occurrence_ids if audit else ()
        existing_ids = list(audit.occurrence_ids if audit else rendered_ids)
        blocked_ids = tuple(sorted(blocked_by_section.get(key, [])))
        zero_ids = tuple(sorted(zero_by_section.get(key, [])))
        occurrence_ids = tuple(dict.fromkeys(existing_ids + list(blocked_ids) + list(zero_ids)))
        completed.append(
            SectionAssemblyAudit(
                chapter_id=key[0],
                section_id=key[1],
                title=str(catalog_item.get("title") or (audit.title if audit else "")),
                occurrence_ids=occurrence_ids,
                rendered_occurrence_ids=tuple(rendered_ids),
                blocked_occurrence_ids=blocked_ids,
                zero_render_occurrence_ids=zero_ids,
                visible_title_count=audit.visible_title_count if audit else 1,
                order_preserved=audit.order_preserved if audit else True,
                passage_id=(audit.passage_id if audit else f"{key[0]}:{key[1]}:passage"),
                visible_passage_count=(audit.visible_passage_count if audit else (1 if rendered_ids else 0)),
                transitions=audit.transitions if audit else (),
            )
        )
    return completed


def _transition_for(
    brief: OccurrenceWritingBrief,
    previous_row: dict[str, Any] | None,
    brief_by_id: dict[str, OccurrenceWritingBrief],
) -> SectionDiscourseTransition:
    previous_id = str((previous_row or {}).get("occurrence_id") or "")
    prior_ids = tuple(brief.availability_source_occurrence_ids)
    prior_title = ""
    for source_id in prior_ids:
        source_brief = brief_by_id.get(source_id)
        if source_brief:
            prior_title = source_brief.canonical_title or source_brief.source_title
            break
    current_title = brief.canonical_title or brief.source_title
    context = brief.source_title or current_title

    if brief.role == LearningRole.APPLY:
        if prior_title:
            text = f"前面已建立“{prior_title}”的基础理解，下面在当前任务“{context}”中使用这项知识。"
        else:
            text = f"下面将在当前任务“{context}”中使用已经建立的“{current_title}”知识。"
        return SectionDiscourseTransition(
            occurrence_id=brief.occurrence_id,
            previous_occurrence_id=previous_id,
            role=brief.role,
            kind="PRIOR_TO_APPLICATION",
            text=text,
            source_occurrence_ids=prior_ids,
        )

    if brief.role == LearningRole.EXTEND:
        extension = "、".join(brief.extension_keys)
        suffix = f"新增关注点为：{extension}。" if extension else "本处只保留新的条件、限制或变体。"
        if prior_title:
            text = f"在前面“{prior_title}”的基础上，本处转入新增的条件、限制或变体。{suffix}"
        else:
            text = f"在已有相关基础上，本处转入新增的条件、限制或变体。{suffix}"
        return SectionDiscourseTransition(
            occurrence_id=brief.occurrence_id,
            previous_occurrence_id=previous_id,
            role=brief.role,
            kind="KNOWN_TO_NEW_INCREMENT",
            text=text,
            source_occurrence_ids=prior_ids,
        )

    if brief.role == LearningRole.RECALL:
        text = f"下面只恢复当前任务“{context}”所需的前文内容。"
        return SectionDiscourseTransition(
            occurrence_id=brief.occurrence_id,
            previous_occurrence_id=previous_id,
            role=brief.role,
            kind="RECALL_TO_TASK",
            text=text,
            source_occurrence_ids=prior_ids,
        )

    if brief.role == LearningRole.INTRO and previous_id:
        text = f"在继续当前任务之前，先对“{current_title}”建立初步认识。"
        return SectionDiscourseTransition(
            occurrence_id=brief.occurrence_id,
            previous_occurrence_id=previous_id,
            role=brief.role,
            kind="ORIENTATION",
            text=text,
        )

    return SectionDiscourseTransition(
        occurrence_id=brief.occurrence_id,
        previous_occurrence_id=previous_id,
        role=brief.role,
    )


def _body_already_has_transition(body: str, role: str) -> bool:
    """Avoid duplicating a writer's already-rendered role bridge.

    This is a presentation-only guard.  It recognizes discourse markers, not
    instructional facts, and therefore never changes role or contribution.
    """
    text = body.strip()
    if not text:
        return False
    markers = {
        LearningRole.APPLY: ("前面已", "此前", "基于前文", "在当前任务", "使用此前", "应用前面"),
        LearningRole.EXTEND: ("在已学习", "在前面", "基础上", "新增", "新的条件", "新的限制", "变体"),
        LearningRole.RECALL: ("恢复", "回顾", "前文内容", "已学习"),
        LearningRole.INTRO: ("初步认识", "初步认知", "学习方向", "首先了解"),
    }
    return any(marker in text[:180] for marker in markers.get(role, ()))
