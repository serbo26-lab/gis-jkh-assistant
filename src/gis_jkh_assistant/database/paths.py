"""Paths for user-specific application data."""

from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "GIS-JKH-Assistant"


def user_data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = root / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return user_data_dir() / "data.db"


def template_files_dir() -> Path:
    path = user_data_dir() / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def gis_export_template_dir() -> Path:
    path = user_data_dir() / "gis-export-template"
    path.mkdir(parents=True, exist_ok=True)
    return path
