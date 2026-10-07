from __future__ import annotations

import json
from pathlib import Path

from gis_jkh_assistant import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_version_is_initialized() -> None:
    assert __version__ == "0.1.0.dev54"


def test_readme_versions_match_sources() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
    )
    helper_version = str(manifest["version"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert version == __version__
    assert f"Текущая тестовая версия приложения: **{version}**." in readme
    assert (
        f"Текущая версия **ГИС ЖКХ помощника**: **{helper_version}**."
        in readme
    )
