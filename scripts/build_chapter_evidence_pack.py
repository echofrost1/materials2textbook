#!/usr/bin/env python
"""Build a chapter-level evidence pack and readiness report.

This script is the first implementation step from
docs/textbook_generation_optimization_plan.md. It does not generate textbook
prose. It turns loose processed evidence JSONL files into a chapter-scoped
material package that the writer can consume more safely.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root
from typing import Any

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from materials2textbook.adapters.document_segments import document_segment_to_evidence_chunk
from materials2textbook.adapters.video_segments import video_segment_to_evidence_chunk
from materials2textbook.io_utils import write_jsonl


DEFAULT_KNOWLEDGE_POINTS = [
    "基本原理",
    "设备组成",
    "焊前准备",
    "非接触引弧",
    "送丝操作",
    "收弧操作",
    "打底焊",
    "填充焊",
    "盖面焊",
    "常见缺陷与纠正",
]

SOURCE_FILES = [
    ("video", "video_segments.jsonl"),
    ("ppt", "ppt_assets.jsonl"),
    ("reference_text", "reference_text_assets.jsonl"),
    ("structured", "structured_assets.jsonl"),
    ("structured", "assessment_table_assets.jsonl"),
    ("structured", "structured_table_assets.jsonl"),
    ("audio", "audio_segments.jsonl"),
]

MANUAL_KNOWLEDGE_OVERRIDES = {
    "PPT_A000039_S044": "焊前准备",
    "PPT_A000039_S052": "焊前准备",
    "PPT_A000051_S015": "基本原理",
    "PPT_A000054_S007": "__exclude__",
    "PPT_A000056_S001": "__exclude__",
    "PPT_A000065_S050": "基本原理",
    "PPT_A000076_S040": "基本原理",
    "PPT_A000145_S086": "基本原理",
    "REF000007": "基本原理",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a chapter evidence pack and readiness report.")
    parser.add_argument("--material-root", type=Path, default=default_work_root())
    parser.add_argument("--chapter", default="钨极氩弧焊", help="Chapter/material block to package.")
    parser.add_argument(
        "--knowledge-points",
        default=";".join(DEFAULT_KNOWLEDGE_POINTS),
        help="Semicolon-separated target knowledge points for readiness checks.",
    )
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--max-reference-records", type=int, default=80)
    parser.add_argument("--max-structured-records", type=int, default=40)
    parser.add_argument("--max-audio-records", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    material_root = args.material_root.resolve()
    json_dir = material_root / "02_working_processing" / "json"
    output_root = args.output_root or material_root / "05_final_deliverables" / "chapter_work" / slugify(args.chapter)
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    target_points = [item.strip() for item in args.knowledge_points.split(";") if item.strip()]
    records = load_source_records(json_dir)
    selected = select_chapter_records(
        records,
        chapter=args.chapter,
        target_points=target_points,
        max_reference_records=args.max_reference_records,
        max_structured_records=args.max_structured_records,
        max_audio_records=args.max_audio_records,
    )
    paired_rows = [(record, build_pack_row(record, args.chapter, target_points)) for record in selected]
    active_pairs = [(record, row) for record, row in paired_rows if row["quality_gate_status"] != "reject"]
    excluded_rows = [row for _, row in paired_rows if row["quality_gate_status"] == "reject"]
    active_pairs = sorted(active_pairs, key=lambda pair: pack_sort_key(pair[1]))
    selected = [record for record, _ in active_pairs]
    pack_rows = [row for _, row in active_pairs]
    readiness_rows = build_readiness_rows(pack_rows, target_points)
    summary = build_summary(args.chapter, records, pack_rows, readiness_rows)

    jsonl_path = output_root / "chapter_evidence_pack.jsonl"
    xlsx_path = output_root / "chapter_evidence_pack.xlsx"
    excluded_path = output_root / "chapter_excluded_assets.xlsx"
    readiness_path = output_root / "chapter_readiness_report.xlsx"
    gap_log_path = output_root / "chapter_evidence_gap_log.xlsx"
    summary_path = output_root / "chapter_evidence_pack_summary.json"
    video_path = output_root / "chapter_video_segments.jsonl"
    ppt_path = output_root / "chapter_ppt_assets.jsonl"
    document_path = output_root / "chapter_document_segments.jsonl"

    write_jsonl(jsonl_path, pack_rows)
    write_compatible_inputs(selected, pack_rows, video_path, ppt_path, document_path)
    to_excel_safe(pack_rows, xlsx_path)
    to_excel_safe(excluded_rows, excluded_path)
    to_excel_safe(readiness_rows, readiness_path)
    to_excel_safe(build_gap_rows(args.chapter, pack_rows, readiness_rows), gap_log_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Chapter evidence pack generated:")
    print(f"- chapter: {args.chapter}")
    print(f"- source_records: {len(records)}")
    print(f"- selected_records: {len(pack_rows)}")
    print(f"- knowledge_points: {len(target_points)}")
    print(f"- output_jsonl: {jsonl_path}")
    print(f"- output_xlsx: {xlsx_path}")
    print(f"- excluded_assets: {excluded_path}")
    print(f"- chapter_video_segments: {video_path}")
    print(f"- chapter_ppt_assets: {ppt_path}")
    print(f"- chapter_document_segments: {document_path}")
    print(f"- readiness_report: {readiness_path}")
    print(f"- gap_log: {gap_log_path}")
    print(f"- summary: {summary_path}")
    return 0


def load_source_records(json_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source_type, filename in SOURCE_FILES:
        path = json_dir / filename
        if not path.exists():
            continue
        for row in read_jsonl(path):
            row = dict(row)
            row["_source_file"] = filename
            row["_source_type"] = source_type
            records.append(row)
    return records


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_compatible_inputs(
    selected: list[dict[str, Any]],
    pack_rows: list[dict[str, Any]],
    video_path: Path,
    ppt_path: Path,
    document_path: Path,
) -> None:
    video_rows = []
    ppt_rows = []
    document_rows = []
    for record, pack_row in zip(selected, pack_rows):
        source_type = str(record.get("_source_type", ""))
        cleaned = {key: value for key, value in record.items() if not key.startswith("_")}
        cleaned["knowledge_point"] = pack_row["knowledge_point"]
        cleaned["recommended_chapter"] = pack_row["chapter"]
        cleaned["evidence_role"] = pack_row["evidence_role"]
        cleaned["quality_gate_status"] = pack_row["quality_gate_status"]
        cleaned["quality_gate_reason"] = pack_row["quality_gate_reason"]
        if source_type == "video":
            video_rows.append(cleaned)
        elif source_type == "ppt":
            ppt_rows.append(cleaned)
        else:
            document_rows.append(cleaned)
    write_jsonl(video_path, video_rows)
    write_jsonl(ppt_path, ppt_rows)
    write_jsonl(document_path, document_rows)


def to_excel_safe(rows: list[dict[str, Any]], path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    if headers:
        sheet.append(headers)
        for row in rows:
            sheet.append([excel_safe(row.get(header, "")) for header in headers])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def excel_safe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", value)


def select_chapter_records(
    records: list[dict[str, Any]],
    *,
    chapter: str,
    target_points: list[str],
    max_reference_records: int,
    max_structured_records: int,
    max_audio_records: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    limits = {
        "reference_text": max_reference_records,
        "structured": max_structured_records,
        "audio": max_audio_records,
    }
    counts: Counter[str] = Counter()
    for record in records:
        source_type = str(record.get("_source_type", ""))
        if not record_matches_chapter(record, chapter, target_points):
            continue
        if source_type in limits and limits[source_type] > 0 and counts[source_type] >= limits[source_type]:
            continue
        selected.append(record)
        counts[source_type] += 1
    return selected


def record_matches_chapter(record: dict[str, Any], chapter: str, target_points: list[str]) -> bool:
    source_type = str(record.get("_source_type", ""))
    primary_text = " ".join(
        text_value(record.get(key))
        for key in [
            "material_block",
            "material_block_cn",
            "recommended_chapter",
            "knowledge_point",
            "knowledge_point_cn",
            "tags",
            "domain_term_hits",
            "original_path",
            "source_file",
            "source_video",
            "source_ppt",
        ]
    )
    haystack = normalize_for_match(primary_text)
    chapter_terms = chapter_terms_for(chapter)
    if any(term in haystack for term in chapter_terms):
        return True

    evidence = normalize_for_match(
        text_value(
            record.get("evidence_text")
            or record.get("extracted_text")
            or record.get("transcript_text")
            or record.get("slide_text")
            or record.get("text_preview")
        )
    )
    if source_type in {"video", "ppt"} and any(term in evidence for term in chapter_terms):
        return True

    if source_type in {"reference_text", "structured", "audio"}:
        if any(term in evidence for term in chapter_terms):
            return True
        return any(normalize_for_match(point) in evidence for point in target_points if point)
    return False


def chapter_terms_for(chapter: str) -> list[str]:
    normalized = normalize_for_match(chapter)
    terms = [normalized]
    if "钨极" in chapter or "氩弧" in chapter or "tig" in normalized:
        terms.extend(["钨极", "氩弧", "钨极氩弧", "tig"])
    if "焊条" in chapter:
        terms.extend(["焊条", "手工电弧", "焊条电弧"])
    if "安全" in chapter:
        terms.extend(["安全", "防护"])
    if "缺陷" in chapter or "质量" in chapter or "检验" in chapter:
        terms.extend(["缺陷", "缺欠", "常见缺陷", "焊后检查", "焊缝清理", "质量检验", "外观检查", "无损检测", "返修"])
    if "实训" in chapter or "考核" in chapter or "鉴定" in chapter:
        terms.extend(["实训", "训练", "考核", "鉴定", "评分", "评分标准", "题库", "试题", "评价"])
    return dedupe([term for term in terms if term])


def build_pack_row(record: dict[str, Any], chapter: str, target_points: list[str]) -> dict[str, Any]:
    source_type = str(record.get("_source_type", ""))
    chunk = to_evidence_chunk(record)
    knowledge_point = map_knowledge_point(record, chunk.title, chapter, target_points)
    role, gate_status, gate_reason = classify_quality(record, source_type)
    if knowledge_point == "__exclude__":
        knowledge_point = "排除_非本章主线"
        role = "hold"
        gate_status = "reject"
        gate_reason = "manual audit: not part of this chapter's main knowledge line"
    row = {
        "chapter": chapter,
        "knowledge_point": knowledge_point,
        "evidence_pack_id": f"{slugify(chapter)}_{chunk.chunk_id}",
        "chunk_id": chunk.chunk_id,
        "asset_id": chunk.asset_id,
        "source_type": source_type,
        "source_file": record.get("_source_file", ""),
        "title": chunk.title,
        "summary": chunk.summary,
        "evidence_text": chunk.content,
        "source_path": chunk.locator.original_path or chunk.locator.path,
        "start_time": chunk.metadata.get("start_time", ""),
        "end_time": chunk.metadata.get("end_time", ""),
        "page_or_slide": chunk.locator.page or record.get("slide_index") or record.get("chunk_index") or "",
        "image_paths": ";".join(chunk.locator.keyframe_paths),
        "review_status": chunk.review_status,
        "teaching_value": chunk.score.teaching_value,
        "relevance": chunk.score.relevance,
        "confidence": chunk.score.confidence,
        "evidence_role": role,
        "quality_gate_status": gate_status,
        "quality_gate_reason": gate_reason,
        "recommended_usage": record.get("recommended_usage", ""),
        "tags": ";".join(chunk.keywords),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    return row


def to_evidence_chunk(record: dict[str, Any]):
    source_type = str(record.get("_source_type", ""))
    if source_type == "video":
        return video_segment_to_evidence_chunk(record)
    if source_type == "audio":
        adapted = dict(record)
        adapted["segment_id"] = adapted.get("audio_segment_id")
        adapted["clip_summary"] = adapted.get("segment_summary")
        adapted["source_video"] = adapted.get("source_audio")
        adapted["keyframe_paths"] = ""
        return video_segment_to_evidence_chunk(adapted)
    if source_type in {"reference_text", "structured"}:
        adapted = dict(record)
        adapted["segment_id"] = (
            adapted.get("reference_text_id")
            or adapted.get("structured_asset_id")
            or adapted.get("assessment_id")
            or adapted.get("table_asset_id")
            or adapted.get("asset_unit_id")
            or adapted.get("chunk_id")
        )
        return document_segment_to_evidence_chunk(adapted)
    return document_segment_to_evidence_chunk(record)


def manual_knowledge_override(record: dict[str, Any]) -> str:
    for key in (
        "clip_id",
        "segment_id",
        "ppt_asset_id",
        "reference_text_id",
        "structured_asset_id",
        "audio_segment_id",
        "asset_unit_id",
        "chunk_id",
    ):
        value = text_value(record.get(key))
        if value in MANUAL_KNOWLEDGE_OVERRIDES:
            return MANUAL_KNOWLEDGE_OVERRIDES[value]
    return ""


def map_knowledge_point(record: dict[str, Any], title: str, chapter: str, target_points: list[str]) -> str:
    override = manual_knowledge_override(record)
    if override:
        return override
    text = normalize_for_match(
        " ".join(
            [
                text_value(record.get("knowledge_point") or record.get("knowledge_point_cn")),
                title,
                text_value(record.get("slide_title")),
                text_value(record.get("evidence_text")),
                text_value(record.get("original_path")),
            ]
        )
    )
    aliases = {
        "打底焊": ["打底层", "打底焊", "打底"],
        "填充焊": ["填充层", "填充層", "填充焊"],
        "盖面焊": ["盖面层", "盖面層", "盖面焊", "盖面", "蓋面層", "蓋面焊", "蓋面"],
        "常见缺陷与纠正": [
            "常见缺陷",
            "焊接缺陷",
            "缺陷",
            "缺欠",
            "气孔",
            "裂纹",
            "烧穿",
            "未焊透",
            "胃汗透",
            "未熔合",
            "咬边",
            "内凹",
            "內歐",
            "焊瘤",
            "夹钨",
            "钨极烧损",
            "夹渣",
            "纠正",
        ],
        "焊前准备": ["焊前准备", "焊前", "坡口准备", "装配定位", "定位焊", "清理", "坡口"],
        "非接触引弧": ["非接触引弧", "高频引弧", "引弧"],
        "送丝操作": ["送丝操作", "断续送丝", "连续送丝", "填丝", "送丝"],
        "收弧操作": ["收弧操作", "收弧", "弧坑", "熄弧"],
        "基本原理": ["基本原理", "原理", "特点", "适用范围", "应用"],
        "设备组成": ["设备组成", "焊机", "焊枪", "喷嘴", "氩气瓶", "电源", "钨极伸出长度"],
        "基础安全": ["安全", "防护", "危险", "高温", "辐射", "烟尘", "有害"],
        "个人防护": ["个人防护", "面罩", "手套", "工作服", "护目", "防护用品"],
        "焊机与电源": ["焊机", "电源", "弧焊电源", "电压", "电流", "外特性", "铭牌"],
        "焊接辅助设备": ["辅助设备", "变位机", "回转台", "工装", "夹具"],
        "气瓶与用气安全": ["气瓶", "氧气瓶", "乙炔瓶", "氩气瓶", "减压器", "气体", "供气", "用气"],
        "用电安全": ["用电", "触电", "接地", "漏电", "电缆", "绝缘"],
        "作业环境": ["作业环境", "通风", "场地", "现场", "环境"],
        "章节概述": ["概述", "简介", "任务", "学习目标", "模块"],
        "焊条与药皮": ["焊条", "药皮", "焊芯", "焊条型号", "焊条牌号", "酸性焊条", "碱性焊条"],
        "焊接电弧": ["焊接电弧", "电弧", "弧长", "熔滴", "熔池"],
        "设备与工具": ["设备", "工具", "焊钳", "面罩", "焊接电缆", "清渣锤", "钢丝刷"],
        "引弧": ["引弧", "划擦", "直击", "起弧"],
        "运条": ["运条", "摆动", "焊条角度", "焊接速度", "电弧长度"],
        "接头与收弧": ["接头", "收弧", "弧坑", "续弧", "熄弧"],
        "平焊操作": ["平焊", "平对接", "平角焊", "平位"],
        "焊接姿势": ["姿势", "站位", "握枪", "握钳", "角度"],
        "接头": ["接头", "接续", "搭接"],
        "收弧": ["收弧", "弧坑", "熄弧"],
        "坡口与装配": ["坡口", "装配", "定位", "间隙", "钝边", "错边"],
        "焊缝尺寸": ["焊缝尺寸", "余高", "焊脚", "焊缝宽度", "背面余高"],
        "缺陷识别": ["缺陷", "气孔", "裂纹", "夹渣", "咬边", "未焊透", "未熔合", "焊瘤"],
        "缺陷原因": ["原因", "产生原因", "成因"],
        "缺陷纠正": ["纠正", "防止措施", "改进", "处理"],
        "质量检验": ["质量检验", "检验", "外观检查", "无损检测", "评定"],
        "返修与预防": ["返修", "预防", "补焊"],
        "实训任务": ["实训", "训练", "任务", "项目"],
        "操作步骤": ["操作步骤", "步骤", "流程", "工艺过程"],
        "评分标准": ["评分", "评分标准", "考核标准", "分值", "评分表"],
        "题库练习": ["题库", "练习", "试题", "选择题", "判断题"],
        "综合考核": ["综合考核", "考核", "鉴定", "评价"],
    }
    priority = [point for point in aliases if point in target_points] + [
        point for point in target_points if point not in aliases
    ]
    best_point = ""
    best_score = 0
    best_index = len(priority)
    for point_index, point in enumerate(priority):
        point_text = normalize_for_match(point)
        candidates = [point_text, *[normalize_for_match(item) for item in aliases.get(point, [])]]
        score = 0
        for candidate in candidates:
            if not candidate:
                continue
            count = text.count(candidate)
            if candidate == point_text and count:
                score += count * 2
            else:
                score += count
        if score > best_score or (score == best_score and score > 0 and point_index < best_index):
            best_point = point
            best_score = score
            best_index = point_index
    if best_point:
        return best_point
    raw = text_value(record.get("knowledge_point") or title)
    if any(term in normalize_for_match(raw) for term in ["钨极", "氩弧", "tig"]) and "基本原理" in target_points:
        return "基本原理"
    if raw in target_points:
        return raw
    if target_points and record_matches_chapter(record, chapter, target_points):
        return target_points[0]
    return "待归入知识点"


def classify_quality(record: dict[str, Any], source_type: str) -> tuple[str, str, str]:
    status = text_value(record.get("review_status")).lower()
    evidence = text_value(record.get("evidence_text") or record.get("transcript_text") or record.get("slide_text"))
    score = numeric(record.get("quality_score") or record.get("usefulness_score"), 0.0)
    noise = text_noise_score(evidence)
    has_images = bool(text_value(record.get("image_paths") or record.get("keyframe_paths")))

    if "rejected" in status:
        return "hold", "reject", "review_status rejected"
    if source_type == "video" and "manual_timecode" in status:
        return "support", "weak", "video timecode needs manual confirmation"
    if source_type == "reference_text":
        if noise > 0.35:
            return "reference", "weak", "reference text has extraction noise"
        return "reference", "pass", "reference text kept as explanation/source support"
    if source_type == "ppt":
        if not evidence.strip() and not has_images:
            return "hold", "hold", "ppt page has no usable text or image"
        if noise > 0.35:
            return "reference", "weak", "ppt text appears noisy"
        return "support", "pass", "ppt page usable as structure or visual support"
    if source_type == "structured":
        return "reference", "pass" if "agent_keep" in status else "weak", "structured data should support exercises or assessment"
    if source_type == "audio":
        if noise > 0.35:
            return "reference", "weak", "audio transcript appears noisy"
        return "support", "pass", "audio transcript usable after review"
    if source_type == "video" and "pending_agent_review" in status:
        if noise > 0.35:
            return "support", "weak", "video transcript appears noisy; use with caution"
        return "primary", "weak", "agent-review pending video can be used as weak primary evidence for draft"
    if "agent_keep" in status or score >= 0.75:
        if noise > 0.35:
            return "support", "weak", "asr text appears noisy"
        return "primary", "pass", "agent kept or high score"
    if "pending_agent_review" in status:
        return "support", "weak", "pending agent review; use as support only"
    return "reference", "weak", "not enough review signal for primary evidence"


def text_noise_score(text: str) -> float:
    if not text:
        return 1.0
    sample = text[:1000]
    suspicious = len(re.findall(r"[�□]|nan|鍦|鐒|姘|绗|鈥|€", sample, flags=re.IGNORECASE))
    punctuation = len(re.findall(r"[?？]{2,}", sample))
    return min(1.0, (suspicious + punctuation) / max(20, len(sample) / 20))


def build_readiness_rows(pack_rows: list[dict[str, Any]], target_points: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pack_rows:
        grouped[row["knowledge_point"]].append(row)

    rows = []
    for point in target_points:
        items = grouped.get(point, [])
        source_counts = Counter(row["source_type"] for row in items)
        gate_counts = Counter(row["quality_gate_status"] for row in items)
        role_counts = Counter(row["evidence_role"] for row in items)
        primary_video_count = sum(1 for row in items if row["source_type"] == "video" and row["evidence_role"] == "primary")
        usable_count = sum(1 for row in items if row["quality_gate_status"] in {"pass", "weak"})
        status, reason = readiness_status(
            video_count=source_counts["video"],
            primary_video_count=primary_video_count,
            ppt_count=source_counts["ppt"],
            reference_count=source_counts["reference_text"],
            usable_count=usable_count,
        )
        rows.append(
            {
                "knowledge_point": point,
                "total_evidence_count": len(items),
                "video_segment_count": source_counts["video"],
                "primary_video_count": primary_video_count,
                "ppt_page_count": source_counts["ppt"],
                "ppt_image_count": sum(1 for row in items if row["source_type"] == "ppt" and row["image_paths"]),
                "reference_text_count": source_counts["reference_text"],
                "structured_asset_count": source_counts["structured"],
                "audio_segment_count": source_counts["audio"],
                "agent_keep_count": sum(1 for row in items if str(row["review_status"]).lower() == "agent_keep"),
                "pending_review_count": sum(1 for row in items if "pending" in str(row["review_status"]).lower()),
                "manual_timecode_count": sum(1 for row in items if "manual_timecode" in str(row["review_status"]).lower()),
                "summary_needs_source_review_count": sum(
                    1 for row in items if str(row["review_status"]) == "Summary_Needs_Source_Review"
                ),
                "pass_count": gate_counts["pass"],
                "weak_count": gate_counts["weak"],
                "hold_count": gate_counts["hold"],
                "primary_count": role_counts["primary"],
                "support_count": role_counts["support"],
                "reference_count": role_counts["reference"],
                "readiness_status": status,
                "readiness_reason": reason,
                "recommended_action": recommended_action(status),
            }
        )
    return rows


def readiness_status(
    *,
    video_count: int,
    primary_video_count: int,
    ppt_count: int,
    reference_count: int,
    usable_count: int,
) -> tuple[str, str]:
    source_type_count = sum(1 for count in (video_count, ppt_count, reference_count) if count > 0)
    has_strong_source = primary_video_count > 0 or ppt_count >= 3 or reference_count >= 3
    if usable_count >= 8 and source_type_count >= 1:
        return "ready", "has enough usable evidence; video, PPT, and reference text are not all mandatory"
    if usable_count >= 5 and has_strong_source:
        return "ready", "has a usable source set for drafting with available materials"
    if usable_count >= 2:
        return "partial_ready", "has some usable evidence; draft only with explicit evidence limitations"
    if usable_count >= 1:
        return "partial_ready", "has sparse evidence; use only for a short or cautious section"
    return "not_ready", "no usable evidence for expanded textbook writing"


def recommended_action(status: str) -> str:
    if status == "ready":
        return "generate expanded chapter section"
    if status == "partial_ready":
        return "generate draft with explicit evidence gaps"
    return "do not expand yet; add or clean evidence first"


def build_summary(
    chapter: str,
    all_records: list[dict[str, Any]],
    pack_rows: list[dict[str, Any]],
    readiness_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "chapter": chapter,
        "source_records_total": len(all_records),
        "selected_records": len(pack_rows),
        "source_type_counts": dict(Counter(row["source_type"] for row in pack_rows)),
        "quality_gate_counts": dict(Counter(row["quality_gate_status"] for row in pack_rows)),
        "evidence_role_counts": dict(Counter(row["evidence_role"] for row in pack_rows)),
        "readiness_counts": dict(Counter(row["readiness_status"] for row in readiness_rows)),
        "ready_knowledge_points": [
            row["knowledge_point"] for row in readiness_rows if row["readiness_status"] == "ready"
        ],
        "partial_ready_knowledge_points": [
            row["knowledge_point"] for row in readiness_rows if row["readiness_status"] == "partial_ready"
        ],
        "not_ready_knowledge_points": [
            row["knowledge_point"] for row in readiness_rows if row["readiness_status"] == "not_ready"
        ],
    }


def build_gap_rows(
    chapter: str,
    pack_rows: list[dict[str, Any]],
    readiness_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = datetime.now().isoformat(timespec="seconds")
    for row in readiness_rows:
        if row["readiness_status"] == "ready":
            continue
        gaps: list[tuple[str, str]] = []
        usable_count = int(row.get("pass_count", 0)) + int(row.get("weak_count", 0))
        if usable_count == 0:
            gaps.append(("no_usable_evidence", "没有可用证据，不能进入教材正文生成"))
        elif usable_count < 5:
            gaps.append(("thin_usable_evidence", f"只有 {usable_count} 条可用证据，只适合短段落或谨慎生成"))
        else:
            gaps.append(("needs_evidence_review", "证据可用但仍需复核质量或章节归属"))
        for gap_type, description in gaps:
            rows.append(
                {
                    "chapter": chapter,
                    "knowledge_point": row["knowledge_point"],
                    "gap_type": gap_type,
                    "gap_description": description,
                    "found_by": "chapter_readiness_report",
                    "assigned_to": "角色A-数据清洗与素材库",
                    "status": "open",
                    "resolution": "",
                    "updated_at": now,
                }
            )

    unknown_count = sum(1 for row in pack_rows if row.get("knowledge_point") == "待归入知识点")
    if unknown_count:
        rows.append(
            {
                "chapter": chapter,
                "knowledge_point": "待归入知识点",
                "gap_type": "needs_manual_classification",
                "gap_description": f"仍有 {unknown_count} 条素材未归入目标知识点，需要人工复核或扩充映射规则。",
                "found_by": "chapter_evidence_pack",
                "assigned_to": "角色A-数据清洗与素材库",
                "status": "open",
                "resolution": "",
                "updated_at": now,
            }
        )
    return rows


def pack_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    role_rank = {"primary": 0, "support": 1, "reference": 2, "hold": 3}
    gate_rank = {"pass": 0, "weak": 1, "hold": 2, "reject": 3}
    return role_rank.get(row["evidence_role"], 9), gate_rank.get(row["quality_gate_status"], 9), row["chunk_id"]


def slugify(value: str) -> str:
    text = normalize_for_match(value)
    if "钨极" in text or "氩弧" in text:
        return "tig_welding"
    slug = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", value).strip("_")
    return slug or "chapter"


def normalize_for_match(value: Any) -> str:
    return re.sub(r"\s+", "", text_value(value).lower())


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def numeric(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
