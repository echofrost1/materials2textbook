from __future__ import annotations

import json


PROMPT_VERSION = "controlled-repair.v1"


def build_controlled_repair_messages(payload: dict) -> list[dict[str, str]]:
    """Ask only for a minimal insertion; code owns action, gap and placement."""
    return [
        {
            "role": "system",
            "content": (
                "You generate a minimal textbook repair patch. Return one JSON object only. "
                "Never change the supplied role, canonical identity, prerequisite context, action, target gap, "
                "or insertion strategy. Do not rewrite or repeat the existing occurrence."
            ),
        },
        {
            "role": "user",
            "content": (
                "Return exactly {\"generated_text\":str,\"evidence_chunk_ids\":[str],"
                "\"evidence_support_terms\":[str]}. generated_text must contain exactly one sentence, "
                "must address only target_gap, and must use only the supplied evidence. "
                "The supplied target_contract is binding and must be satisfied in the one sentence. "
                "Each evidence_support_term must be copied character-for-character from a cited evidence chunk, "
                "and the same character-for-character term must also occur in generated_text; choose only terms "
                "you can verify literally in both places. Do not paraphrase a support term. "
                "Do not define an already available concept, repeat an existing method or procedure, add headings, "
                "or include commentary, source citations, measurements, or facts absent from the supplied evidence.\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)
            ),
        },
    ]
