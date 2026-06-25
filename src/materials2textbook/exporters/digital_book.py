from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.domain_config import DomainConfig, default_domain_config
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.ability_graph import build_ability_graph_messages
from materials2textbook.prompts.book_sections import (
    build_general_preface_messages,
    build_preface_messages,
    build_project_intro_messages,
    build_project_summary_messages,
)
from materials2textbook.prompts.digital_book_polisher import build_digital_book_polisher_messages
from materials2textbook.schemas import (
    BookChapterPlan,
    BookPlan,
    BookSectionPlan,
    CaseExample,
    ChapterPlan,
    DigitalBook,
    DigitalBookBlock,
    DigitalBookProject,
    DigitalBookTask,
    EvidenceChunk,
    KnowledgePoint,
)
from materials2textbook.agents.exercise_designer import ExerciseDesignerAgent

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
    book_plan: BookPlan | None = None,
    domain_config: DomainConfig | None = None,
) -> DigitalBook:
    domain_config = domain_config or default_domain_config()
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    assets: dict[str, list[dict]] = {"videos": [], "keyframes": [], "images": []}
    projects: list[DigitalBookProject] = []

    project_titles: list[str] = []

    for project_index, plan in enumerate(plans, start=1):
        chapter_no = _book_chapter_no(book_plan, plan.chapter_id) or project_index
        project_titles.append(plan.title)

    general_preface = _generate_general_preface(
        llm_provider=llm_provider,
        use_llm=use_llm,
        book_title=title,
        project_titles=project_titles,
        chunks=chunks,
    )
    preface = _generate_preface(
        llm_provider=llm_provider,
        use_llm=use_llm,
        book_title=title,
        project_titles=project_titles,
    )

    for project_index, plan in enumerate(plans, start=1):
        chapter_no = _book_chapter_no(book_plan, plan.chapter_id) or project_index
        chapter_plan = _book_chapter_plan(book_plan, plan.chapter_id)
        tasks = _build_chapter_tasks(
            plan=plan,
            chapter_plan=chapter_plan,
            chapter_no=chapter_no,
            project_index=project_index,
            chunk_map=chunk_map,
            output_dir=output_dir,
            assets=assets,
            copy_media_assets=copy_media_assets,
            llm_provider=llm_provider,
            use_llm=use_llm,
            domain_config=domain_config,
        )
        project_title = plan.title
        project_chunks = [
            chunk_map[cid] for cid in plan.evidence_chunk_ids if cid in chunk_map
        ]
        task_titles = [task.title for task in tasks]
        knowledge_points: list[str] = []
        for task in tasks:
            knowledge_points.extend(task.knowledge_points)
        project_intro = _generate_project_intro(
            llm_provider=llm_provider,
            use_llm=use_llm,
            project_title=project_title,
            learning_goals=plan.learning_goals,
            task_titles=task_titles,
            chunks=project_chunks,
            fallback_title=plan.title,
        )
        project_summary = _generate_project_summary(
            llm_provider=llm_provider,
            use_llm=use_llm,
            project_title=project_title,
            learning_goals=plan.learning_goals,
            task_titles=task_titles,
            knowledge_points=knowledge_points,
        )
        projects.append(
            DigitalBookProject(
                project_id=plan.chapter_id,
                title=project_title,
                project_intro=project_intro,
                ability_map=[
                    "示范观察与要点提取",
                    "知识点理解与复述",
                    "操作过程分析与质量判断",
                ],
                learning_goals=plan.learning_goals,
                tasks=tasks,
                ability_graph=_build_ability_graph(
                    project_id=plan.chapter_id,
                    project_title=project_title,
                    learning_goals=plan.learning_goals,
                    ability_map=[
                        "示范观察与要点提取",
                        "知识点理解与复述",
                        "操作过程分析与质量判断",
                    ],
                    tasks=tasks,
                    llm_provider=llm_provider,
                    use_llm=use_llm,
                    domain_config=domain_config,
                ),
                project_summary=project_summary,
            )
        )

    references = _collect_references(chunks)

    return DigitalBook(
        book_id=_slugify(title),
        title=title,
        metadata={
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "format": "materials2textbook.digital_book.v1",
            "book_plan": _book_plan_metadata(book_plan),
            "domain_config": domain_config.to_dict(),
        },
        projects=projects,
        assets=assets,
        general_preface=general_preface,
        preface=preface,
        references=references,
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
    book_plan: BookPlan | None = None,
    domain_config: DomainConfig | None = None,
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
        book_plan=book_plan,
        domain_config=domain_config,
    )
    json_path = output_dir / "digital_book.json"
    index_path = output_dir / "index.html"
    write_json(json_path, book)
    asset_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    write_text(index_path, VIEWER_HTML.replace("__ASSET_VERSION__", asset_version))
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
    sections = _student_learning_sections(chunks, title=title)
    summary_text = _clean_student_text(summary)
    if summary_text and (
        _looks_like_internal_review_text(summary_text) or _looks_like_low_value_slide_text(summary_text)
    ):
        summary_text = ""
    return _render_textbook_style_markdown(title, summary_text, sections, chunks)


def _render_textbook_style_markdown(
    title: str,
    summary_text: str,
    sections: dict[str, list[str]],
    chunks: list[EvidenceChunk],
) -> str:
    paragraphs = [
        _compose_concept_paragraph(title, summary_text, sections.get("concept", [])),
        _compose_operation_paragraph(sections.get("steps", [])),
        _compose_quality_paragraph(sections.get("notes", []), sections.get("mistakes", [])),
    ]

    video_count = sum(1 for chunk in chunks if chunk.source_type in {"video_segment", "video", "audio_segment"})
    if video_count:
        video_hint = _compose_video_observation_paragraph(title, sections, chunks)
        if video_hint:
            paragraphs.append(video_hint)

    readable = [paragraph for paragraph in paragraphs if paragraph]
    if not readable:
        readable.append(f"本节围绕“{title}”展开学习，需要结合示范素材理解关键概念、操作要求和常见注意事项。")
    return "\n\n".join(readable).strip()


def _compose_concept_paragraph(title: str, summary_text: str, concept_items: list[str]) -> str:
    clauses = _paragraph_clauses([summary_text, *concept_items], limit=3)
    if clauses:
        body = "。".join(clauses)
        return _finish_sentence(f"本节围绕“{title}”展开学习，核心是理解{body}")
    return f"本节围绕“{title}”展开学习，需要先建立对相关概念、适用条件和学习重点的整体认识。"


def _compose_operation_paragraph(step_items: list[str]) -> str:
    steps = _paragraph_clauses(step_items, limit=4)
    if not steps:
        return ""
    if _looks_like_continuous_operation(steps):
        sequence = "；".join(f"{_chinese_ordinal(index)}，{item}" for index, item in enumerate(steps, start=1))
        return _finish_sentence(f"实际操作时，可以按连续动作把握：{sequence}")
    body = "；".join(steps)
    return _finish_sentence(f"操作观察的重点在于{body}")


def _compose_quality_paragraph(note_items: list[str], mistake_items: list[str]) -> str:
    notes = _paragraph_clauses(note_items, limit=3)
    mistakes = [_strip_problem_prefix(item) for item in _paragraph_clauses(mistake_items, limit=2)]
    pieces = []
    if notes:
        pieces.append("质量控制和安全操作的重点在于" + "；".join(notes))
    if mistakes:
        pieces.append("若控制不当，容易出现" + "；".join(mistakes))
    if not pieces:
        return ""
    return _finish_sentence("。".join(pieces))


def _strip_problem_prefix(text: str) -> str:
    return re.sub(r"^(?:如果|若)?控制不当[，,]?(?:容易|会)?出现", "", text).strip(" ：:-，。；;")


def _compose_video_observation_paragraph(
    title: str,
    sections: dict[str, list[str]],
    chunks: list[EvidenceChunk],
) -> str:
    if not _is_practice_title(title):
        return ""
    observations = _paragraph_clauses(sections.get("steps", []) + sections.get("notes", []), limit=2)
    if observations:
        return _finish_sentence(f"观看示范视频时，应把“{title}”的文字要点与画面对应起来，重点观察{'；'.join(observations)}")
    video_sentences: list[str] = []
    for chunk in chunks:
        if chunk.source_type not in {"video_segment", "video", "audio_segment"}:
            continue
        video_sentences.extend(_student_sentences(chunk.content))
    observations = _paragraph_clauses(video_sentences, limit=2)
    if observations:
        return _finish_sentence(f"观看示范视频时，应重点观察{'；'.join(observations)}，并记录动作变化与工件状态")
    return "学习时可结合下方示范视频，重点观察操作动作、工件状态变化和教师提示，将文字要点与现场画面对应起来。"


def _is_practice_title(title: str) -> bool:
    return any(term in title for term in ("操作", "送丝", "引弧", "收弧", "焊接过程"))


def _paragraph_clauses(items: list[str], *, limit: int) -> list[str]:
    clauses = []
    for item in items:
        cleaned = _clean_student_text(item, max_chars=140)
        if not cleaned or _contains_student_forbidden_trace(cleaned):
            continue
        cleaned = _compact_student_sentence(cleaned, max_chars=76)
        cleaned = cleaned.strip("。；;，, ")
        if cleaned and cleaned not in clauses:
            clauses.append(cleaned)
        if len(clauses) >= limit:
            break
    return clauses


def _looks_like_continuous_operation(steps: list[str]) -> bool:
    if len(steps) < 3:
        return False
    joined = " ".join(steps)
    if any(term in joined for term in ("先", "再", "然后", "随后", "最后", "第一", "第二", "第三")):
        return True
    action_terms = ("操作", "观察", "送入", "送丝", "引弧", "收弧", "填丝", "填加", "摆动", "移动", "拉回", "打磨", "调整")
    return sum(1 for step in steps if any(term in step for term in action_terms)) >= 3


def _chinese_ordinal(index: int) -> str:
    return {1: "第一", 2: "第二", 3: "第三", 4: "第四"}.get(index, f"第{index}")


def _finish_sentence(text: str) -> str:
    cleaned = text.strip(" ：:-，。；;")
    if not cleaned:
        return ""
    if cleaned[-1] in "。！？":
        return cleaned
    return cleaned + "。"


# ASR 同音字纠错词典：键为错误形式，值为正确焊接术语。
# 这些是焊接领域专业术语，只有一个正确写法，替换安全。
_WELDING_ASR_CORRECTIONS: dict[str, str] = {
    # 按长度降序排列，避免短键先匹配导致部分替换
    "颇厚质备": "坡口制备",
    "脚变形": "角变形",
    "直肌法": "直击法",
    "汉宫": "焊工",
    "汉公": "焊工",
    "汉奉": "焊缝",
    "汉戒": "焊接",
    "汉条": "焊条",
    "汉键": "焊件",
    "汉前": "焊钳",
    "汉箱": "焊枪",
    "西骨": "熄弧",
    "飞箭": "飞溅",
    "破口": "坡口",
}


def _fix_welding_terms(text: str) -> str:
    """对 ASR 同音字错误做安全术语替换。"""
    if not text:
        return text
    for wrong, correct in _WELDING_ASR_CORRECTIONS.items():
        if wrong in text:
            text = text.replace(wrong, correct)
    return text


def _student_learning_sections(chunks: list[EvidenceChunk], *, title: str = "") -> dict[str, list[str]]:
    sections = {"concept": [], "steps": [], "notes": [], "mistakes": []}
    for chunk in chunks:
        if (
            chunk.source_type in {"video_segment", "video", "audio_segment"}
            and "approved" not in chunk.review_status.lower()
            and not _is_practice_title(title)
        ):
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
    text = str(markdown or "")
    # Strip Qwen3 thinking blocks before line-by-line processing
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
    lines = []
    for raw_line in text.splitlines():
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
    if _looks_like_outline_markdown(markdown):
        return False
    return True


