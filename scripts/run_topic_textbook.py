#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from materials2textbook.agents.domain_config_agent import DomainConfigAgent
from materials2textbook.domain_config import load_domain_config, write_domain_config
from materials2textbook.exporters.digital_book import (
    smoke_test_student_package_static_assets,
    validate_student_digital_book_package,
    write_student_digital_book_package,
)
from materials2textbook.io_utils import read_jsonl, write_json, write_jsonl, write_text
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.orchestrator import TextbookWorkflow
from run_full_digital_textbook import ProgressLLMProvider, load_dotenv
from materials2textbook.llm.cache import CachingLLMProvider
from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider
from materials2textbook.llm.retry import RetryingLLMProvider


EVIDENCE_FILENAMES = [
    "video_segments.jsonl",
    "ppt_assets.jsonl",
    "reference_text_assets.jsonl",
    "audio_segments.jsonl",
    "structured_assets.jsonl",
]


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run one-click topic textbook generation from a material directory.")
    parser.add_argument("--material-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=None)
    parser.add_argument("--title", default="Digital Textbook")
    parser.add_argument("--use-llm", nargs="?", const="true", default="false")
    parser.add_argument("--domain-config", type=Path, default=None)
    parser.add_argument("--book-plan-input", type=Path, default=None)
    parser.add_argument("--manifest-xlsx", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--student-package-output", type=Path, default=None)
    parser.add_argument("--min-evidence-chunks", type=int, default=20)
    parser.add_argument("--min-candidate-chapters", type=int, default=3)
    parser.add_argument("--max-chapters", type=int, default=0)
    parser.add_argument("--review-rounds", type=int, default=1)
    parser.add_argument("--copy-media-assets", action="store_true")
    parser.add_argument("--skip-resource-analyst-llm", action="store_true")
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-max-retries", type=int, default=2)
    parser.add_argument("--llm-retry-backoff", type=float, default=1.0)
    parser.add_argument("--llm-cache-path", type=Path, default=None)
    parser.add_argument("--no-llm-cache", action="store_true")
    args = parser.parse_args()
    args.use_llm = _parse_bool(args.use_llm)

    material_root = args.material_root.resolve()
    json_dir = material_root / "02_working_processing" / "json"
    manifest_dir = material_root / "01_manifest_inventory"
    review_dir = material_root / "03_review_manual_check"
    deliverables_dir = material_root / "05_final_deliverables"
    output_dir = (args.output_dir or deliverables_dir / "agent_workflow").resolve()
    warnings: list[dict[str, str]] = []

    manifest_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_xlsx = args.manifest_xlsx.resolve() if args.manifest_xlsx else manifest_dir / "assets_manifest.xlsx"
    if args.raw_root and not manifest_xlsx.exists():
        warnings.extend(_try_inventory(material_root, args.raw_root.resolve()))

    evidence_paths = _existing_evidence_paths(json_dir)
    if not evidence_paths:
        _write_insufficient_report(
            deliverables_dir,
            title=args.title,
            reason="No processed evidence JSONL files were found.",
            evidence_count=0,
            candidate_chapters=0,
        )
        raise SystemExit(1)

    records_by_path: dict[Path, list[dict]] = {}
    for path in evidence_paths:
        try:
            records_by_path[path] = read_jsonl(path)
        except Exception as exc:
            warnings.append({"stage": "read_evidence", "path": str(path), "warning": str(exc)})
            records_by_path[path] = []

    all_records = [record for records in records_by_path.values() for record in records]
    candidate_chapters = _candidate_chapter_count(all_records)
    write_json(review_dir / "pipeline_warnings.json", warnings)
    if len(all_records) < args.min_evidence_chunks or candidate_chapters < args.min_candidate_chapters:
        _write_insufficient_report(
            deliverables_dir,
            title=args.title,
            reason="Processed evidence is below the minimum threshold.",
            evidence_count=len(all_records),
            candidate_chapters=candidate_chapters,
        )
        raise SystemExit(1)

    provider = _build_llm_provider(args, output_dir)
    if args.domain_config:
        domain_config = load_domain_config(args.domain_config.resolve())
    else:
        domain_agent = DomainConfigAgent(llm_provider=provider, use_llm=args.use_llm)
        domain_config = domain_agent.run(title=args.title, material_root=material_root)
        write_domain_config(manifest_dir / "domain_config.generated.yml", domain_config)
        write_text(manifest_dir / "domain_config_review.md", domain_agent.render_review(domain_config, material_root))

    video_path = output_dir / "selected_video_segments.jsonl"
    doc_path = output_dir / "combined_document_segments.jsonl"
    write_jsonl(video_path, records_by_path.get(json_dir / "video_segments.jsonl", []))
    document_records = [
        record
        for path, records in records_by_path.items()
        if path.name != "video_segments.jsonl"
        for record in records
    ]
    write_jsonl(doc_path, document_records)

    workflow = TextbookWorkflow(
        llm_provider=provider,
        use_llm=args.use_llm,
        domain_config=domain_config,
        auto_plan=True,
        llm_book_planning=args.use_llm,
    )
    if args.skip_resource_analyst_llm:
        workflow.resource_analyst.use_llm = False

    outputs = workflow.run(
        video_segments_path=video_path,
        output_dir=output_dir,
        title=args.title,
        config=WorkflowConfig(review_rounds=args.review_rounds, copy_media_assets=args.copy_media_assets),
        document_segments_path=doc_path if document_records else None,
        book_mode=True,
        manifest_xlsx=manifest_xlsx if manifest_xlsx.exists() else None,
        book_plan_output=output_dir / "book_plan.json",
        max_chapters=args.max_chapters,
        domain_config=domain_config,
        auto_plan=True,
        llm_book_planning=args.use_llm,
        book_plan_input=args.book_plan_input.resolve() if args.book_plan_input else None,
    )
    _copy_if_exists(output_dir / "book_plan.json", manifest_dir / "book_plan.generated.json")
    _copy_if_exists(output_dir / "book_plan_review.md", manifest_dir / "book_plan_review.md")

    if args.student_package_output:
        package_path = write_student_digital_book_package(
            source_dir=Path(outputs.digital_book_dir),
            output_zip=args.student_package_output.resolve(),
        )
        issues = validate_student_digital_book_package(package_path)
        issues.extend(smoke_test_student_package_static_assets(package_path))
        if issues:
            write_json(review_dir / "student_package_issues.json", issues)
            raise SystemExit(1)

    print("Topic textbook generated:")
    print(f"- material_root: {material_root}")
    print(f"- domain_name: {domain_config.domain_name}")
    print(f"- evidence_records: {len(all_records)}")
    print(f"- candidate_chapters: {candidate_chapters}")
    print(f"- agent_outputs: {outputs.manifest_path}")
    print(f"- digital_book_json: {outputs.digital_book_path}")
    print(f"- digital_book_index: {outputs.digital_book_index_path}")
    if args.student_package_output:
        print(f"- student_package_zip: {args.student_package_output.resolve()}")


def _parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_llm_provider(args: argparse.Namespace, output_dir: Path):
    if not args.use_llm:
        return None
    llm_config = OpenAICompatibleConfig.from_env()
    if args.llm_base_url:
        llm_config.base_url = args.llm_base_url
    if args.llm_api_key:
        llm_config.api_key = args.llm_api_key
    if args.llm_model:
        llm_config.model = args.llm_model
    if not llm_config.is_configured:
        raise SystemExit("LLM is enabled but not configured. Set API env vars or pass --llm-* options.")
    provider = OpenAICompatibleProvider(llm_config)
    if args.llm_max_retries:
        provider = RetryingLLMProvider(provider, max_retries=args.llm_max_retries, backoff_seconds=args.llm_retry_backoff)
    if not args.no_llm_cache:
        provider = CachingLLMProvider(provider, args.llm_cache_path or output_dir / "llm_cache.json")
    return ProgressLLMProvider(provider)


def _existing_evidence_paths(json_dir: Path) -> list[Path]:
    return [json_dir / name for name in EVIDENCE_FILENAMES if (json_dir / name).exists()]


def _candidate_chapter_count(records: list[dict]) -> int:
    values = set()
    for record in records:
        text = (
            record.get("recommended_chapter")
            or record.get("chapter")
            or record.get("chapter_title")
            or record.get("material_block")
            or record.get("material_block_cn")
            or record.get("source_file")
            or record.get("asset_id")
            or ""
        )
        if str(text).strip():
            values.add(str(text).strip())
    return len(values)


def _try_inventory(material_root: Path, raw_root: Path) -> list[dict[str, str]]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "build_material_inventory.py"),
        "--material-root",
        str(material_root),
        "--raw-root",
        str(raw_root),
    ]
    try:
        subprocess.run(command, cwd=ROOT, check=True)
        return []
    except Exception as exc:
        return [{"stage": "build_material_inventory", "path": str(raw_root), "warning": str(exc)}]


def _write_insufficient_report(
    deliverables_dir: Path,
    *,
    title: str,
    reason: str,
    evidence_count: int,
    candidate_chapters: int,
) -> None:
    report = "\n".join(
        [
            f"# Insufficient material report for {title}",
            "",
            f"- reason: {reason}",
            f"- evidence_chunks_or_records: {evidence_count}",
            f"- candidate_chapters: {candidate_chapters}",
            "- minimum_evidence_chunks: 20 by default",
            "- minimum_candidate_chapters: 3 by default",
            "",
            "Add or process more source materials, then rerun the one-click pipeline.",
        ]
    )
    write_text(deliverables_dir / "insufficient_material_report.md", report)


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


if __name__ == "__main__":
    main()
