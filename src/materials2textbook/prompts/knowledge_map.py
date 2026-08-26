from __future__ import annotations

import json


def build_identity_messages(candidates: list[dict]) -> list[dict[str, str]]:
    return _messages(
        "Judge knowledge identity only. False merges cost more than missed merges. "
        "For multiple right_ids, DECOMPOSE means the left knowledge item should be split into those targets. "
        "Return exactly {\"judgements\":[{\"left_id\":str,\"right_id\":str,"
        "\"relation\":\"SAME|RELATED|DECOMPOSE|DISTINCT|UNCERTAIN\",\"confidence\":0..1,"
        "\"rationale\":str,\"evidence_ids\":[str]}]}. SAME requires evidence of identical transferable learning content; "
        "low-confidence SAME must instead be UNCERTAIN.",
        candidates,
    )


def build_semantic_delta_messages(trajectory: dict, *, retry: bool = False) -> list[dict[str, str]]:
    instruction = (
        "For each supplied occurrence, report semantic facts only. The supplied occurrence is the only current "
        "target; use prior_occurrences only as earlier same-canonical context. Never use a later occurrence, an "
        "unrelated canonical item, or a future section to infer the current contribution. "
        "Do NOT emit INTRO, TEACH, RECALL, APPLY, or EXTEND; deterministic code will derive the role. "
        "Compare each occurrence with the supplied earlier context only. A new_facet is only a genuinely new instructional facet, "
        "not a restatement. A new_extension_key must encode a new condition, constraint, variant, or context. "
        "Apply these binding examples: a first complete definition/principle/method gives new_facets=[EXPLAIN]; "
        "an occurrence that says it uses an already taught method and does not re-explain it gives uses_prior_knowledge=true and no new facets; "
        "a new thin-material limit, abnormal condition, or parameter constraint gives a non-empty new_extension_keys; "
        "a repeated full explanation with no new condition gives repeats_prior_explanation=true, no new facets, and recall_needed=false. "
        "recall_needed=true only when the current text explicitly restores a minimal earlier context for a task. "
        "orientation_only=true only when the occurrence deliberately establishes initial intuition without a complete "
        "definition, principle, method, or procedure. restores_prior_context=true only when it restores minimum prior "
        "context; repeats_complete_teaching=true only when it repeats a complete already-taught explanation or method. "
        "required_self_facets are facets that must exist BEFORE this occurrence: they must never overlap new_facets. "
        "For the first occurrence, required_self_facets and required_self_extension_keys must both be empty. "
        "A same-canonical prerequisite is always represented by required_self_facets, never cross_prerequisite_uses. "
        "Use a cross prerequisite only when needed knowledge is a DIFFERENT canonical ID from canonical_id_whitelist; "
        "never output source IDs or unknown IDs. For each cross prerequisite, minimum_required_facet is the lowest "
        "capability the CURRENT task actually needs, independently of the upstream occurrence's planned facet. "
        "Use HARD only when absence must block execution; use SOFT_CONTEXT for helpful context and NOT_REQUIRED when "
        "the task does not depend on that knowledge. Do not infer a hard EXPLAIN prerequisite merely because the "
        "upstream plan says EXPLAIN. "
        "Return exactly {\"deltas\":[{\"occurrence_id\":str,\"repeats_prior_explanation\":bool,"
        "\"uses_prior_knowledge\":bool,\"recall_needed\":bool,\"orientation_only\":bool,"
        "\"restores_prior_context\":bool,\"repeats_complete_teaching\":bool,\"required_self_facets\":[\"ORIENTED|EXPLAIN|PERFORM|ANALYZE\"],"
        "\"required_self_extension_keys\":[str],\"cross_prerequisite_uses\":[{\"knowledge_id\":str,"
        "\"required_facets\":[\"ORIENTED|EXPLAIN|PERFORM|ANALYZE\"],\"minimum_required_facet\":\"ORIENTED|EXPLAIN|PERFORM|ANALYZE\","
        "\"necessity\":\"HARD|SOFT_CONTEXT|NOT_REQUIRED\",\"required_extension_keys\":[str],"
        "\"relation\":\"HARD|SUPPORTING\",\"use_type\":\"DIRECT|BACKGROUND\","
        "\"rationale\":str,\"evidence_ids\":[str],\"provenance\":str,\"supporting_basis\":str,"
        "\"confidence\":0..1}],"
        "\"new_facets\":[\"ORIENTED|EXPLAIN|PERFORM|ANALYZE\"],\"new_extension_keys\":[str],"
        "\"new_context\":str,\"repeated_aspects\":[str],\"contribution_summary\":str,"
        "\"confidence\":0..1,\"rationale\":str,\"evidence_ids\":[str]}]}.")
    if retry:
        instruction += (
            " This is a bounded retry for only the failed occurrence(s). Return one compact, valid JSON object. "
            "Include exactly one delta for each supplied occurrence_id; do not omit required keys, add commentary, "
            "or repeat the full evidence text. Keep rationale, contribution_summary, and new_context concise while "
            "remaining evidence-grounded. For the first occurrence in the supplied trajectory, set "
            "required_self_facets and required_self_extension_keys to empty arrays. Never put a facet in both "
            "required_self_facets and new_facets."
        )
    return _messages(instruction, trajectory)


def _messages(instruction: str, payload: object) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a conservative textbook semantic auditor. Read the supplied titles and evidence. "
                "Return one JSON object only, with no Markdown or commentary."
            ),
        },
        {"role": "user", "content": instruction + "\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)},
    ]
