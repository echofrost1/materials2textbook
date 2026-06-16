from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.digital_book_polisher import build_digital_book_polisher_messages
from materials2textbook.schemas import (
    ChapterPlan,
    DigitalBook,
    DigitalBookBlock,
    DigitalBookProject,
    DigitalBookTask,
    EvidenceChunk,
    KnowledgePoint,
)

MAX_VIDEO_BLOCKS_PER_KNOWLEDGE_POINT = 2
STUDENT_PACKAGE_EXCLUDED_FILES = {"digital_book_review.json", "digital_book_review.md"}
STUDENT_PACKAGE_FORBIDDEN_TERMS = [
    "teacher_evidence",
    "evidence_chunk_ids",
    "chunk_id",
    "C000",
    "Pending_",
    "PPT_",
    "证据：",
    "证据编号",
    "来源：",
    "教师应",
    "basic/",
    "learning_path",
]


def build_digital_book(
    *,
    title: str,
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    output_dir: Path,
    copy_media_assets: bool = True,
    llm_provider: LLMProvider | None = None,
    use_llm: bool = False,
) -> DigitalBook:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    assets: dict[str, list[dict]] = {"videos": [], "keyframes": [], "images": []}
    projects: list[DigitalBookProject] = []

    for project_index, plan in enumerate(plans, start=1):
        task_blocks: list[DigitalBookBlock] = [
            DigitalBookBlock(
                block_id=f"p{project_index:02d}_scenario",
                type="scenario",
                title="情境导入",
                markdown=f"围绕“{plan.title}”的真实教学素材，观察视频片段并完成本任务的知识学习与操作分析。",
                evidence_chunk_ids=plan.evidence_chunk_ids,
            ),
            DigitalBookBlock(
                block_id=f"p{project_index:02d}_learning_nav",
                type="learning_nav",
                title="学习路径",
                items=[_format_learning_path_item(point, plan.knowledge_points) for point in plan.knowledge_points],
                evidence_chunk_ids=plan.evidence_chunk_ids,
            ),
        ]

        key_terms: list[str] = []
        for point_index, point in enumerate(plan.knowledge_points, start=1):
            point_chunks = [chunk_map[chunk_id] for chunk_id in point.chunk_ids if chunk_id in chunk_map]
            key_terms.extend(point.title for _chunk in point_chunks[:1])
            implementation_text, polish_metadata = _render_point_markdown(
                point.title,
                point.summary,
                point_chunks,
                llm_provider=llm_provider,
                use_llm=use_llm,
            )
            task_blocks.append(
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_kp{point_index:02d}_text",
                    type="implementation",
                    title=point.title,
                    markdown=implementation_text,
                    evidence_chunk_ids=[chunk.chunk_id for chunk in point_chunks],
                    metadata={"teacher_evidence": _teacher_evidence_refs(point_chunks), **polish_metadata},
                )
            )
            for media_index, chunk in enumerate(_select_video_chunks(point_chunks), start=1):
                media_block = _build_video_block(
                    chunk=chunk,
                    output_dir=output_dir,
                    block_id=f"p{project_index:02d}_kp{point_index:02d}_media{media_index:02d}",
                    assets=assets,
                    copy_media_assets=copy_media_assets,
                )
                if media_block:
                    task_blocks.append(media_block)

        if plan.case_examples:
            for case_index, case in enumerate(plan.case_examples, start=1):
                task_blocks.append(
                    DigitalBookBlock(
                        block_id=f"p{project_index:02d}_case{case_index:02d}",
                        type="case_example",
                        title=case.title,
                        markdown=f"**例题**：{case.prompt}\n\n**参考分析**：{case.reference_answer}",
                        evidence_chunk_ids=case.evidence_chunk_ids,
                    )
                )

        task_blocks.extend(
            [
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_assessment",
                    type="assessment",
                    title="任务评价",
                    items=_assessment_items(plan),
                    evidence_chunk_ids=plan.evidence_chunk_ids,
                ),
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_exercises",
                    type="exercises",
                    title="思考与练习",
                    items=_exercise_items(plan),
                    evidence_chunk_ids=plan.evidence_chunk_ids,
                ),
            ]
        )

        task = DigitalBookTask(
            task_id=f"task_{project_index:02d}_01",
            title=f"任务{project_index}.1 {plan.title}",
            blocks=task_blocks,
            knowledge_points=[point.title for point in plan.knowledge_points],
            key_terms=_dedupe(key_terms),
            evidence_chunk_ids=plan.evidence_chunk_ids,
        )
        projects.append(
            DigitalBookProject(
                project_id=f"project_{project_index:02d}",
                title=f"项目{project_index} {plan.title}",
                project_intro=f"本项目围绕“{plan.title}”展开学习，结合示范视频和学习要点理解相关知识与操作。",
                ability_map=[
                    "示范观察与要点提取",
                    "知识点理解与复述",
                    "操作过程分析与质量判断",
                ],
                learning_goals=plan.learning_goals,
                tasks=[task],
            )
        )

    return DigitalBook(
        book_id=_slugify(title),
        title=title,
        metadata={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "format": "materials2textbook.digital_book.v1",
        },
        projects=projects,
        assets=assets,
    )


def export_digital_book(
    *,
    title: str,
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    output_dir: Path,
    copy_media_assets: bool = True,
    llm_provider: LLMProvider | None = None,
    use_llm: bool = False,
) -> tuple[DigitalBook, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    book = build_digital_book(
        title=title,
        plans=plans,
        chunks=chunks,
        output_dir=output_dir,
        copy_media_assets=copy_media_assets,
        llm_provider=llm_provider,
        use_llm=use_llm,
    )
    json_path = output_dir / "digital_book.json"
    index_path = output_dir / "index.html"
    write_json(json_path, book)
    write_text(index_path, VIEWER_HTML)
    write_text(output_dir / "styles.css", VIEWER_CSS)
    write_text(output_dir / "ask_config.js", ASK_CONFIG_JS)
    write_text(output_dir / "app.js", VIEWER_JS)
    return book, json_path, index_path


def write_student_digital_book_package(
    *,
    source_dir: Path,
    output_zip: Path,
    asset_fallback_zip: Path | None = None,
) -> Path:
    """Create a student-facing reader package without teacher audit traces."""
    source_dir = Path(source_dir)
    output_zip = Path(output_zip)
    if not source_dir.exists():
        raise FileNotFoundError(f"Digital book directory not found: {source_dir}")

    with tempfile.TemporaryDirectory(prefix="digital_book_student_") as temp_name:
        package_root = Path(temp_name) / "digital_book"
        package_root.mkdir(parents=True, exist_ok=True)
        if asset_fallback_zip and Path(asset_fallback_zip).exists():
            _copy_assets_from_zip(Path(asset_fallback_zip), package_root)
        _copy_student_package_files(source_dir, package_root)
        _write_student_book_json(source_dir / "digital_book.json", package_root / "digital_book.json")
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        if output_zip.exists():
            output_zip.unlink()
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package_root.parent).as_posix())
    return output_zip


