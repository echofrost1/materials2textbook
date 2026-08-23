from __future__ import annotations

"""Small, targeted LLM contracts for pre-freeze completeness patches.

This is intentionally separate from the whole-book BookPlan planner.  One call
handles one section (or one curriculum requirement), and the deterministic
compiler remains the authority that accepts or rejects the result.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from materials2textbook.domain_config import parse_json_object
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.schemas import BookChapterPlan, BookSectionPlan, EvidenceChunk


@dataclass
class TargetedPatchCall:
    payload: dict[str, Any] | None
    raw: str = ""
    error: str = ""
    call_count: int = 0


class BookPlanCompletenessPatchAgent:
    """Generate bounded section/curriculum proposals without a full BookPlan."""

    def __init__(self, llm_provider: LLMProvider | None = None) -> None:
        self.llm_provider = llm_provider
        self.call_count = 0
        self.last_call = TargetedPatchCall(None)

    def run_section(
        self,
        *,
        chapter: BookChapterPlan,
        section: BookSectionPlan,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> TargetedPatchCall:
        messages = self._section_messages(chapter, section, issues, candidates)
        return self._call(messages)

    def run_section_retry(
        self,
        *,
        chapter: BookChapterPlan,
        section: BookSectionPlan,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> TargetedPatchCall:
        """One finite retry for a structured-output truncation only.

        The retry is intentionally smaller than the normal section patch and
        cannot be used to replan a successful section or alter identity.
        """

        messages = self._section_retry_messages(chapter, section, issues, candidates)
        return self._call(messages)

    def run_section_evidence(
        self,
        *,
        chapter: BookChapterPlan,
        section: BookSectionPlan,
        obligations: list[dict[str, str]],
        candidates: list[dict[str, Any]],
    ) -> TargetedPatchCall:
        """Judge obligation coverage inside one already retrieved candidate pool."""

        messages = self._section_evidence_messages(chapter, section, obligations, candidates)
        return self._call(messages)

    def run_curriculum(
        self,
        *,
        expected_requirement: str,
        coverage: dict[str, Any],
    ) -> TargetedPatchCall:
        messages = self._curriculum_messages(expected_requirement, coverage)
        return self._call(messages)

    def _call(self, messages: list[dict[str, str]]) -> TargetedPatchCall:
        if self.llm_provider is None:
            result = TargetedPatchCall(None, error="LLM provider is not configured")
            self.last_call = result
            return result
        self.call_count += 1
        try:
            raw = self.llm_provider.generate(messages)
            payload = parse_json_object(raw)
            result = TargetedPatchCall(payload, raw=raw, call_count=1)
        except Exception as exc:  # pragma: no cover - provider failures vary.
            result = TargetedPatchCall(None, raw=locals().get("raw", ""), error=str(exc), call_count=1)
        self.last_call = result
        return result

    @staticmethod
    def _section_retry_messages(
        chapter: BookChapterPlan,
        section: BookSectionPlan,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        missing = [str(item.get("expected_requirement") or "") for item in issues]
        fields = [
            "section_purpose",
            "expected_learning_outcome",
            "current_task_action",
            "knowledge_scope",
            "case_purpose",
            "exercise_purpose",
            "assessment_purpose",
            "activity_purpose",
        ]
        schema = {
            "chapter_id": chapter.chapter_id,
            "section_id": section.section_id,
            "identity": {"section_title": section.title, "knowledge_point_ids": list(section.knowledge_point_ids)},
            "fields": {key: "" if key != "knowledge_scope" else [] for key in fields},
            "primary_material_ids": [],
            "reference_material_ids": [],
            "rationale": "",
        }
        context = {
            "chapter_id": chapter.chapter_id,
            "section_id": section.section_id,
            "section_title": section.title,
            "knowledge_point_ids": list(section.knowledge_point_ids),
            "missing_fields": missing,
            "allowed_evidence_ids": [str(item.get("chunk_id")) for item in candidates],
            "evidence_titles": [str(item.get("title") or "") for item in candidates],
        }
        return [
            {
                "role": "system",
                "content": (
                    "Return exactly one compact JSON object and nothing else. "
                    "This is one finite retry after truncated JSON. Use only exact keys in OUTPUT SHAPE, "
                    "fill only missing fields, keep identity unchanged, and use only allowed evidence IDs. "
                    "Do not return prose, a BookPlan, or new nodes."
                ),
            },
            {
                "role": "user",
                "content": "RETRY CONTEXT:\n" + json.dumps(context, ensure_ascii=False) + "\nOUTPUT SHAPE:\n" + json.dumps(schema, ensure_ascii=False),
            },
        ]

    @staticmethod
    def _section_evidence_messages(
        chapter: BookChapterPlan,
        section: BookSectionPlan,
        obligations: list[dict[str, str]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        schema = {
            "chapter_id": chapter.chapter_id,
            "section_id": section.section_id,
            "identity": {"section_title": section.title, "knowledge_point_ids": list(section.knowledge_point_ids)},
            "status": "SECTION_EVIDENCE_SUFFICIENT|SECTION_EVIDENCE_PARTIAL|SECTION_EVIDENCE_UNRESOLVED",
            "primary_material_ids": [],
            "reference_material_ids": [],
            "obligation_coverage": [
                {"key": "declared obligation key", "status": "SUPPORTED|PARTIAL|UNRESOLVED", "evidence_ids": [], "rationale": ""}
            ],
            "confidence": 0.0,
            "rationale": "",
        }
        context = {
            "chapter_id": chapter.chapter_id,
            "section_id": section.section_id,
            "section_title": section.title,
            "knowledge_point_ids": list(section.knowledge_point_ids),
            "obligations": obligations,
            "allowed_evidence": candidates,
        }
        return [
            {
                "role": "system",
                "content": (
                    "Judge evidence coverage for this existing section only. Return compact JSON. "
                    "Use only candidate evidence IDs supplied in ALLOWED_EVIDENCE; never add IDs or facts. "
                    "Mark every obligation exactly once. Evidence count alone is not coverage: explain which "
                    "candidate supports each obligation. Do not change section identity or generate textbook prose."
                ),
            },
            {
                "role": "user",
                "content": "SECTION EVIDENCE COVERAGE:\n" + json.dumps(context, ensure_ascii=False) + "\nOUTPUT SHAPE:\n" + json.dumps(schema, ensure_ascii=False),
            },
        ]

    @staticmethod
    def _section_messages(
        chapter: BookChapterPlan,
        section: BookSectionPlan,
        issues: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        context = {
            "chapter_id": chapter.chapter_id,
            "chapter_title": chapter.title,
            "section_id": section.section_id,
            "section_title": section.title,
            "knowledge_point_ids": list(section.knowledge_point_ids),
            "knowledge_scope": list(section.knowledge_scope),
            "missing_fields": [item.get("expected_requirement", "") for item in issues],
            "allowed_evidence": candidates,
        }
        schema = {
            "chapter_id": chapter.chapter_id,
            "section_id": section.section_id,
            "identity": {
                "chapter_title": chapter.title,
                "section_title": section.title,
                "knowledge_point_ids": list(section.knowledge_point_ids),
                "knowledge_scope": list(section.knowledge_scope),
            },
            # Keep the patch contract explicit.  An empty object invited the
            # model to invent labels such as "specific section purpose";
            # deterministic compilation must only receive these exact keys.
            "fields": {
                "section_purpose": "",
                "expected_learning_outcome": "",
                "current_task_action": "",
                "knowledge_scope": [],
                "case_purpose": "",
                "exercise_purpose": "",
                "assessment_purpose": "",
                "activity_purpose": "",
            },
            "primary_material_ids": [],
            "reference_material_ids": [],
            "rationale": "",
        }
        return [
            {
                "role": "system",
                "content": (
                    "Return one compact JSON object for this existing section only. "
                    "Do not return a BookPlan, chapters, new sections, or prose. "
                    "Never change chapter_id, section_id, title, knowledge_point_ids, or order. "
                    "Use only the evidence IDs in allowed_evidence. Omit unsupported fields, "
                    "but if a field is returned it MUST use the exact snake_case names in "
                    "OUTPUT SHAPE (never natural-language labels or renamed keys). "
                    "Keep each generated field concise and specific; never use placeholders."
                ),
            },
            {
                "role": "user",
                "content": "SECTION PATCH CONTEXT:\n" + json.dumps(context, ensure_ascii=False) + "\nOUTPUT SHAPE:\n" + json.dumps(schema, ensure_ascii=False),
            },
        ]

    @staticmethod
    def _curriculum_messages(expected_requirement: str, coverage: dict[str, Any]) -> list[dict[str, str]]:
        schema = {
            "expected_requirement": expected_requirement,
            "coverage": "COVERED|PARTIALLY_COVERED|NO_SUPPORT_FOUND",
            "supporting_evidence_ids": [],
            "rationale": "",
        }
        return [
            {
                "role": "system",
                "content": (
                    "Return one compact JSON curriculum-resolution patch only. "
                    "Use only candidate evidence IDs in the supplied coverage record. "
                    "Do not add sections, titles, knowledge points, or textbook prose."
                ),
            },
            {
                "role": "user",
                "content": "COVERAGE CONTEXT:\n" + json.dumps({"expected_requirement": expected_requirement, **coverage}, ensure_ascii=False) + "\nOUTPUT SHAPE:\n" + json.dumps(schema, ensure_ascii=False),
            },
        ]