def _looks_like_outline_markdown(markdown: str) -> bool:
    old_section_labels = ("概念说明：", "操作步骤：", "注意事项：", "常见问题：")
    if any(label in markdown for label in old_section_labels):
        return True
    list_lines = [
        line
        for line in markdown.splitlines()
        if line.strip().startswith(("- ", "* ")) or re.match(r"^\s*\d+[.、]\s+", line)
    ]
    content_lines = [line for line in markdown.splitlines() if line.strip()]
    return len(list_lines) >= 3 and len(list_lines) >= max(2, len(content_lines) // 2)


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
    cleaned = _strip_student_trace_fragments(cleaned)
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
            part = _compact_student_sentence(part, max_chars=90)
        if len(part) < 8:
            continue
        sentences.append(part)
    return sentences


def _compact_student_sentence(text: str, *, max_chars: int) -> str:
    cleaned = str(text).strip(" ：:-，。；;")
    if len(cleaned) <= max_chars:
        return cleaned
    boundary = max(
        cleaned.rfind("。", 0, max_chars),
        cleaned.rfind("；", 0, max_chars),
        cleaned.rfind("，", 0, max_chars),
        cleaned.rfind(",", 0, max_chars),
    )
    if boundary >= 24:
        return cleaned[:boundary].strip(" ：:-，。；;")
    return cleaned[:max_chars].rstrip(" ：:-，。；;") + "…"


def _strip_student_trace_fragments(text: str) -> str:
    cleaned = str(text)
    cleaned = re.sub(r"(?:证据|来源|文件|路径|review_status|chunk_id)\s*[:：]\s*[^。；;\n]*[。；;]?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bPending_[A-Za-z0-9_-]*\b", "", cleaned)
    cleaned = re.sub(r"\bC\d{3,}\b", "", cleaned)
    return " ".join(cleaned.split()).strip(" ：:-，。；;")


def _student_section_bucket(sentence: str) -> str:
    if "烧损很少" in sentence:
        return "concept"
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
    normalized = _normalize_asr_terms(str(text))
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。；：、“”])", "", normalized)
    normalized = re.sub(r"(?<=[，、。；：])\s+(?=[\u4e00-\u9fffA-Za-z0-9])", "", normalized)
    normalized = re.sub(r"(?<=[“（(])\s+(?=[\u4e00-\u9fffA-Za-z0-9])", "", normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fffA-Za-z0-9])\s+(?=[”）)])", "", normalized)
    normalized = re.sub(r"[①②③④⑤⑥⑦⑧⑨⑩]", "", normalized)
    normalized = re.sub(r"\bT\s*1\s*G\b", "TIG", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("高文刚", "高温钢")
    normalized = re.sub(r"\[\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\s*-->\s*\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\]", "。", normalized)
    normalized = " ".join(normalized.split())
    normalized = re.sub(r"`?[A-Za-z]{1,5}[_-]?\d{3,}[A-Za-z0-9_-]*`?", "", normalized)
    normalized = re.sub(r"[\w\u4e00-\u9fff（）()、.-]+\s*\.\s*(?:mp4|flv|mp3|wav|pptx|ppt|jsonl|docx?)", "", normalized, flags=re.IGNORECASE)
    normalized = _strip_outline_prefix(normalized)
    normalized = normalized.strip(" ：:-，。")
    if len(normalized) > max_chars:
        normalized = normalized[: max_chars - 3].rstrip() + "..."
    return normalized


def _normalize_asr_terms(text: str) -> str:
    normalized = str(text)
    replacements = {
        "Tick": "TIG",
        "TIG漢": "TIG焊",
        "採用": "采用",
        "送司法": "送丝法",
        "第二回饭，": "",
        "第二回饭": "",
        "焊接带由": "焊机带有",
        "焊接没有": "焊机没有",
        "焊接電燃": "焊接电缆",
        "焊接电燃": "焊接电缆",
        "保护效果好，焊缝质量高氩气": "保护效果好，焊缝质量高。氩气",
        "漢阶": "焊接",
        "漢階": "焊接",
        "汉阶": "焊接",
        "汉階": "焊接",
        "汗階": "焊接",
        "看階": "焊接",
        "汗機": "焊接",
        "漢機": "焊接",
        "漢後": "焊后",
        "汉后": "焊后",
        "漢丝": "焊丝",
        "漢師": "焊丝",
        "漢师": "焊丝",
        "漢絲": "焊丝",
        "含思": "焊丝",
        "龙磁": "熔池",
        "龍磁": "熔池",
        "龍池": "熔池",
        "融持": "熔池",
        "融池": "熔池",
        "電湖": "电弧",
        "电湖": "电弧",
        "電火": "电弧",
        "电火": "电弧",
        "電骨": "电弧",
        "电骨": "电弧",
        "隱糊": "引弧",
        "隐糊": "引弧",
        "隱燃": "引燃",
        "隐燃": "引燃",
        "漢": "焊",
        "為": "为",
        "與": "与",
        "餘": "与",
        "這": "这",
        "來": "来",
        "過": "过",
        "會": "会",
        "時": "时",
        "後": "后",
        "應": "应",
        "長": "长",
        "開": "开",
        "準": "准",
        "確": "确",
        "種": "种",
        "質": "质",
        "態": "态",
        "處": "处",
        "觸": "触",
        "氣": "气",
        "電": "电",
        "壓": "压",
        "縮": "缩",
        "卻": "却",
        "收骨": "收弧",
        "骨坑": "弧坑",
        "弧坑练纹": "弧坑裂纹",
        "练纹": "裂纹",
        "确线": "缺陷",
        "電流衰竭": "电流衰减",
        "电流衰竭": "电流衰减",
        "框框制": "控制",
        "新疆熔池铁碼": "填满熔池铁水",
        "铁碼": "铁水",
        "緊小": "减小",
        "紧小": "减小",
        "細滅": "熄灭",
        "细灭": "熄灭",
        "乳急": "钨极",
        "估計": "钨极",
        "碰水": "喷嘴",
        "朵性氣勤": "惰性气体",
        "朵性氣": "惰性气体",
        "壓氣": "氩气",
        "亞氣": "氩气",
        "壓湖": "氩弧",
        "亞湖": "氩弧",
        "高頻": "高频",
        "長度為": "长度为",
        "電流為": "电流为",
        "處於": "处于",
        "狀態": "状态",
        "開關": "开关",
        "起伏": "起弧",
        "電燃": "电源",
        "电燃": "电缆",
        "式板": "试板",
        "漢腔": "焊枪",
        "焊腔": "焊枪",
        "空嘴": "喷嘴",
        "隔止": "搁置",
        "夾角": "夹角",
        "大母指": "拇指",
        "十指": "食指",
        "吴明指": "无名指",
        "送开": "松开",
        "一座": "夹住",
        "善用": "采用",
        "替个汉的": "",
        "替个汉": "",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = normalized.replace("，", "，").replace(",", "，")
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
        source_key = _video_source_key(chunk)
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        selected.append(chunk)
        if len(selected) >= MAX_VIDEO_BLOCKS_PER_KNOWLEDGE_POINT:
            return selected
    if not selected and video_chunks:
        return video_chunks[:1]
    return selected


def _video_source_key(chunk: EvidenceChunk) -> str:
    return str(chunk.locator.path or chunk.metadata.get("source_video", "") or chunk.asset_id or chunk.chunk_id)


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


def _build_chapter_tasks(
    *,
    plan: ChapterPlan,
    chapter_plan: BookChapterPlan | None,
    chapter_no: int,
    project_index: int,
    chunk_map: dict[str, EvidenceChunk],
    output_dir: Path,
    assets: dict[str, list[dict]],
    copy_media_assets: bool,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    domain_config: DomainConfig,
) -> list[DigitalBookTask]:
    section_groups = _section_groups_for_plan(plan, chapter_plan)
    tasks: list[DigitalBookTask] = []
    used_case_ids: set[str] = set()

    for task_index, (section, points) in enumerate(section_groups, start=1):
        task_title = _section_task_title(chapter_no, task_index, section, plan)
        task_evidence_ids = _valid_chunk_ids(_task_evidence_ids(section, points, plan), chunk_map) or _valid_chunk_ids(
            plan.evidence_chunk_ids,
            chunk_map,
        )
        task_blocks: list[DigitalBookBlock] = [
            DigitalBookBlock(
                block_id=f"p{project_index:02d}_t{task_index:02d}_scenario",
                type="scenario",
                title="情境导入",
                markdown=f"本节围绕“{_section_display_title(section, plan)}”展开学习，结合教材正文、示范视频和课堂任务理解关键知识。",
                evidence_chunk_ids=task_evidence_ids,
            )
        ]

        task_blocks.append(
            DigitalBookBlock(
                block_id=f"p{project_index:02d}_t{task_index:02d}_learning_nav",
                type="learning_nav",
                title="任务导学",
                items=_learning_nav_items(section, plan, points, task_evidence_ids, chunk_map),
                evidence_chunk_ids=task_evidence_ids,
            )
        )

        key_terms: list[str] = []
        used_video_sources: set[str] = set()
        for point_index, point in enumerate(points, start=1):
            point_title = _student_display_title(point.title)
            point_chunks = _chunks_for_point(point, task_evidence_ids, chunk_map)
            if not point_chunks:
                continue
            key_terms.extend(point_title for _chunk in point_chunks[:1])
            implementation_text, polish_metadata = _render_point_markdown(
                point_title,
                point.summary,
                point_chunks,
                llm_provider=llm_provider,
                use_llm=use_llm,
            )
            task_blocks.append(
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_t{task_index:02d}_kp{point_index:02d}_text",
                    type="implementation",
                    title=point_title,
                    markdown=implementation_text,
                    evidence_chunk_ids=[chunk.chunk_id for chunk in point_chunks],
                    metadata={
                        "teacher_evidence": _teacher_evidence_refs(point_chunks),
                        "source_title": point.title,
                        **polish_metadata,
                    },
                )
            )
            for media_index, chunk in enumerate(_select_video_chunks(point_chunks), start=1):
                video_source_key = _video_source_key(chunk)
                if video_source_key in used_video_sources:
                    continue
                media_block = _build_video_block(
                    chunk=chunk,
                    output_dir=output_dir,
                    block_id=f"p{project_index:02d}_t{task_index:02d}_kp{point_index:02d}_media{media_index:02d}",
                    assets=assets,
                    copy_media_assets=copy_media_assets,
                )
                if media_block:
                    used_video_sources.add(video_source_key)
                    task_blocks.append(media_block)

        section_cases = _cases_for_points(plan.case_examples, points, used_case_ids)
        for case_index, case in enumerate(section_cases, start=1):
            case_evidence_ids = _valid_chunk_ids(case.evidence_chunk_ids, chunk_map)
            if not case_evidence_ids:
                continue
            used_case_ids.add(case.case_id)
            task_blocks.append(
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_t{task_index:02d}_case{case_index:02d}",
                    type="case_example",
                    title=_case_display_title(case.title),
                    markdown=f"**例题**：{case.prompt}\n\n**参考分析**：{case.reference_answer}",
                    evidence_chunk_ids=case_evidence_ids,
                )
            )

        task_blocks.extend(
            [
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_t{task_index:02d}_assessment",
                    type="assessment",
                    title="学习评价",
                    items=_assessment_items_for_points(points),
                    evidence_chunk_ids=task_evidence_ids,
                ),
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_t{task_index:02d}_exercises",
                    type="exercises",
                    title="思考与练习",
                    items=_generate_exercise_items(
                        points=points,
                        chunk_map=chunk_map,
                        task_title=task_title,
                        llm_provider=llm_provider,
                        use_llm=use_llm,
                        domain_config=domain_config,
                    ),
                    evidence_chunk_ids=task_evidence_ids,
                ),
            ]
        )

        tasks.append(
            DigitalBookTask(
                task_id=f"{plan.chapter_id}_task_{task_index:02d}",
                title=task_title,
                blocks=task_blocks,
                knowledge_points=[_student_display_title(point.title) for point in points],
                key_terms=_dedupe(key_terms),
                evidence_chunk_ids=task_evidence_ids,
            )
        )

    unused_cases = [case for case in plan.case_examples if case.case_id not in used_case_ids]
    if unused_cases and tasks:
        for case_index, case in enumerate(unused_cases, start=1):
            case_evidence_ids = _valid_chunk_ids(case.evidence_chunk_ids, chunk_map)
            if not case_evidence_ids:
                continue
            tasks[-1].blocks.append(
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_t{len(tasks):02d}_extra_case{case_index:02d}",
                    type="case_example",
                    title=_case_display_title(case.title),
                    markdown=f"**例题**：{case.prompt}\n\n**参考分析**：{case.reference_answer}",
                    evidence_chunk_ids=case_evidence_ids,
                )
            )
    return tasks


