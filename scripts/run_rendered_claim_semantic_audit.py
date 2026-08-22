#!/usr/bin/env python
"""Run the read-only Phase 4B-1 claim evidence audit on an existing artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider  # noqa: E402
from materials2textbook.knowledge_map.rendered_claim_semantic_audit import (  # noqa: E402
    OpenAICompatibleEntailmentJudge,
    audit_rendered_claims,
    write_audit_artifacts,
)
from materials2textbook.schemas import EvidenceChunk, EvidenceLocator, EvidenceScore  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit rendered claims against occurrence-authorized evidence.")
    parser.add_argument("--artifact-dir", type=Path, required=True, help="Existing semantic artifact directory; no generation is performed.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Audit output directory. Defaults to ARTIFACT_DIR/claim_evidence_audit.")
    parser.add_argument("--use-llm", action="store_true", help="Run Qwen/OpenAI-compatible entailment only for deterministic-uncertain claims.")
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key", default=None)
    parser.add_argument("--llm-model", default=None)
    return parser


def _chunk(payload: dict[str, Any]) -> EvidenceChunk:
    locator_payload = payload.get("locator") or {}
    score_payload = payload.get("score") or {}
    locator = EvidenceLocator(**{key: locator_payload.get(key) for key in EvidenceLocator.__dataclass_fields__})
    score = EvidenceScore(**{key: score_payload.get(key, 0.0) for key in EvidenceScore.__dataclass_fields__})
    fields = EvidenceChunk.__dataclass_fields__
    values = {key: payload.get(key) for key in fields if key not in {"locator", "score"}}
    values.setdefault("keywords", [])
    values.setdefault("metadata", {})
    values.setdefault("source_type", "video_segment")
    values.setdefault("review_status", "")
    return EvidenceChunk(locator=locator, score=score, **values)


def _load_artifact(root: Path) -> tuple[str, list[dict[str, Any]], dict[str, EvidenceChunk]]:
    markdown_candidates = [
        root / "materialization" / "textbook_materialized.md",
        root / "textbook_final.md",
        root / "textbook_draft.md",
    ]
    markdown_path = next((path for path in markdown_candidates if path.exists()), None)
    if markdown_path is None:
        raise FileNotFoundError(f"No rendered Markdown found under {root}")
    execution_path = root / "semantic_execution_audit.json"
    if not execution_path.exists():
        raise FileNotFoundError(f"Missing semantic_execution_audit.json under {root}")
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    briefs = list((execution.get("coverage") or {}).get("briefs") or [])
    evidence_path = root / "evidence_chunks.jsonl"
    if not evidence_path.exists():
        raise FileNotFoundError(f"Missing evidence_chunks.jsonl under {root}")
    evidence: dict[str, EvidenceChunk] = {}
    for line in evidence_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = _chunk(json.loads(line))
            evidence[item.chunk_id] = item
    return markdown_path.read_text(encoding="utf-8"), briefs, evidence


def main() -> None:
    args = _parser().parse_args()
    artifact_dir = args.artifact_dir.resolve()
    markdown, briefs, evidence = _load_artifact(artifact_dir)
    judge = None
    if args.use_llm:
        config = OpenAICompatibleConfig.from_env()
        if args.llm_base_url:
            config.base_url = args.llm_base_url
        if args.llm_api_key:
            config.api_key = args.llm_api_key
        if args.llm_model:
            config.model = args.llm_model
        if not config.is_configured:
            raise SystemExit("--use-llm requires OPENAI_API_KEY, OPENAI_BASE_URL, and OPENAI_MODEL (or explicit --llm-* options).")
        judge = OpenAICompatibleEntailmentJudge(OpenAICompatibleProvider(config), model=config.model)
    report = audit_rendered_claims(
        markdown=markdown,
        briefs=briefs,
        evidence_by_id=evidence,
        artifact_root=str(artifact_dir),
        judge=judge,
    )
    output_dir = (args.output_dir or artifact_dir / "claim_evidence_audit").resolve()
    json_path, markdown_path = write_audit_artifacts(report, output_dir=str(output_dir))
    print(json.dumps(report.to_dict()["summary"], ensure_ascii=False, indent=2))
    print(f"json: {json_path}")
    print(f"markdown: {markdown_path}")


if __name__ == "__main__":
    main()

