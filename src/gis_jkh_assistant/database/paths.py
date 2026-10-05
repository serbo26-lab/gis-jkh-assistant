"""Paths for user-specific application data."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


APP_DIR_NAME = "GIS JKH Assistant"
_LEGACY_APP_DIR_NAMES = ("GIS-JKH-Assistant",)


def _data_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def user_data_dir() -> Path:
    root = _data_root()
    path = root / APP_DIR_NAME

    if not path.exists():
        for legacy_name in _LEGACY_APP_DIR_NAMES:
            legacy = root / legacy_name
            if not legacy.exists():
                continue
            try:
                legacy.rename(path)
            except OSError:
                shutil.copytree(legacy, path, dirs_exist_ok=True)
            break

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