def _generate_general_preface(
    *,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    book_title: str,
    project_titles: list[str],
    chunks: list[EvidenceChunk],
) -> str:
    if not use_llm or llm_provider is None:
        return ""
    try:
        material_summary = _summarize_chunks(chunks)
        raw = llm_provider.generate(
            build_general_preface_messages(
                book_title=book_title,
                project_titles=project_titles,
                material_summary=material_summary,
            )
        )
        text = _sanitize_section_text(raw)
        return text if text else ""
    except Exception:
        return ""


def _generate_preface(
    *,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    book_title: str,
    project_titles: list[str],
) -> str:
    if not use_llm or llm_provider is None:
        return ""
    try:
        raw = llm_provider.generate(
            build_preface_messages(
                book_title=book_title,
                project_titles=project_titles,
            )
        )
        return _sanitize_section_text(raw)
    except Exception:
        return ""


def _generate_project_intro(
    *,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    project_title: str,
    learning_goals: list[str],
    task_titles: list[str],
    chunks: list[EvidenceChunk],
    fallback_title: str = "",
) -> str:
    fallback = (
        f'本章围绕\u201c{fallback_title}\u201d展开学习，'
        '结合示范视频和学习要点理解相关知识与操作。'
    )
    if not use_llm or llm_provider is None:
        return fallback
    try:
        material_summary = _summarize_chunks(chunks)
        raw = llm_provider.generate(
            build_project_intro_messages(
                project_title=project_title,
                learning_goals=learning_goals,
                task_titles=task_titles,
                material_summary=material_summary,
            )
        )
        text = _sanitize_section_text(raw)
        return text if text else fallback
    except Exception:
        return fallback


def _generate_project_summary(
    *,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    project_title: str,
    learning_goals: list[str],
    task_titles: list[str],
    knowledge_points: list[str],
) -> str:
    if not use_llm or llm_provider is None:
        return ""
    try:
        raw = llm_provider.generate(
            build_project_summary_messages(
                project_title=project_title,
                learning_goals=learning_goals,
                task_titles=task_titles,
                knowledge_points=knowledge_points,
            )
        )
        return _sanitize_section_text(raw)
    except Exception:
        return ""


def _sanitize_section_text(raw: str) -> str:
    text = str(raw or "").strip()
    # Strip Qwen3 thinking blocks
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    return text.strip()


def _summarize_chunks(chunks: list[EvidenceChunk]) -> str:
    if not chunks:
        return ""
    subjects = sorted({chunk.subject for chunk in chunks if chunk.subject})
    titles = [chunk.title for chunk in chunks[:20] if chunk.title]
    parts: list[str] = []
    if subjects:
        parts.append("主题领域：" + "、".join(subjects[:5]))
    if titles:
        parts.append("素材示例：" + "；".join(titles[:10]))
    return "\n".join(parts)


def _collect_references(chunks: list[EvidenceChunk]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    references: list[dict[str, Any]] = []
    for chunk in chunks:
        key = chunk.title or chunk.locator.original_path or chunk.asset_id
        if not key or key in seen:
            continue
        seen.add(key)
        ref: dict[str, Any] = {
            "title": chunk.title,
            "type": chunk.source_type,
        }
        if chunk.subject:
            ref["subject"] = chunk.subject
        if chunk.material_block:
            ref["material_block"] = chunk.material_block
        if chunk.locator.original_path:
            ref["source"] = chunk.locator.original_path
        references.append(ref)
    return references


def _build_ability_graph(
    *,
    project_id: str,
    project_title: str,
    learning_goals: list[str],
    ability_map: list[str],
    tasks: list[DigitalBookTask],
    llm_provider: LLMProvider | None = None,
    use_llm: bool = False,
    domain_config: DomainConfig | None = None,
) -> dict:
    fallback = _build_rule_ability_graph(
        project_id=project_id,
        project_title=project_title,
        ability_map=ability_map,
        tasks=tasks,
    )
    if not use_llm or llm_provider is None:
        fallback["generation_method"] = "rule"
        return fallback
    try:
        raw_graph = llm_provider.generate(
            build_ability_graph_messages(
                project_title=project_title,
                learning_goals=learning_goals,
                tasks=_ability_graph_prompt_tasks(tasks),
                fallback_graph=fallback,
                domain_config=domain_config,
            )
        )
        graph = _normalize_llm_ability_graph(_parse_json_object(raw_graph), fallback)
        graph["generation_method"] = "llm"
        return graph
    except Exception:
        fallback["generation_method"] = "rule_fallback"
        return fallback


def _build_rule_ability_graph(
    *,
    project_id: str,
    project_title: str,
    ability_map: list[str],
    tasks: list[DigitalBookTask],
) -> dict:
    columns = [
        {"id": "project", "title": "项目"},
        {"id": "task", "title": "任务"},
        {"id": "ability", "title": "能力目标"},
        {"id": "knowledge", "title": "知识点"},
        {"id": "content", "title": "学习内容"},
    ]
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []

    project_node_id = f"{project_id}_project"
    nodes.append({"id": project_node_id, "column": "project", "label": project_title})

    for task_index, task in enumerate(tasks, start=1):
        task_node_id = f"{task.task_id}_task"
        nodes.append({"id": task_node_id, "column": "task", "label": task.title})
        edges.append({"from": project_node_id, "to": task_node_id})

        knowledge_labels = _ability_graph_knowledge_labels(task)
        content_nodes = _ability_graph_content_nodes(task)
        for point_index, point_title in enumerate(knowledge_labels, start=1):
            ability_node_id = f"{task.task_id}_ability_{point_index:02d}"
            point_node_id = f"{task.task_id}_knowledge_{point_index:02d}"
            content_node_id = f"{task.task_id}_content_{point_index:02d}"
            nodes.append({"id": ability_node_id, "column": "ability", "label": _ability_goal_label(point_title)})
            nodes.append({"id": point_node_id, "column": "knowledge", "label": point_title})
            nodes.append(
                {
                    "id": content_node_id,
                    "column": "content",
                    "label": _content_label_for_knowledge(point_title, content_nodes) or f"{point_title}学习要点",
                }
            )
            edges.append({"from": task_node_id, "to": ability_node_id})
            edges.append({"from": ability_node_id, "to": point_node_id})
            edges.append({"from": point_node_id, "to": content_node_id})

    return {
        "schema": "materials2textbook.ability_graph.v1",
        "columns": columns,
        "nodes": nodes,
        "edges": _dedupe_edges(edges),
    }


def _ability_graph_prompt_tasks(tasks: list[DigitalBookTask]) -> list[dict]:
    prompt_tasks = []
    for task in tasks:
        prompt_tasks.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "knowledge_points": _ability_graph_knowledge_labels(task),
                "contents": [node["label"] for node in _ability_graph_content_nodes(task)],
            }
        )
    return prompt_tasks


def _ability_goal_label(point_title: str) -> str:
    title = _student_display_title(point_title)
    if any(term in title for term in ("安全", "防护", "检查")):
        return f"能完成{title}判断"
    if any(term in title for term in ("操作", "焊", "送丝", "引弧", "收弧")):
        return f"能规范完成{title}"
    return f"能说明{title}"


