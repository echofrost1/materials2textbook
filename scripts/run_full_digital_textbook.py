#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from material_paths import default_generated_output_dir, default_raw_root, default_work_root


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.io_utils import read_jsonl, write_json, write_jsonl
from materials2textbook.domain_config import load_domain_config
from materials2textbook.llm.cache import CachingLLMProvider
from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider
from materials2textbook.llm.retry import RetryingLLMProvider
from materials2textbook.exporters.digital_book import (
    smoke_test_student_package_static_assets,
    smoke_test_student_package_ask,
    validate_student_digital_book_package,
    write_student_digital_book_package,
)
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.workflow.orchestrator import TextbookWorkflow


DEFAULT_SEMANTIC_BOOK_MODE = True


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _source_record_key(record: object) -> str:
    """Return a stable identity for duplicate document/PPT source records."""
    if isinstance(record, dict):
        for field in ("ppt_asset_id", "document_segment_id", "segment_id", "chunk_id", "evidence_id", "id"):
            value = record.get(field)
            if value is not None and str(value).strip():
                return f"{field}:{value}"
        source_asset_id = record.get("source_asset_id") or record.get("asset_id")
        slide_or_page = record.get("slide_index")
        if slide_or_page is None:
            slide_or_page = record.get("page_or_slide")
        if source_asset_id is not None and slide_or_page is not None:
            return f"source:{source_asset_id}:page:{slide_or_page}"
        identity_payload = {
            field: record.get(field)
            for field in (
                "title",
                "slide_title",
                "evidence_text",
                "text",
                "summary",
            )
            if record.get(field) is not None
        }
        canonical = json.dumps(identity_payload or record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        canonical = repr(record)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"content:{digest}"


def _dedupe_source_records(records: list[dict]) -> tuple[list[dict], int]:
    """Drop exact/stable duplicate document records while preserving order."""
    seen: set[str] = set()
    unique: list[dict] = []
    duplicates = 0
    for record in records:
        key = _source_record_key(record)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(record)
    return unique, duplicates


def _validate_expected_source_record_count(actual: int, expected: int | None) -> None:
    """Fail before provider/workflow startup when an expected count is supplied."""
    if expected is not None and actual != expected:
        raise SystemExit(
            "Source record count mismatch before workflow/model startup: "
            f"expected={expected} actual={actual}"
        )


class ProgressLLMProvider:
    def __init__(self, provider) -> None:
        self.provider = provider
        self.calls = 0

    def generate(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> str:
        self.calls += 1
        print(f"[llm] request {self.calls} start", flush=True)
        try:
            response = self.provider.generate(messages, max_tokens=max_tokens)
        except TypeError as exc:
            if "max_tokens" not in str(exc):
                raise
            response = self.provider.generate(messages)
        print(f"[llm] request {self.calls} done, chars={len(response)}", flush=True)
        return response


def default_material_root() -> Path:
    return default_work_root()


def build_llm_provider(args: argparse.Namespace, output_dir: Path):
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
        raise SystemExit(
            "LLM is enabled but not configured. Set OPENAI_API_KEY, "
            "OPENAI_BASE_URL, OPENAI_MODEL or pass --llm-* options."
        )
    provider = OpenAICompatibleProvider(llm_config)
    server_max_model_len = provider.validate_server_context()
    print(
        f"[llm] server context validated: client={llm_config.context_window} "
        f"server_max_model_len={server_max_model_len}",
        flush=True,
    )
    if args.llm_max_retries:
        provider = RetryingLLMProvider(
            provider,
            max_retries=args.llm_max_retries,
            backoff_seconds=args.llm_retry_backoff,
        )
    if not args.no_llm_cache:
        provider = CachingLLMProvider(provider, args.llm_cache_path or output_dir / "llm_cache.json")
    provider = ProgressLLMProvider(provider)
    return provider


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Run the whole local-material pipeline and generate a front-end readable digital textbook."
    )
    parser.add_argument(
        "--material-root",
        type=Path,
        default=default_material_root(),
        help="Material workspace root. Defaults to local_runs/work_material1 unless configured.",
    )
    parser.add_argument("--title", default="钨极氩弧焊数字教材")
    parser.add_argument("--segments", type=Path, default=None, help="Override video_segments.jsonl path.")
    parser.add_argument("--chapter", default="", help="Only build records whose chapter contains this text.")
    parser.add_argument("--knowledge-point", default="", help="Only build records whose knowledge point contains this text.")
    parser.add_argument("--max-video-records", type=int, default=0, help="Limit video records after filtering; 0 means no limit.")
    parser.add_argument("--max-document-records", type=int, default=0, help="Limit document/PPT records after filtering; 0 means no limit.")
    parser.add_argument(
        "--ppt-assets",
        type=Path,
        default=None,
        help="Override ppt_assets.jsonl path. Defaults to MATERIAL_ROOT/02_working_processing/json/ppt_assets.jsonl.",
    )
    parser.add_argument(
        "--document-segments",
        type=Path,
        action="append",
        default=[],
        help="Additional document/PPT/table JSONL evidence. Can be passed multiple times.",
    )
    parser.add_argument(
        "--expected-source-record-count",
        type=int,
        default=None,
        help="Fail before model/workflow startup when the deduplicated source-record count differs.",
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Override agent workflow output directory.")
    parser.add_argument("--book-mode", action="store_true", help="Enable whole-book planning before chapter generation.")
    parser.add_argument(
        "--semantic-book-mode",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_SEMANTIC_BOOK_MODE,
        help=(
            "Run the verified semantic closed loop after the final BookPlan is generated and frozen "
            "(default: enabled; use --no-semantic-book-mode only for legacy diagnostics)."
        ),
    )
    parser.add_argument(
        "--completeness-first-authoring",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Use SectionTeachingBlueprint/SectionEvidencePacket and section-level authoring "
            "in semantic production mode (default: enabled)."
        ),
    )
    parser.add_argument(
        "--preview-fullbook",
        action="store_true",
        help=(
            "Generate a source-bounded full-book reading preview. Requires a validated frozen BookPlan; "
            "never establishes strict verified availability and is never publication-approved."
        ),
    )
    parser.add_argument(
        "--manifest-xlsx",
        type=Path,
        default=None,
        help="XLSX manifest prepared by the teammate. Used first for chapter/section allocation in --book-mode.",
    )
    parser.add_argument("--book-plan-output", type=Path, default=None, help="Optional output path for book_plan.json.")
    parser.add_argument("--book-plan-input", type=Path, default=None, help="Optional external book_plan.json. Skips automatic planning.")
    parser.add_argument(
        "--book-plan-is-frozen",
        action="store_true",
        help="Treat --book-plan-input as an already post-processed immutable source BookPlan.",
    )
    parser.add_argument(
        "--freeze-calibration-input",
        type=Path,
        default=None,
        help=(
            "Freeze calibration report required with --book-plan-is-frozen. "
            "The report must validate the snapshot/signature/fingerprint before semantic execution."
        ),
    )
    parser.add_argument("--domain-config", type=Path, default=None, help="Optional domain_config YAML/JSON.")
    parser.add_argument("--domain-name", default="", help="Override generated/loaded domain name.")
    parser.add_argument("--audience", default="", help="Override generated/loaded audience.")
    parser.add_argument("--disable-auto-plan", action="store_true", help="Disable automatic book planning in --book-mode.")
    parser.add_argument("--disable-llm-book-planning", action="store_true", help="Disable LLM book planning and use rule fallback.")
    parser.add_argument("--chapter-output-root", type=Path, default=None, help="Output root for per-chapter book-mode artifacts.")
    parser.add_argument(
        "--force-rebuild-chapters",
        action="store_true",
        help="In --book-mode, rebuild chapters even when chapter_status.json says a chapter completed successfully.",
    )
    parser.add_argument("--max-chapter-input-tokens", type=int, default=12000, help="Per-chapter input token budget for --book-mode.")
    parser.add_argument("--max-chapters", type=int, default=0, help="Limit planned chapters in --book-mode; 0 means no limit.")
    parser.add_argument("--approved-only", action="store_true", help="Only include approved evidence.")
    parser.add_argument("--include-rejected", action="store_true", help="Include rejected evidence.")
    parser.add_argument("--min-teaching-value", type=float, default=0.0)
    parser.add_argument("--max-chunks-per-knowledge-point", type=int, default=None)
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        default=0,
        help="Estimated evidence-token budget before LLM/writer/reviewer stages. 0 disables automatic trimming.",
    )
    parser.add_argument(
        "--max-tokens-per-evidence-chunk",
        type=int,
        default=1200,
        help="When --max-input-tokens is enabled, trim any single evidence chunk to this estimated token limit.",
    )
    parser.add_argument(
        "--summarize-over-budget",
        action="store_true",
        help="Reserve part of --max-input-tokens for summary evidence chunks built from dropped evidence.",
    )
    parser.add_argument("--summary-token-reserve-ratio", type=float, default=0.3)
    parser.add_argument("--max-tokens-per-summary-chunk", type=int, default=500)
    parser.add_argument("--max-summary-source-chunks", type=int, default=8)
    parser.add_argument("--review-rounds", type=int, default=1)
    parser.add_argument(
        "--copy-media-assets",
        action="store_true",
        help="Copy videos/keyframes into digital_book/assets. Default links to the processing workspace to avoid duplicating large media.",
    )
    parser.add_argument(
        "--student-package-output",
        type=Path,
        default=None,
        help="Optional output path for a validated student-facing digital_book.zip package.",
    )
    parser.add_argument(
        "--student-package-asset-fallback-zip",
        type=Path,
        default=None,
        help="Optional existing digital_book.zip to reuse assets when --copy-media-assets is not enabled.",
    )
    parser.add_argument(
        "--student-package-ask-smoke-question",
        default="",
        help="Optional local ask-book smoke-test question for the generated student package.",
    )
    parser.add_argument(
        "--student-package-ask-smoke-expected",
        action="append",
        default=[],
        help="Expected term for --student-package-ask-smoke-question. Can be passed multiple times.",
    )
    parser.add_argument(
        "--student-package-max-mb",
        type=float,
        default=2048.0,
        help="Maximum allowed student package size in MB. Use 0 to disable.",
    )
    parser.add_argument(
        "--student-package-max-asset-files",
        type=int,
        default=0,
        help="Maximum allowed number of packaged media asset files. Use 0 to disable.",
    )
    parser.add_argument(
        "--student-package-skip-static-smoke",
        action="store_true",
        help="Skip static HTML/JSON/media reference smoke test for the generated student package.",
    )
    parser.add_argument("--use-llm", action="store_true", help="Use OpenAI-compatible LLM for supported agents.")
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-max-retries", type=int, default=2)
    parser.add_argument("--llm-retry-backoff", type=float, default=1.0)
    parser.add_argument("--llm-cache-path", type=Path, default=None)
    parser.add_argument("--no-llm-cache", action="store_true")
    parser.add_argument(
        "--skip-resource-analyst-llm",
        action="store_true",
        help="Skip per-chunk LLM enhancement in ResourceAnalystAgent. Saves ~1000 LLM calls. "
        "Rule-based conversion still runs; downstream agents still use LLM.",
    )
    args = parser.parse_args()

    material_root = args.material_root.resolve()
    os.environ.setdefault("DTEXTBOOKS_WORK", str(material_root))
    json_dir = material_root / "02_working_processing" / "json"
    video_segments_path = (args.segments or json_dir / "video_segments.jsonl").resolve()
    ppt_assets_path = (args.ppt_assets or json_dir / "ppt_assets.jsonl").resolve()
    output_dir = (args.output_dir or default_generated_output_dir(material_root, args.title)).resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[runner] material_root={material_root}", flush=True)
    print(f"[runner] output_dir={output_dir}", flush=True)
    print("[runner] loading video segments", flush=True)
    video_records = read_jsonl(video_segments_path) if video_segments_path.exists() else []
    video_records = filter_records(
        video_records,
        chapter=args.chapter,
        knowledge_point=args.knowledge_point,
        limit=args.max_video_records,
    )
    print(f"[runner] selected video records={len(video_records)}", flush=True)
    selected_video_segments_path = output_dir / "selected_video_segments.jsonl"
    write_jsonl(selected_video_segments_path, video_records)

    explicit_document_paths = [path.resolve() for path in args.document_segments]
    auto_document_paths = [ppt_assets_path] if ppt_assets_path.exists() else []
    document_paths: list[Path] = []
    seen_paths: set[Path] = set()
    duplicate_source_paths: list[str] = []
    for path in [*auto_document_paths, *explicit_document_paths]:
        if path in seen_paths:
            duplicate_source_paths.append(str(path))
            continue
        seen_paths.add(path)
        document_paths.append(path)
    print(f"[runner] auto-discovered document sources={len(auto_document_paths)}", flush=True)
    print(f"[runner] explicit document sources={len(explicit_document_paths)}", flush=True)
    print(f"[runner] deduplicated document sources={len(document_paths)}", flush=True)
    combined_document_path = None
    document_records: list[dict] = []
    source_record_audit: list[dict[str, object]] = []
    for path in document_paths:
        if not path.exists():
            raise SystemExit(f"Missing document evidence file: {path}")
        records = read_jsonl(path)
        document_records.extend(records)
        source_record_audit.append({
            "path": str(path),
            "source_kind": "auto_discovered" if path in auto_document_paths else "explicit",
            "records": len(records),
        })
    document_records_before_deduplication = len(document_records)
    document_records, duplicate_record_count = _dedupe_source_records(document_records)
    document_records_after_deduplication = len(document_records)
    if duplicate_source_paths or duplicate_record_count:
        print(
            "[runner] duplicate material source detected: "
            f"duplicate_paths={len(duplicate_source_paths)} "
            f"duplicate_records_removed={duplicate_record_count}",
            flush=True,
        )
    document_records = filter_records(
        document_records,
        chapter=args.chapter,
        knowledge_point=args.knowledge_point,
        limit=args.max_document_records,
    )
    if document_records:
        combined_document_path = output_dir / "combined_document_segments.jsonl"
        write_jsonl(combined_document_path, document_records)
    source_record_count = len(video_records) + len(document_records)
    material_input_audit_path = output_dir / "material_input_audit.json"
    material_input_audit = {
        "schema": "materials2textbook.material_input_audit.v1",
        "explicit_sources": [str(path) for path in explicit_document_paths],
        "auto_discovered_sources": [str(path) for path in auto_document_paths],
        "deduplicated_sources": [str(path) for path in document_paths],
        "duplicate_source_paths": duplicate_source_paths,
        "source_record_audit": source_record_audit,
        "video_records": len(video_records),
        "document_records_before_deduplication": document_records_before_deduplication,
        "document_records_after_deduplication": document_records_after_deduplication,
        "selected_document_records": len(document_records),
        "duplicate_document_records_removed": duplicate_record_count,
        "final_source_record_count": source_record_count,
        "expected_source_record_count": args.expected_source_record_count,
        "source_record_count_valid": (
            args.expected_source_record_count is None
            or source_record_count == args.expected_source_record_count
        ),
    }
    write_json(material_input_audit_path, material_input_audit)
    print(f"[runner] selected document records={len(document_records)}", flush=True)
    print(f"[runner] deduplicated source records={source_record_count}", flush=True)
    print(f"[runner] material input audit={material_input_audit_path}", flush=True)
    if not video_records and not document_records:
        raise SystemExit("No video or document evidence records matched the selected filters.")
    _validate_expected_source_record_count(source_record_count, args.expected_source_record_count)

    provider = build_llm_provider(args, output_dir)
    domain_config = load_domain_config(
        args.domain_config.resolve() if args.domain_config else None,
        domain_name=args.domain_name,
        audience=args.audience,
    )
    print(f"[runner] use_llm={args.use_llm}", flush=True)
    if args.semantic_book_mode and not args.use_llm:
        print(
            "[runner] semantic planner mode=deterministic (enable --use-llm for model-backed semantic planning)",
            flush=True,
        )
    workflow = TextbookWorkflow(
        llm_provider=provider,
        use_llm=args.use_llm,
        domain_config=domain_config,
        auto_plan=not args.disable_auto_plan,
        llm_book_planning=not args.disable_llm_book_planning,
    )
    if args.skip_resource_analyst_llm:
        workflow.resource_analyst.use_llm = False
        print("[runner] ResourceAnalystAgent LLM enhancement: SKIPPED (rule-based only)", flush=True)
    config = WorkflowConfig(
        include_pending=not args.approved_only,
        include_rejected=args.include_rejected,
        min_teaching_value=args.min_teaching_value,
        max_chunks_per_knowledge_point=args.max_chunks_per_knowledge_point,
        max_input_tokens=args.max_input_tokens,
        max_tokens_per_evidence_chunk=args.max_tokens_per_evidence_chunk,
        summarize_over_budget=args.summarize_over_budget,
        summary_token_reserve_ratio=args.summary_token_reserve_ratio,
        max_tokens_per_summary_chunk=args.max_tokens_per_summary_chunk,
        max_summary_source_chunks=args.max_summary_source_chunks,
        review_rounds=args.review_rounds,
        copy_media_assets=args.copy_media_assets,
    )
    outputs = workflow.run(
        video_segments_path=selected_video_segments_path,
        output_dir=output_dir,
        title=args.title,
        config=config,
        document_segments_path=combined_document_path,
        book_mode=args.book_mode or args.semantic_book_mode,
        semantic_book_mode=args.semantic_book_mode,
        manifest_xlsx=args.manifest_xlsx.resolve() if args.manifest_xlsx else None,
        book_plan_output=args.book_plan_output.resolve() if args.book_plan_output else None,
        chapter_output_root=args.chapter_output_root.resolve() if args.chapter_output_root else None,
        max_chapters=args.max_chapters,
        max_chapter_input_tokens=args.max_chapter_input_tokens,
        resume_chapters=not args.force_rebuild_chapters,
        domain_config=domain_config,
        auto_plan=not args.disable_auto_plan,
        llm_book_planning=not args.disable_llm_book_planning,
        book_plan_input=args.book_plan_input.resolve() if args.book_plan_input else None,
        book_plan_is_frozen=args.book_plan_is_frozen,
        freeze_calibration_input=args.freeze_calibration_input.resolve() if args.freeze_calibration_input else None,
        completeness_first_authoring=args.completeness_first_authoring,
        preview_fullbook=args.preview_fullbook,
    )
    try:
        workflow_manifest = json.loads(Path(outputs.manifest_path).read_text(encoding="utf-8"))
        final_chunk_count = workflow_manifest.get("summary", {}).get("evidence_chunks")
        material_input_audit["final_chunk_count"] = final_chunk_count
        material_input_audit["final_chunk_count_matches_source_records"] = (
            final_chunk_count == source_record_count
        )
        write_json(material_input_audit_path, material_input_audit)
        print(f"[runner] final chunk count={final_chunk_count}", flush=True)
    except (OSError, TypeError, ValueError) as exc:
        material_input_audit["final_chunk_count_error"] = f"{type(exc).__name__}: {exc}"
        write_json(material_input_audit_path, material_input_audit)

    print("Full digital textbook generated:")
    print(f"- material_root: {material_root}")
    print(f"- video_segments: {video_segments_path}")
    print(f"- selected_video_records: {len(video_records)}")
    print(f"- document_sources: {len(document_paths)}")
    print(f"- selected_document_records: {len(document_records)}")
    print(f"- deduplicated_source_records: {source_record_count}")
    print(f"- material_input_audit: {material_input_audit_path}")
    print(f"- copy_media_assets: {args.copy_media_assets}")
    print(f"- max_input_tokens: {args.max_input_tokens}")
    print(f"- max_tokens_per_evidence_chunk: {args.max_tokens_per_evidence_chunk}")
    print(f"- summarize_over_budget: {args.summarize_over_budget}")
    print(f"- book_mode: {args.book_mode or args.semantic_book_mode}")
    print(f"- semantic_book_mode: {args.semantic_book_mode}")
    print(f"- preview_fullbook: {args.preview_fullbook}")
    print(f"- domain_name: {domain_config.domain_name}")
    if args.manifest_xlsx:
        print(f"- manifest_xlsx: {args.manifest_xlsx.resolve()}")
    if combined_document_path:
        print(f"- combined_document_segments: {combined_document_path}")
    print(f"- agent_outputs: {outputs.manifest_path}")
    print(f"- digital_book_json: {outputs.digital_book_path}")
    print(f"- digital_book_index: {outputs.digital_book_index_path}")
    if args.student_package_output:
        final_package_path = args.student_package_output
        staged_package_path = _staged_output_path(final_package_path)
        staged_package_path.unlink(missing_ok=True)
        package_path = write_student_digital_book_package(
            source_dir=Path(outputs.digital_book_dir),
            output_zip=staged_package_path,
            asset_fallback_zip=args.student_package_asset_fallback_zip,
        )
        package_issues = validate_student_digital_book_package(
            package_path,
            max_package_bytes=int(args.student_package_max_mb * 1024 * 1024),
            max_asset_files=args.student_package_max_asset_files,
        )
        if args.student_package_ask_smoke_question:
            package_issues.extend(
                smoke_test_student_package_ask(
                    package_path,
                    question=args.student_package_ask_smoke_question,
                    expected_terms=args.student_package_ask_smoke_expected,
                )
            )
        if not args.student_package_skip_static_smoke:
            package_issues.extend(smoke_test_student_package_static_assets(package_path))
        if package_issues:
            for issue in package_issues:
                print(f"- student_package_issue: {issue}")
            package_path.unlink(missing_ok=True)
            raise SystemExit(1)
        final_package_path.parent.mkdir(parents=True, exist_ok=True)
        package_path.replace(final_package_path)
        print(f"- student_package_zip: {final_package_path}")

def filter_records(
    records: list[dict],
    *,
    chapter: str = "",
    knowledge_point: str = "",
    limit: int = 0,
) -> list[dict]:
    chapter = chapter.strip().lower()
    knowledge_point = knowledge_point.strip().lower()
    filtered = []
    for record in records:
        if chapter and chapter not in _record_chapter_text(record).lower():
            continue
        if knowledge_point and knowledge_point not in _record_knowledge_text(record).lower():
            continue
        filtered.append(record)
        if limit and len(filtered) >= limit:
            break
    return filtered


def _record_chapter_text(record: dict) -> str:
    values = [
        record.get("recommended_chapter"),
        record.get("chapter"),
        record.get("chapter_title"),
        record.get("target_chapter"),
    ]
    return " ".join(str(value) for value in values if value)


def _record_knowledge_text(record: dict) -> str:
    values = [
        record.get("knowledge_point"),
        record.get("knowledge_point_cn"),
        record.get("heading"),
        record.get("slide_title"),
        record.get("section_title"),
    ]
    return " ".join(str(value) for value in values if value)


def _staged_output_path(output: Path) -> Path:
    output = Path(output)
    return output.with_name(f".{output.name}.tmp")


if __name__ == "__main__":
    main()
