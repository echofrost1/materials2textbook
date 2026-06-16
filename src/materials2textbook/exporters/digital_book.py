from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.schemas import (
    ChapterPlan,
    DigitalBook,
    DigitalBookBlock,
    DigitalBookProject,
    DigitalBookTask,
    EvidenceChunk,
    KnowledgePoint,
)


def build_digital_book(
    *,
    title: str,
    plans: list[ChapterPlan],
    chunks: list[EvidenceChunk],
    output_dir: Path,
    copy_media_assets: bool = True,
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
                items=[_format_learning_path_item(point) for point in plan.knowledge_points],
                evidence_chunk_ids=plan.evidence_chunk_ids,
            ),
        ]

        key_terms: list[str] = []
        for point_index, point in enumerate(plan.knowledge_points, start=1):
            point_chunks = [chunk_map[chunk_id] for chunk_id in point.chunk_ids if chunk_id in chunk_map]
            key_terms.extend(point.title for _chunk in point_chunks[:1])
            implementation_text = _render_point_markdown(point.title, point.summary, point_chunks)
            task_blocks.append(
                DigitalBookBlock(
                    block_id=f"p{project_index:02d}_kp{point_index:02d}_text",
                    type="implementation",
                    title=point.title,
                    markdown=implementation_text,
                    evidence_chunk_ids=[chunk.chunk_id for chunk in point_chunks],
                )
            )
            for media_index, chunk in enumerate(point_chunks, start=1):
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
                project_intro=f"本项目基于已处理的教学素材片段，学习“{plan.title}”相关知识与操作。",
                ability_map=[
                    "素材观察与证据定位",
                    "知识点理解与复述",
                    "操作过程分析与人工复核",
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
) -> tuple[DigitalBook, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    book = build_digital_book(
        title=title,
        plans=plans,
        chunks=chunks,
        output_dir=output_dir,
        copy_media_assets=copy_media_assets,
    )
    json_path = output_dir / "digital_book.json"
    index_path = output_dir / "index.html"
    write_json(json_path, book)
    write_text(index_path, VIEWER_HTML)
    write_text(output_dir / "styles.css", VIEWER_CSS)
    write_text(output_dir / "ask_config.js", ASK_CONFIG_JS)
    write_text(output_dir / "app.js", VIEWER_JS)
    return book, json_path, index_path


def _render_point_markdown(title: str, summary: str, chunks: list[EvidenceChunk]) -> str:
    lines = []
    if summary:
        lines.append(summary)
        lines.append("")
    for chunk in chunks:
        source = chunk.metadata.get("source_video", "") or chunk.locator.original_path or chunk.locator.path
        start = chunk.metadata.get("start_time", "")
        end = chunk.metadata.get("end_time", "")
        text = " ".join(chunk.content.split())
        if len(text) > 220:
            text = text[:217] + "..."
        lines.append(f"- 证据 `{chunk.chunk_id}`：{text}")
        lines.append(f"  来源：{source} [{start}-{end}]")
        if chunk.review_status and "approved" not in chunk.review_status.lower():
            lines.append(f"  状态：{chunk.review_status}，正式使用前需要人工复核。")
    return "\n".join(lines).strip() or f"围绕“{title}”完成素材观察和知识整理。"


def _format_learning_path_item(point: KnowledgePoint) -> str:
    prerequisites = ", ".join(point.prerequisite_ids) if point.prerequisite_ids else "无"
    return f"{point.order_index}. {point.title}（难度：{point.difficulty_level}；先修：{prerequisites}）"


def _assessment_items(plan: ChapterPlan) -> list[str]:
    items: list[str] = []
    for activity in plan.activity_items:
        for rubric in activity.rubric:
            items.append(f"{activity.difficulty_level}/{activity.type}：{rubric}")
    return items or [
        "能说出本任务涉及的核心概念或关键操作。",
        "能根据视频证据指出需要人工复核的片段和时间码。",
        "能用自己的话复述至少一个知识点，并保留证据来源。",
    ]


def _exercise_items(plan: ChapterPlan) -> list[str]:
    items = [
        f"{activity.difficulty_level}/{activity.type}：{activity.prompt}"
        for activity in plan.activity_items
    ]
    if items:
        return items
    return [
        f"结合证据片段，说明“{point.title}”的关键内容。"
        for point in plan.knowledge_points[:5]
    ] or ["结合证据片段，总结本任务的关键学习收获。"]


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
        <input id="askInput" class="ask-input" type="search" placeholder="输入知识点、操作或证据编号">
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
  if (block.evidence_chunk_ids && block.evidence_chunk_ids.length) {
    const evidence = el('div', 'evidence');
    evidence.textContent = `证据：${block.evidence_chunk_ids.join(', ')}`;
    node.appendChild(evidence);
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
          ...(block.evidence_chunk_ids || []),
        ];
        const text = textParts.join(' ').replace(/\\s+/g, ' ').trim();
        if (!text) continue;
        rows.push({
          id: block.block_id,
          projectTitle: project.title,
          taskTitle: task.title,
          blockTitle: block.title,
          text,
          evidence: block.evidence_chunk_ids || [],
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
    answer.textContent = '请输入要检索的知识点、操作步骤或证据编号。';
    return;
  }
  const results = state.askIndex
    .map((row) => ({ row, score: scoreAskRow(row, terms) }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, 3);
  if (!results.length) {
    answer.textContent = '暂未在本教材中找到直接相关内容。可以换一个知识点、操作词或 chunk_id 再问。';
    return;
  }
  const remoteAnswered = await answerWithRemoteService(rawQuestion, results, answer);
  if (!remoteAnswered) {
    renderLocalAskResults(results, terms, answer);
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
  wrap.appendChild(paragraph(payload.answer || payload.markdown || 'Remote service returned an empty answer.'));
  const citations = payload.citations || payload.evidence_chunk_ids || [];
  if (citations.length) {
    const evidence = document.createElement('small');
    evidence.textContent = `Evidence: ${citations.join(', ')}`;
    wrap.appendChild(evidence);
  }
  answer.appendChild(wrap);
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
  if (row.evidence.length) {
    const evidence = document.createElement('small');
    evidence.textContent = `证据：${row.evidence.join(', ')}`;
    wrap.appendChild(evidence);
  }
  return wrap;
}

function tokenizeQuestion(value) {
  return [...new Set((value || '')
    .toLowerCase()
    .split(/[^\\p{L}\\p{N}_-]+/u)
    .map((term) => term.trim())
    .filter((term) => term.length >= 2))];
}

function scoreAskRow(row, terms) {
  const text = row.text.toLowerCase();
  const title = `${row.taskTitle} ${row.blockTitle}`.toLowerCase();
  let score = 0;
  for (const term of terms) {
    if (text.includes(term)) score += term.length >= 5 ? 3 : 1;
    if (title.includes(term)) score += 4;
    if ((row.evidence || []).some((chunkId) => chunkId.toLowerCase() === term)) {
      score += Math.max(1, 12 / Math.max(1, row.evidence.length));
    }
  }
  return score;
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
