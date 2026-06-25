"""Shared filesystem defaults for local materials data."""

from __future__ import annotations

import os
from pathlib import Path


DATA_ROOT = Path(os.environ.get("DTEXTBOOKS_DATA", "local_runs"))
RAW_ROOT = Path(os.environ.get("DTEXTBOOKS_RAW", str(DATA_ROOT / "raw")))
WORK_ROOT = Path(os.environ.get("DTEXTBOOKS_WORK", str(DATA_ROOT / "work_material1")))
MODELS_ROOT = Path(os.environ.get("DTEXTBOOKS_MODELS", str(DATA_ROOT / "models")))


def default_data_root() -> Path:
    return DATA_ROOT


def default_raw_root() -> Path:
    return RAW_ROOT


def default_work_root() -> Path:
    return WORK_ROOT


def default_models_root() -> Path:
    return MODELS_ROOT
