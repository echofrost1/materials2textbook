#!/usr/bin/env python3
"""
Custom framework injection: rebuild the digital textbook with a
project-oriented "认知→准备→操作→质检" pedagogical structure.

Replaces the auto BookPlanner with a deterministic 4-project/13-task plan,
maps evidence chunks by material_block + keyword matching, and runs the
existing chapter pipeline with generous per-chapter token budgets.

Reuses the existing LLM cache so ResourceAnalyst calls are instant.
"""

from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

# ── path setup ──────────────────────────────────────────────
REPO = Path("/ai/data/repos/work-manuscript")
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from materials2textbook.schemas import (
    BookPlan,
    BookChapterPlan,
    BookSectionPlan,
    EvidenceChunk,
)
from materials2textbook.workflow.orchestrator import TextbookWorkflow
from materials2textbook.workflow.config import WorkflowConfig
from materials2textbook.llm.provider import OpenAICompatibleConfig, OpenAICompatibleProvider
from materials2textbook.llm.retry import RetryingLLMProvider
from materials2textbook.llm.cache import CachingLLMProvider


class ProgressLLMProvider:
    """Thin wrapper that prints progress for each LLM call."""
    def __init__(self, provider) -> None:
        self.provider = provider
        self.calls = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        print(f"[llm] request {self.calls} start", flush=True)
        response = self.provider.generate(messages)
        print(f"[llm] request {self.calls} done, chars={len(response)}", flush=True)
        return response

# ════════════════════════════════════════════════════════════
#  FRAMEWORK DEFINITION  (user-approved Option C structure)
# ════════════════════════════════════════════════════════════

