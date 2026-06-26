#!/usr/bin/env python
"""Backfill material-block mappings for weak whole-book chapters.

This script records the agent-confirmed mappings needed to continue the
materials-to-textbook MVP. It does not copy or modify raw client materials.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MATERIAL_ROOT = default_work_root()
MANIFEST_DIR = MATERIAL_ROOT / "01_manifest_inventory"


BLOCK_ROWS = [
    {
        "material_block_code": "welding_defects_quality",
        "material_block_cn": "焊接缺陷与质量检验",
        "subject_cn": "焊接技术",
        "subject_code": "welding_technology",
        "include_scope": "常见缺陷、焊后检查、焊缝清理、质量检验、外观检查、返修与预防",
        "exclude_scope": "单纯操作演示、与质量检验无关的设备介绍",
        "scope_note": "用于教材中缺陷识别、原因分析、质量检查和返修预防内容。",
    },
    {
        "material_block_code": "welding_training_assessment",
        "material_block_cn": "综合实训与考核",
        "subject_cn": "焊接技术",
        "subject_code": "welding_technology",
        "include_scope": "实训任务、操作步骤、评分标准、鉴定表、题库练习和综合评价",
        "exclude_scope": "不直接承担某一焊法的完整知识讲解",
        "scope_note": "用于教材中的实训项目、考核要求、练习和评价材料。",
    },
]


MAP_OVERRIDES = [
    {
        "asset_id": "A000032",
        "material_block_code": "welding_defects_quality",
        "material_block_cn": "焊接缺陷与质量检验",
        "subject_cn": "焊接技术",
        "subject_code": "welding_technology",
        "knowledge_point_cn": "缺陷识别",
        "knowledge_point_code": "defect_identification",
        "relation_type": "primary",
        "confidence": 0.82,
        "scope_note": "agent-confirmed from filename: 常见缺陷.mp4",
        "needs_confirmation": False,
    },
    {
        "asset_id": "A000036",
        "material_block_code": "welding_defects_quality",
        "material_block_cn": "焊接缺陷与质量检验",
        "subject_cn": "焊接技术",
        "subject_code": "welding_technology",
        "knowledge_point_cn": "质量检验",
        "knowledge_point_code": "quality_inspection",
        "relation_type": "primary",
        "confidence": 0.8,
        "scope_note": "agent-confirmed from filename: 焊缝清理焊后检查.flv",
        "needs_confirmation": False,
    },
    {
        "asset_id": "A000002",
        "material_block_code": "welding_training_assessment",
        "material_block_cn": "综合实训与考核",
        "subject_cn": "焊接技术",
        "subject_code": "welding_technology",
        "knowledge_point_cn": "评分标准",
        "knowledge_point_code": "assessment_rubric",
        "relation_type": "primary",
        "confidence": 0.86,
        "scope_note": "agent-confirmed from table type: 初级焊接鉴定1-12.xlsx",
        "needs_confirmation": False,
    },
    {
        "asset_id": "A000003",
        "material_block_code": "welding_training_assessment",
        "material_block_cn": "综合实训与考核",
        "subject_cn": "焊接技术",
        "subject_code": "welding_technology",
        "knowledge_point_cn": "综合考核",
        "knowledge_point_code": "comprehensive_assessment",
        "relation_type": "primary",
        "confidence": 0.86,
        "scope_note": "agent-confirmed from table type: 初级焊接鉴定13-24.xlsx",
        "needs_confirmation": False,
    },
]


def main() -> int:
    manifest_path = MANIFEST_DIR / "assets_manifest.xlsx"
    blocks_path = MANIFEST_DIR / "material_blocks.xlsx"
    map_path = MANIFEST_DIR / "asset_block_map.xlsx"
    report_path = MANIFEST_DIR / "weak_chapter_backfill_map_report.xlsx"

    manifest = pd.read_excel(manifest_path)
    blocks = pd.read_excel(blocks_path)
    mapping = pd.read_excel(map_path)

    blocks = upsert_blocks(blocks)
    mapping, report = upsert_mappings(mapping, manifest)

    blocks.to_excel(blocks_path, index=False, engine="openpyxl")
    mapping.to_excel(map_path, index=False, engine="openpyxl")
    pd.DataFrame(report).to_excel(report_path, index=False, engine="openpyxl")

    print("Weak chapter mappings backfilled:")
    print(f"  material_blocks: {blocks_path}")
    print(f"  asset_block_map: {map_path}")
    print(f"  report: {report_path}")
    print(f"  rows_added_or_updated: {len(report)}")
    return 0


def upsert_blocks(blocks: pd.DataFrame) -> pd.DataFrame:
    rows = blocks.to_dict("records")
    by_code = {str(row["material_block_code"]): row for row in rows}
    for row in BLOCK_ROWS:
        by_code[row["material_block_code"]] = {**by_code.get(row["material_block_code"], {}), **row}
    ordered = []
    seen = set()
    for row in rows:
        code = str(row["material_block_code"])
        ordered.append(by_code[code])
        seen.add(code)
    for row in BLOCK_ROWS:
        if row["material_block_code"] not in seen:
            ordered.append(by_code[row["material_block_code"]])
    return pd.DataFrame(ordered)


def upsert_mappings(mapping: pd.DataFrame, manifest: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    manifest_by_id = manifest.set_index("asset_id").to_dict("index")
    rows = mapping.to_dict("records")
    report = []
    now = datetime.now().isoformat(timespec="seconds")
    for override in MAP_OVERRIDES:
        asset = manifest_by_id.get(override["asset_id"])
        if not asset:
            report.append({**override, "action": "missing_asset", "generated_time": now})
            continue
        new_row = {
            "asset_id": override["asset_id"],
            "original_path": asset.get("original_path", ""),
            "filename": asset.get("filename", ""),
            **override,
            "active_for_index": True,
        }
        key = (new_row["asset_id"], new_row["material_block_code"], new_row["knowledge_point_code"])
        matched = False
        for index, row in enumerate(rows):
            current_key = (
                str(row.get("asset_id", "")),
                str(row.get("material_block_code", "")),
                str(row.get("knowledge_point_code", "")),
            )
            if current_key == key:
                rows[index] = {**row, **new_row}
                matched = True
                break
        action = "updated" if matched else "added"
        if not matched:
            rows.append(new_row)
        report.append({**new_row, "action": action, "generated_time": now})
    return pd.DataFrame(rows), report


if __name__ == "__main__":
    raise SystemExit(main())