def validate_student_digital_book_package(
    package_zip: Path,
    *,
    max_package_bytes: int = 0,
    max_asset_files: int = 0,
) -> list[str]:
    package_zip = Path(package_zip)
    issues: list[str] = []
    if not package_zip.exists():
        return [f"Package not found: {package_zip}"]
    if max_package_bytes > 0 and package_zip.stat().st_size > max_package_bytes:
        issues.append(
            f"Package is too large: {package_zip.stat().st_size} bytes > {max_package_bytes} bytes"
        )

    try:
        with zipfile.ZipFile(package_zip) as archive:
            names = archive.namelist()
            name_set = set(names)
            duplicate_names = _duplicate_zip_members(names)
            if duplicate_names:
                issues.append(f"Duplicate zip member paths: {', '.join(duplicate_names[:5])}")
            unsafe_names = [name for name in names if _unsafe_zip_member(name)]
            if unsafe_names:
                issues.append(f"Unsafe zip member paths: {', '.join(unsafe_names[:5])}")
            for required in [
                "digital_book/index.html",
                "digital_book/app.js",
                "digital_book/styles.css",
                "digital_book/ask_config.js",
                "digital_book/digital_book.json",
            ]:
                if required not in name_set:
                    issues.append(f"Missing required file: {required}")
            review_files = [name for name in names if Path(name).name in STUDENT_PACKAGE_EXCLUDED_FILES]
            if review_files:
                issues.append(f"Teacher review files must not be packaged: {', '.join(review_files)}")
            asset_files = [name for name in names if name.startswith("digital_book/assets/") and not name.endswith("/")]
            if not asset_files:
                issues.append("No packaged media assets found under digital_book/assets/")
            if max_asset_files > 0 and len(asset_files) > max_asset_files:
                issues.append(f"Too many packaged media assets: {len(asset_files)} > {max_asset_files}")
            if "digital_book/digital_book.json" in name_set:
                book_text = archive.read("digital_book/digital_book.json").decode("utf-8")
                book = json.loads(book_text)
                json_hits = [term for term in STUDENT_PACKAGE_FORBIDDEN_TERMS if term in book_text]
                if json_hits:
                    issues.append(f"Student JSON contains internal terms: {', '.join(json_hits)}")
                visible_text = _student_visible_book_text(book)
                visible_hits = [term for term in STUDENT_PACKAGE_FORBIDDEN_TERMS if term in visible_text]
                if visible_hits:
                    issues.append(f"Student-visible text contains internal terms: {', '.join(visible_hits)}")
                media_ref_issues = _student_package_media_ref_issues(book, name_set)
                issues.extend(media_ref_issues)
            if "digital_book/app.js" in name_set:
                app_js = archive.read("digital_book/app.js").decode("utf-8")
                if "block.type || block.block_type" not in app_js:
                    issues.append("Reader ask index is missing block type compatibility logic.")
                if "[2, 3, 4]" not in app_js:
                    issues.append("Reader ask tokenizer is missing Chinese short-term splitting.")
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.append(f"Invalid student package: {exc}")
    return issues


def smoke_test_student_package_ask(
    package_zip: Path,
    *,
    question: str,
    expected_terms: list[str],
    forbidden_terms: list[str] | None = None,
    max_results: int = 3,
) -> list[str]:
    package_zip = Path(package_zip)
    if not package_zip.exists():
        return [f"Package not found: {package_zip}"]
    try:
        with zipfile.ZipFile(package_zip) as archive:
            book = json.loads(archive.read("digital_book/digital_book.json").decode("utf-8"))
    except (KeyError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"Cannot load packaged digital_book.json: {exc}"]

    terms = _ask_tokenize_question(question)
    results = _prefer_ask_results(
        [
            {"row": row, "score": _score_ask_row(row, terms)}
            for row in _build_package_ask_index(book)
        ],
        terms,
    )
    focused = [item for item in results if item["score"] > 0][:max_results]
    if not focused:
        return [f"Ask smoke test found no result for question: {question}"]

    answer_text = "\n".join(
        f"{item['row']['task_title']} / {item['row']['block_title']}\n{item['row']['text']}"
        for item in focused
    )
    issues: list[str] = []
    missing = [term for term in expected_terms if term not in answer_text]
    if missing:
        issues.append(f"Ask smoke test missing expected terms: {', '.join(missing)}")
    blocked_terms = forbidden_terms or STUDENT_PACKAGE_FORBIDDEN_TERMS
    hits = [term for term in blocked_terms if term in answer_text]
    if hits:
        issues.append(f"Ask smoke test returned forbidden terms: {', '.join(hits)}")
    return issues


def smoke_test_student_package_static_assets(package_zip: Path) -> list[str]:
    package_zip = Path(package_zip)
    if not package_zip.exists():
        return [f"Package not found: {package_zip}"]
    try:
        with zipfile.ZipFile(package_zip) as archive:
            names = set(archive.namelist())
            issues: list[str] = []
            for required in [
                "digital_book/index.html",
                "digital_book/app.js",
                "digital_book/styles.css",
                "digital_book/ask_config.js",
                "digital_book/digital_book.json",
            ]:
                if required not in names:
                    issues.append(f"Static smoke test missing required file: {required}")
            if issues:
                return issues
            html = archive.read("digital_book/index.html").decode("utf-8")
            for ref in _html_local_refs(html):
                if f"digital_book/{ref}" not in names:
                    issues.append(f"Static smoke test missing HTML asset: {ref}")
            book = json.loads(archive.read("digital_book/digital_book.json").decode("utf-8"))
            for ref in _book_media_refs(book):
                if f"digital_book/{ref}" not in names:
                    issues.append(f"Static smoke test missing book media: {ref}")
            return issues
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"Static smoke test failed to read package: {exc}"]


def _copy_student_package_files(source_dir: Path, package_root: Path) -> None:
    for source_path in source_dir.rglob("*"):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(source_dir)
        if relative.name in STUDENT_PACKAGE_EXCLUDED_FILES:
            continue
        if relative.as_posix() == "digital_book.json":
            continue
        target_path = package_root / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def _copy_assets_from_zip(zip_path: Path, package_root: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if not info.filename.startswith("digital_book/assets/"):
                continue
            if _unsafe_zip_member(info.filename):
                raise ValueError(f"Unsafe asset path in fallback zip: {info.filename}")
            relative = Path(PurePosixPath(info.filename).relative_to("digital_book"))
            target_path = package_root / relative
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target)


def _unsafe_zip_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    )


