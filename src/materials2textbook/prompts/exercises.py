from __future__ import annotations

import json

from materials2textbook.domain_config import DomainConfig, default_domain_config


def build_exercises_messages(
    *,
    task_title: str,
    knowledge_points: list[dict],
    evidence_summary: str,
    fill_blank_count: int = 5,
    thinking_count: int = 2,
    max_chars: int = 4000,
    domain_config: DomainConfig | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for generating fill-in-the-blank and thinking exercises."""

    config = domain_config or default_domain_config()
    payload = {
        "task_title": task_title,
        "knowledge_points": knowledge_points,
        "evidence": evidence_summary[:max_chars],
        "domain_config": config.to_dict(),
    }
    clipped = json.dumps(payload, ensure_ascii=False, indent=2)[:max_chars]

    system = (
        "You are a vocational digital textbook exercise design agent. "
        "Create student-facing fill-in-the-blank and thinking questions from the task knowledge points and teaching evidence. "
        "Every question must be strictly grounded in the provided evidence and domain configuration. "
        "Do not invent parameters, process conditions, or domain facts outside the evidence. "
        "Return only one JSON object, without Markdown fences or explanations."
    )

    user = "\n".join(
        [
            "Return JSON matching this schema:",
            "",
            "{",
            '  "fill_blank": ["question text with answer", ...],',
            '  "thinking": ["thinking question text", ...]',
            "}",
            "",
            f"Fill-in-the-blank requirements: generate up to {fill_blank_count} items.",
            '1. Each item is one complete sentence and contains exactly one "______" blank.',
            "2. The blank must be an explicit term, value, class, or condition found in the evidence.",
            "3. Append the answer at the end in the form `(Answer: XXX)`.",
            "4. Do not add numbering or labels; the caller will add them.",
            "",
            f"Thinking-question requirements: generate up to {thinking_count} items.",
            "1. Each question should require analysis, comparison, or transfer based on evidence.",
            "2. Do not provide answers for thinking questions.",
            "3. Avoid vague prompts like 'explain the key content'.",
            "",
            "General requirements:",
            f"- Use terminology appropriate for {config.domain_name}: {', '.join(config.domain_terms[:12])}.",
            f"- Prefer operation verbs from: {', '.join(config.operation_terms[:12])}.",
            "- Do not include evidence IDs or internal labels in the question text.",
            "- Do not repeat the same knowledge point across questions.",
            "",
            "Task knowledge points and evidence:",
            clipped,
        ]
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
