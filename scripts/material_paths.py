"""Shared filesystem defaults for local materials data."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path


DATA_ROOT = Path(os.environ.get("DTEXTBOOKS_DATA") or os.environ.get("MATERIALS2TEXTBOOK_DATA", "local_runs"))
RAW_ROOT = Path(os.environ.get("DTEXTBOOKS_RAW") or os.environ.get("MATERIALS2TEXTBOOK_RAW", str(DATA_ROOT / "raw")))
WORK_ROOT = Path(os.environ.get("DTEXTBOOKS_WORK") or os.environ.get("MATERIALS2TEXTBOOK_WORK", str(DATA_ROOT / "work_material1")))
MODELS_ROOT = Path(os.environ.get("DTEXTBOOKS_MODELS") or os.environ.get("MATERIALS2TEXTBOOK_MODELS", str(DATA_ROOT / "models")))
GENERATED_ROOT = Path(
    os.environ.get("DTEXTBOOKS_GENERATED")
    or os.environ.get("MATERIALS2TEXTBOOK_GENERATED", str(DATA_ROOT / "generated_textbooks"))
)


def default_data_root() -> Path:
    return DATA_ROOT


def default_raw_root() -> Path:
    return RAW_ROOT


def default_work_root() -> Path:
    return WORK_ROOT


def default_models_root() -> Path:
    return MODELS_ROOT


def default_generated_root() -> Path:
    return GENERATED_ROOT


def default_generated_output_dir(material_root: Path, title: str = "digital_textbook") -> Path:
    material_name = _slugify(material_root.name) or "material"
    title_slug = _slugify(title) or "digital_textbook"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return GENERATED_ROOT / "runs" / material_name / f"{timestamp}_{title_slug}"


def _slugify(value: str, *, max_length: int = 48) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return slug[:max_length].strip("._-")