FRAMEWORK = [
    # ── 项目一 焊接安全与设备认知 ──────────────────────────
    {
        "chapter_id": "ch01",
        "chapter_no": 1,
        "title": "项目一　焊接安全与设备认知",
        "learning_goals": [
            "了解焊接技术的基本概念、分类与应用领域",
            "掌握焊接安全防护的基本要求与操作规程",
            "能够识别常用焊接设备并完成基本参数设置",
        ],
        "material_blocks": ["焊接设备与安全", "焊接基本操作"],
        "shared_blocks": ["教材参考资料"],
        "token_budget": 12000,
        "tasks": [
            {
                "sid": "sec_1_1",
                "sno": "1.1",
                "title": "认识焊接技术与应用领域",
                "kps": ["焊接基本概念与分类", "焊接技术特点与应用领域"],
                "kw": ["基本原理", "认识", "应用", "特点", "适用范围",
                       "概述", "简介", "分类", "原理", "概念"],
                "skw": ["焊接", "概述", "应用", "认识"],
            },
            {
                "sid": "sec_1_2",
                "sno": "1.2",
                "title": "焊接安全防护规范",
                "kps": ["焊接作业危险因素与防护要求", "个人防护用品的正确使用"],
                "kw": ["安全", "防护", "劳保", "用品", "危险",
                       "规程", "触电", "烧伤", "烟尘", "弧光"],
                "skw": ["安全", "防护", "危险"],
            },
            {
                "sid": "sec_1_3",
                "sno": "1.3",
                "title": "焊接设备识别与功能",
                "kps": ["弧焊电源的类型与结构", "焊接工具与附件识别"],
                "kw": ["设备", "焊机", "焊钳", "焊枪", "氩弧焊枪",
                       "钨极", "电极", "结构", "电源", "电缆"],
                "skw": ["设备", "焊机", "焊钳"],
            },
            {
                "sid": "sec_1_4",
                "sno": "1.4",
                "title": "焊机参数设置与调试",
                "kps": ["焊接参数选择与设备调试"],
                "kw": ["参数", "电流", "电压", "设置", "调试",
                       "调节", "极性", "接线", "接法"],
                "skw": ["参数", "电流", "电压"],
            },
        ],
    },
    # ── 项目二 焊条电弧焊操作 ──────────────────────────────
    {
        "chapter_id": "ch02",
        "chapter_no": 2,
        "title": "项目二　焊条电弧焊操作",
        "learning_goals": [
            "了解焊条电弧焊的基本原理与焊条选用",
            "掌握引弧、运条及平对接焊的操作要领",
            "能够完成平对接焊操作并进行焊缝外观质量检查",
        ],
        "material_blocks": ["焊条电弧焊"],
        "shared_blocks": ["教材参考资料"],
        "token_budget": 12000,
        "tasks": [
            {
                "sid": "sec_2_1",
                "sno": "2.1",
                "title": "焊条的选择与保管",
                "kps": ["焊条分类、型号与保管要求"],
                "kw": ["选择", "保管", "型号", "牌号",
                       "存储", "烘干", "药皮", "酸性", "碱性", "分类"],
                "skw": ["焊条选择"],
            },
            {
                "sid": "sec_2_2",
                "sno": "2.2",
                "title": "引弧与运条基本操作",
                "kps": ["引弧方法与操作要领", "运条方式与焊道连接"],
                "kw": ["引弧", "运条", "起弧", "操作", "基本操作",
                       "手法", "划擦", "直击", "焊道", "连接"],
                "skw": ["引弧", "运条"],
            },
            {
                "sid": "sec_2_3",
                "sno": "2.3",
                "title": "平对接焊操作",
                "kps": ["平对接焊装配与定位焊", "平对接焊焊接操作"],
                "kw": ["平焊", "对接", "平对接", "焊接操作", "接头",
                       "定位焊", "装配", "打底", "填充", "盖面"],
                "skw": ["平对接", "平焊"],
            },
            {
                "sid": "sec_2_4",
                "sno": "2.4",
                "title": "焊缝外观质量检查",
                "kps": ["焊缝外观检验标准", "常见焊缝外观缺陷识别"],
                "kw": ["焊缝", "外观", "质量", "检查", "检验",
                       "缺陷", "余高", "宽度", "咬边", "焊瘤"],
                "skw": ["外观", "质量", "焊缝"],
            },
        ],
    },
    # ── 项目三 钨极氩弧焊操作 ──────────────────────────────
    {
        "chapter_id": "ch03",
        "chapter_no": 3,
        "title": "项目三　钨极氩弧焊操作",
        "learning_goals": [
            "掌握钨极氩弧焊的基本原理与设备组成",
            "能够完成焊前准备、参数调节与平对接焊操作",
            "掌握收弧操作与焊后质量检查方法",
        ],
        "material_blocks": ["钨极氩弧焊"],
        "shared_blocks": ["教材参考资料"],
        "token_budget": 12000,
        "tasks": [
            {
                "sid": "sec_3_1",
                "sno": "3.1",
                "title": "钨极氩弧焊原理与设备",
                "kps": ["钨极氩弧焊基本原理", "钨极氩弧焊设备组成"],
                "kw": ["基本原理", "原理", "特点", "适用范围",
                       "氩弧焊枪", "氩气", "保护气体", "原理特点"],
                "skw": ["钨极原理", "氩弧原理"],
            },
            {
                "sid": "sec_3_2",
                "sno": "3.2",
                "title": "焊前准备与参数调节",
                "kps": ["焊前准备与钨极选择", "焊接参数设定与气体保护"],
                "kw": ["准备", "参数", "钨极伸出长度", "钨极烧损",
                       "送丝", "气流量", "电流", "非接触引弧", "喷嘴"],
                "skw": ["准备", "钨极", "送丝"],
            },
            {
                "sid": "sec_3_3",
                "sno": "3.3",
                "title": "平对接焊接操作",
                "kps": ["平对接焊坡口与装配", "平对接焊操作与送丝技术"],
                "kw": ["平对接", "平焊", "对接", "焊接操作", "坡口",
                       "送丝", "打底", "左焊法", "右焊法", "定位焊",
                       "装配", "多层焊", "填充", "盖面"],
                "skw": ["平对接", "坡口", "送丝"],
            },
            {
                "sid": "sec_3_4",
                "sno": "3.4",
                "title": "收弧与焊后检查",
                "kps": ["收弧操作与弧坑处理", "焊后质量检查"],
                "kw": ["收弧", "焊后", "检查", "质量", "填弧坑",
                       "弧坑", "裂纹", "保护", "冷却"],
                "skw": ["收弧", "弧坑"],
            },
        ],
    },
    # ── 项目四 焊接缺陷分析与质量控制 ──────────────────────
    {
        "chapter_id": "ch04",
        "chapter_no": 4,
        "title": "项目四　焊接缺陷分析与质量控制",
        "learning_goals": [
            "能够识别常见焊接缺陷并分析成因",
            "掌握焊接质量检验的基本方法与标准",
        ],
        "material_blocks": ["焊接缺陷与质量检验"],
        "shared_blocks": ["教材参考资料", "焊接基本操作"],
        "token_budget": 12000,
        "tasks": [
            {
                "sid": "sec_4_1",
                "sno": "4.1",
                "title": "常见焊接缺陷识别",
                "kps": ["焊接缺陷的分类与特征", "常见焊接缺陷的识别"],
                "kw": ["缺陷", "裂纹", "气孔", "夹渣", "咬边",
                       "未焊透", "未熔合", "变形", "焊瘤", "烧穿"],
                "skw": ["缺陷", "裂纹", "气孔"],
            },
            {
                "sid": "sec_4_2",
                "sno": "4.2",
                "title": "缺陷成因分析",
                "kps": ["焊接缺陷的成因分析", "焊接变形与控制措施"],
                "kw": ["成因", "原因", "分析", "产生", "预防",
                       "措施", "控制", "变形", "应力"],
                "skw": ["成因", "原因", "变形"],
            },
            {
                "sid": "sec_4_3",
                "sno": "4.3",
                "title": "质量检验方法",
                "kps": ["焊接质量检验方法与标准"],
                "kw": ["检验", "检查", "质量", "方法", "检测",
                       "无损", "外观", "射线", "超声", "渗透", "标准"],
                "skw": ["检验", "检测", "质量"],
            },
        ],
    },
]


