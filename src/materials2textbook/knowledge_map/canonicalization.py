from __future__ import annotations

import re
from collections import defaultdict

from materials2textbook.knowledge_map.models import (
    KnowledgeKind,
    KnowledgeMapping,
    KnowledgePoint,
    SourceKnowledgePoint,
)


def canonicalize_source_points(
    source_points: list[SourceKnowledgePoint],
    semantic_planner=None,
) -> tuple[list[KnowledgePoint], list[KnowledgeMapping]]:
    """Create stable book-wide identities from outline knowledge-point seeds.

    This deliberately does not use text similarity as a duplicate decision.  The
    deterministic fallback only joins exact normalized labels; an LLM semantic
    planner can replace or augment this step later.
    """
    if semantic_planner is not None:
        proposal = semantic_planner.canonicalize_source_points(source_points)
        if proposal is not None:
            return proposal
    return _canonicalize_deterministic(source_points)


def _canonicalize_deterministic(
    source_points: list[SourceKnowledgePoint],
) -> tuple[list[KnowledgePoint], list[KnowledgeMapping]]:
    points_by_key: dict[str, KnowledgePoint] = {}
    mappings: list[KnowledgeMapping] = []
    used_ids: set[str] = set()

    for source in source_points:
        labels = _decompose_label(source.title)
        canonical_ids: list[str] = []
        for label in labels:
            key = _normalize(label)
            if not key:
                continue
            point = points_by_key.get(key)
            if point is None:
                knowledge_id = _knowledge_id(label, used_ids)
                point = KnowledgePoint(
                    knowledge_id=knowledge_id,
                    title=label,
                    aliases=[label],
                    kind=_infer_kind(label),
                    source_chunk_ids=list(source.source_chunk_ids),
                    extraction_confidence=0.9 if len(labels) == 1 else 0.72,
                )
                points_by_key[key] = point
            else:
                if source.title and source.title not in point.aliases:
                    point.aliases.append(source.title)
                point.source_chunk_ids = _unique([*point.source_chunk_ids, *source.source_chunk_ids])
            canonical_ids.append(point.knowledge_id)

        mapping_type = "DECOMPOSED" if len(canonical_ids) > 1 else "EXACT"
        if len(canonical_ids) == 1:
            point = next(item for item in points_by_key.values() if item.knowledge_id == canonical_ids[0])
            if source.title != point.title:
                mapping_type = "ALIAS"
        if not canonical_ids:
            mapping_type = "UNCERTAIN"
        mappings.append(
            KnowledgeMapping(
                source_knowledge_point_id=source.source_knowledge_point_id,
                canonical_knowledge_ids=canonical_ids,
                mapping_type=mapping_type,
                confidence=0.9 if mapping_type == "EXACT" else (0.72 if mapping_type == "DECOMPOSED" else 0.6),
                rationale=(
                    "Normalized outline label matched an existing canonical knowledge point."
                    if mapping_type == "ALIAS"
                    else "The composite outline label was decomposed into separately teachable knowledge points."
                    if mapping_type == "DECOMPOSED"
                    else "The outline label was retained as a canonical knowledge point."
                    if mapping_type == "EXACT"
                    else "The outline label could not be resolved safely."
                ),
                evidence_chunk_ids=list(source.source_chunk_ids),
            )
        )
    return list(points_by_key.values()), mappings


def _decompose_label(label: str) -> list[str]:
    value = str(label or "").strip()
    if not value:
        return []
    parts = [part.strip() for part in re.split(r"[、/]|与|及|和", value) if part.strip()]
    if len(parts) <= 1:
        return [value]
    # Avoid splitting short fixed terms.  A label such as “原理与应用” is a
    # useful source signal for an LLM review, while these labels remain explicit
    # and auditable rather than silently discarded.
    return parts if all(len(part) >= 2 for part in parts) else [value]


def _infer_kind(label: str) -> str:
    text = label.lower()
    if any(term in text for term in ("原理", "原则", "机制", "规律", "principle")):
        return KnowledgeKind.PRINCIPLE
    if any(term in text for term in ("方法", "策略", "method")):
        return KnowledgeKind.METHOD
    if any(term in text for term in ("技能", "操作", "skill")):
        return KnowledgeKind.SKILL
    if any(term in text for term in ("流程", "步骤", "程序", "procedure")):
        return KnowledgeKind.PROCEDURE
    return KnowledgeKind.CONCEPT


def _knowledge_id(label: str, used: set[str]) -> str:
    base = _normalize(label)[:48] or "unresolved"
    candidate = f"kp:{base}"
    suffix = 2
    while candidate in used:
        candidate = f"kp:{base}:{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "")).lower()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
