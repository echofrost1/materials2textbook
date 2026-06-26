from __future__ import annotations

import json
from typing import Any


def build_domain_config_messages(
    *,
    title: str,
    material_samples: list[dict[str, Any]],
    max_chars: int = 9000,
) -> list[dict[str, str]]:
    payload = {
        "requested_title": title,
        "material_samples": material_samples,
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]
    system = (
        "You are a vocational textbook domain-analysis agent. "
        "Infer a reusable domain configuration only from the provided material samples. "
        "Do not invent unrelated fields or unsupported subject areas. "
        "Return one strict JSON object and no Markdown."
    )
    user = "\n".join(
        [
            "Infer the domain configuration for an automatically generated vocational digital textbook.",
            "Return this exact JSON shape:",
            "{",
            '  "domain_name": "short subject/domain name",',
            '  "audience": "target student audience",',
            '  "textbook_type": "digital textbook",',
            '  "domain_terms": ["8-20 core terms from the evidence"],',
            '  "operation_terms": ["3-12 verbs or practical-task terms"],',
            '  "quality_dimensions": ["3-10 dimensions used to judge student work"],',
            '  "observation_examples": ["2-6 generic observation task patterns"],',
            '  "common_misconceptions": ["0-8 common mistakes if supported"],',
            '  "chapter_order": ["3-12 likely chapter titles"]',
            "}",
            "",
            "Rules:",
            "- Use the same language as the material titles when possible.",
            "- chapter_order must be broad textbook chapters, not individual filenames.",
            "- Terms and chapters must be supported by the samples.",
            "- If the topic is uncertain, choose a conservative domain_name and explain uncertainty through generic terms.",
            "",
            "Material samples:",
            clipped,
        ]
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
