#!/usr/bin/env python
"""Run a blind, stratified semantic calibration of lexical-SUPPORTED claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider  # noqa: E402
from materials2textbook.knowledge_map.rendered_claim_semantic_audit import (  # noqa: E402
    OpenAICompatibleEntailmentJudge,
    audit_rendered_claims,
    calibrate_lexically_supported_claims,
    write_lexical_calibration_artifacts,
)
from run_rendered_claim_semantic_audit import _load_artifact  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Blindly calibrate lexical evidence support on an existing artifact.")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260822)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key", default=None)
    parser.add_argument("--llm-model", default=None)
    return parser


def main() -> None:
    args = _parser().parse_args()
    artifact_dir = args.artifact_dir.resolve()
    markdown, briefs, evidence = _load_artifact(artifact_dir)
    config = OpenAICompatibleConfig.from_env()
    if args.llm_base_url:
        config.base_url = args.llm_base_url
    if args.llm_api_key:
        config.api_key = args.llm_api_key
    if args.llm_model:
        config.model = args.llm_model
    if not config.is_configured:
        raise SystemExit("Calibration requires an OpenAI-compatible Qwen endpoint.")
    judge = OpenAICompatibleEntailmentJudge(OpenAICompatibleProvider(config), model=config.model)
    baseline = audit_rendered_claims(
        markdown=markdown,
        briefs=briefs,
        evidence_by_id=evidence,
        artifact_root=str(artifact_dir),
        judge=None,
    )
    report = calibrate_lexically_supported_claims(
        audit=baseline,
        briefs=briefs,
        judge=judge,
        artifact_root=str(artifact_dir),
        sample_size=args.sample_size,
        seed=args.seed,
    )
    output_dir = (args.output_dir or artifact_dir / "claim_evidence_calibration").resolve()
    json_path, markdown_path = write_lexical_calibration_artifacts(report, output_dir=str(output_dir))
    print(json.dumps(report.to_dict()["summary"], ensure_ascii=False, indent=2))
    print(f"json: {json_path}")
    print(f"markdown: {markdown_path}")


if __name__ == "__main__":
    main()