def _duplicate_zip_members(names: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def _write_student_book_json(source_json: Path, target_json: Path) -> None:
    book = json.loads(source_json.read_text(encoding="utf-8"))
    packaged_assets = _packaged_asset_map(target_json.parent)
    for project in book.get("projects", []):
        for task in project.get("tasks", []):
            task.pop("evidence_chunk_ids", None)
            for block in task.get("blocks", []):
                _rewrite_block_media_refs(block, packaged_assets)
                block.pop("evidence_chunk_ids", None)
                block.pop("metadata", None)
    for asset_group in book.get("assets", {}).values():
        if isinstance(asset_group, list):
            for asset in asset_group:
                if isinstance(asset, dict):
                    _rewrite_asset_media_ref(asset, packaged_assets)
                    asset.pop("chunk_id", None)
    write_json(target_json, book)


def _packaged_asset_map(package_root: Path) -> dict[str, list[str]]:
    assets_root = package_root / "assets"
    mapping: dict[str, list[str]] = {}
    if not assets_root.exists():
        return mapping
    for path in assets_root.rglob("*"):
        if path.is_file():
            mapping.setdefault(path.name, []).append(path.relative_to(package_root).as_posix())
    return mapping


def _rewrite_block_media_refs(block: dict, packaged_assets: dict[str, list[str]]) -> None:
    for key, preferred_prefixes in {
        "src": ("assets/videos/",),
        "poster": ("assets/keyframes/", "assets/images/"),
    }.items():
        value = str(block.get(key, ""))
        rewritten = _rewrite_packaged_media_ref(value, packaged_assets, preferred_prefixes)
        if rewritten:
            block[key] = rewritten


def _rewrite_asset_media_ref(asset: dict, packaged_assets: dict[str, list[str]]) -> None:
    for key, preferred_prefixes in {
        "src": ("assets/videos/", "assets/keyframes/", "assets/images/"),
        "poster": ("assets/keyframes/", "assets/images/"),
    }.items():
        value = str(asset.get(key, ""))
        rewritten = _rewrite_packaged_media_ref(value, packaged_assets, preferred_prefixes)
        if rewritten:
            asset[key] = rewritten


def _rewrite_packaged_media_ref(
    value: str,
    packaged_assets: dict[str, list[str]],
    preferred_prefixes: tuple[str, ...],
) -> str:
    if not value:
        return ""
    if value.startswith("assets/"):
        return value
    candidates = packaged_assets.get(Path(value).name, [])
    for prefix in preferred_prefixes:
        for candidate in candidates:
            if candidate.startswith(prefix):
                return candidate
    return candidates[0] if len(candidates) == 1 else value


def _student_visible_book_text(book: dict) -> str:
    values: list[str] = []
    for project in book.get("projects", []):
        values.extend(
            [
                str(project.get("title", "")),
                str(project.get("project_intro", "")),
                *[str(item) for item in project.get("ability_map", [])],
                *[str(item) for item in project.get("learning_goals", [])],
            ]
        )
        for task in project.get("tasks", []):
            values.extend(
                [
                    str(task.get("title", "")),
                    *[str(item) for item in task.get("knowledge_points", [])],
                    *[str(item) for item in task.get("key_terms", [])],
                ]
            )
            for block in task.get("blocks", []):
                values.extend(
                    [
                        str(block.get("title", "")),
                        str(block.get("markdown", "")),
                        *[str(item) for item in block.get("items", [])],
                    ]
                )
    return "\n".join(values)


def _student_package_media_ref_issues(book: dict, package_names: set[str]) -> list[str]:
    issues: list[str] = []
    refs: list[str] = []
    for project in book.get("projects", []):
        for task in project.get("tasks", []):
            for block in task.get("blocks", []):
                refs.extend(_media_refs_from_mapping(block))
    for asset_group in book.get("assets", {}).values():
        if isinstance(asset_group, list):
            for asset in asset_group:
                if isinstance(asset, dict):
                    refs.extend(_media_refs_from_mapping(asset))
    for ref in _dedupe([item for item in refs if item]):
        if not ref.startswith("assets/"):
            issues.append(f"Packaged media reference must stay under assets/: {ref}")
            continue
        if f"digital_book/{ref}" not in package_names:
            issues.append(f"Packaged media reference is missing from zip: {ref}")
    return issues


def _book_media_refs(book: dict) -> list[str]:
    refs: list[str] = []
    for project in book.get("projects", []):
        for task in project.get("tasks", []):
            for block in task.get("blocks", []):
                refs.extend(_media_refs_from_mapping(block))
    for asset_group in book.get("assets", {}).values():
        if isinstance(asset_group, list):
            for asset in asset_group:
                if isinstance(asset, dict):
                    refs.extend(_media_refs_from_mapping(asset))
    return _dedupe([ref for ref in refs if ref])


def _media_refs_from_mapping(value: dict) -> list[str]:
    return [str(value.get(key, "")) for key in ("src", "poster") if value.get(key)]


def _html_local_refs(html: str) -> list[str]:
    refs = re.findall(r"""(?:src|href)=["']([^"']+)["']""", html)
    local_refs: list[str] = []
    for ref in refs:
        clean_ref = ref.split("?", 1)[0].split("#", 1)[0]
        if not clean_ref or "://" in clean_ref or clean_ref.startswith(("/", "#")):
            continue
        local_refs.append(clean_ref)
    return _dedupe(local_refs)


def _build_package_ask_index(book: dict) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for project in book.get("projects", []):
        for task in project.get("tasks", []):
            for block in task.get("blocks", []):
                text_parts = [
                    str(project.get("title", "")),
                    str(task.get("title", "")),
                    str(block.get("title", "")),
                    str(block.get("markdown", "")),
                    *[str(item) for item in block.get("items", [])],
                ]
                text = re.sub(r"\s+", " ", " ".join(text_parts)).strip()
                if not text:
                    continue
                rows.append(
                    {
                        "task_title": str(task.get("title", "")),
                        "block_title": str(block.get("title", "")),
                        "block_type": str(block.get("type", "")),
                        "text": text,
                    }
                )
    return rows


def _ask_tokenize_question(value: str) -> list[str]:
    terms: list[str] = []

    def push(term: str) -> None:
        cleaned = term.lower().strip()
        if len(cleaned) >= 2:
            terms.append(cleaned)

    for token in re.split(r"[^\w\u3400-\u9fff-]+", value or ""):
        push(token)
        for run in re.findall(r"[\u3400-\u9fff]{2,}", token):
            for size in (2, 3, 4):
                for index in range(0, len(run) - size + 1):
                    push(run[index : index + size])
    return _dedupe(terms)


def _score_ask_row(row: dict[str, str], terms: list[str]) -> int:
    text = row["text"].lower()
    title = f"{row['task_title']} {row['block_title']}".lower()
    score = 0
    for term in terms:
        if term in text:
            score += 3 if len(term) >= 5 else 1
        if term in title:
            score += 4
    if row.get("block_type") in {"learning_nav", "assessment", "exercises"}:
        score -= 3
    return score


def _prefer_ask_results(results: list[dict], terms: list[str]) -> list[dict]:
    scored = [item for item in results if item["score"] > 0]
    scored.sort(key=lambda item: item["score"], reverse=True)
    primary = [
        item
        for item in scored
        if item["row"].get("block_type") not in {"learning_nav", "assessment", "exercises"}
    ]
    candidates = primary or scored
    focus = _focus_ask_terms(terms)
    if not focus:
        return candidates
    focused = [
        item
        for item in candidates
        if any(term in f"{item['row']['block_title']} {item['row']['text']}".lower() for term in focus)
    ]
    return focused or candidates


def _focus_ask_terms(terms: list[str]) -> list[str]:
    generic = {
        "操作",
        "注意",
        "什么",
        "怎么",
        "如何",
        "要点",
        "说明",
        "相关",
        "学习",
        "知识",
        "任务",
        "操作要",
        "作要",
        "要注",
        "注意什",
        "意什",
        "要注意",
        "注意什么",
    }
    return [term for term in terms if len(term) >= 2 and term not in generic]


def _render_point_markdown(
    title: str,
    summary: str,
    chunks: list[EvidenceChunk],
    *,
    llm_provider: LLMProvider | None = None,
    use_llm: bool = False,
) -> tuple[str, dict[str, str]]:
    fallback = _render_point_markdown_fallback(title, summary, chunks)
    if not use_llm:
        return fallback, {"student_text_method": "extractive"}
    if llm_provider is None:
        return fallback, {"student_text_method": "extractive", "student_text_polish_status": "missing_llm_provider"}
    try:
        polished = llm_provider.generate(
            build_digital_book_polisher_messages(title=title, fallback_markdown=fallback)
        )
    except Exception as exc:  # pragma: no cover - defensive fallback for external LLM failures.
        return fallback, {
            "student_text_method": "extractive",
            "student_text_polish_status": f"llm_error:{type(exc).__name__}",
        }
    polished = _sanitize_polished_markdown(polished)
    if not _is_usable_student_markdown(polished):
        return fallback, {"student_text_method": "extractive", "student_text_polish_status": "llm_rejected"}
    return polished, {"student_text_method": "llm_polished"}


def _render_point_markdown_fallback(title: str, summary: str, chunks: list[EvidenceChunk]) -> str:
    sections = _student_learning_sections(chunks)
    lines = []
    summary_text = _clean_student_text(summary)
    if summary_text and not _looks_like_internal_review_text(summary_text) and not _looks_like_low_value_slide_text(summary_text):
        lines.append(f"本节围绕“{title}”展开，重点理解：{summary_text}")
        lines.append("")

    if any(sections.values()):
        if sections["concept"]:
            lines.append("概念说明：")
            for item in sections["concept"]:
                lines.append(f"- {item}")
            lines.append("")
        if sections["steps"]:
            lines.append("操作步骤：")
            for index, item in enumerate(sections["steps"], start=1):
                lines.append(f"{index}. {item}")
            lines.append("")
        if sections["notes"]:
            lines.append("注意事项：")
            for item in sections["notes"]:
                lines.append(f"- {item}")
            lines.append("")
        if sections["mistakes"]:
            lines.append("常见问题：")
            for item in sections["mistakes"]:
                lines.append(f"- {item}")
    elif not lines:
        lines.append(f"围绕“{title}”观察示范视频，理解关键概念、操作要求和常见注意事项。")

    video_count = sum(1 for chunk in chunks if chunk.source_type in {"video_segment", "video", "audio_segment"})
    if video_count:
        lines.append("")
        lines.append("请结合下方示范视频，重点观察操作动作、工件状态和教师提示。")
    return "\n".join(lines).strip()


def _student_learning_sections(chunks: list[EvidenceChunk]) -> dict[str, list[str]]:
    sections = {"concept": [], "steps": [], "notes": [], "mistakes": []}
    for chunk in chunks:
        if _is_unapproved_video_transcript(chunk):
            continue
        source_text = chunk.summary or chunk.content
        if _looks_like_internal_review_text(_clean_student_text(source_text)):
            source_text = chunk.content
        for sentence in _student_sentences(source_text):
            bucket = _student_section_bucket(sentence)
            if len(sections[bucket]) >= _section_limit(bucket):
                continue
            sections[bucket].append(sentence)
    return {key: _dedupe(values)[: _section_limit(key)] for key, values in sections.items()}


def _sanitize_polished_markdown(markdown: str) -> str:
    lines = []
    for raw_line in str(markdown).splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if _looks_like_internal_review_text(line) or _looks_like_low_quality_asr(line):
            continue
        if _contains_student_forbidden_trace(line):
            continue
        if line.startswith(("- ", "* ")):
            prefix = "- "
            body = line[2:]
            line = prefix + _clean_student_text(body, max_chars=180)
        elif re.match(r"^\d+[.、]\s*", line):
            prefix_match = re.match(r"^(\d+[.、]\s*)", line)
            prefix = prefix_match.group(1) if prefix_match else ""
            body = line[len(prefix) :]
            line = prefix + _clean_student_text(body, max_chars=180)
        elif line.endswith("："):
            line = _clean_student_text(line[:-1], max_chars=40) + "："
        else:
            line = _clean_student_text(line, max_chars=220)
        if line.strip(" -0123456789.、："):
            lines.append(line)
    return "\n".join(lines).strip()


def _is_usable_student_markdown(markdown: str) -> bool:
    if len(markdown.strip()) < 40:
        return False
    if _contains_student_forbidden_trace(markdown):
        return False
    if _looks_like_internal_review_text(markdown) or _looks_like_low_quality_asr(markdown):
        return False
    return True


def _contains_student_forbidden_trace(text: str) -> bool:
    forbidden_terms = (
        "chunk_id",
        "证据：",
        "来源：",
        "review_status",
        "Pending_",
        "待人工",
        "人工复核",
        "时间码",
        "kp_",
        "agent",
    )
    normalized = text.lower()
    if any(term.lower() in normalized for term in forbidden_terms):
        return True
    if re.search(r"`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?", text):
        return True
    if re.search(r"\.(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)\b", text, flags=re.IGNORECASE):
        return True
    return False


def _student_sentences(text: str) -> list[str]:
    cleaned = _clean_student_text(text, max_chars=420)
    if not cleaned or _looks_like_internal_review_text(cleaned) or _looks_like_low_value_slide_text(cleaned):
        return []
    parts = [
        part.strip(" ：:-，。；;")
        for part in re.split(r"[。；;]\s*|\s{2,}", cleaned)
        if part.strip(" ：:-，。；;")
    ]
    sentences = []
    for part in parts:
        part = _strip_outline_prefix(part)
        if _looks_like_internal_review_text(part) or _looks_like_low_value_slide_text(part) or _looks_like_low_quality_asr(part):
            continue
        if _looks_like_off_topic_sentence(part):
            continue
        if _looks_like_slide_heading_fragment(part):
            continue
        if len(part) < 8:
            continue
        if len(part) > 90:
            part = part[:87].rstrip() + "..."
        sentences.append(part)
    return sentences


def _student_section_bucket(sentence: str) -> str:
    if any(term in sentence for term in ("缺陷", "气孔", "夹钨", "烧穿", "熔合不良", "裂纹", "过热", "烧损")):
        return "mistakes"
    if any(term in sentence for term in ("应", "必须", "不允许", "注意", "保持", "避免", "防止", "清除", "检查")):
        return "notes"
    if any(term in sentence for term in ("包括", "组成", "装置", "系统", "性能", "特点", "应用", "分为")):
        return "concept"
    step_terms = ("操作时", "观察", "送入", "送丝", "引弧", "收弧", "填丝", "填加", "摆动", "移动", "拉回", "打磨", "调整")
    if any(term in sentence for term in step_terms):
        return "steps"
    return "concept"


def _section_limit(bucket: str) -> int:
    return {"concept": 2, "steps": 4, "notes": 4, "mistakes": 3}.get(bucket, 3)


def _clean_student_text(text: str, max_chars: int = 120) -> str:
    normalized = " ".join(str(text).split())
    normalized = re.sub(r"\bT\s*1\s*G\b", "TIG", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("高文刚", "高温钢")
    normalized = re.sub(r"\[\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\s*-->\s*\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\]", "", normalized)
    normalized = re.sub(r"`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?", "", normalized)
    normalized = re.sub(r"[\w\u4e00-\u9fff（）()、.-]+\s*\.\s*(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)", "", normalized, flags=re.IGNORECASE)
    normalized = _strip_outline_prefix(normalized)
    normalized = normalized.strip(" ：:-，。")
    if len(normalized) > max_chars:
        normalized = normalized[: max_chars - 3].rstrip() + "..."
    return normalized


def _strip_outline_prefix(text: str) -> str:
    cleaned = text.strip(" ：:-，。；;")
    heading_terms = (
        "手工钨极氩弧焊焊接参数",
        "焊接工艺参数的内容",
        "手工钨极氩弧焊设备",
        "手工 TIG 焊设备",
        "钨极氩弧焊的原理、特点及应用",
        "钨极氩弧焊的焊接过程",
        "焊接材料",
        "焊接操作要领",
        "焊接过程操作",
        "坡口准备及定位",
        "常见缺陷及其识别预防",
        "焊丝填充方式",
        "安全检查",
        "学习单元",
        "课程",
    )
    changed = True
    while changed:
        changed = False
        before = cleaned
        cleaned = re.sub(r"^\s*\d+(?:\.\d+)*[.、．]\s*", "", cleaned)
        cleaned = re.sub(r"^\s*[（(]\s*\d+\s*[）)]\s*", "", cleaned)
        cleaned = re.sub(r"^\s*\d+\s*[）)]\s*", "", cleaned)
        cleaned = re.sub(r"^\s*[一二三四五六七八九十]+[、.．]\s*", "", cleaned)
        cleaned = re.sub(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*", "", cleaned)
        for term in heading_terms:
            cleaned = re.sub(rf"^\s*{re.escape(term)}\s*", "", cleaned)
        cleaned = re.sub(r"^\s*的组成\s*", "", cleaned)
        changed = cleaned != before
    return cleaned.strip(" ：:-，。；;")


def _looks_like_slide_heading_fragment(text: str) -> bool:
    stripped = _strip_outline_prefix(text)
    if stripped in {"手工钨极氩弧焊焊接参数", "焊接工艺参数的内容", "安全检查"}:
        return True
    if any(term in stripped for term in ("焊接方法的分类", "课程 2-", "课程2-", "基础知识", "原理图", "连接方式 (a)", "连接方式（a）")):
        return True
    if any(term in stripped for term in ("清除焊缝或铸件缺陷", "被刨削面")):
        return True
    if stripped in {"钨极", "基本原理", "特点", "应用", "焊接过程"}:
        return True
    if len(stripped) < 24 and any(term in stripped for term in ("基本原理", "特点", "应用", "焊接过程", "设备组成")):
        return True
    if len(re.findall(r"\d+\s*[—-]\s*[\u4e00-\u9fff]", stripped)) >= 3:
        return True
    dense_numbering = len(re.findall(r"(?:^|\s)\d+[.、．]", text)) >= 3
    sparse_text = len(re.sub(r"[\s\d.、．（）()一二三四五六七八九十-]+", "", text)) < 12
    return dense_numbering and sparse_text


def _looks_like_low_quality_asr(text: str) -> bool:
    bad_terms = ("無幾", "壓護", "夫妻娘", "罕", "隱", "鈉", "漢师", "龙磁")
    return any(term in text for term in bad_terms)


def _looks_like_off_topic_sentence(text: str) -> bool:
    off_topic_terms = (
        "焊接应力",
        "焊接变形",
        "机械拉伸法",
        "温差拉伸法",
        "振动时效法",
        "焊条电弧焊",
        "焊接识图",
        "常用金属材料",
        "焊接材料知识",
    )
    return any(term in text for term in off_topic_terms)


def _looks_like_internal_review_text(text: str) -> bool:
    internal_terms = ("候选片段", "待人工", "待 agent", "处理队列", "人工复核", "确认边界", "时间码")
    normalized = text.lower()
    return any(term.lower() in normalized for term in internal_terms)


def _looks_like_low_value_slide_text(text: str) -> bool:
    normalized = text.lower()
    if "contents" in normalized or "目录" in text:
        return True
    short = re.sub(r"[\s\d.、一二三四五六七八九十]+", "", text)
    return len(short) < 8


def _is_unapproved_video_transcript(chunk: EvidenceChunk) -> bool:
    is_video = chunk.source_type in {"video_segment", "video", "audio_segment"}
    return is_video and "approved" not in chunk.review_status.lower()


def _select_video_chunks(chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    selected: list[EvidenceChunk] = []
    seen_sources: set[str] = set()
    video_chunks = [chunk for chunk in chunks if chunk.source_type in {"video_segment", "video", "audio_segment"}]
    for chunk in video_chunks:
        source_key = chunk.locator.path or chunk.metadata.get("source_video", "") or chunk.asset_id
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        selected.append(chunk)
        if len(selected) >= MAX_VIDEO_BLOCKS_PER_KNOWLEDGE_POINT:
            return selected
    if not selected and video_chunks:
        return video_chunks[:1]
    return selected


def _teacher_evidence_refs(chunks: list[EvidenceChunk]) -> list[dict[str, str]]:
    refs = []
    for chunk in chunks:
        source = chunk.metadata.get("source_video", "") or chunk.metadata.get("source_document", "") or chunk.locator.original_path or chunk.locator.path
        refs.append(
            {
                "chunk_id": chunk.chunk_id,
                "source": str(source),
                "start_time": str(chunk.metadata.get("start_time", "")),
                "end_time": str(chunk.metadata.get("end_time", "")),
                "review_status": chunk.review_status,
                "source_type": chunk.source_type,
            }
        )
    return refs


def _format_learning_path_item(point: KnowledgePoint, all_points: list[KnowledgePoint] | None = None) -> str:
    title_by_id = {item.knowledge_point_id: item.title for item in all_points or []}
    prerequisites = ", ".join(title_by_id.get(point_id, point_id) for point_id in point.prerequisite_ids) if point.prerequisite_ids else "无"
    return f"{point.order_index}. {point.title}（层级：{_difficulty_label(point.difficulty_level)}；先修：{prerequisites}）"


def _assessment_items(plan: ChapterPlan) -> list[str]:
    items: list[str] = []
    for activity in plan.activity_items:
        for rubric in activity.rubric:
            items.append(f"{_activity_label(activity)}：{rubric}")
    return items or [
        "能说出本任务涉及的核心概念或关键操作。",
        "能结合视频观察说明关键动作、工件状态或质量要求。",
        "能用自己的话复述至少一个知识点，并说明课堂操作中的注意事项。",
    ]


def _exercise_items(plan: ChapterPlan) -> list[str]:
    items = [
        f"{_activity_label(activity)}：{activity.prompt}"
        for activity in plan.activity_items
    ]
    if items:
        return items
    return [
        f"结合示范视频和学习要点，说明“{point.title}”的关键内容。"
        for point in plan.knowledge_points[:5]
    ] or ["结合示范视频和学习要点，总结本任务的关键学习收获。"]


def _activity_label(activity) -> str:
    return f"{_difficulty_label(activity.difficulty_level)}·{_activity_type_label(activity.type)}"


def _difficulty_label(level: str) -> str:
    return {"basic": "基础", "practice": "实操", "advanced": "拓展"}.get(level, level)


def _activity_type_label(activity_type: str) -> str:
    return {"observation": "观察任务", "explanation": "解释任务", "analysis": "迁移任务"}.get(activity_type, activity_type)


def _build_video_block(
    *,
    chunk: EvidenceChunk,
    output_dir: Path,
    block_id: str,
    assets: dict[str, list[dict]],
    copy_media_assets: bool,
) -> DigitalBookBlock | None:
    if chunk.source_type not in {"video_segment", "video", "audio_segment"}:
        return None
    source_path = _resolve_source_path(chunk.locator.path, asset_id=chunk.asset_id)
    if not source_path:
        return None
    if source_path.suffix.lower() not in {".mp4", ".webm", ".ogg", ".mov", ".m4v"}:
        return None

    video_rel = _copy_asset(source_path, output_dir / "assets" / "videos") if copy_media_assets else _relative_asset_path(source_path, output_dir)
    poster_rel = ""
    if chunk.locator.keyframe_paths:
        poster_source = _resolve_source_path(chunk.locator.keyframe_paths[0])
        if poster_source:
            poster_rel = (
                _copy_asset(poster_source, output_dir / "assets" / "keyframes")
                if copy_media_assets
                else _relative_asset_path(poster_source, output_dir)
            )
            assets["keyframes"].append(
                {
                    "chunk_id": chunk.chunk_id,
                    "src": poster_rel,
                    "original_path": str(poster_source),
                }
            )

    assets["videos"].append(
        {
            "chunk_id": chunk.chunk_id,
            "src": video_rel,
            "poster": poster_rel,
            "original_path": str(source_path),
            "start_time": chunk.metadata.get("start_time", ""),
            "end_time": chunk.metadata.get("end_time", ""),
        }
    )
    return DigitalBookBlock(
        block_id=block_id,
        type="video",
        title=f"{chunk.title} 视频片段",
        src=video_rel,
        poster=poster_rel,
        start_time=str(chunk.metadata.get("start_time", "")),
        end_time=str(chunk.metadata.get("end_time", "")),
        evidence_chunk_ids=[chunk.chunk_id],
        metadata={
            "source_video": chunk.metadata.get("source_video", ""),
            "original_path": chunk.locator.original_path,
        },
    )


def _resolve_source_path(path_value: str, asset_id: str = "") -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    candidates = [path]
    if not path.is_absolute():
        candidates.append(Path.cwd() / path)
        candidates.append(Path.cwd() / "work_materials" / "work_material1" / path)
        candidates.append(Path.cwd() / "work_materials" / "work_material1" / "02_working_processing" / "converted_mp4" / path.name)
        candidates.append(Path.cwd() / "work_material1" / path)
        candidates.append(Path.cwd() / "work_material1" / "02_working_processing" / "converted_mp4" / path.name)
        if asset_id:
            converted_dirs = [
                Path.cwd() / "work_materials" / "work_material1" / "02_working_processing" / "converted_mp4",
                Path.cwd() / "work_material1" / "02_working_processing" / "converted_mp4",
            ]
            for converted_dir in converted_dirs:
                candidates.extend(sorted(converted_dir.glob(f"{asset_id}_*.mp4")))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _copy_asset(source: Path, asset_dir: Path) -> str:
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / source.name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target.relative_to(asset_dir.parents[1]).as_posix()


def _relative_asset_path(source: Path, output_dir: Path) -> str:
    import os

    return os.path.relpath(source.resolve(), output_dir.resolve()).replace("\\", "/")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_").lower()
    return slug or "digital_book"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


VIEWER_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>数字教材</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <aside class="sidebar">
    <div class="brand">数字教材</div>
    <input id="searchInput" class="search" type="search" placeholder="搜索目录与正文">
    <nav id="toc" class="toc"></nav>
    <section class="study-panel">
      <div class="progress-row">
        <span>阅读进度</span>
        <strong id="progressLabel">0%</strong>
      </div>
      <div class="progress-track"><div id="progressBar" class="progress-bar"></div></div>
      <button id="bookmarkCurrent" class="icon-action" title="收藏当前任务" aria-label="收藏当前任务">★</button>
      <div id="bookmarks" class="bookmarks"></div>
      <label class="note-label" for="readerNote">学习笔记</label>
      <textarea id="readerNote" class="note" rows="6" placeholder="记录本教材的课堂提示、疑问或复核意见"></textarea>
      <div class="sync-actions">
        <button id="exportStudyData" type="button">导出学习数据</button>
        <button id="importStudyData" type="button">导入学习数据</button>
        <button id="syncStudyData" type="button">同步学习数据</button>
        <input id="studyDataFile" type="file" accept="application/json" hidden>
      </div>
      <div id="syncStatus" class="sync-status" aria-live="polite"></div>
      <label class="note-label" for="askInput">AI 问书</label>
      <div class="ask-box">
        <input id="askInput" class="ask-input" type="search" placeholder="输入知识点或操作问题">
        <button id="askButton" class="ask-button" type="button">问</button>
      </div>
      <div id="askAnswer" class="ask-answer"></div>
    </section>
  </aside>
  <main class="reader">
    <header class="toolbar">
      <div>
        <h1 id="bookTitle">加载中...</h1>
        <p id="bookMeta"></p>
      </div>
      <div class="tools">
        <button id="fontSmaller" title="缩小字号">A-</button>
        <button id="fontLarger" title="放大字号">A+</button>
      </div>
    </header>
    <div id="content" class="content"></div>
  </main>
  <script src="ask_config.js?v=20260616"></script>
  <script src="app.js?v=20260616"></script>
</body>
</html>
"""


ASK_CONFIG_JS = """// Optional teacher-owned ask-book endpoint.
// Leave empty to use the built-in local retrieval answer.
window.DIGITAL_BOOK_ASK_ENDPOINT = '';

// Optional learning-data endpoint.
// Leave empty to use JSON export/import only.
window.DIGITAL_BOOK_STUDY_ENDPOINT = '';
"""


VIEWER_CSS = """* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  grid-template-columns: 300px 1fr;
  font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
  color: #20242a;
  background: #f6f7f9;
}
.sidebar {
  height: 100vh;
  position: sticky;
  top: 0;
  overflow: auto;
  background: #ffffff;
  border-right: 1px solid #dfe3e8;
  padding: 18px 16px;
}
.brand {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 14px;
}
.search {
  width: 100%;
  height: 36px;
  border: 1px solid #cfd6df;
  border-radius: 6px;
  padding: 0 10px;
  margin-bottom: 14px;
}
.toc a {
  display: block;
  color: #2d3748;
  text-decoration: none;
  padding: 7px 8px;
  border-radius: 6px;
  line-height: 1.35;
}
.toc a:hover { background: #edf2f7; }
.toc a.active { background: #e6f0fb; color: #0f4f8a; font-weight: 700; }
.toc .task { padding-left: 20px; font-size: 14px; }
.study-panel {
  border-top: 1px solid #dfe3e8;
  margin-top: 18px;
  padding-top: 16px;
}
.progress-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #4a5568;
  font-size: 13px;
}
.progress-track {
  height: 8px;
  overflow: hidden;
  background: #e5eaf0;
  border-radius: 999px;
  margin: 8px 0 12px;
}
.progress-bar {
  width: 0%;
  height: 100%;
  background: #2274a5;
}
.icon-action {
  width: 36px;
  height: 34px;
  border: 1px solid #cfd6df;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
}
.bookmarks { margin: 10px 0 14px; }
.bookmark-item {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.bookmark-item a {
  flex: 1;
  color: #2d3748;
  text-decoration: none;
  font-size: 13px;
  line-height: 1.35;
}
.bookmark-item button {
  width: 28px;
  height: 28px;
  border: 1px solid #cfd6df;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
}
.note-label {
  display: block;
  color: #4a5568;
  font-size: 13px;
  margin-bottom: 6px;
}
.note {
  width: 100%;
  resize: vertical;
  border: 1px solid #cfd6df;
  border-radius: 6px;
  padding: 9px 10px;
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
}
.sync-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin: 10px 0 8px;
}
.sync-actions button {
  min-height: 34px;
  border: 1px solid #cfd6df;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.sync-status {
  min-height: 18px;
  color: #4a5568;
  font-size: 12px;
  line-height: 1.4;
  margin-bottom: 12px;
}
.ask-box {
  display: grid;
  grid-template-columns: 1fr 38px;
  gap: 6px;
  margin-bottom: 10px;
}
.ask-input {
  min-width: 0;
  height: 34px;
  border: 1px solid #cfd6df;
  border-radius: 6px;
  padding: 0 9px;
  font: inherit;
  font-size: 13px;
}
.ask-button {
  height: 34px;
  border: 1px solid #cfd6df;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.ask-answer {
  color: #2d3748;
  font-size: 13px;
  line-height: 1.5;
}
.ask-result {
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 8px;
  background: #fff;
}
.ask-result strong { display: block; margin-bottom: 4px; }
.ask-result a { color: #0f4f8a; text-decoration: none; }
.ask-result small { display: block; color: #667085; margin-top: 4px; }
.reader { min-width: 0; }
.toolbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  background: rgba(255,255,255,.96);
  border-bottom: 1px solid #dfe3e8;
  padding: 18px 28px;
}
.toolbar h1 { margin: 0; font-size: 24px; }
.toolbar p { margin: 4px 0 0; color: #687385; }
.tools button {
  height: 34px;
  min-width: 42px;
  border: 1px solid #cfd6df;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
}
.content {
  max-width: 980px;
  margin: 0 auto;
  padding: 28px;
  font-size: var(--reader-font-size, 17px);
  line-height: 1.75;
}
.project, .task, .block {
  background: #fff;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  margin-bottom: 18px;
  padding: 20px;
}
.project h2, .task h3, .block h4 { margin-top: 0; }
.tag-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.tag {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 2px 9px;
  background: #eef6ff;
  color: #164a7a;
  border: 1px solid #c7ddf4;
  border-radius: 999px;
  font-size: 13px;
}
.evidence {
  color: #667085;
  font-size: 13px;
  margin-top: 8px;
}
video {
  display: block;
  width: 100%;
  max-height: 520px;
  background: #111827;
  border-radius: 8px;
  margin-top: 10px;
}
pre.markdown {
  white-space: pre-wrap;
  font: inherit;
  margin: 0;
}
@media (max-width: 820px) {
  body { grid-template-columns: 1fr; }
  .sidebar { position: relative; height: auto; border-right: 0; border-bottom: 1px solid #dfe3e8; }
  .toolbar { position: relative; padding: 16px; }
  .content { padding: 16px; }
}
"""


VIEWER_JS = """const state = {
  book: null,
  fontSize: Number(localStorage.getItem('reader.fontSize') || '17'),
  activeId: '',
  storageKey: 'digital_book',
  askIndex: []
};

async function loadBook() {
  const response = await fetch('digital_book.json');
  state.book = await response.json();
  renderBook(state.book);
}

function renderBook(book) {
  state.storageKey = `digitalBook.${book.book_id || book.title || 'local'}`;
  document.title = book.title;
  document.getElementById('bookTitle').textContent = book.title;
  document.getElementById('bookMeta').textContent = `${book.metadata?.format || ''} · ${book.metadata?.generated_at || ''}`;
  document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  renderToc(book);
  renderContent(book);
  state.askIndex = buildAskIndex(book);
  restoreReaderState();
  bindScrollTracking();
}

function renderToc(book) {
  const toc = document.getElementById('toc');
  toc.innerHTML = '';
  for (const project of book.projects || []) {
    toc.appendChild(tocLink(project.title, project.project_id, 'project'));
    for (const task of project.tasks || []) {
      toc.appendChild(tocLink(task.title, task.task_id, 'task'));
    }
  }
}

function tocLink(text, id, className) {
  const link = document.createElement('a');
  link.href = `#${id}`;
  link.textContent = text;
  link.className = className;
  link.addEventListener('click', () => setActiveSection(id));
  return link;
}

function renderContent(book) {
  const root = document.getElementById('content');
  root.innerHTML = '';
  for (const project of book.projects || []) {
    const section = el('section', 'project');
    section.id = project.project_id;
    section.appendChild(heading('h2', project.title));
    section.appendChild(paragraph(project.project_intro));
    section.appendChild(listBlock('学习目标', project.learning_goals || []));
    section.appendChild(listBlock('能力图谱', project.ability_map || []));
    root.appendChild(section);

    for (const task of project.tasks || []) {
      const taskEl = el('section', 'task');
      taskEl.id = task.task_id;
      taskEl.appendChild(heading('h3', task.title));
      taskEl.appendChild(tagRow('知识点', task.knowledge_points || []));
      taskEl.appendChild(tagRow('重点词', task.key_terms || []));
      for (const block of task.blocks || []) {
        taskEl.appendChild(renderBlock(block));
      }
      root.appendChild(taskEl);
    }
  }
}

function renderBlock(block) {
  const node = el('article', `block block-${block.type}`);
  node.id = block.block_id;
  node.appendChild(heading('h4', block.title));
  if (block.type === 'video') {
    const video = document.createElement('video');
    video.controls = true;
    video.src = block.src;
    if (block.poster) video.poster = block.poster;
    if (block.start_time) video.dataset.startTime = block.start_time;
    video.addEventListener('loadedmetadata', () => {
      const seconds = timeToSeconds(video.dataset.startTime || '');
      if (seconds > 0) video.currentTime = seconds;
    }, { once: true });
    node.appendChild(video);
    node.appendChild(paragraph(`${block.start_time || ''} - ${block.end_time || ''}`));
  } else if (block.items && block.items.length) {
    node.appendChild(list(block.items));
  }
  if (block.markdown) {
    const pre = el('pre', 'markdown');
    pre.textContent = block.markdown;
    node.appendChild(pre);
  }
  return node;
}

function buildAskIndex(book) {
  const rows = [];
  for (const project of book.projects || []) {
    for (const task of project.tasks || []) {
      for (const block of task.blocks || []) {
        const textParts = [
          project.title,
          task.title,
          block.title,
          block.markdown || '',
          ...(block.items || []),
        ];
        const text = textParts.join(' ').replace(/\\s+/g, ' ').trim();
        if (!text) continue;
        rows.push({
          id: block.block_id,
          blockType: block.type || block.block_type || '',
          projectTitle: project.title,
          taskTitle: task.title,
          blockTitle: block.title,
          text,
          evidence: [],
        });
      }
    }
  }
  return rows;
}

async function answerQuestion(rawQuestion) {
  const answer = document.getElementById('askAnswer');
  const terms = tokenizeQuestion(rawQuestion);
  answer.innerHTML = '';
  if (!terms.length) {
    answer.textContent = '请输入要检索的知识点、操作步骤或学习问题。';
    return;
  }
  const results = state.askIndex
    .map((row) => ({ row, score: scoreAskRow(row, terms) }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score);
  const focusedResults = preferAskResults(results, terms).slice(0, 3);
  if (!focusedResults.length) {
    answer.textContent = '暂未在本教材中找到直接相关内容。可以换一个知识点、操作词或学习问题再问。';
    return;
  }
  const remoteAnswered = await answerWithRemoteService(rawQuestion, focusedResults, answer);
  if (!remoteAnswered) {
    renderLocalAskResults(focusedResults, terms, answer);
  }
}

function renderLocalAskResults(results, terms, answer) {
  for (const result of results) {
    answer.appendChild(renderAskResult(result.row, terms));
  }
}

async function answerWithRemoteService(question, results, answer) {
  const endpoint = (window.DIGITAL_BOOK_ASK_ENDPOINT || '').trim();
  if (!endpoint) return false;
  answer.textContent = 'AI ask-book service is generating...';
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildAskPayload(question, results)),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderRemoteAnswer(payload, answer);
    return true;
  } catch (error) {
    answer.innerHTML = '';
    const warning = el('div', 'ask-result');
    warning.appendChild(paragraph(`Remote ask-book service is unavailable. Falling back to local retrieval. ${error.message || error}`));
    answer.appendChild(warning);
    return false;
  }
}

function buildAskPayload(question, results) {
  return {
    schema: 'materials2textbook.ask_book.v1',
    book_id: state.book?.book_id || '',
    book_title: state.book?.title || '',
    question,
    sources: results.map((item) => ({
      block_id: item.row.id,
      project_title: item.row.projectTitle,
      task_title: item.row.taskTitle,
      block_title: item.row.blockTitle,
      text: item.row.text,
      evidence_chunk_ids: item.row.evidence || [],
      score: item.score,
    })),
  };
}

function renderRemoteAnswer(payload, answer) {
  answer.innerHTML = '';
  const wrap = el('div', 'ask-result');
  wrap.appendChild(paragraph(sanitizeStudentAnswer(payload.answer || payload.markdown || 'Remote service returned an empty answer.')));
  answer.appendChild(wrap);
}

function sanitizeStudentAnswer(value) {
  return String(value || '')
    .replace(/证据\\s*[：:]\\s*`?[A-Za-z]{1,5}[_-]?\\d{3,}[A-Za-z0-9_-]*`?/gi, '')
    .replace(/chunk_id\\s*[：:]\\s*`?[A-Za-z]{1,5}[_-]?\\d{3,}[A-Za-z0-9_-]*`?/gi, '')
    .replace(/`?[A-Za-z]{1,5}[_-]?\\d{3,}[A-Za-z0-9_-]*`?/g, '')
    .replace(/\\.(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)\\b/gi, '')
    .replace(/证据编号|来源：|Pending_|待人工|人工复核|时间码|PPT_/g, '')
    .replace(/\\s+/g, ' ')
    .trim() || '已找到相关教材内容，请结合对应知识点继续阅读。';
}

function renderAskResult(row, terms) {
  const wrap = el('div', 'ask-result');
  const title = document.createElement('strong');
  const link = document.createElement('a');
  link.href = `#${row.id}`;
  link.textContent = `${row.taskTitle} / ${row.blockTitle}`;
  link.addEventListener('click', () => setActiveSection(row.id));
  title.appendChild(link);
  wrap.appendChild(title);
  wrap.appendChild(paragraph(excerptForTerms(row.text, terms)));
  return wrap;
}

function tokenizeQuestion(value) {
  const terms = [];
  const pushTerm = (term) => {
    const cleaned = String(term || '').toLowerCase().trim();
    if (cleaned.length >= 2) terms.push(cleaned);
  };
  for (const token of String(value || '').split(/[^\\p{L}\\p{N}_-]+/u)) {
    pushTerm(token);
    const cjkRuns = token.match(/[\\u3400-\\u9fff]{2,}/g) || [];
    for (const run of cjkRuns) {
      for (const size of [2, 3, 4]) {
        for (let index = 0; index <= run.length - size; index += 1) {
          pushTerm(run.slice(index, index + size));
        }
      }
    }
  }
  return [...new Set(terms)];
}

function scoreAskRow(row, terms) {
  const text = row.text.toLowerCase();
  const title = `${row.taskTitle} ${row.blockTitle}`.toLowerCase();
  let score = 0;
  for (const term of terms) {
    if (text.includes(term)) score += term.length >= 5 ? 3 : 1;
    if (title.includes(term)) score += 4;
  }
  if (['learning_nav', 'assessment', 'exercises'].includes(row.blockType)) score -= 3;
  return score;
}

function preferAskResults(results, terms) {
  const primary = results.filter((item) => !['learning_nav', 'assessment', 'exercises'].includes(item.row.blockType));
  const candidates = primary.length ? primary : results;
  const focus = focusAskTerms(terms);
  if (!focus.length) return candidates;
  const focused = candidates.filter((item) => {
    const haystack = `${item.row.blockTitle} ${item.row.text}`.toLowerCase();
    return focus.some((term) => haystack.includes(term));
  });
  return focused.length ? focused : candidates;
}

function focusAskTerms(terms) {
  const generic = new Set(['操作', '注意', '什么', '怎么', '如何', '要点', '说明', '相关', '学习', '知识', '任务', '操作要', '作要', '要注', '注意什', '意什', '要注意', '注意什么']);
  return terms.filter((term) => term.length >= 2 && !generic.has(term));
}

function excerptForTerms(text, terms) {
  const normalized = text.replace(/\\s+/g, ' ').trim();
  const lower = normalized.toLowerCase();
  const index = terms.map((term) => lower.indexOf(term)).find((position) => position >= 0) ?? 0;
  const start = Math.max(0, index - 45);
  const excerpt = normalized.slice(start, start + 150);
  return `${start > 0 ? '...' : ''}${excerpt}${start + 150 < normalized.length ? '...' : ''}`;
}

function listBlock(title, items) {
  const block = el('div', 'block');
  block.appendChild(heading('h4', title));
  block.appendChild(list(items));
  return block;
}

function tagRow(label, items) {
  const wrap = el('div', 'tag-row');
  if (items.length) {
    const strong = document.createElement('strong');
    strong.textContent = `${label}：`;
    wrap.appendChild(strong);
  }
  for (const item of items) {
    const tag = el('span', 'tag');
    tag.textContent = item;
    wrap.appendChild(tag);
  }
  return wrap;
}

function list(items) {
  const ul = document.createElement('ul');
  for (const item of items) {
    const li = document.createElement('li');
    li.textContent = item;
    ul.appendChild(li);
  }
  return ul;
}

function paragraph(text) {
  const p = document.createElement('p');
  p.textContent = text || '';
  return p;
}

function heading(level, text) {
  const h = document.createElement(level);
  h.textContent = text;
  return h;
}

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function restoreReaderState() {
  const note = document.getElementById('readerNote');
  note.value = localStorage.getItem(`${state.storageKey}.note`) || '';
  renderBookmarks();
  const progress = Number(localStorage.getItem(`${state.storageKey}.progress`) || '0');
  updateProgress(progress);
  const lastSection = localStorage.getItem(`${state.storageKey}.activeId`);
  if (lastSection && document.getElementById(lastSection) && !location.hash) {
    document.getElementById(lastSection).scrollIntoView({ block: 'start' });
    setActiveSection(lastSection);
  }
}

function bindScrollTracking() {
  const sections = [...document.querySelectorAll('.project, .task')];
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
      const progress = maxScroll > 0 ? Math.round((window.scrollY / maxScroll) * 100) : 100;
      updateProgress(progress);
      localStorage.setItem(`${state.storageKey}.progress`, String(progress));
      const current = sections.findLast((section) => section.getBoundingClientRect().top <= 120) || sections[0];
      if (current) setActiveSection(current.id);
    });
  }, { passive: true });
}

function updateProgress(progress) {
  const normalized = Math.max(0, Math.min(100, progress));
  document.getElementById('progressLabel').textContent = `${normalized}%`;
  document.getElementById('progressBar').style.width = `${normalized}%`;
}

function setActiveSection(id) {
  state.activeId = id;
  localStorage.setItem(`${state.storageKey}.activeId`, id);
  for (const link of document.querySelectorAll('.toc a')) {
    link.classList.toggle('active', link.getAttribute('href') === `#${id}`);
  }
}

function getBookmarks() {
  try {
    return JSON.parse(localStorage.getItem(`${state.storageKey}.bookmarks`) || '[]');
  } catch (_error) {
    return [];
  }
}

function saveBookmarks(bookmarks) {
  localStorage.setItem(`${state.storageKey}.bookmarks`, JSON.stringify(bookmarks));
}

function renderBookmarks() {
  const root = document.getElementById('bookmarks');
  root.innerHTML = '';
  for (const bookmark of getBookmarks()) {
    const item = el('div', 'bookmark-item');
    const link = document.createElement('a');
    link.href = `#${bookmark.id}`;
    link.textContent = bookmark.text;
    link.addEventListener('click', () => setActiveSection(bookmark.id));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.title = '删除书签';
    remove.addEventListener('click', () => {
      saveBookmarks(getBookmarks().filter((entry) => entry.id !== bookmark.id));
      renderBookmarks();
    });
    item.appendChild(link);
    item.appendChild(remove);
    root.appendChild(item);
  }
}

function addBookmark() {
  const id = state.activeId || location.hash.replace('#', '') || document.querySelector('.task, .project')?.id;
  if (!id) return;
  const target = document.getElementById(id);
  const heading = target?.querySelector('h2, h3')?.textContent || id;
  const bookmarks = getBookmarks().filter((entry) => entry.id !== id);
  bookmarks.unshift({ id, text: heading });
  saveBookmarks(bookmarks.slice(0, 12));
  renderBookmarks();
}

function collectStudyData() {
  return {
    schema: 'materials2textbook.study_data.v1',
    exported_at: new Date().toISOString(),
    book_id: state.book?.book_id || '',
    book_title: state.book?.title || '',
    progress: Number(localStorage.getItem(`${state.storageKey}.progress`) || '0'),
    active_id: localStorage.getItem(`${state.storageKey}.activeId`) || state.activeId || '',
    font_size: state.fontSize,
    note: localStorage.getItem(`${state.storageKey}.note`) || '',
    bookmarks: getBookmarks(),
  };
}

function exportStudyData() {
  const payload = collectStudyData();
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const link = document.createElement('a');
  const date = new Date().toISOString().slice(0, 10).replaceAll('-', '');
  link.href = URL.createObjectURL(blob);
  link.download = `${payload.book_id || 'digital-book'}-study-data-${date}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  showSyncStatus('学习数据已导出。');
}

async function syncStudyDataToEndpoint() {
  const endpoint = (window.DIGITAL_BOOK_STUDY_ENDPOINT || '').trim();
  if (!endpoint) {
    showSyncStatus('No learning-data endpoint is configured. Export a JSON data package instead.');
    return;
  }
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectStudyData()),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    showSyncStatus('Learning data synced.');
  } catch (error) {
    showSyncStatus(`Learning data sync failed: ${error.message || error}`);
  }
}

function importStudyDataFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.addEventListener('load', () => {
    try {
      const payload = JSON.parse(String(reader.result || '{}'));
      applyStudyData(payload);
      showSyncStatus('学习数据已导入。');
    } catch (_error) {
      showSyncStatus('导入失败：文件不是有效的学习数据 JSON。');
    }
  });
  reader.readAsText(file, 'utf-8');
}

function applyStudyData(payload) {
  if (!payload || payload.schema !== 'materials2textbook.study_data.v1') {
    throw new Error('Unsupported study data schema');
  }
  const currentBookId = state.book?.book_id || '';
  if (payload.book_id && currentBookId && payload.book_id !== currentBookId) {
    throw new Error('Study data belongs to another book');
  }
  const progress = Math.max(0, Math.min(100, Number(payload.progress || 0)));
  localStorage.setItem(`${state.storageKey}.progress`, String(progress));
  if (typeof payload.active_id === 'string') {
    localStorage.setItem(`${state.storageKey}.activeId`, payload.active_id);
  }
  if (typeof payload.note === 'string') {
    localStorage.setItem(`${state.storageKey}.note`, payload.note);
  }
  if (Array.isArray(payload.bookmarks)) {
    saveBookmarks(payload.bookmarks.filter((entry) => entry && entry.id && entry.text).slice(0, 12));
  }
  if (payload.font_size) {
    state.fontSize = Math.max(14, Math.min(24, Number(payload.font_size)));
    localStorage.setItem('reader.fontSize', String(state.fontSize));
    document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  }
  restoreReaderState();
}

function showSyncStatus(message) {
  document.getElementById('syncStatus').textContent = message;
}

function timeToSeconds(value) {
  const parts = value.split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return Number(value) || 0;
}

document.getElementById('fontSmaller').addEventListener('click', () => {
  state.fontSize = Math.max(14, state.fontSize - 1);
  document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  localStorage.setItem('reader.fontSize', String(state.fontSize));
});

document.getElementById('fontLarger').addEventListener('click', () => {
  state.fontSize = Math.min(24, state.fontSize + 1);
  document.documentElement.style.setProperty('--reader-font-size', `${state.fontSize}px`);
  localStorage.setItem('reader.fontSize', String(state.fontSize));
});

document.getElementById('searchInput').addEventListener('input', (event) => {
  const term = event.target.value.trim().toLowerCase();
  for (const link of document.querySelectorAll('.toc a')) {
    link.style.display = !term || link.textContent.toLowerCase().includes(term) ? '' : 'none';
  }
});

document.getElementById('bookmarkCurrent').addEventListener('click', addBookmark);
document.getElementById('readerNote').addEventListener('input', (event) => {
  localStorage.setItem(`${state.storageKey}.note`, event.target.value);
});
document.getElementById('exportStudyData').addEventListener('click', exportStudyData);
document.getElementById('syncStudyData').addEventListener('click', syncStudyDataToEndpoint);
document.getElementById('importStudyData').addEventListener('click', () => {
  document.getElementById('studyDataFile').click();
});
document.getElementById('studyDataFile').addEventListener('change', (event) => {
  importStudyDataFile(event.target.files?.[0]);
  event.target.value = '';
});
document.getElementById('askButton').addEventListener('click', () => {
  answerQuestion(document.getElementById('askInput').value);
});
document.getElementById('askInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') answerQuestion(event.target.value);
});

window.studyDataApi = {
  collect: collectStudyData,
  apply: applyStudyData,
  sync: syncStudyDataToEndpoint,
};

loadBook().catch((error) => {
  document.getElementById('content').textContent = `电子教材加载失败：${error}`;
});
"""
