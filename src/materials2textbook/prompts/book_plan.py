from __future__ import annotations

import json
from typing import Any

from materials2textbook.domain_config import DomainConfig
from materials2textbook.schemas import EvidenceChunk


def build_book_plan_messages(
    *,
    title: str,
    chunks: list[EvidenceChunk],
    domain_config: DomainConfig,
    max_chapters: int = 12,
    max_chars: int = 14000,
) -> list[dict[str, str]]:
    samples = []
    for chunk in chunks[:160]:
        samples.append(
            {
                "chunk_id": chunk.chunk_id,
                "title": chunk.title,
                "summary": chunk.summary,
                "content": (chunk.content or "")[:500],
                "subject": chunk.subject,
                "material_block": chunk.material_block,
                "recommended_chapter": chunk.recommended_chapter,
                "source_type": chunk.source_type,
                "review_status": chunk.review_status,
            }
        )
    payload: dict[str, Any] = {
        "title": title,
        "domain_config": domain_config.to_dict(),
        "evidence_chunks": samples,
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "You are a vocational digital textbook planning agent. "
        "Create a complete, evidence-grounded book plan. "
        "Return one strict JSON object and no Markdown."
    )
    user = "\n".join(
        [
            "Generate a whole-book plan for the provided evidence chunks.",
            "Return this exact JSON shape:",
            "{",
            '  "title": "book title",',
            '  "chapters": [',
            "    {",
            '      "chapter_no": 1,',
            '      "title": "chapter title",',
            '      "learning_goals": ["goal 1", "goal 2"],',
            '      "sections": [',
            "        {",
            '          "section_no": "1.1",',
            '          "title": "section title",',
            '          "knowledge_points": ["point 1", "point 2"],',
            '          "primary_material_ids": ["existing_chunk_id"]',
            "        }",
            "      ]",
            "    }",
            "  ]",
            "}",
            "",
            "Hard rules:",
            "- Plan at least 3 chapters and at most " + str(max_chapters) + " chapters.",
            "- Every chapter must contain at least 3 sections.",
            "- Every section must contain at least 1 knowledge point.",
            "- primary_material_ids must only use chunk_id values from the input.",
            "- Do not create chapters from individual filenames.",
            "- Do not invent a chapter unrelated to the evidence.",
            "- Prefer balanced chapters with enough evidence to support writing.",
            "",
            "Planning input:",
            clipped,
        ]
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
