from __future__ import annotations

import json


PROMPT_VERSION = "recall-capsule.v1"


def build_recall_capsule_messages(payload: dict) -> list[dict[str, str]]:
    """Ask for a tiny, source-bound memory cue rather than a rewrite."""
    return [
        {
            "role": "system",
            "content": (
                "Return one JSON object only. You write a minimal recall capsule, never a lesson rewrite. "
                "The role, required facets, source occurrences, evidence and insertion strategy are fixed by code."
            ),
        },
        {
            "role": "user",
            "content": (
                "Return exactly {\"generated_text\":str,\"evidence_chunk_ids\":[str]}. "
                "Write one or two sentences only. Restore only the listed required aspects for the current task. "
                "Use only supplied evidence and cite only supplied evidence_chunk_ids. Do not add any facet, "
                "extension, condition, parameter, definition, full method, procedural step, heading, or commentary.\n\n"
                "INPUT:\n" + json.dumps(payload, ensure_ascii=False)
            ),
        },
    ]