# ════════════════════════════════════════════════════════════
#  EVIDENCE MAPPING
# ════════════════════════════════════════════════════════════

def _chunk_text(chunk: EvidenceChunk) -> str:
    """Concatenate searchable text fields for keyword matching."""
    parts = [chunk.title, chunk.summary, chunk.content[:300]]
    parts.extend(chunk.keywords)
    meta_kp = chunk.metadata.get("knowledge_point", "")
    if meta_kp:
        parts.append(meta_kp)
    return " ".join(p for p in parts if p)


def _score_task(text: str, keywords: list[str]) -> int:
    """Count how many keywords appear in text."""
    return sum(1 for kw in keywords if kw in text)


def map_chunks_to_framework(chunks: list[EvidenceChunk]):
    """
    Assign each chunk to exactly one (chapter, section) pair using
    keyword scoring + load-balanced fallback for zero-score chunks.

    Returns:
        chapter_chunk_ids:  dict[chapter_id → list[chunk_id]]
        section_chunk_ids:  dict[section_id → list[chunk_id]]
        stats:              dict with assignment statistics
    """
    chapter_chunk_ids: dict[str, list[str]] = defaultdict(list)
    section_chunk_ids: dict[str, list[str]] = defaultdict(list)

    scored = 0       # assigned via keyword scoring
    balanced = 0     # assigned via load-balancing (no keyword match)
    shared_assigned = 0  # assigned via shared_blocks
    truly_unassigned = 0

    # ── Phase 1: score-based assignment for primary material_blocks ──
    unbalanced: dict[str, list[EvidenceChunk]] = defaultdict(list)
    # ^ chapter_id → list of chunks that scored 0 for all tasks

    for chunk in chunks:
        block = chunk.material_block or ""
        text = _chunk_text(chunk)

        matched_chapter = None
        for ch_def in FRAMEWORK:
            if block in ch_def["material_blocks"]:
                matched_chapter = ch_def
                break

        if matched_chapter is None:
            # Try shared blocks — these use skw (shared keywords)
            for ch_def in FRAMEWORK:
                if block in ch_def.get("shared_blocks", []):
                    best_task = None
                    best_score = 0
                    for task in ch_def["tasks"]:
                        s = _score_task(text, task.get("skw", []))
                        if s > best_score:
                            best_score = s
                            best_task = task
                    if best_task and best_score > 0:
                        chapter_chunk_ids[ch_def["chapter_id"]].append(chunk.chunk_id)
                        section_chunk_ids[best_task["sid"]].append(chunk.chunk_id)
                        shared_assigned += 1
                        matched_chapter = "SHARED"  # sentinel
                        break

            if matched_chapter is None:
                truly_unassigned += 1
            continue

        # Primary block match — score against each task
        best_task = None
        best_score = 0
        for task in matched_chapter["tasks"]:
            s = _score_task(text, task["kw"])
            if s > best_score:
                best_score = s
                best_task = task

        if best_task and best_score > 0:
            chapter_chunk_ids[matched_chapter["chapter_id"]].append(chunk.chunk_id)
            section_chunk_ids[best_task["sid"]].append(chunk.chunk_id)
            scored += 1
        else:
            # Zero score — defer to load-balancing phase
            unbalanced[matched_chapter["chapter_id"]].append(chunk)

    # ── Phase 2: load-balance zero-score chunks to least-loaded task ──
    for ch_def in FRAMEWORK:
        cid = ch_def["chapter_id"]
        pending = unbalanced.get(cid, [])
        if not pending:
            continue
        for chunk in pending:
            # pick task with fewest chunks
            task_loads = [
                (task, len(section_chunk_ids.get(task["sid"], [])))
                for task in ch_def["tasks"]
            ]
            task_loads.sort(key=lambda x: x[1])
            target_task = task_loads[0][0]
            chapter_chunk_ids[cid].append(chunk.chunk_id)
            section_chunk_ids[target_task["sid"]].append(chunk.chunk_id)
            balanced += 1

    stats = {
        "total": len(chunks),
        "scored": scored,
        "balanced": balanced,
        "shared": shared_assigned,
        "unassigned": truly_unassigned,
    }
    return chapter_chunk_ids, section_chunk_ids, stats


