"""The installation assistant may write its own files, never a browser profile or GIS."""

from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from gis_jkh_assistant.browser import helper_installation as installation


def archive_with(content):
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in content:
            archive.writestr(name, value)
    return output.getvalue()


def test_bundled_resource_matches_canonical_extension_sources():
    bundle = installation.read_bundle()
    root = Path(__file__).resolve().parents[1]
    assert bundle.version == json.loads((root / "browser-extension/manifest.json").read_text(encoding="utf-8"))["version"]
    assert dict(bundle.content) == {
        name: (root / "browser-extension" / name).read_bytes().replace(b"\r\n", b"\n") for name in installation.HELPER_FILES
    }


@pytest.mark.parametrize("extra", ["../escaped.js", "/absolute.js", "nested/background.js", "manifest.json"])
def test_bundle_rejects_unexpected_paths_and_duplicates(extra):
    content = list(installation.read_bundle().content) + [(extra, b"bad")]
    with pytest.raises(ValueError):
        installation.read_bundle(archive_with(content))


@pytest.mark.parametrize("change", [
    {"version": "../../data"}, {"version": "1.2.3.4.5"}, {"version": "65536.1"},
    {"manifest_version": 2}, {"background": {"service_worker": "../background.js"}},
])
def test_bundle_rejects_invalid_manifests(change):
    content = dict(installation.read_bundle().content)
    manifest = json.loads(content["manifest.json"])
    manifest.update(change)
    content["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError):
        installation.read_bundle(archive_with(content.items()))


def test_corrupt_archive_does_not_create_installation_directory(tmp_path):
    with pytest.raises(ValueError):
        installation.read_bundle(b"not a ZIP")
    assert list(tmp_path.iterdir()) == []


def test_damaged_deflate_payload_is_reported_as_recoverable_bundle_error():
    resource = Path(installation.__file__).parent / "assets/browser-helper.zip"
    payload = bytearray(resource.read_bytes())
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        entry = archive.getinfo("background.js")
        offset = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
    # BTYPE=3 is reserved by DEFLATE; this must not escape as zlib.error.
    payload[offset] = 0x07
    with pytest.raises(ValueError, match="повреждён"):
        installation.read_bundle(bytes(payload))


def test_redirected_root_is_refused_before_writing(tmp_path, monkeypatch):
    root = tmp_path / "junction"
    root.mkdir()
    monkeypatch.setattr(Path, "is_junction", lambda p: p == root)
    with pytest.raises(ValueError, match="перенаправлен"):
        installation.prepare_helper(installation.read_bundle(), root)
    assert list(root.iterdir()) == []


def test_prepare_is_persistent_verified_and_idempotent(tmp_path):
    bundle = installation.read_bundle()
    root = tmp_path / "prepared"
    directory = installation.prepare_helper(bundle, root)
    before = {entry.name: (entry.read_bytes(), entry.stat().st_mtime_ns) for entry in directory.iterdir()}
    assert installation.prepare_helper(bundle, root) == directory
    assert {entry.name: (entry.read_bytes(), entry.stat().st_mtime_ns) for entry in directory.iterdir()} == before
    assert dict(bundle.content) == {entry.name: entry.read_bytes() for entry in directory.iterdir()}
    assert sorted(entry.name for entry in root.iterdir()) == [bundle.directory_name]


def test_changed_bundle_keeps_previous_installed_files(tmp_path):
    before = installation.read_bundle()
    previous = installation.prepare_helper(before, tmp_path)
    content = dict(before.content)
    content["background.js"] += b"\n// isolated test modification\n"
    after = installation.read_bundle(archive_with(content.items()))
    current = installation.prepare_helper(after, tmp_path)
    assert current != previous
    assert after.version == before.version
    assert (previous / "background.js").read_bytes() == dict(before.content)["background.js"]
    assert (current / "background.js").read_bytes() == content["background.js"]


@pytest.mark.parametrize("extra_file", [False, True])
def test_changed_existing_directory_is_not_overwritten(tmp_path, extra_file):
    bundle = installation.read_bundle()
    directory = installation.prepare_helper(bundle, tmp_path)
    changed = directory / ("user-file.txt" if extra_file else "background.js")
    changed.write_bytes(b"keep local contents")
    with pytest.raises(ValueError):
        installation.prepare_helper(bundle, tmp_path)
    assert changed.read_bytes() == b"keep local contents"


def test_write_failure_cleans_only_staging_and_keeps_previous_versions(tmp_path, monkeypatch):
    bundle = installation.read_bundle()
    previous = tmp_path / "previous-helper"
    previous.mkdir()
    saved = previous / "keep.txt"
    saved.write_bytes(b"old helper")
    original = Path.write_bytes

    def fail_background(path, data):
        if path.name == "background.js":
            raise PermissionError("isolated write denied")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_background)
    with pytest.raises(PermissionError):
        installation.prepare_helper(bundle, tmp_path)
    assert list(tmp_path.iterdir()) == [previous]
    assert saved.read_bytes() == b"old helper"


def test_prepare_race_reuses_verified_directory(tmp_path, monkeypatch):
    bundle = installation.read_bundle()

    def publish_in_another_instance(_staging, destination):
        destination.mkdir()
        for name, value in bundle.content:
            (destination / name).write_bytes(value)
        raise FileExistsError("another instance won")

    monkeypatch.setattr(Path, "rename", publish_in_another_instance)
    destination = installation.prepare_helper(bundle, tmp_path)
    assert list(tmp_path.iterdir()) == [destination]
    assert (destination / "manifest.json").read_bytes() == dict(bundle.content)["manifest.json"]


def test_preparation_does_not_call_database_paths_or_migrate_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "gis-jkh-assistant"
    legacy.mkdir()
    user_db = legacy / "data.db"
    user_db.write_bytes(b"untouched working database")
    from gis_jkh_assistant.database import paths
    monkeypatch.setattr(paths, "user_data_dir", lambda: pytest.fail("No database migration"))
    folder = installation.prepare_helper(installation.read_bundle())
    assert folder.parent == tmp_path / "GIS JKH Assistant/browser-helper"
    assert user_db.read_bytes() == b"untouched working database"


def test_browser_detection_only_reads_standard_executable_paths(tmp_path):
    roots = {"LOCALAPPDATA": str(tmp_path / "Local"), "PROGRAMFILES": str(tmp_path / "Programs")}
    for relative in ("Google/Chrome/Application/chrome.exe", "Yandex/YandexBrowser/Application/browser.exe"):
        path = Path(roots["LOCALAPPDATA"]) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake executable; never run")
    detected = installation.detect_browsers(roots)
    assert [browser.key for browser in detected] == ["chrome", "edge", "yandex"]
    assert [bool(browser.executable) for browser in detected] == [True, False, True]
    assert installation.detect_browsers({}) == installation.BROWSERS


@pytest.mark.parametrize("browser", installation.BROWSERS)
def test_open_page_launches_only_selected_executable_and_internal_url(tmp_path, monkeypatch, browser):
    executable = tmp_path / browser.executable_name
    executable.write_bytes(b"not executed")
    calls = []
    monkeypatch.setattr(installation.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    installation.open_extensions_page(replace(browser, executable=executable))
    args, kwargs = calls[0]
    assert args == ([str(executable), browser.extensions_url],)
    assert kwargs["shell"] is False
    assert len(calls) == 1


def test_missing_browser_never_launches_a_default_browser(monkeypatch):
    monkeypatch.setattr(installation.subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected browser launch"))
    with pytest.raises(FileNotFoundError):
        installation.open_extensions_page(installation.BROWSERS[0])