def _parse_json_object(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM ability graph did not contain a JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM ability graph must be a JSON object")
    return data


def _normalize_llm_ability_graph(graph: dict, fallback: dict) -> dict:
    allowed_columns = [column["id"] for column in fallback["columns"]]
    fallback_columns = fallback["columns"]
    raw_nodes = graph.get("nodes", [])
    raw_edges = graph.get("edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise ValueError("LLM ability graph nodes and edges must be lists")

    nodes: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    column_counts: dict[str, int] = {column_id: 0 for column_id in allowed_columns}
    for index, raw_node in enumerate(raw_nodes, start=1):
        if not isinstance(raw_node, dict):
            continue
        column = str(raw_node.get("column", "")).strip()
        label = _student_display_title(str(raw_node.get("label", "")), fallback="")
        if column not in allowed_columns or not label or _contains_student_forbidden_trace(label):
            continue
        raw_id = str(raw_node.get("id", "")).strip()
        node_id = _safe_graph_id(raw_id or f"{column}_{index:02d}", column, index)
        while node_id in seen_ids:
            node_id = f"{node_id}_{index:02d}"
        seen_ids.add(node_id)
        column_counts[column] += 1
        nodes.append({"id": node_id, "column": column, "label": label})

    node_ids = {node["id"] for node in nodes}
    edges: list[dict[str, str]] = []
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, dict):
            continue
        source = str(raw_edge.get("from", "")).strip()
        target = str(raw_edge.get("to", "")).strip()
        if source in node_ids and target in node_ids and source != target:
            edges.append({"from": source, "to": target})

    if len(nodes) < 3 or not edges:
        raise ValueError("LLM ability graph is too sparse")
    missing_columns = [column_id for column_id in allowed_columns if column_counts.get(column_id, 0) == 0]
    if missing_columns:
        raise ValueError(f"LLM ability graph missing columns: {', '.join(missing_columns)}")
    _validate_ability_graph_tree(nodes, edges, allowed_columns)
    nodes = _dedupe_content_labels_vs_knowledge(nodes)
    return {
        "schema": "materials2textbook.ability_graph.v1",
        "columns": fallback_columns,
        "nodes": nodes,
        "edges": _dedupe_edges(edges),
    }


def _dedupe_content_labels_vs_knowledge(nodes: list[dict[str, str]]) -> list[dict[str, str]]:
    """Ensure content node labels differ from knowledge node labels."""
    knowledge_labels = {
        n["label"].strip() for n in nodes if n.get("column") == "knowledge"
    }
    for node in nodes:
        if node.get("column") == "content" and node["label"].strip() in knowledge_labels:
            node["label"] = f"{node['label'].strip()}（学习材料）"
    return nodes


def _safe_graph_id(value: str, column: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    if not cleaned:
        cleaned = f"{column}_{index:02d}"
    if not re.match(r"^[A-Za-z]", cleaned):
        cleaned = f"{column}_{cleaned}"
    return cleaned[:64]


def _validate_ability_graph_tree(
    nodes: list[dict[str, str]],
    edges: list[dict[str, str]],
    columns: list[str],
) -> None:
    column_order = {column: index for index, column in enumerate(columns)}
    node_columns = {node["id"]: node["column"] for node in nodes}
    parent_counts = {node["id"]: 0 for node in nodes}
    child_counts = {node["id"]: 0 for node in nodes}
    for edge in edges:
        source = edge["from"]
        target = edge["to"]
        if source not in node_columns or target not in node_columns:
            raise ValueError("Ability graph edge references a missing node")
        if column_order[node_columns[target]] != column_order[node_columns[source]] + 1:
            raise ValueError("Ability graph edges must connect adjacent columns")
        parent_counts[target] += 1
        child_counts[source] += 1

    for node in nodes:
        node_id = node["id"]
        column = node["column"]
        if column == columns[0]:
            if parent_counts[node_id] != 0:
                raise ValueError("Project nodes must not have parents")
        elif parent_counts[node_id] != 1:
            raise ValueError("Non-project ability graph nodes must have exactly one parent")
        if column != columns[-1] and child_counts[node_id] == 0:
            raise ValueError("Non-content ability graph nodes must have at least one child")


def _ability_graph_knowledge_labels(task: DigitalBookTask) -> list[str]:
    labels = task.knowledge_points or task.key_terms
    return _dedupe(labels)[:6] or [task.title]


def _ability_graph_content_nodes(task: DigitalBookTask) -> list[dict[str, list[str] | str]]:
    blocked_types = {"assessment", "exercises", "learning_nav", "scenario"}
    nodes: list[dict[str, list[str] | str]] = []
    for block in task.blocks:
        block_type = block.type or block.metadata.get("block_type", "")
        if block_type in blocked_types or not block.title:
            continue
        matched_titles = [
            point_title
            for point_title in _ability_graph_knowledge_labels(task)
            if _titles_overlap(point_title, block.title)
        ]
        nodes.append({"label": block.title, "knowledge_titles": matched_titles})
        if len(nodes) >= 6:
            break
    return nodes


def _content_label_for_knowledge(
    point_title: str,
    content_nodes: list[dict[str, list[str] | str]],
) -> str:
    for content in content_nodes:
        matched_titles = content.get("knowledge_titles", [])
        if isinstance(matched_titles, str):
            matched_titles = [matched_titles]
        if any(_match_key(point_title) == _match_key(title) for title in matched_titles):
            return str(content.get("label", ""))
    for content in content_nodes:
        label = str(content.get("label", ""))
        if _titles_overlap(point_title, label):
            return label
    return ""


def _matching_knowledge_node_ids(
    knowledge_node_ids: list[str],
    knowledge_titles: list[str],
    matched_titles: list[str] | str,
) -> list[str]:
    if isinstance(matched_titles, str):
        matched_titles = [matched_titles]
    matched_keys = {_match_key(title) for title in matched_titles}
    return [
        node_id
        for node_id, title in zip(knowledge_node_ids, knowledge_titles)
        if _match_key(title) in matched_keys
    ]


def _dedupe_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for edge in edges:
        key = (edge["from"], edge["to"])
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _section_groups_for_plan(
    plan: ChapterPlan,
    chapter_plan: BookChapterPlan | None,
) -> list[tuple[BookSectionPlan | None, list[KnowledgePoint]]]:
    if not chapter_plan or not chapter_plan.sections:
        return [(None, plan.knowledge_points)]

    remaining = list(plan.knowledge_points)
    groups: list[tuple[BookSectionPlan | None, list[KnowledgePoint]]] = []
    for section in chapter_plan.sections:
        points = _points_for_section(section, remaining)
        if not points:
            continue
        used_ids = {id(point) for point in points}
        remaining = [point for point in remaining if id(point) not in used_ids]
        if _is_redundant_project_section(section, plan):
            if groups:
                previous_section, previous_points = groups[-1]
                groups[-1] = (previous_section, previous_points + points)
            continue
        groups.append((section, points))
    if remaining:
        groups.append((None, remaining))
    return groups or [(None, plan.knowledge_points)]


def _points_for_section(section: BookSectionPlan, points: list[KnowledgePoint]) -> list[KnowledgePoint]:
    keys = {_match_key(value) for value in section.knowledge_point_ids + [section.title] if value}
    result = [point for point in points if _match_key(point.title) in keys]
    if result:
        return result
    return [
        point
        for point in points
        if any(key and (key in _match_key(point.title) or _match_key(point.title) in key) for key in keys)
    ]


def _is_redundant_project_section(section: BookSectionPlan, plan: ChapterPlan) -> bool:
    section_title = _strip_outline_prefix(section.title)
    plan_title = _strip_outline_prefix(plan.title)
    if not section_title or not plan_title:
        return False
    section_key = _match_key(section_title)
    plan_key = _match_key(plan_title)
    if not section_key or not plan_key:
        return False
    if section_key == plan_key:
        return True
    return section_key.startswith(_match_key("项目")) and _titles_overlap(section_title, plan_title)


def _task_evidence_ids(section: BookSectionPlan | None, points: list[KnowledgePoint], plan: ChapterPlan) -> list[str]:
    ids: list[str] = []
    if section:
        ids.extend(section.primary_material_ids)
    for point in points:
        ids.extend(point.chunk_ids)
    if section:
        ids.extend(section.reference_material_ids)
    return _dedupe(ids) or plan.evidence_chunk_ids


def _valid_chunk_ids(ids: list[str], chunk_map: dict[str, EvidenceChunk]) -> list[str]:
    return _dedupe([chunk_id for chunk_id in ids if chunk_id in chunk_map])


def _chunks_for_point(
    point: KnowledgePoint,
    task_evidence_ids: list[str],
    chunk_map: dict[str, EvidenceChunk],
) -> list[EvidenceChunk]:
    explicit_chunks = [chunk_map[chunk_id] for chunk_id in point.chunk_ids if chunk_id in chunk_map]
    if explicit_chunks:
        return explicit_chunks
    point_key = _match_key(_student_display_title(point.title))
    point_terms = {_match_key(term) for term in [point.title, *getattr(point, "keywords", [])] if term}
    point_terms = {term for term in point_terms if term}
    matched: list[EvidenceChunk] = []
    for chunk_id in task_evidence_ids:
        chunk = chunk_map.get(chunk_id)
        if not chunk:
            continue
        chunk_values = [
            chunk.title,
            chunk.summary,
            chunk.material_block,
            chunk.recommended_chapter,
            *chunk.keywords,
        ]
        chunk_keys = {_match_key(value) for value in chunk_values if value}
        chunk_keys = {key for key in chunk_keys if key}
        if any(
            point_key and chunk_key and (point_key in chunk_key or chunk_key in point_key)
            for chunk_key in chunk_keys
        ) or any(
            point_term and chunk_key and (point_term in chunk_key or chunk_key in point_term)
            for point_term in point_terms
            for chunk_key in chunk_keys
        ):
            matched.append(chunk)
    return matched


def _learning_nav_items(
    section: BookSectionPlan | None,
    plan: ChapterPlan,
    points: list[KnowledgePoint],
    task_evidence_ids: list[str],
    chunk_map: dict[str, EvidenceChunk],
) -> list[str]:
    title = _section_display_title(section, plan)
    point_titles = [_student_display_title(point.title) for point in points if point.title]
    video_count = sum(
        1
        for chunk_id in task_evidence_ids
        if chunk_id in chunk_map and chunk_map[chunk_id].source_type in {"video_segment", "video", "audio_segment"}
    )
    items = [
        f"先阅读“{title}”任务情境，明确本任务要解决的学习问题。",
        "重点掌握：" + "、".join(point_titles[:5]) if point_titles else "重点梳理本任务涉及的核心知识点。",
    ]
    if video_count:
        items.append("观看配套视频时，重点对应正文中的操作步骤、观察要点和质量要求。")
    else:
        items.append("阅读正文时，重点把概念、操作要求和常见问题对应起来。")
    items.append("完成任务后，用自己的话说明关键知识点，并完成思考与练习。")
    return items


def _cases_for_points(
    cases: list[CaseExample],
    points: list[KnowledgePoint],
    used_case_ids: set[str],
) -> list[CaseExample]:
    point_ids = {point.knowledge_point_id for point in points}
    point_titles = {_match_key(point.title) for point in points}
    result: list[CaseExample] = []
    for case in cases:
        if case.case_id in used_case_ids:
            continue
        target_ids = set(case.target_knowledge_point_ids)
        title_key = _match_key(case.title)
        if target_ids & point_ids or any(key and key in title_key for key in point_titles):
            result.append(case)
    return result


def _section_task_title(chapter_no: int, task_index: int, section: BookSectionPlan | None, plan: ChapterPlan) -> str:
    title = _section_display_title(section, plan)
    section_no = section.section_no if section and section.section_no else f"{chapter_no}.{task_index}"
    return f"{section_no} {title}"


def _section_display_title(section: BookSectionPlan | None, plan: ChapterPlan) -> str:
    if section and section.title:
        title = _student_display_title(section.title)
        if len(plan.knowledge_points) == 1 and not _titles_overlap(title, plan.title):
            return _student_display_title(plan.title)
        return title
    return _student_display_title(plan.title)


_STUDENT_TITLE_SUFFIX_PATTERNS = (
    r"(课堂)?应用示例$",
    r"案例分析$",
    r"视频片段$",
    r"示范视频$",
    r"学习内容$",
    r"知识梳理$",
    r"操作要点$",
    r"应用分析$",
    r"综合分析$",
    r"认知$",
)


def _student_display_title(title: str, *, fallback: str = "本任务") -> str:
    cleaned = _clean_student_text(_fix_welding_terms(_strip_outline_prefix(str(title or ""))), max_chars=48)
    cleaned = re.sub(r"\s+", "", cleaned)
    previous = ""
    while cleaned and cleaned != previous:
        previous = cleaned
        for pattern in _STUDENT_TITLE_SUFFIX_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned).strip(" ：:-，。；;")
    if not cleaned:
        cleaned = _clean_student_text(_fix_welding_terms(_strip_outline_prefix(str(fallback or ""))), max_chars=48)
    return cleaned.strip(" ：:-，。；;") or fallback


def _case_display_title(title: str) -> str:
    cleaned = _student_display_title(title, fallback="案例分析")
    if cleaned == "案例分析" or cleaned.startswith("案例"):
        return cleaned
    return f"案例分析：{cleaned}"


def _assessment_items_for_points(points: list[KnowledgePoint]) -> list[str]:
    if not points:
        return _assessment_items(ChapterPlan("", "", [], [], []))
    lead = _student_display_title(points[0].title)
    return [
        f"能说出“{lead}”相关的核心概念或关键操作。",
        "能结合教材资源说明关键动作、工件状态或质量要求。",
        "能用自己的话概括本节至少一个注意事项或判断依据。",
    ]


def _exercise_items_for_points(points: list[KnowledgePoint]) -> list[str]:
    return [
        f"结合示范视频和学习要点，说明“{_student_display_title(point.title)}”的关键内容。"
        for point in points[:5]
    ] or ["结合示范视频和学习要点，总结本节的关键学习收获。"]


def _generate_exercise_items(
    *,
    points: list[KnowledgePoint],
    chunk_map: dict[str, EvidenceChunk],
    task_title: str,
    llm_provider: LLMProvider | None,
    use_llm: bool,
    domain_config: DomainConfig | None = None,
) -> list[str]:
    """Generate real fill-blank/thinking exercises via LLM, else fall back to template."""
    if use_llm and llm_provider is not None:
        designer = ExerciseDesignerAgent(llm_provider=llm_provider, use_llm=True, domain_config=domain_config)
        items = designer.design_items(
            points=points,
            chunk_map=chunk_map,
            task_title=task_title,
        )
        if items:
            return items
    return _exercise_items_for_points(points)


def _match_key(value: str) -> str:
    return re.sub(r"[\s\-_/：:，,。、《》()（）.0-9]+", "", str(value or "").strip().lower())


def _titles_overlap(left: str, right: str) -> bool:
    left_key = _match_key(left)
    right_key = _match_key(right)
    if not left_key or not right_key:
        return False
    return left_key in right_key or right_key in left_key or any(term in left_key and term in right_key for term in ("焊接", "安全", "焊条", "钨极", "气焊"))


def _book_chapter_plan(book_plan: BookPlan | None, chapter_id: str) -> BookChapterPlan | None:
    if not book_plan:
        return None
    for chapter in book_plan.chapters:
        if chapter.chapter_id == chapter_id:
            return chapter
    return None


def _book_chapter_no(book_plan: BookPlan | None, chapter_id: str) -> int:
    if not book_plan:
        return 0
    for chapter in book_plan.chapters:
        if chapter.chapter_id == chapter_id:
            return chapter.chapter_no
    return 0


def _book_plan_metadata(book_plan: BookPlan | None) -> dict:
    if not book_plan:
        return {}
    result = {
        "book_id": book_plan.book_id,
        "title": book_plan.title,
        "planning_strategy": book_plan.planning_strategy,
        "chapters": [
            {
                "chapter_id": chapter.chapter_id,
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "sections": [
                    {
                        "section_id": section.section_id,
                        "section_no": section.section_no,
                        "title": section.title,
                        "knowledge_points": section.knowledge_point_ids,
                    }
                    for section in chapter.sections
                ],
            }
            for chapter in book_plan.chapters
        ],
    }
    result.update(dict(getattr(book_plan, "metadata", {}) or {}))
    return result


def _format_learning_path_item(point: KnowledgePoint, all_points: list[KnowledgePoint] | None = None) -> str:
    order_index = point.order_index if point.order_index > 0 else 1
    return f"{order_index}. {point.title}"


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
        f"结合示范视频和学习要点，说明“{_student_display_title(point.title)}”的关键内容。"
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
        title=_video_display_title(chunk),
        markdown=_render_video_observation_markdown(chunk),
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


def _render_video_observation_markdown(chunk: EvidenceChunk) -> str:
    title = _clean_video_observation_title(chunk.title)
    if title:
        return f"观看本视频时，重点把“{title}”与本任务正文对应起来，观察操作步骤、工件状态变化和安全要求。"
    return "观看本视频时，重点观察操作步骤、工件状态变化和安全要求，并与本任务正文要点对应起来。"


def _clean_video_observation_title(title: str) -> str:
    cleaned = _student_display_title(title, fallback="")
    cleaned = re.sub(r"\s*视频\s*$", "", cleaned)
    if _looks_like_low_quality_asr(cleaned) or _contains_student_forbidden_trace(cleaned):
        return ""
    return cleaned.strip(" ：:-，。")


def _video_display_title(chunk: EvidenceChunk) -> str:
    title = _clean_video_observation_title(chunk.title)
    if not title:
        title = _student_display_title(chunk.material_block or chunk.recommended_chapter or "", fallback="操作示范")
    return f"示范视频：{title}"


def _resolve_source_path(path_value: str, asset_id: str = "") -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    candidates = [path]
    if not path.is_absolute():
        work_root = Path(os.environ.get("DTEXTBOOKS_WORK", "local_runs/work_material1"))
        candidates.append(Path.cwd() / path)
        candidates.append(work_root / path)
        candidates.append(work_root / "02_working_processing" / "converted_mp4" / path.name)
        candidates.append(Path.cwd() / "work_materials" / "work_material1" / path)
        candidates.append(Path.cwd() / "work_materials" / "work_material1" / "02_working_processing" / "converted_mp4" / path.name)
        candidates.append(Path.cwd() / "work_material1" / path)
        candidates.append(Path.cwd() / "work_material1" / "02_working_processing" / "converted_mp4" / path.name)
        if asset_id:
            converted_dirs = [
                work_root / "02_working_processing" / "converted_mp4",
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
  <link rel="stylesheet" href="styles.css?v=__ASSET_VERSION__">
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
  <script src="ask_config.js?v=__ASSET_VERSION__"></script>
  <script src="app.js?v=__ASSET_VERSION__"></script>
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
.toc-chapter {
  margin-bottom: 4px;
}
.toc-chapter-toggle {
  width: 100%;
  min-height: 34px;
  border: 0;
  background: transparent;
  color: #2d3748;
  cursor: pointer;
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 4px;
  align-items: center;
  text-align: left;
  padding: 7px 8px;
  border-radius: 6px;
  font: inherit;
  line-height: 1.35;
}
.toc-chapter-toggle:hover { background: #edf2f7; }
.toc-chapter-toggle.active { background: #e6f0fb; color: #0f4f8a; font-weight: 700; }
.toc-chapter-icon {
  color: #667085;
  font-size: 12px;
}
.toc-section-list {
  margin: 2px 0 6px 18px;
}
.toc-chapter.collapsed .toc-section-list {
  display: none;
}
.toc-section-list a {
  font-size: 14px;
  padding-left: 8px;
}
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
.book-outline {
  background: #fff;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  margin-bottom: 18px;
  padding: 20px;
}
.book-outline > ol,
.book-outline > ol > li > ol {
  list-style: none;
  margin: 8px 0 0;
  padding-left: 16px;
}
.book-outline > ol > li > ol > li > ol {
  margin: 8px 0 0;
  padding-left: 22px;
}
.book-outline li { margin: 4px 0; }
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
.video-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 8px 0 0;
}
.video-toggle {
  min-height: 34px;
  border: 1px solid #cfd6df;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.video-status {
  color: #667085;
  font-size: 13px;
}
.markdown {
  margin: 0;
}
.markdown p { margin: 0 0 12px; }
.markdown p:last-child { margin-bottom: 0; }
.markdown strong { font-weight: 700; }
.markdown h1,
.markdown h2,
.markdown h3,
.markdown h4,
.markdown h5,
.markdown h6 {
  margin: 18px 0 10px;
  color: #101828;
  line-height: 1.35;
}
.markdown h1 { font-size: 22px; }
.markdown h2 { font-size: 20px; }
.markdown h3 { font-size: 18px; }
.markdown h4 { font-size: 16px; }
.markdown h5,
.markdown h6 { font-size: 15px; }
.markdown ul,
.markdown ol {
  margin: 0 0 14px 22px;
  padding: 0;
}
.markdown li { margin: 4px 0; }
.markdown blockquote {
  margin: 0 0 14px;
  padding: 10px 14px;
  border-left: 3px solid #2f80ed;
  background: #f6f9ff;
  color: #344054;
}
.markdown pre {
  margin: 0 0 14px;
  padding: 12px;
  overflow-x: auto;
  border-radius: 6px;
  background: #111827;
  color: #f9fafb;
}
.markdown code {
  padding: 2px 5px;
  border-radius: 4px;
  background: #eef2f7;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 0.92em;
}
.markdown pre code {
  padding: 0;
  background: transparent;
  color: inherit;
}
.markdown table {
  width: 100%;
  margin: 0 0 14px;
  border-collapse: collapse;
  font-size: 14px;
}
.markdown th,
.markdown td {
  padding: 8px 10px;
  border: 1px solid #d0d5dd;
  vertical-align: top;
}
.markdown th {
  background: #f2f4f7;
  font-weight: 700;
}
.markdown a {
  color: #1d4ed8;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.markdown img {
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  margin: 8px 0 14px;
}
.markdown hr {
  margin: 18px 0;
  border: 0;
  border-top: 1px solid #d0d5dd;
}

/* Gemini-inspired immersive reader theme. Kept as an override layer so the
   existing generated HTML and learning-state behavior stay compatible. */
:root {
  --bg-main: #f8fafc;
  --bg-sidebar: #ffffff;
  --bg-card: #ffffff;
  --bg-hover: #f1f5f9;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-muted: #94a3b8;
  --brand-color: #2563eb;
  --brand-color-hover: #1d4ed8;
  --brand-light: #eff6ff;
  --border-color: #e2e8f0;
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 8px;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.05);
  --shadow-md: 0 8px 20px rgba(15, 23, 42, 0.06);
  --shadow-lg: 0 14px 34px rgba(15, 23, 42, 0.12);
}

* {
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

body {
  grid-template-columns: 320px 1fr;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  color: var(--text-primary);
  background: var(--bg-main);
}

.sidebar {
  overflow-y: auto;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border-color);
  padding: 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.sidebar::-webkit-scrollbar { width: 5px; }
.sidebar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.brand {
  font-size: 20px;
  line-height: 1.25;
  font-weight: 700;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 0;
}
.brand::before {
  content: "";
  flex: 0 0 auto;
  width: 8px;
  height: 18px;
  border-radius: 2px;
  background: var(--brand-color);
}

.search {
  height: 40px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 0 14px;
  margin-bottom: 0;
  background: var(--bg-main);
  color: var(--text-primary);
  font-size: 14px;
  transition: border-color .2s ease, box-shadow .2s ease, background .2s ease;
}
.search:focus,
.note:focus,
.ask-input:focus {
  outline: none;
  border-color: var(--brand-color);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, .14);
}

.toc {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.toc a,
.toc-chapter-toggle {
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
  transition: background .15s ease, color .15s ease, padding-left .15s ease;
}
.toc a {
  padding: 8px 12px;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.toc a:hover,
.toc-chapter-toggle:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.toc a:hover { padding-left: 16px; }
.toc a.active,
.toc-chapter-toggle.active {
  background: var(--brand-light);
  color: var(--brand-color);
  font-weight: 700;
}
.toc-chapter {
  margin-bottom: 4px;
}
.toc-chapter-toggle {
  min-height: 38px;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 6px;
  padding: 8px 12px;
}
.toc-chapter-icon {
  color: var(--text-muted);
  font-size: 11px;
}
.toc-section-list {
  margin: 3px 0 8px 18px;
  border-left: 1px solid var(--border-color);
  padding-left: 6px;
}
.toc-section-list a {
  font-size: 13px;
}

.study-panel {
  margin-top: auto;
  border-top: 1px solid var(--border-color);
  padding-top: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.progress-row {
  color: var(--text-secondary);
  font-size: 13px;
}
.progress-row strong {
  color: var(--brand-color);
  font-weight: 700;
}
.progress-track {
  height: 6px;
  margin: -6px 0 0;
  background: var(--border-color);
}
.progress-bar {
  background: linear-gradient(90deg, var(--brand-color), #60a5fa);
}
.icon-action {
  width: auto;
  min-height: 34px;
  padding: 0 12px;
  border: 0;
  background: var(--brand-light);
  color: var(--brand-color);
  border-radius: var(--radius-sm);
  font-weight: 600;
}
.icon-action:hover {
  background: var(--brand-color);
  color: #fff;
}
.bookmarks {
  margin: 0;
  max-height: 90px;
  overflow-y: auto;
}
.bookmark-item a {
  color: var(--text-secondary);
}
.bookmark-item button,
.sync-actions button,
.tools button,
.video-toggle {
  border: 1px solid var(--border-color);
  background: #fff;
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
  transition: background .15s ease, color .15s ease, border-color .15s ease;
}
.bookmark-item button:hover,
.sync-actions button:hover,
.tools button:hover,
.video-toggle:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: #cbd5e1;
}
.note-label {
  display: block;
  margin-bottom: -8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
}
.note {
  border: 1px solid var(--border-color);
  background: var(--bg-main);
  color: var(--text-primary);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.5;
}
.sync-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.sync-actions button {
  height: 34px;
  font-size: 12px;
  font-weight: 600;
}
#syncStudyData {
  grid-column: span 2;
  background: var(--brand-color);
  border-color: var(--brand-color);
  color: #fff;
}
#syncStudyData:hover {
  background: var(--brand-color-hover);
}
.sync-status {
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}

.ask-box {
  display: flex;
  gap: 6px;
}
.ask-input {
  height: 38px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-main);
  color: var(--text-primary);
  font-size: 13px;
}
.ask-button {
  width: 44px;
  height: 38px;
  border: 0;
  border-radius: var(--radius-md);
  background: var(--text-primary);
  color: #fff;
  font-weight: 700;
}
.ask-button:hover {
  background: #1e293b;
}
.ask-answer {
  max-height: 220px;
  overflow-y: auto;
  border-radius: var(--radius-md);
  background: var(--bg-main);
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.55;
}
.ask-answer:not(:empty) {
  padding: 12px;
  border: 1px solid var(--border-color);
}
.ask-result {
  border-color: var(--border-color);
  border-radius: var(--radius-md);
  background: #fff;
}
.ask-result a {
  color: var(--brand-color);
}

.reader {
  min-width: 0;
  height: 100vh;
  overflow-y: auto;
  background: var(--bg-main);
}
.toolbar {
  position: sticky;
  top: 0;
  z-index: 10;
  padding: 16px 40px;
  background: rgba(255, 255, 255, .86);
  border-bottom: 1px solid var(--border-color);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.toolbar h1 {
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 700;
}
.toolbar p {
  color: var(--text-muted);
  font-size: 13px;
}
.tools {
  display: flex;
  gap: 6px;
}
.tools button {
  height: 32px;
  min-width: 40px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 600;
}

.content {
  width: 100%;
  max-width: 900px;
  margin: 0 auto;
  padding: 40px 24px 80px;
  color: #334155;
  font-size: var(--reader-font-size, 17px);
  line-height: var(--reader-line-height, 1.78);
}
.book-outline,
.project,
.task,
.block {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}
.book-outline,
.project,
.task,
.block {
  margin-bottom: 24px;
  padding: 28px;
}
.book-outline {
  border-top: 3px solid var(--brand-color);
}
.book-outline h2,
.project h2,
.task h3,
.block h4 {
  color: var(--text-primary);
  letter-spacing: 0;
}
.project h2 {
  padding-bottom: 10px;
  border-bottom: 2px solid var(--brand-light);
  color: var(--brand-color);
  font-size: 24px;
}
.task h3 {
  font-size: 20px;
}
.block h4 {
  color: var(--text-secondary);
  font-size: 16px;
}
.tag-row {
  gap: 6px;
  margin: 14px 0;
}
.tag {
  min-height: 26px;
  padding: 3px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-main);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}
.ability-map-panel {
  margin: 18px 0 8px;
}
.ability-map-panel h4 {
  margin: 0 0 12px;
}
.ability-map-graph {
  overflow-x: auto;
  padding: 8px 0 4px;
}
.ability-map-canvas {
  position: relative;
  min-width: 980px;
  padding: 10px 0;
}
.ability-map-grid {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: 130px 170px 210px 210px 230px;
  gap: 22px;
  align-items: center;
}
.ability-map-column {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
  min-height: 64px;
}
.ability-map-node {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 7px 10px;
  border: 1px solid #2274a5;
  border-radius: 0;
  background: #fff;
  color: var(--text-primary);
  font-size: 12px;
  line-height: 1.35;
  text-align: center;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.ability-map-node.project-node,
.ability-map-node.task-node {
  border-color: #344054;
}
.ability-map-node.content-node {
  border-color: #e4a83a;
}
.ability-map-svg {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  overflow: visible;
}
.ability-map-link {
  fill: none;
  stroke: #2274a5;
  stroke-width: 1.1;
}
.ability-map-link.content-link {
  stroke: #e4a83a;
}
.evidence {
  color: var(--text-muted);
}
.block[data-type="scenario"] {
  border-left: 4px solid #f59e0b;
}
.block[data-type="case_example"] {
  border-left: 4px solid #10b981;
}
.media-box {
  margin-top: 16px;
  overflow: hidden;
  border-radius: var(--radius-md);
  background: #000;
  box-shadow: var(--shadow-md);
}
video {
  margin-top: 0;
  max-height: 480px;
  border-radius: 0;
  background: #000;
}
.video-actions {
  margin-top: 8px;
}
.video-toggle {
  min-height: 34px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 600;
}
.video-status {
  color: var(--text-muted);
}
.markdown {
  color: #334155;
}
.markdown p {
  margin: 0 0 14px;
}
.markdown strong {
  color: var(--text-primary);
}
.toast {
  right: 24px;
  bottom: 24px;
  border-radius: var(--radius-md);
  background: var(--text-primary);
  box-shadow: var(--shadow-lg);
}

/* Compact collapsible table of contents. */
.toc {
  gap: 1px;
}
.toc-chapter {
  margin-bottom: 1px;
}
.toc-chapter-toggle {
  min-height: 28px;
  padding: 4px 8px;
  gap: 4px;
}
.toc-section-list {
  margin: 0 0 2px 12px;
  padding-left: 4px;
}
.toc-section-list a {
  padding: 3px 8px;
  font-size: 13px;
  line-height: 1.22;
}
.toc-section-link {
  display: flex !important;
  align-items: center;
  gap: 6px;
}
.block-marker {
  display: inline-block;
  width: 0;
  height: 0;
  flex: 0 0 auto;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 7px solid var(--brand-color);
}
.block-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}
.block-heading-marker {
  margin-top: 2px;
  filter: drop-shadow(0 1px 1px rgba(37, 99, 235, 0.2));
}
.block-heading-label {
  min-width: 0;
}
.toc-section-list a:hover {
  padding-left: 10px;
}
.toc a.task,
.toc-section-list a.task {
  display: block;
  margin: 0;
  padding: 3px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  box-shadow: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.22;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.toc a.task:hover,
.toc-section-list a.task:hover {
  padding-left: 10px;
  background: var(--bg-hover);
  color: var(--text-primary);
}
.toc a.task.active,
.toc-section-list a.task.active {
  background: var(--brand-light);
  color: var(--brand-color);
  font-weight: 700;
}
.section-anchor {
  display: block;
  height: 0;
  overflow: hidden;
  scroll-margin-top: 92px;
}
@media (max-width: 820px) {
  body { grid-template-columns: 1fr; }
  .sidebar {
    position: relative;
    height: auto;
    border-right: 0;
    border-bottom: 1px solid var(--border-color);
    padding: 18px 16px;
  }
  .study-panel { margin-top: 0; }
  .toolbar {
    position: relative;
    padding: 16px;
    align-items: flex-start;
  }
  .tools { flex-wrap: wrap; justify-content: flex-end; }
  .content {
    padding: 18px 14px 56px;
    font-size: 16px;
  }
  .ability-map-canvas {
    min-width: 900px;
  }
  .ability-map-grid {
    grid-template-columns: 120px 150px 190px 190px 210px;
    gap: 18px;
  }
  .book-outline,
  .project,
  .task,
  .block {
    padding: 18px;
    margin-bottom: 16px;
  }
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
  const version = new URLSearchParams(window.location.search).get('v') || Date.now().toString();
  const response = await fetch(`digital_book.json?v=${version}`);
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
  requestAnimationFrame(drawAbilityMapConnectors);
}

function renderToc(book) {
  const toc = document.getElementById('toc');
  toc.innerHTML = '';
  if (book.general_preface) {
    toc.appendChild(tocLink('总序', 'general_preface', 'front-matter'));
  }
  if (book.preface) {
    toc.appendChild(tocLink('前言', 'preface', 'front-matter'));
  }
  const plan = book.metadata?.book_plan;
  if (plan?.chapters?.length) {
    for (const [index, chapter] of plan.chapters.entries()) {
      toc.appendChild(tocChapter(chapter, index === 0));
    }
  } else {
    for (const project of book.projects || []) {
      toc.appendChild(tocLink(project.title, project.project_id, 'project'));
      for (const task of project.tasks || []) {
        toc.appendChild(tocLink(task.title, task.task_id, 'task'));
      }
    }
  }
  if (book.references && book.references.length) {
    toc.appendChild(tocLink('参考文献', 'references', 'back-matter'));
  }
}

function tocChapter(chapter, expanded) {
  const wrap = el('div', `toc-chapter${expanded ? '' : ' collapsed'}`);
  const button = el('button', 'toc-chapter-toggle');
  button.type = 'button';
  button.dataset.target = chapter.chapter_id;
  const icon = el('span', 'toc-chapter-icon');
  const label = el('span', '');
  label.textContent = displayChapterTitle(chapter);
  button.appendChild(icon);
  button.appendChild(label);
  const sectionList = el('div', 'toc-section-list');
  for (const section of chapter.sections || []) {
    sectionList.appendChild(tocLink(displaySectionTitle(section), section.section_id || chapter.chapter_id, 'task toc-section-link'));
  }
  const syncIcon = () => {
    icon.textContent = wrap.classList.contains('collapsed') ? '▶' : '▼';
  };
  button.addEventListener('click', () => {
    wrap.classList.toggle('collapsed');
    syncIcon();
    setActiveSection(chapter.chapter_id);
    document.getElementById(chapter.chapter_id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  syncIcon();
  wrap.appendChild(button);
  wrap.appendChild(sectionList);
  return wrap;
}

function displayChapterTitle(chapter) {
  return cleanTitle(chapter?.title);
}

function displaySectionTitle(section) {
  const title = cleanTitle(section?.title);
  const sectionNo = cleanTitle(section?.section_no);
  if (!title) return sectionNo;
  if (sectionNo && titleStartsWithSectionNo(title, sectionNo)) return title;
  return sectionNo ? `${sectionNo} ${title}` : title;
}

function titleStartsWithSectionNo(title, sectionNo) {
  const normalizedTitle = title.replace(/．/g, '.');
  const escaped = escapeRegExp(sectionNo.replace(/．/g, '.'));
  return new RegExp(`^${escaped}(?:\\s|[：:、.-]|$)`).test(normalizedTitle);
}

function cleanTitle(value) {
  return String(value || '').trim();
}

function escapeRegExp(value) {
  const specials = new Set(['\\\\', '.', '*', '+', '?', '^', '$', '{', '}', '(', ')', '|', '[', ']']);
  return [...String(value)].map((char) => specials.has(char) ? `\\\\${char}` : char).join('');
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
  if (book.general_preface) {
    root.appendChild(renderMarkdownSection('general_preface', 'h2', '总序', book.general_preface));
  }
  if (book.preface) {
    root.appendChild(renderMarkdownSection('preface', 'h2', '前言', book.preface));
  }
  const outline = renderBookOutline(book);
  if (outline) root.appendChild(outline);
  const bookChapters = book.metadata?.book_plan?.chapters || [];
  for (const project of book.projects || []) {
    const chapterPlan = bookChapters.find((chapter) => chapter.chapter_id === project.project_id);
    const anchoredSections = new Set();
    const section = el('section', 'project');
    section.id = project.project_id;
    section.appendChild(heading('h2', project.title));
    section.appendChild(renderMarkdownSection(null, 'h3', '项目导学', project.project_intro));
    section.appendChild(renderAbilityMap(project));
    section.appendChild(listBlock('学习目标', project.learning_goals || []));
    root.appendChild(section);

    for (const task of project.tasks || []) {
      const taskEl = el('section', 'task');
      taskEl.id = task.task_id;
      taskEl.appendChild(heading('h3', task.title));
      taskEl.appendChild(tagRow('知识点', task.knowledge_points || []));
      for (const block of task.blocks || []) {
        for (const sectionPlan of matchingChapterSections(chapterPlan, block, anchoredSections)) {
          taskEl.appendChild(sectionAnchor(sectionPlan.section_id));
          anchoredSections.add(sectionPlan.section_id);
        }
        taskEl.appendChild(renderBlock(block));
      }
      root.appendChild(taskEl);
    }

    if (project.project_summary) {
      root.appendChild(renderMarkdownSection(`${project.project_id}_summary`, 'h3', '项目小结', project.project_summary));
    }
  }
  if (book.references && book.references.length) {
    root.appendChild(renderReferencesSection(book.references));
  }
}

function renderMarkdownSection(id, level, title, markdown) {
  const section = el('section', 'markdown-section');
  if (id) section.id = id;
  section.appendChild(heading(level, title));
  const md = el('div', 'markdown');
  md.innerHTML = renderMarkdown(markdown || '');
  section.appendChild(md);
  return section;
}

function renderReferencesSection(references) {
  const section = el('section', 'references-section');
  section.id = 'references';
  section.appendChild(heading('h2', '参考文献'));
  const list = document.createElement('ol');
  for (const ref of references) {
    const item = document.createElement('li');
    const parts = [ref.title || ''];
    if (ref.subject) parts.push(ref.subject);
    if (ref.material_block) parts.push(ref.material_block);
    if (ref.type) parts.push(ref.type);
    item.textContent = parts.filter(Boolean).join('．');
    list.appendChild(item);
  }
  section.appendChild(list);
  return section;
}

function renderAbilityMap(project) {
  if (project.ability_graph?.nodes?.length) return renderGeneratedAbilityMap(project.ability_graph);
  return renderGeneratedAbilityMap(buildFallbackAbilityGraph(project));
  const panel = el('section', 'ability-map-panel');
  panel.appendChild(heading('h4', '能力图谱'));
  const graph = el('div', 'ability-map-graph');
  const canvas = el('div', 'ability-map-canvas');
  const svg = createSvg('svg', 'ability-map-svg');
  const grid = el('div', 'ability-map-grid');
  const columns = [0, 1, 2, 3, 4].map(() => el('div', 'ability-map-column'));
  const projectId = abilityNodeId('project', 0, 0);
  const taskIds = (project.tasks || []).map((_task, index) => abilityNodeId('task', index, 0));
  columns[0].appendChild(abilityNode(project.title || '项目', projectId, taskIds, 'project-node'));

  for (const [taskIndex, task] of (project.tasks || []).entries()) {
    const taskId = taskIds[taskIndex];
    const abilityIds = abilityTargetsForTask(project, taskIndex);
    columns[1].appendChild(abilityNode(task.title || `任务 ${taskIndex + 1}`, taskId, abilityIds, 'task-node'));

    for (const [abilityIndex, ability] of abilityLabels(project).entries()) {
      const abilityId = abilityNodeId('ability', taskIndex, abilityIndex);
      const knowledgeIds = knowledgeTargetsForTask(task, taskIndex);
      columns[2].appendChild(abilityNode(ability, abilityId, knowledgeIds, 'ability-node'));
    }

    const contentIds = contentTargetsForTask(task, taskIndex);
    for (const [pointIndex, point] of knowledgeLabels(task).entries()) {
      const pointId = abilityNodeId('knowledge', taskIndex, pointIndex);
      columns[3].appendChild(abilityNode(point, pointId, contentIds, 'knowledge-node'));
    }

    for (const [contentIndex, content] of contentLabels(task).entries()) {
      const contentId = abilityNodeId('content', taskIndex, contentIndex);
      columns[4].appendChild(abilityNode(content, contentId, [], 'content-node'));
    }
  }

  for (const column of columns) grid.appendChild(column);
  canvas.appendChild(svg);
  canvas.appendChild(grid);
  applyAbilityMapDensity(canvas);
  graph.appendChild(canvas);
  panel.appendChild(graph);
  return panel;
}

function renderGeneratedAbilityMap(graphData) {
  const panel = el('section', 'ability-map-panel');
  panel.appendChild(heading('h4', '能力图谱'));
  const graph = el('div', 'ability-map-graph');
  const canvas = el('div', 'ability-map-canvas');
  const svg = createSvg('svg', 'ability-map-svg');
  const grid = el('div', 'ability-map-grid');
  const columnIds = graphData.columns?.map((column) => column.id) || ['project', 'task', 'ability', 'knowledge', 'content'];
  const edgeTargets = new Map();
  for (const edge of graphData.edges || []) {
    if (!edge?.from || !edge?.to) continue;
    if (!edgeTargets.has(edge.from)) edgeTargets.set(edge.from, []);
    edgeTargets.get(edge.from).push(edge.to);
  }
  for (const columnId of columnIds) {
    const column = el('div', 'ability-map-column');
    const nodes = (graphData.nodes || []).filter((node) => node.column === columnId);
    for (const node of nodes) {
      column.appendChild(abilityNode(node.label || '', node.id, edgeTargets.get(node.id) || [], abilityNodeClass(columnId)));
    }
    grid.appendChild(column);
  }
  canvas.appendChild(svg);
  canvas.appendChild(grid);
  applyAbilityMapDensity(canvas);
  graph.appendChild(canvas);
  panel.appendChild(graph);
  return panel;
}

function applyAbilityMapDensity(canvas) {
  const nodeCount = canvas.querySelectorAll('.ability-map-node').length;
  canvas.classList.toggle('dense', nodeCount > 22);
  canvas.classList.toggle('ultra-dense', nodeCount > 38);
}

function buildFallbackAbilityGraph(project) {
  const columns = [
    { id: 'project', title: '项目' },
    { id: 'task', title: '任务' },
    { id: 'ability', title: '能力目标' },
    { id: 'knowledge', title: '知识点' },
    { id: 'content', title: '学习内容' },
  ];
  const nodes = [];
  const edges = [];
  const projectId = abilityNodeId('project', 0, 0);
  nodes.push({ id: projectId, column: 'project', label: project.title || '项目' });
  for (const [taskIndex, task] of (project.tasks || []).entries()) {
    const taskId = abilityNodeId('task', taskIndex, 0);
    nodes.push({ id: taskId, column: 'task', label: task.title || `任务 ${taskIndex + 1}` });
    edges.push({ from: projectId, to: taskId });
    const knowledge = knowledgeLabels(task);
    for (const [pointIndex, point] of knowledge.entries()) {
      const abilityId = abilityNodeId('ability', taskIndex, pointIndex);
      const pointId = abilityNodeId('knowledge', taskIndex, pointIndex);
      const contentId = abilityNodeId('content', taskIndex, pointIndex);
      nodes.push({ id: abilityId, column: 'ability', label: fallbackAbilityLabel(point) });
      nodes.push({ id: pointId, column: 'knowledge', label: point });
      nodes.push({ id: contentId, column: 'content', label: fallbackContentLabel(task, point) });
      edges.push({ from: taskId, to: abilityId });
      edges.push({ from: abilityId, to: pointId });
      edges.push({ from: pointId, to: contentId });
    }
  }
  return { schema: 'materials2textbook.ability_graph.v1', columns, nodes, edges, generation_method: 'viewer_fallback' };
}

function fallbackAbilityLabel(point) {
  if (/安全|防护|检查/.test(point)) return `能完成${point}判断`;
  if (/操作|焊|送丝|引弧|收弧/.test(point)) return `能规范完成${point}`;
  return `能说明${point}`;
}

function fallbackContentLabel(task, point) {
  for (const block of task.blocks || []) {
    if (!block?.title || ['assessment', 'exercises', 'learning_nav', 'scenario'].includes(block.type || block.block_type || '')) continue;
    if (block.title.includes(point) || point.includes(block.title)) return block.title;
  }
  return `${point}学习要点`;
}

function abilityNodeClass(columnId) {
  if (columnId === 'project') return 'project-node';
  if (columnId === 'task') return 'task-node';
  if (columnId === 'content') return 'content-node';
  if (columnId === 'knowledge') return 'knowledge-node';
  return 'ability-node';
}

function abilityLabels(project) {
  const labels = project.ability_map?.length ? project.ability_map : ['知识理解', '示范观察', '操作分析'];
  return labels.slice(0, 4);
}

function knowledgeLabels(task) {
  const labels = task.knowledge_points?.length ? task.knowledge_points : task.key_terms || [];
  return labels.length ? labels.slice(0, 6) : [task.title || '学习要点'];
}

function contentLabels(task) {
  const blockedTypes = new Set(['assessment', 'exercises', 'learning_nav', 'scenario']);
  const labels = [];
  for (const block of task.blocks || []) {
    if (!block?.title || blockedTypes.has(block.type || block.block_type || '')) continue;
    if (!labels.includes(block.title)) labels.push(block.title);
    if (labels.length >= 6) break;
  }
  return labels.length ? labels : ['教材正文与课堂活动'];
}

function abilityTargetsForTask(project, taskIndex) {
  return abilityLabels(project).map((_label, abilityIndex) => abilityNodeId('ability', taskIndex, abilityIndex));
}

function knowledgeTargetsForTask(task, taskIndex) {
  return knowledgeLabels(task).map((_label, pointIndex) => abilityNodeId('knowledge', taskIndex, pointIndex));
}

function contentTargetsForTask(task, taskIndex) {
  return contentLabels(task).map((_label, contentIndex) => abilityNodeId('content', taskIndex, contentIndex));
}

function abilityNode(label, id, targets, className) {
  const node = el('div', `ability-map-node ${className || ''}`.trim());
  node.dataset.nodeId = id;
  node.dataset.connectTo = targets.join(',');
  node.textContent = label;
  return node;
}

function abilityNodeId(kind, first, second) {
  return `${kind}_${first}_${second}`;
}

function drawAbilityMapConnectors() {
  for (const canvas of document.querySelectorAll('.ability-map-canvas')) {
    const svg = canvas.querySelector('.ability-map-svg');
    if (!svg) continue;
    svg.innerHTML = '';
    const rect = canvas.getBoundingClientRect();
    svg.setAttribute('width', String(canvas.scrollWidth));
    svg.setAttribute('height', String(canvas.scrollHeight));
    svg.setAttribute('viewBox', `0 0 ${canvas.scrollWidth} ${canvas.scrollHeight}`);
    for (const source of canvas.querySelectorAll('.ability-map-node[data-connect-to]')) {
      const targets = source.dataset.connectTo.split(',').filter(Boolean);
      for (const targetId of targets) {
        const target = canvas.querySelector(`[data-node-id="${targetId}"]`);
        if (!target) continue;
        svg.appendChild(abilityConnectorPath(source, target, rect));
      }
    }
  }
}

function abilityConnectorPath(source, target, rootRect) {
  const sourceRect = source.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  const x1 = sourceRect.right - rootRect.left;
  const y1 = sourceRect.top + sourceRect.height / 2 - rootRect.top;
  const x2 = targetRect.left - rootRect.left;
  const y2 = targetRect.top + targetRect.height / 2 - rootRect.top;
  const gap = Math.max(1, x2 - x1);
  const control = Math.max(6, Math.min(42, gap * 0.5));
  const path = createSvg('path', `ability-map-link ${target.classList.contains('content-node') ? 'content-link' : ''}`);
  path.setAttribute('d', `M ${x1} ${y1} C ${x1 + control} ${y1}, ${x2 - control} ${y2}, ${x2} ${y2}`);
  return path;
}

function createSvg(tag, className) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  if (className) node.setAttribute('class', className);
  return node;
}

function renderBlock(block) {
  const node = el('article', `block block-${block.type}`);
  node.id = block.block_id;
  node.appendChild(blockHeading(block.title));
  if (block.type === 'video') {
    const video = document.createElement('video');
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    const startSec = timeToSeconds(block.start_time || '');
    const endSec = timeToSeconds(block.end_time || '');
    if (endSec > startSec) {
      video.src = block.src + '#t=' + startSec + ',' + endSec;
    } else {
      video.src = block.src;
    }
    if (block.poster) video.poster = block.poster;
    if (block.start_time) video.dataset.startTime = block.start_time;
    if (block.end_time) video.dataset.endTime = block.end_time;
    node.appendChild(video);
    node.appendChild(videoActions(video));
    node.appendChild(paragraph(`${block.start_time || ''} - ${block.end_time || ''}`));
  } else if (block.items && block.items.length) {
    node.appendChild(list(block.items));
  }
  if (block.markdown) {
    const markdown = el('div', 'markdown');
    markdown.innerHTML = renderMarkdown(block.markdown);
    node.appendChild(markdown);
  }
  return node;
}

function blockHeading(text) {
  const title = heading('h4', '');
  title.className = 'block-heading';
  const marker = el('span', 'block-marker block-heading-marker');
  marker.setAttribute('aria-hidden', 'true');
  const label = el('span', 'block-heading-label');
  label.textContent = text;
  title.appendChild(marker);
  title.appendChild(label);
  return title;
}

function videoActions(video) {
  const wrap = el('div', 'video-actions');
  const toggle = el('button', 'video-toggle');
  const status = el('span', 'video-status');
  toggle.type = 'button';
  const syncLabel = () => {
    toggle.textContent = video.paused ? '播放' : '暂停';
    if (!video.paused) status.textContent = '';
  };
  toggle.addEventListener('click', async () => {
    if (video.paused) {
      try {
        status.textContent = '';
        const startSeconds = timeToSeconds(video.dataset.startTime || '');
        const endSeconds = timeToSeconds(video.dataset.endTime || '');
        if (startSeconds > 0 && (video.currentTime < startSeconds || (endSeconds > 0 && video.currentTime >= endSeconds))) {
          video.currentTime = startSeconds;
        }
        await video.play();
      } catch (error) {
        status.textContent = '浏览器阻止了自动播放，请直接点击视频控件播放。';
      }
    } else {
      video.pause();
    }
    syncLabel();
  });
  video.addEventListener('play', syncLabel);
  video.addEventListener('pause', syncLabel);
  video.addEventListener('ended', syncLabel);
  syncLabel();
  wrap.appendChild(toggle);
  wrap.appendChild(status);
  return wrap;
}

function renderMarkdown(value) {
  const lines = String(value || '').replace(/\\r\\n?/g, '\\n').split('\\n');
  const html = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```\\s*([\\w-]+)?\\s*$/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```\\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const language = fence[1] ? ` language-${escapeHtml(fence[1])}` : '';
      html.push(`<pre><code class="${language.trim()}">${escapeHtml(codeLines.join('\\n'))}</code></pre>`);
      continue;
    }

    if (/^\\s*([-*_])(?:\\s*\\1){2,}\\s*$/.test(line)) {
      html.push('<hr>');
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^>\\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\\s?/, ''));
        index += 1;
      }
      html.push(`<blockquote>${renderMarkdown(quoteLines.join('\\n'))}</blockquote>`);
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines = [lines[index], lines[index + 1]];
      index += 2;
      while (index < lines.length && isTableRow(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      html.push(renderMarkdownTable(tableLines));
      continue;
    }

    const listMatch = line.match(/^(\\s*)([-*+] |\\d+[.)]\\s+)(.+)$/);
    if (listMatch) {
      const ordered = /\\d/.test(listMatch[2][0]);
      const tag = ordered ? 'ol' : 'ul';
      const items = [];
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^(\\s*)([-*+] |\\d+[.)]\\s+)(.+)$/);
        if (!itemMatch || (/\\d/.test(itemMatch[2][0]) !== ordered)) break;
        const itemLines = [itemMatch[3]];
        index += 1;
        while (index < lines.length && lines[index].trim() && !/^(\\s*)([-*+] |\\d+[.)]\\s+)/.test(lines[index])) {
          itemLines.push(lines[index].trim());
          index += 1;
        }
        items.push(`<li>${renderInlineMarkdown(itemLines.join('\\n')).replace(/\\n/g, '<br>')}</li>`);
      }
      html.push(`<${tag}>${items.join('')}</${tag}>`);
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^```/.test(lines[index]) &&
      !/^(#{1,6})\\s+/.test(lines[index]) &&
      !/^>\\s?/.test(lines[index]) &&
      !/^\\s*([-*_])(?:\\s*\\1){2,}\\s*$/.test(lines[index]) &&
      !/^(\\s*)([-*+] |\\d+[.)]\\s+)/.test(lines[index]) &&
      !isTableStart(lines, index)
    ) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    html.push(`<p>${renderInlineMarkdown(paragraphLines.join('\\n')).replace(/\\n/g, '<br>')}</p>`);
  }
  return html.join('');
}

function renderInlineMarkdown(value) {
  const codeSpans = [];
  let text = escapeHtml(value).replace(/`([^`]+)`/g, (_match, code) => {
    const token = `%%CODE${codeSpans.length}%%`;
    codeSpans.push(`<code>${code}</code>`);
    return token;
  });
  text = text
    .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/(^|[^*])\\*([^*\\n]+)\\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\\n]+)_/g, '$1<em>$2</em>')
    .replace(/!\\[([^\\]]*)\\]\\(([^\\s)]+)(?:\\s+&quot;[^&]*&quot;)?\\)/g, (_match, alt, rawUrl) => {
      const url = safeMarkdownUrl(rawUrl);
      return url ? `<img src="${url}" alt="${escapeHtml(alt)}">` : escapeHtml(alt);
    })
    .replace(/\\[([^\\]]+)\\]\\(([^\\s)]+)(?:\\s+&quot;[^&]*&quot;)?\\)/g, (_match, label, rawUrl) => {
      const url = safeMarkdownUrl(rawUrl);
      return url ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
    });
  return text.replace(/%%CODE(\\d+)%%/g, (_match, index) => codeSpans[Number(index)] || '');
}