# ════════════════════════════════════════════════════════════
#  CUSTOM BOOK PLANNER
# ════════════════════════════════════════════════════════════

class FrameworkBookPlanner:
    """Drop-in replacement for BookPlannerAgent that returns our framework."""

    MAX_CHUNKS_PER_SECTION = 15
    MAX_CHUNKS_PER_CHAPTER = 50

    def run(self, *, title, chunks, manifest_xlsx=None,
            max_chapters=0, chapter_token_budget=12000, **kw):
        ch_ids, sec_ids, stats = map_chunks_to_framework(chunks)
        chunk_map = {c.chunk_id: c for c in chunks}

        chapters: list[BookChapterPlan] = []
        for ch_def in FRAMEWORK:
            cid = ch_def["chapter_id"]

            sections: list[BookSectionPlan] = []
            capped_ch_chunks: list[str] = []
            for task in ch_def["tasks"]:
                sid = task["sid"]
                raw = sec_ids.get(sid, [])
                # Prioritise video chunks, then highest teaching_value
                videos = [cid_ for cid_ in raw if cid_.startswith("C")]
                docs = [cid_ for cid_ in raw if not cid_.startswith("C")]
                capped = (videos[:6] + docs[:self.MAX_CHUNKS_PER_SECTION - len(videos[:6])])
                capped_ch_chunks.extend(capped)
                sections.append(BookSectionPlan(
                    section_id=sid,
                    section_no=task["sno"],
                    title=task["title"],
                    knowledge_point_ids=list(task["kps"]),
                    primary_material_ids=capped,
                    recommended_video_ids=[c for c in capped if c.startswith("C")],
                ))

            # Deduplicate and cap chapter-level IDs
            seen = set()
            deduped = []
            for cid_ in capped_ch_chunks:
                if cid_ not in seen:
                    seen.add(cid_)
                    deduped.append(cid_)
            deduped = deduped[:self.MAX_CHUNKS_PER_CHAPTER]

            chapters.append(BookChapterPlan(
                chapter_id=cid,
                chapter_no=ch_def["chapter_no"],
                title=ch_def["title"],
                learning_goals=list(ch_def["learning_goals"]),
                sections=sections,
                primary_material_ids=deduped,
                token_budget=ch_def["token_budget"],
            ))

        plan = BookPlan(
            book_id="framework_v6",
            title=title,
            planning_strategy="custom_project_framework",
            chapters=chapters,
            metadata={
                "framework": "project_task",
                "version": "v6",
                "evidence_stats": stats,
            },
        )

        # Print evidence distribution report
        print("\n" + "=" * 60)
        print("EVIDENCE DISTRIBUTION REPORT")
        print("=" * 60)
        print(f"  Total chunks: {stats['total']}")
        print(f"  Scored (keyword match): {stats['scored']}")
        print(f"  Balanced (load-balanced): {stats['balanced']}")
        print(f"  Shared (shared_blocks): {stats['shared']}")
        print(f"  Unassigned: {stats['unassigned']}")
        for ch_def in FRAMEWORK:
            cid = ch_def["chapter_id"]
            ch_total = len(ch_ids.get(cid, []))
            print(f"\n  {ch_def['title']}  ({ch_total} chunks)")
            for task in ch_def["tasks"]:
                n = len(sec_ids.get(task["sid"], []))
                print(f"    {task['sno']} {task['title']}: {n} chunks")
        print("=" * 60 + "\n")

        return plan


