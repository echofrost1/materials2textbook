from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_full_digital_textbook.py"
SCRIPTS_DIR = str(SCRIPT_PATH.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
SPEC = importlib.util.spec_from_file_location("run_full_digital_textbook", SCRIPT_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_source_records_dedupe_by_stable_ppt_identity() -> None:
    original = {"ppt_asset_id": "PPT_A1_S1", "evidence_text": "same"}
    duplicate = {"ppt_asset_id": "PPT_A1_S1", "evidence_text": "same from another path"}
    distinct = {"ppt_asset_id": "PPT_A1_S2", "evidence_text": "different"}
    records, duplicate_count = runner._dedupe_source_records([original, duplicate, distinct])
    assert records == [original, distinct]
    assert duplicate_count == 1


def test_source_records_dedupe_content_when_no_stable_id_exists() -> None:
    original = {
        "title": "same",
        "evidence_text": "content",
        "summary": "summary",
        "source_path": "ppt_assets.jsonl",
    }
    duplicate = {
        "summary": "summary",
        "evidence_text": "content",
        "title": "same",
        "source_path": "combined_document_segments.jsonl",
    }
    records, duplicate_count = runner._dedupe_source_records([original, duplicate])
    assert records == [original]
    assert duplicate_count == 1