function isTableStart(lines, index) {
  return isTableRow(lines[index]) && index + 1 < lines.length && /^\\s*\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)+\\|?\\s*$/.test(lines[index + 1] || '');
}

function isTableRow(line) {
  return /^\\s*\\|.*\\|\\s*$/.test(line || '');
}

function renderMarkdownTable(lines) {
  const headers = splitTableRow(lines[0]);
  const aligns = splitTableRow(lines[1]).map((cell) => {
    const value = cell.trim();
    if (value.startsWith(':') && value.endsWith(':')) return 'center';
    if (value.endsWith(':')) return 'right';
    return 'left';
  });
  const bodyRows = lines.slice(2).map(splitTableRow);
  const ths = headers.map((cell, index) => `<th style="text-align:${aligns[index] || 'left'}">${renderInlineMarkdown(cell.trim())}</th>`).join('');
  const rows = bodyRows.map((row) => {
    const tds = headers.map((_header, index) => `<td style="text-align:${aligns[index] || 'left'}">${renderInlineMarkdown((row[index] || '').trim())}</td>`).join('');
    return `<tr>${tds}</tr>`;
  }).join('');
  return `<table><thead><tr>${ths}</tr></thead><tbody>${rows}</tbody></table>`;
}

function splitTableRow(line) {
  return String(line || '').trim().replace(/^\\|/, '').replace(/\\|$/, '').split('|');
}

