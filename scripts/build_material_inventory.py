#!/usr/bin/env python
"""Build the front inventory, coarse classification, block map, and indexes.

This implements the upstream part of docs/material_pipeline_forward_plan.md:

1. scan raw files without moving them;
2. write a manifest with lightweight classification evidence;
3. mark exact duplicates and active assets;
4. create coarse, reviewable classifications;
5. create a multi-block asset map and pure index layer under 04_assets_by_course;
6. create a next-processing queue for selected material blocks.

The script is deliberately rule-based. At this stage the goal is not perfect
classification; it is complete registration plus traceable coarse evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import pandas as pd


MANIFEST_SUBDIR = "01_manifest_inventory"
WORK_JSON_SUBDIR = "02_working_processing/json"
INDEX_SUBDIR = "04_assets_by_course"

VIDEO_EXTS = {".mp4", ".flv", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma"}
PPT_EXTS = {".ppt", ".pptx"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md"}
SHEET_EXTS = {".xls", ".xlsx", ".csv"}

PPT_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


@dataclass(frozen=True)
class MaterialBlock:
    code: str
    name: str
    subject: str
    subject_code: str
    include_scope: str
    exclude_scope: str
    scope_note: str


MATERIAL_BLOCKS = [
    MaterialBlock(
        "welding_assessment_exam",
        "焊接鉴定与试题",
        "焊接技术",
        "welding_technology",
        "题库、鉴定 Excel、练习和评价表",
        "通常不做视频深处理",
        "用于题库和练习，不默认作为视频片段素材。",
    ),
    MaterialBlock(
        "shielded_metal_arc_welding",
        "焊条电弧焊",
        "焊接技术",
        "welding_technology",
        "焊条、药皮、焊芯、焊条电弧焊操作",
        "通用安全可副归属到设备与安全",
        "焊条电弧焊相关概念、设备、工艺和操作。",
    ),
    MaterialBlock(
        "welding_basic_operation",
        "焊接基本操作",
        "焊接技术",
        "welding_technology",
        "坡口、运条、引弧、收弧、焊缝连接、参数选择、缺陷",
        "纯设备介绍、纯安全规范可放入设备与安全",
        "跨焊接方法的基础操作大块，允许与具体焊法多归属。",
    ),
    MaterialBlock(
        "welding_equipment_safety",
        "焊接设备与安全",
        "焊接技术",
        "welding_technology",
        "设备、电源、焊钳、电缆、面罩、防护、安全检查",
        "不把所有焊接操作都放进来",
        "设备和安全相关资料，可作为多数焊法的 secondary 块。",
    ),
    MaterialBlock(
        "tig_welding",
        "钨极氩弧焊",
        "焊接技术",
        "welding_technology",
        "非接触引弧、送丝、钨极、TIG 操作",
        "通用焊接安全、非 TIG 焊法不默认放入",
        "第一轮试点大块，保留高置信归属。",
    ),
    MaterialBlock(
        "gas_welding_and_cutting",
        "气焊与气割",
        "焊接技术",
        "welding_technology",
        "切割速度、预热火焰、焊炬角度、气割安全",
        "非气焊气割资料不默认放入",
        "气焊、气割及相关安全操作。",
    ),
    MaterialBlock(
        "mechanical_drawing_projection",
        "机械制图-投影法",
        "机械制图",
        "mechanical_drawing",
        "中心投影、正投影、斜投影",
        "不默认进入焊接教材检索",
        "非焊接主体资料，默认只索引不深处理。",
    ),
    MaterialBlock(
        "engineering_material_testing",
        "工程材料-性能测试",
        "工程材料",
        "engineering_materials",
        "布氏硬度、洛氏硬度、冲击韧性",
        "不默认进入焊接教材检索",
        "材料性能测试资料，默认只索引不深处理。",
    ),
    MaterialBlock(
        "textbook_reference",
        "教材参考资料",
        "教材参考",
        "textbook_reference",
        "PDF 教材、综合参考资料、课程标准",
        "不直接当视频片段素材",
        "用于纲目、术语和定义参考。",
    ),
]

BLOCK_BY_CODE = {block.code: block for block in MATERIAL_BLOCKS}


def clean_text(value: Any, limit: int | None = None) -> str:
    if value is None:
        text = ""
    elif isinstance(value, float) and math.isnan(value):
        text = ""
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text


def normalize_hint(value: str) -> str:
    text = Path(value).stem if "." in Path(value).name else value
    text = text.lower()
    text = re.sub(r"[_\-—–+（）()\[\]【】,，.。]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def file_type_for(ext: str) -> str:
    ext = ext.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in PPT_EXTS:
        return "ppt"
    if ext in DOC_EXTS:
        return "document"
    if ext in SHEET_EXTS:
        return "spreadsheet"
    if ext == ".swf":
        return "swf"
    return "other"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def read_pptx_preview(path: Path, limit: int = 1200) -> tuple[str, str]:
    if path.suffix.lower() != ".pptx":
        return "", ""
    texts: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            slides = sorted(
                [name for name in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)],
                key=lambda value: int(re.search(r"slide(\d+)\.xml", value).group(1)),
            )
            for slide in slides[:8]:
                root = ET.fromstring(zf.read(slide))
                slide_text = clean_text(" ".join(node.text or "" for node in root.findall(".//a:t", PPT_NS)))
                if slide_text:
                    texts.append(slide_text)
    except Exception:
        return "", ""
    title = texts[0][:80] if texts else ""
    return title, clean_text(" ".join(texts), limit)


def read_spreadsheet_preview(path: Path, limit: int = 1200) -> tuple[str, str]:
    if path.suffix.lower() not in SHEET_EXTS:
        return "", ""
    try:
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path, nrows=8)
            return "", clean_text(" ".join(map(str, df.columns)) + " " + df.to_string(index=False), limit)
        sheets = pd.read_excel(path, sheet_name=None, nrows=8)
    except Exception:
        return "", ""
    parts: list[str] = []
    sheet_names: list[str] = []
    for sheet_name, df in list(sheets.items())[:4]:
        sheet_names.append(str(sheet_name))
        parts.append(f"[{sheet_name}] {' '.join(map(str, df.columns))} {df.to_string(index=False)}")
    return ";".join(sheet_names), clean_text(" ".join(parts), limit)


def read_document_preview(path: Path, limit: int = 1200) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return "", ""
        return "", clean_text(text, limit)
    if ext == ".docx":
        try:
            import zipfile as _zipfile

            with _zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml")
            root = ET.fromstring(xml)
            texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
            return "", clean_text(" ".join(texts), limit)
        except Exception:
            return "", ""
    return "", ""


def candidate_source_files(raw_root: Path) -> list[tuple[Path, str]]:
    """Return source files and their manifest original_path values."""

    files: list[tuple[Path, str]] = []
    files.extend((path, path.relative_to(raw_root).as_posix()) for path in raw_root.rglob("*") if path.is_file())
    return sorted(files, key=lambda item: item[1].lower())


def scan_raw(raw_root: Path, compute_hash: bool = True) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    files = candidate_source_files(raw_root)
    for index, (path, rel) in enumerate(files, start=1):
        parts = Path(rel).parts
        directory_path = "/".join(parts[:-1])
        source_week = next((part for part in parts if re.search(r"第.+周", part)), "")
        source_folder = parts[0] if len(parts) > 1 else ""
        ext = path.suffix.lower()
        file_type = file_type_for(ext)
        title = ""
        ppt_preview = ""
        doc_preview = ""
        if file_type == "ppt":
            title, ppt_preview = read_pptx_preview(path)
        elif file_type == "spreadsheet":
            title, doc_preview = read_spreadsheet_preview(path)
        elif file_type == "document":
            title, doc_preview = read_document_preview(path)
        duration = ffprobe_duration(path) if file_type in {"video", "audio"} else None
        filename_hint = normalize_hint(path.name)
        evidence_parts = [
            directory_path,
            filename_hint,
            title,
            ppt_preview,
            doc_preview,
        ]
        classification_evidence = clean_text(" | ".join(part for part in evidence_parts if clean_text(part)), 2000)
        rows.append(
            {
                "asset_id": f"A{index:06d}",
                "original_path": rel,
                "absolute_path": str(path.resolve()),
                "filename": path.name,
                "extension": ext,
                "file_type": file_type,
                "size_bytes": path.stat().st_size,
                "modified_time": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                "file_hash": sha256_file(path) if compute_hash else "",
                "directory_path": directory_path,
                "source_week": source_week,
                "source_folder": source_folder,
                "filename_hint": filename_hint,
                "normalized_filename": filename_hint,
                "file_title_if_readable": title,
                "ppt_text_preview": ppt_preview,
                "document_text_preview": doc_preview,
                "duration_seconds": duration,
                "duration_or_pages": duration,
                "own_classification_evidence": classification_evidence,
                "classification_evidence": classification_evidence,
                "directory_context_evidence": clean_text(f"{directory_path} | {filename_hint}", 1000),
            }
        )
    return pd.DataFrame(rows)


def classify_asset(row: pd.Series) -> dict[str, Any]:
    text = clean_text(
        " ".join(
            [
                row.get("filename", ""),
                row.get("directory_path", ""),
                row.get("filename_hint", ""),
                row.get("file_title_if_readable", ""),
                row.get("ppt_text_preview", ""),
                row.get("document_text_preview", ""),
            ]
        )
    ).lower()
    file_type = clean_text(row.get("file_type"))

    def hit(*terms: str) -> bool:
        return any(term.lower() in text for term in terms)

    if file_type == "spreadsheet" and hit("鉴定", "题库", "试题", "考试", "评分", "答案"):
        return classification(row, "焊接技术", "welding_technology", "焊接鉴定与试题", "焊接鉴定与试题", "exact", "Excel/表格题库或鉴定资料", 0.90)
    if hit("钨极", "氩弧", "tig", "tungsten", "非接触引弧", "送丝", "收弧"):
        return classification(row, "焊接技术", "welding_technology", "钨极氩弧焊", "钨极氩弧焊", "exact", "命中钨极氩弧焊/TIG 关键词", 0.90)
    if hit("焊条电弧焊", "焊条", "药皮", "焊芯", "手弧焊"):
        return classification(row, "焊接技术", "welding_technology", "焊条电弧焊", "焊条电弧焊", "exact", "命中焊条电弧焊关键词", 0.88)
    if hit("坡口", "运条", "引弧", "收弧", "焊缝", "熔池", "焊接操作", "定位焊", "打底层", "填充层", "盖面层"):
        return classification(row, "焊接技术", "welding_technology", "焊接基本操作", "焊接基本操作", "coarse", "命中焊接基础操作关键词", 0.72)
    if hit("焊机", "电源", "焊钳", "电缆", "面罩", "安全", "防护", "检查"):
        return classification(row, "焊接技术", "welding_technology", "焊接设备与安全", "焊接设备与安全", "coarse", "命中设备/安全关键词", 0.72)
    if hit("气焊", "气割", "割炬", "火焰", "乙炔", "氧气"):
        return classification(row, "焊接技术", "welding_technology", "气焊与气割", "气焊与气割", "exact", "命中气焊/气割关键词", 0.86)
    if hit("投影", "三视图", "机械制图"):
        return classification(row, "机械制图", "mechanical_drawing", "机械制图-投影法", "机械制图-投影法", "coarse", "命中机械制图关键词", 0.78)
    if hit("硬度", "布氏", "洛氏", "冲击韧性", "材料性能"):
        return classification(row, "工程材料", "engineering_materials", "工程材料-性能测试", "工程材料-性能测试", "coarse", "命中工程材料/性能测试关键词", 0.78)
    if file_type == "document" and hit("教材", "标准", "培训"):
        return classification(row, "教材参考", "textbook_reference", "教材参考资料", "教材参考资料", "exact", "综合教材或参考资料", 0.85)
    return classification(row, "待确认", "unconfirmed", clean_text(row.get("filename_hint")) or "待确认", "待确认", "fallback", "仅按文件名/目录登记，需确认", 0.30)


def classification(
    row: pd.Series,
    subject_cn: str,
    subject_code: str,
    kp_cn: str,
    kp_code: str,
    status: str,
    basis: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "asset_id": row["asset_id"],
        "original_path": row["original_path"],
        "filename": row["filename"],
        "file_type": row["file_type"],
        "subject_cn": subject_cn,
        "subject_code": subject_code,
        "knowledge_point_cn": kp_cn,
        "knowledge_point_code": kp_code,
        "classification_status": status,
        "classification_basis": basis,
        "classification_confidence": confidence,
        "needs_confirmation": status in {"uncertain", "fallback"} or confidence < 0.65,
        "uncertain_reason": "" if status not in {"uncertain", "fallback"} else "证据不足，不能作为可靠教材检索分类",
        "classification_evidence": clean_text(row.get("classification_evidence"), 2000),
    }


def build_active_assets(manifest: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    hash_counts = manifest["file_hash"].fillna("").value_counts().to_dict()
    preferred_by_hash: dict[str, str] = {}
    for file_hash, group in manifest.groupby("file_hash", dropna=False):
        if not file_hash:
            continue
        sorted_group = group.sort_values(["size_bytes", "asset_id"], ascending=[False, True])
        preferred_by_hash[str(file_hash)] = str(sorted_group.iloc[0]["asset_id"])

    active_rows: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    for _, row in manifest.iterrows():
        file_hash = clean_text(row.get("file_hash"))
        is_dup = bool(file_hash and hash_counts.get(file_hash, 0) > 1)
        preferred = preferred_by_hash.get(file_hash, row["asset_id"])
        group_id = f"DUP_{file_hash[:12]}" if is_dup else ""
        active = not is_dup or preferred == row["asset_id"]
        active_rows.append(
            {
                "asset_id": row["asset_id"],
                "original_path": row["original_path"],
                "duplicate_group_id": group_id,
                "is_exact_duplicate": is_dup and preferred != row["asset_id"],
                "preferred_asset_id": preferred,
                "active_for_index": active,
                "active_for_processing": active,
                "reason": "preferred asset" if active else f"exact duplicate of {preferred}",
            }
        )
        if is_dup:
            duplicate_rows.append(
                {
                    "duplicate_group_id": group_id,
                    "asset_id": row["asset_id"],
                    "original_path": row["original_path"],
                    "file_hash": file_hash,
                    "preferred_asset_id": preferred,
                    "duplicate_type": "exact_duplicate",
                }
            )
    return pd.DataFrame(active_rows), pd.DataFrame(duplicate_rows)


def block_rows_for(class_row: pd.Series, active_for_index: bool) -> list[dict[str, Any]]:
    evidence = clean_text(class_row.get("classification_evidence")).lower()
    kp = clean_text(class_row.get("knowledge_point_cn"))
    status = clean_text(class_row.get("classification_status"))
    confidence = float(class_row.get("classification_confidence") or 0)

    rows: list[dict[str, Any]] = []

    def add(code: str, relation: str, conf: float, note: str) -> None:
        block = BLOCK_BY_CODE[code]
        rows.append(
            {
                "asset_id": class_row["asset_id"],
                "original_path": class_row["original_path"],
                "filename": class_row["filename"],
                "material_block_code": block.code,
                "material_block_cn": block.name,
                "subject_cn": block.subject,
                "subject_code": block.subject_code,
                "knowledge_point_cn": kp,
                "knowledge_point_code": clean_text(class_row.get("knowledge_point_code")),
                "relation_type": relation,
                "confidence": round(conf, 2),
                "scope_note": note,
                "active_for_index": active_for_index,
                "needs_confirmation": bool(class_row.get("needs_confirmation")),
            }
        )

    primary_map = {
        "焊接鉴定与试题": "welding_assessment_exam",
        "焊条电弧焊": "shielded_metal_arc_welding",
        "焊接基本操作": "welding_basic_operation",
        "焊接设备与安全": "welding_equipment_safety",
        "钨极氩弧焊": "tig_welding",
        "气焊与气割": "gas_welding_and_cutting",
        "机械制图-投影法": "mechanical_drawing_projection",
        "工程材料-性能测试": "engineering_material_testing",
        "教材参考资料": "textbook_reference",
    }
    primary_code = primary_map.get(kp)
    if primary_code:
        add(primary_code, "primary" if status != "fallback" else "candidate", confidence, clean_text(class_row.get("classification_basis")))

    if any(term in evidence for term in ["安全", "防护", "检查", "面罩", "电缆", "焊机"]):
        if primary_code != "welding_equipment_safety":
            add("welding_equipment_safety", "secondary", min(confidence, 0.68), "证据中包含设备/安全相关内容，允许副归属")
    if any(term in evidence for term in ["引弧", "收弧", "送丝", "坡口", "焊缝", "熔池", "定位焊", "操作"]):
        if primary_code != "welding_basic_operation":
            add("welding_basic_operation", "secondary", min(confidence, 0.68), "证据中包含通用焊接操作内容，允许副归属")
    if not rows:
        add("textbook_reference", "candidate", 0.30, "无法可靠归入具体大块，暂挂参考资料并待确认")
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_excel(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False, engine="openpyxl")


def safe_dir_name(cn: str, code: str) -> str:
    safe_cn = re.sub(r'[<>:"/\\|?*\s]+', "_", cn).strip("_")
    return f"{safe_cn}_{code}"


def rebuild_index_layer(material_root: Path, asset_block_map: pd.DataFrame) -> None:
    index_root = material_root / INDEX_SUBDIR
    index_root.mkdir(parents=True, exist_ok=True)
    active = asset_block_map[asset_block_map["active_for_index"].eq(True)].copy()
    relation_priority = {"primary": 0, "secondary": 1, "candidate": 2, "exclude": 9}
    active["_relation_priority"] = active["relation_type"].map(relation_priority).fillna(8)
    for (subject_cn, subject_code, block_cn, block_code, kp), group in active.groupby(
        ["subject_cn", "subject_code", "material_block_cn", "material_block_code", "knowledge_point_cn"],
        dropna=False,
    ):
        out_dir = index_root / safe_dir_name(subject_cn, subject_code) / safe_dir_name(block_cn, block_code) / safe_dir_name(kp, clean_text(kp) or "unconfirmed")
        out_dir.mkdir(parents=True, exist_ok=True)
        index_cols = [
            "asset_id",
            "filename",
            "original_path",
            "material_block_code",
            "material_block_cn",
            "knowledge_point_cn",
            "relation_type",
            "confidence",
            "scope_note",
            "needs_confirmation",
        ]
        sorted_group = group.sort_values(["_relation_priority", "confidence", "asset_id"], ascending=[True, False, True])
        write_excel(sorted_group[index_cols], out_dir / "assets_index.xlsx")
        cards = group[index_cols].to_dict("records")
        write_jsonl(out_dir / "asset_cards.jsonl", cards)


def processed_source_assets(material_root: Path, file_kind: str) -> dict[str, str]:
    json_dir = material_root / WORK_JSON_SUBDIR
    if file_kind == "video":
        paths = [(json_dir / "video_segments.jsonl", "Processed_Main")]
        paths.extend((path, "Processed_Batch") for path in (json_dir / "batches").glob("*_video_segments_*.jsonl"))
    elif file_kind == "ppt":
        paths = [(json_dir / "ppt_assets.jsonl", "Processed_Main")]
        paths.extend((path, "Processed_Batch") for path in (json_dir / "batches").glob("*_ppt_assets_*.jsonl"))
    elif file_kind == "audio":
        paths = [(json_dir / "audio_segments.jsonl", "Processed_Main")]
        paths.extend((path, "Processed_Batch") for path in (json_dir / "batches").glob("*_audio_segments_*.jsonl"))
    elif file_kind == "structured":
        paths = [(json_dir / "structured_assets.jsonl", "Processed_Main")]
        paths.extend((path, "Processed_Batch") for path in (json_dir / "batches").glob("*_structured_assets_*.jsonl"))
    elif file_kind == "reference":
        paths = [(json_dir / "reference_text_assets.jsonl", "Processed_Main")]
        paths.extend((path, "Processed_Batch") for path in (json_dir / "batches").glob("*_reference_text_assets_*.jsonl"))
    else:
        return {}
    result: dict[str, str] = {}
    for path, status in paths:
        for row in read_jsonl(path):
            asset_id = clean_text(row.get("source_asset_id"))
            if asset_id and asset_id not in result:
                result[asset_id] = status
    return result


def build_processing_queue(material_root: Path, asset_block_map: pd.DataFrame, manifest: pd.DataFrame, active_assets: pd.DataFrame) -> pd.DataFrame:
    merged = (
        asset_block_map.merge(manifest, on=["asset_id", "original_path", "filename"], how="left", suffixes=("", "_manifest"))
        .merge(active_assets[["asset_id", "active_for_processing"]], on="asset_id", how="left")
    )
    allowed_blocks = {
        "shielded_metal_arc_welding",
        "welding_basic_operation",
        "welding_equipment_safety",
        "tig_welding",
        "gas_welding_and_cutting",
        "textbook_reference",
    }
    rows: list[dict[str, Any]] = []
    processed_video = processed_source_assets(material_root, "video")
    processed_ppt = processed_source_assets(material_root, "ppt")
    processed_audio = processed_source_assets(material_root, "audio")
    processed_structured = processed_source_assets(material_root, "structured")
    processed_reference = processed_source_assets(material_root, "reference")
    for _, row in merged.iterrows():
        file_type = clean_text(row.get("file_type"))
        block_code = clean_text(row.get("material_block_code"))
        if block_code not in allowed_blocks:
            continue
        if not bool(row.get("active_for_processing")):
            continue
        processed_status = ""
        if file_type == "video":
            processed_status = processed_video.get(clean_text(row.get("asset_id")), "")
        elif file_type == "ppt":
            processed_status = processed_ppt.get(clean_text(row.get("asset_id")), "")
        elif file_type == "audio":
            processed_status = processed_audio.get(clean_text(row.get("asset_id")), "")
        elif file_type in {"document", "spreadsheet"}:
            if file_type == "document":
                processed_status = processed_reference.get(clean_text(row.get("asset_id")), "")
            else:
                processed_status = processed_structured.get(clean_text(row.get("asset_id")), "")

        if processed_status:
            status = processed_status
            action = "already_processed"
            route = "already has generated evidence in main JSONL or batch JSONL"
            reason = "skip by default; review/merge existing output before reprocessing"
        elif bool(row.get("needs_confirmation")) and clean_text(row.get("relation_type")) == "candidate":
            status = "Needs_Confirmation"
            action = "confirm_before_processing"
            route = "manual_or_agent_confirm"
            reason = "candidate/low-confidence block relation"
        elif file_type == "video":
            status = "Queued"
            action = "process_video_mvp"
            route = "video_mvp: convert_mp4 -> extract_audio -> ASR -> keyframes -> candidate_segments -> validate"
            reason = "target block video; needs ASR/keyframes/segment evidence"
        elif file_type == "audio":
            status = "Queued"
            action = "process_audio_mvp"
            route = "audio_mvp: ASR -> rough time ranges -> summary/tags -> validate"
            reason = "target block audio; needs ASR evidence"
        elif file_type == "ppt":
            status = "Queued"
            action = "extract_ppt_evidence"
            route = "ppt_mvp: extract_text -> extract_images -> page evidence -> validate"
            reason = "target block PPT; needs text/image evidence"
        elif file_type in {"document", "spreadsheet"}:
            status = "Queued"
            action = "extract_structured_evidence"
            route = "document_or_table_mvp: detect type -> extract text/table evidence -> validate"
            reason = "target block reference/table; needs structured evidence"
        else:
            continue
        rows.append(
            {
                "queue_status": status,
                "asset_id": row["asset_id"],
                "filename": row["filename"],
                "file_type": file_type,
                "extension": row.get("extension", ""),
                "original_path": row["original_path"],
                "absolute_path": row.get("absolute_path", ""),
                "duration_or_pages": row.get("duration_or_pages", ""),
                "material_block_code": block_code,
                "material_block_cn": row.get("material_block_cn", ""),
                "knowledge_point_cn": row.get("knowledge_point_cn", ""),
                "relation_type": row.get("relation_type", ""),
                "confidence": row.get("confidence", ""),
                "scope_note": row.get("scope_note", ""),
                "already_processed": bool(processed_status),
                "processed_location": processed_status,
                "recommended_action": action,
                "processing_route": route,
                "processing_reason": reason,
                "generated_time": datetime.now().isoformat(timespec="seconds"),
            }
        )
    queue = pd.DataFrame(rows)
    if queue.empty:
        return queue
    priority = {"process_video_mvp": 1, "extract_ppt_evidence": 2, "process_audio_mvp": 3, "extract_structured_evidence": 4, "confirm_before_processing": 9}
    queue["_priority"] = queue["recommended_action"].map(priority).fillna(99)
    queue = queue.sort_values(["_priority", "confidence", "material_block_code", "asset_id"], ascending=[True, False, True, True]).drop(columns=["_priority"])
    return queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--material-root", type=Path, default=default_work_root())
    parser.add_argument("--raw-root", type=Path, default=default_raw_root() / "谢志怡工作整理")
    parser.add_argument("--skip-hash", action="store_true", help="Skip file hashing for a faster dry inventory.")
    parser.add_argument("--no-index-layer", action="store_true", help="Do not rebuild 04_assets_by_course.")
    args = parser.parse_args()

    material_root = args.material_root
    raw_root = args.raw_root
    manifest_dir = material_root / MANIFEST_SUBDIR
    json_dir = material_root / WORK_JSON_SUBDIR

    manifest = scan_raw(raw_root, compute_hash=not args.skip_hash)
    active_assets, duplicates = build_active_assets(manifest)
    classifications = pd.DataFrame([classify_asset(row) for _, row in manifest.iterrows()])
    active_lookup = active_assets.set_index("asset_id")["active_for_index"].to_dict()
    block_rows: list[dict[str, Any]] = []
    for _, row in classifications.iterrows():
        block_rows.extend(block_rows_for(row, bool(active_lookup.get(row["asset_id"], True))))
    asset_block_map = pd.DataFrame(block_rows)
    material_blocks = pd.DataFrame([block.__dict__ for block in MATERIAL_BLOCKS]).rename(
        columns={
            "code": "material_block_code",
            "name": "material_block_cn",
            "subject": "subject_cn",
        }
    )
    queue = build_processing_queue(material_root, asset_block_map, manifest, active_assets)

    write_excel(manifest, manifest_dir / "assets_manifest.xlsx")
    write_jsonl(json_dir / "assets_manifest.json", manifest.to_dict("records"))
    write_excel(active_assets, manifest_dir / "active_assets.xlsx")
    write_excel(duplicates, manifest_dir / "duplicate_groups.xlsx")
    write_excel(classifications, manifest_dir / "front_classification_all_materials.xlsx")
    write_excel(material_blocks, manifest_dir / "material_blocks.xlsx")
    write_excel(asset_block_map, manifest_dir / "asset_block_map.xlsx")
    write_excel(queue, manifest_dir / "next_processing_queue.xlsx")

    if not args.no_index_layer:
        rebuild_index_layer(material_root, asset_block_map)

    print("Wrote material inventory outputs:")
    print(f"  {manifest_dir / 'assets_manifest.xlsx'} rows={len(manifest)}")
    print(f"  {manifest_dir / 'active_assets.xlsx'} rows={len(active_assets)}")
    print(f"  {manifest_dir / 'front_classification_all_materials.xlsx'} rows={len(classifications)}")
    print(f"  {manifest_dir / 'asset_block_map.xlsx'} rows={len(asset_block_map)}")
    print(f"  {manifest_dir / 'next_processing_queue.xlsx'} rows={len(queue)}")
    if not args.no_index_layer:
        print(f"  {material_root / INDEX_SUBDIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
