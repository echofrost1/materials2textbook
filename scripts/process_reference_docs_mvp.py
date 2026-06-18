#!/usr/bin/env python
"""Extract reference-document text into reviewable batch assets.

MVP scope: text-layer PDFs that can be handled by pdftotext. Scanned PDFs are
reported as weak/empty evidence and should move to an OCR branch later.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from datetime import datetime
from pathlib import Path

from material_paths import default_raw_root, default_work_root
from typing import Any

import pandas as pd


PROJECT_ROOT = default_work_root()
MANIFEST_DIR = PROJECT_ROOT / "01_manifest_inventory"
WORK_DIR = PROJECT_ROOT / "02_working_processing"
JSON_DIR = WORK_DIR / "json"
BATCH_JSON_DIR = JSON_DIR / "batches"
BATCH_MANIFEST_DIR = MANIFEST_DIR / "batches"
REFERENCE_MAIN_JSONL = JSON_DIR / "reference_text_assets.jsonl"


def clean_text(value: Any, limit: int | None = None) -> str:
    if value is None:
        text = ""
    elif isinstance(value, float) and math.isnan(value):
        text = ""
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit is not None else text


def strip_excel_illegal(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", value)
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_excel_with_fallback(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.applymap(strip_excel_illegal)
    try:
        df.to_excel(path, index=False, engine="openpyxl")
        return path
    except PermissionError:
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        df.to_excel(fallback, index=False, engine="openpyxl")
        return fallback


def source_path_for(row: pd.Series) -> Path:
    absolute = clean_text(row.get("absolute_path"))
    if absolute:
        path = Path(absolute)
        if path.exists():
            return path
    return default_raw_root() / "谢志怡工作整理" / clean_text(row.get("original_path"))


def next_number(rows: list[dict[str, Any]]) -> int:
    max_num = 0
    for row in rows:
        match = re.search(r"(\d+)$", clean_text(row.get("reference_text_id")))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return max_num + 1


def run_text_extract(pdf_path: Path, txt_path: Path) -> str:
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf_path), str(txt_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1000:])
    return txt_path.read_text(encoding="utf-8", errors="ignore")


def chunk_text(text: str, max_chars: int = 1400) -> list[str]:
    paragraphs = [clean_text(part) for part in re.split(r"\n\s*\n", text) if clean_text(part)]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if not current:
            current = para
        elif len(current) + len(para) + 1 <= max_chars:
            current = f"{current}\n{para}"
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    if not chunks and clean_text(text):
        compact = clean_text(text)
        chunks = [compact[i : i + max_chars] for i in range(0, len(compact), max_chars)]
    return chunks


def load_source_rows(target_block: str, limit: int) -> pd.DataFrame:
    manifest = pd.read_excel(MANIFEST_DIR / "assets_manifest.xlsx")
    active = pd.read_excel(MANIFEST_DIR / "active_assets.xlsx")
    blocks = pd.read_excel(MANIFEST_DIR / "asset_block_map.xlsx")
    df = (
        blocks.merge(active[["asset_id", "active_for_processing"]], on="asset_id", how="left")
        .merge(manifest, on="asset_id", how="left", suffixes=("_block", ""))
    )
    df = df[
        df["material_block_code"].eq(target_block)
        & df["file_type"].eq("document")
        & df["active_for_processing"].eq(True)
    ].copy()
    processed = {row.get("source_asset_id") for row in read_jsonl(REFERENCE_MAIN_JSONL)}
    df = df[~df["asset_id"].astype(str).isin(processed)]
    relation_priority = {"primary": 0, "secondary": 1, "candidate": 2}
    df["_priority"] = df["relation_type"].map(relation_priority).fillna(8)
    df = df.sort_values(["_priority", "confidence", "asset_id"], ascending=[True, False, True]).drop(columns=["_priority"])
    return df.head(limit) if limit > 0 else df


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-block", default="textbook_reference")
    parser.add_argument("--limit-docs", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected = load_source_rows(args.target_block, args.limit_docs)
    print(f"reference_docs_selected: {len(selected)}")
    for _, row in selected.iterrows():
        print(f"  document {row['asset_id']} {row['filename']} [{row['relation_type']}]")
    if args.dry_run:
        return 0
    if selected.empty:
        return 0

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    existing = read_jsonl(REFERENCE_MAIN_JSONL)
    next_id = next_number(existing)
    rows: list[dict[str, Any]] = []
    for _, asset in selected.iterrows():
        asset_id = clean_text(asset.get("asset_id"))
        filename = clean_text(asset.get("filename"))
        source_path = source_path_for(asset)
        txt_path = WORK_DIR / "reference_text" / f"{asset_id}_{source_path.stem}.txt"
        print(f"Processing reference document {asset_id} {filename}")
        text = run_text_extract(source_path, txt_path)
        chunks = chunk_text(text)
        if not chunks:
            chunks = [""]
        for index, chunk in enumerate(chunks, start=1):
            reference_text_id = f"REF{next_id:06d}"
            next_id += 1
            rows.append(
                {
                    "reference_text_id": reference_text_id,
                    "source_asset_id": asset_id,
                    "source_file": filename,
                    "original_path": clean_text(asset.get("original_path")),
                    "material_block": clean_text(asset.get("material_block_cn")),
                    "material_block_code": clean_text(asset.get("material_block_code")),
                    "knowledge_point": clean_text(asset.get("knowledge_point_cn")) or "教材参考资料",
                    "chunk_index": index,
                    "text_length": len(chunk),
                    "extracted_text": chunk,
                    "evidence_text": clean_text(chunk, 1400),
                    "text_extract_method": "pdftotext_layout",
                    "source_text_path": txt_path.relative_to(PROJECT_ROOT).as_posix(),
                    "recommended_usage": "reference_definition_or_explanation",
                    "review_status": "Pending_Agent_Review",
                    "review_comment": "",
                    "generated_time": datetime.now().isoformat(timespec="seconds"),
                }
            )
    out_jsonl = BATCH_JSON_DIR / f"{args.target_block}_reference_text_assets_{batch_id}.jsonl"
    out_xlsx = BATCH_MANIFEST_DIR / f"{args.target_block}_reference_text_assets_{batch_id}.xlsx"
    write_jsonl(out_jsonl, rows)
    write_excel_with_fallback(pd.DataFrame(rows), out_xlsx)
    print("Done.")
    print(f"target_block={args.target_block}")
    print(f"batch_id={batch_id}")
    print(f"new_reference_text_assets={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