# ════════════════════════════════════════════════════════════
#  LLM PROVIDER  (reuse existing cache)
# ════════════════════════════════════════════════════════════

def build_provider(cache_path: Path):
    cfg = OpenAICompatibleConfig.from_env()
    if not cfg.is_configured:
        raise SystemExit("LLM not configured. Check .env")
    provider = OpenAICompatibleProvider(cfg)
    provider = RetryingLLMProvider(provider, max_retries=3, backoff_seconds=2.0)
    provider = CachingLLMProvider(provider, cache_path)
    provider = ProgressLLMProvider(provider)
    return provider


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

def main():
    base = Path("/ai/data/materials2textbook")
    # Reuse existing selected segments (from previous pipeline run)
    segments = base / "digital_book_full" / "selected_video_segments.jsonl"
    docs = base / "digital_book_full" / "combined_document_segments.jsonl"
    # Reuse existing LLM cache (ResourceAnalyst calls = instant)
    cache = base / "digital_book_full" / "llm_cache.json"
    # NEW output dir — fresh start, no stale chapter artifacts
    output = base / "digital_book_v6"

    output.mkdir(parents=True, exist_ok=True)

    print(f"[framework] segments  = {segments}")
    print(f"[framework] docs      = {docs}")
    print(f"[framework] cache     = {cache}")
    print(f"[framework] output    = {output}")

    provider = build_provider(cache)
    print(f"[framework] LLM provider ready (cache reuse enabled)")

    workflow = TextbookWorkflow(llm_provider=provider, use_llm=True)
    # ── inject custom framework planner ──
    workflow.book_planner = FrameworkBookPlanner()
    print("[framework] custom book planner injected")

    # ── monkey-patch: filter plan chunk_ids to budgeted set only ──
    # This prevents _render_plan from listing hundreds of chunk_ids in the
    # reviewer prompt, which caused 40K+ token overflow on chapters 2 & 3.
    _original_prepare = workflow._prepare_chapter_plans

    def _filtered_prepare(plans, chunks):
        prepared = _original_prepare(plans, chunks)
        budgeted_ids = {c.chunk_id for c in chunks}
        for plan in prepared:
            plan.evidence_chunk_ids = [
                cid for cid in plan.evidence_chunk_ids if cid in budgeted_ids
            ]
            for kp in plan.knowledge_points:
                kp.chunk_ids = [cid for cid in kp.chunk_ids if cid in budgeted_ids]
            for act in plan.activity_items:
                act.evidence_chunk_ids = [
                    cid for cid in act.evidence_chunk_ids if cid in budgeted_ids
                ]
        return prepared

    workflow._prepare_chapter_plans = _filtered_prepare
    print("[framework] chunk_id filter patch applied")

    config = WorkflowConfig(
        max_input_tokens=0,          # disable global budget — all chunks survive
        review_rounds=1,
    )

    print("[framework] starting pipeline run...")
    outputs = workflow.run(
        video_segments_path=segments,
        document_segments_path=docs if docs.exists() else None,
        output_dir=output,
        title="钨极氩弧焊数字教材",
        config=config,
        book_mode=True,
        max_chapter_input_tokens=80000,
        chapter_output_root=output / "chapter_runs",
        resume_chapters=True,
    )

    print("\n[framework] PIPELINE COMPLETE")
    print(f"  digital_book_json = {outputs.digital_book_path}")
    print(f"  digital_book_dir  = {outputs.digital_book_dir}")


if __name__ == "__main__":
    main()
