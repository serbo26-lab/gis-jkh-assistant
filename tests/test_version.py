from __future__ import annotations

import json
from pathlib import Path

from gis_jkh_assistant import __version__
from gis_jkh_assistant.browser.helper_installation import read_bundle


ROOT = Path(__file__).resolve().parents[1]


def test_version_is_initialized() -> None:
    assert __version__ == "0.1.0.dev62"


def test_release_versions_match_sources() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
    )
    helper_version = str(manifest["version"])
    assert version == __version__
    assert read_bundle().version == helper_version
