from __future__ import annotations

import json
from pathlib import Path

from gis_jkh_assistant import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_version_is_initialized() -> None:
    assert __version__ == "0.1.0.dev57"


def test_version_and_helper_metadata_are_separate_from_user_readme() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert version == __version__
    assert manifest["name"] == "ГИС ЖКХ помощник"
    assert manifest["version"]
    assert readme.startswith("# ГИС ЖКХ Ассистент\n")
    assert "releases/tag/dev-latest" in readme
    assert "Настройки → Связь с ГИС ЖКХ → Установить браузерный помощник" in readme
    assert "Текущая тестовая версия" not in readme
    assert "Текущая версия **ГИС ЖКХ помощника**" not in readme