function safeMarkdownUrl(rawUrl) {
  const decoded = String(rawUrl || '').replace(/&amp;/g, '&').replace(/&quot;/g, '"').trim();
  if (!decoded || /^[a-z][a-z0-9+.-]*:/i.test(decoded) && !/^(https?:|mailto:)/i.test(decoded)) return '';
  return escapeHtml(decoded);
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildAskIndex(book) {
  const rows = [];
  if (book.general_preface) {
    rows.push({
      id: 'general_preface',
      blockType: 'preface',
      projectTitle: book.title || '',
      taskTitle: '总序',
      blockTitle: '总序',
      text: String(book.general_preface).replace(/\\s+/g, ' ').trim(),
      evidence: [],
    });
  }
  if (book.preface) {
    rows.push({
      id: 'preface',
      blockType: 'preface',
      projectTitle: book.title || '',
      taskTitle: '前言',
      blockTitle: '前言',
      text: String(book.preface).replace(/\\s+/g, ' ').trim(),
      evidence: [],
    });
  }
  for (const project of book.projects || []) {
    if (project.project_summary) {
      rows.push({
        id: `${project.project_id}_summary`,
        blockType: 'summary',
        projectTitle: project.title || '',
        taskTitle: '项目小结',
        blockTitle: '项目小结',
        text: String(project.project_summary).replace(/\\s+/g, ' ').trim(),
        evidence: [],
      });
    }
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

function matchingChapterSections(chapterPlan, block, anchoredSections) {
  if (!chapterPlan?.sections?.length || !block) return [];
  const title = normalizeText(block.title || '');
  const blockId = normalizeText(block.block_id || '');
  if (!title && !blockId) return [];
  const result = [];
  for (const section of chapterPlan.sections) {
    if (!section.section_id || anchoredSections.has(section.section_id)) continue;
    const candidates = [section.title, ...(section.knowledge_points || [])].map(normalizeText).filter(Boolean);
    if (candidates.some((item) => title.includes(item) || item.includes(title) || blockId.includes(item))) {
      result.push(section);
    }
  }
  return result;
}

function normalizeText(value) {
  return String(value || '').replace(/[\\s\\-_/：:，,。、《》()（）]/g, '').toLowerCase();
}

function sectionAnchor(id) {
  const anchor = el('span', 'section-anchor');
  anchor.id = id;
  return anchor;
}

function renderBookOutline(book) {
  const plan = book.metadata?.book_plan;
  if (!plan?.chapters?.length) return null;
  const section = el('section', 'book-outline');
  section.id = 'book_outline';
  section.appendChild(heading('h2', '教材大纲'));
  const chapterList = document.createElement('ol');
  for (const chapter of plan.chapters) {
    const chapterItem = document.createElement('li');
    chapterItem.appendChild(document.createTextNode(displayChapterTitle(chapter)));
    const sectionList = document.createElement('ol');
    for (const item of chapter.sections || []) {
      const sectionItem = document.createElement('li');
      sectionItem.appendChild(document.createTextNode(displaySectionTitle(item)));
      const pointList = document.createElement('ol');
      for (const point of item.knowledge_points || []) {
        const pointItem = document.createElement('li');
        pointItem.textContent = point;
        pointList.appendChild(pointItem);
      }
      if (pointList.children.length) sectionItem.appendChild(pointList);
      sectionList.appendChild(sectionItem);
    }
    if (sectionList.children.length) chapterItem.appendChild(sectionList);
    chapterList.appendChild(chapterItem);
  }
  section.appendChild(chapterList);
  return section;
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
  const markdown = el('div', 'markdown');
  markdown.innerHTML = renderMarkdown(sanitizeStudentAnswer(payload.answer || payload.markdown || 'Remote service returned an empty answer.'));
  wrap.appendChild(markdown);
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
  const sections = [...document.querySelectorAll('.project, .task, .section-anchor')];
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
  for (const button of document.querySelectorAll('.toc-chapter-toggle')) {
    button.classList.toggle('active', button.dataset.target === id);
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
window.addEventListener('resize', () => requestAnimationFrame(drawAbilityMapConnectors));

window.studyDataApi = {
  collect: collectStudyData,
  apply: applyStudyData,
  sync: syncStudyDataToEndpoint,
};

loadBook().catch((error) => {
  document.getElementById('content').textContent = `电子教材加载失败：${error}`;
});
"""
