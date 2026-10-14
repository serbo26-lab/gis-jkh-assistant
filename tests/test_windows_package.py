"""The distributable is an allowlist, independent of files left by earlier builds."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

from gis_jkh_assistant.browser.helper_installation import read_bundle, verify_prepared


# The pytest CLI does not add the repository root to sys.path.
_spec = importlib.util.spec_from_file_location(
    "package_windows", Path(__file__).resolve().parents[1] / "tools" / "package_windows.py")
package_windows = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(package_windows)
package_windows_build = package_windows.package_windows_build


@pytest.mark.parametrize("include_readme", [True, False])
def test_package_contains_only_exe_helper_and_optional_user_guide(tmp_path, include_readme):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("Пользовательская инструкция", encoding="utf-8")
    for name in ("CHANGELOG.md", "VERSION.txt", "BUILD_INFO.json", "debug.log", "old.py"):
        (source / name).write_text("NOT FOR DISTRIBUTION", encoding="utf-8")
    extension = source / "browser-extension"
    extension.mkdir()
    (extension / "README.md").write_text("OLD INSTRUCTIONS", encoding="utf-8")
    executable = source / "GIS JKH Assistant.exe"
    executable.write_bytes(b"MZ TEST EXECUTABLE")
    output = tmp_path / "package.zip"
    members = package_windows_build(executable, output, root=source, include_readme=include_readme)
    expected = {"GIS JKH Assistant.exe", "browser-extension/manifest.json", "browser-extension/background.js"}
    if include_readme:
        expected.add("README.md")
    assert set(members) == expected
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == expected
        assert archive.testzip() is None
        assert archive.read("GIS JKH Assistant.exe") == executable.read_bytes()
        for name, content in read_bundle().content:
            assert archive.read("browser-extension/" + name) == content
        archive.extractall(tmp_path / "unpacked")
    verify_prepared(tmp_path / "unpacked" / "browser-extension", read_bundle())


def test_failed_packaging_keeps_previous_archive(tmp_path, monkeypatch):
    executable = tmp_path / "test.exe"
    executable.write_bytes(b"MZ TEST")
    output = tmp_path / "package.zip"
    output.write_bytes(b"PREVIOUS ARCHIVE")
    def fail(*_args, **_kwargs):
        raise OSError("TEST WRITE FAILURE")
    monkeypatch.setattr(zipfile.ZipFile, "write", fail)
    with pytest.raises(OSError, match="TEST WRITE FAILURE"):
        package_windows_build(executable, output, include_readme=False)
    assert output.read_bytes() == b"PREVIOUS ARCHIVE"
    assert not list(tmp_path.glob(".windows-package-*"))


def test_missing_executable_cannot_replace_existing_archive(tmp_path):
    output = tmp_path / "package.zip"
    output.write_bytes(b"PREVIOUS ARCHIVE")
    with pytest.raises(FileNotFoundError):
        package_windows_build(tmp_path / "missing.exe", output)
    assert output.read_bytes() == b"PREVIOUS ARCHIVE"
