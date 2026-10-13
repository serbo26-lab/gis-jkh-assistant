"""Prepare bundled helper files, without modifying a browser profile or registry."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import zlib


HELPER_FILES = ("manifest.json", "background.js")
MAX_BUNDLE_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class HelperBundle:
    version: str
    content: tuple[tuple[str, bytes], ...]
    digest: str

    @property
    def directory_name(self) -> str:
        return f"{self.version}-{self.digest[:12]}"


@dataclass(frozen=True)
class Browser:
    key: str
    name: str
    executable_name: str
    extensions_url: str
    executable: Path | None = None


BROWSERS = (
    Browser("chrome", "Google Chrome", "chrome.exe", "chrome://extensions/"),
    Browser("edge", "Microsoft Edge", "msedge.exe", "edge://extensions/"),
    Browser("yandex", "Яндекс Браузер", "browser.exe", "browser://extensions/"),
)


def read_bundle(archive: bytes | None = None) -> HelperBundle:
    """Validate the exact distributable; never extract arbitrary ZIP paths."""
    if archive is None:
        archive = files("gis_jkh_assistant.browser").joinpath("assets/browser-helper.zip").read_bytes()
    if len(archive) > MAX_BUNDLE_SIZE:
        raise ValueError("Архив помощника слишком велик.")
    try:
        with zipfile.ZipFile(BytesIO(archive)) as source:
            entries = source.infolist()
            if sorted(entry.filename for entry in entries) != sorted(HELPER_FILES):
                raise ValueError("В архиве помощника неверный состав файлов.")
            for entry in entries:
                if entry.file_size > MAX_BUNDLE_SIZE or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("В архиве помощника недопустимый файл.")
            content = tuple((name, source.read(name)) for name in HELPER_FILES)
        manifest = json.loads(dict(content)["manifest.json"])
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError, zlib.error) as exc:
        raise ValueError("Встроенный архив помощника повреждён.") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Манифест помощника повреждён.")
    version = manifest.get("version", "")
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"\d+(?:\.\d+){0,3}", version)
        or any(int(part) > 65535 for part in version.split("."))
        or manifest.get("manifest_version") != 3
        or manifest.get("background") != {"service_worker": "background.js"}
    ):
        raise ValueError("Манифест помощника несовместим с мастером установки.")
    digest = hashlib.sha256()
    for name, data in content:
        digest.update(name.encode("utf-8") + b"\0" + len(data).to_bytes(8, "big") + data)
    return HelperBundle(version, content, digest.hexdigest())


def installation_root() -> Path:
    """A persistent location, independent of SQLite paths and frozen-app temp files."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "GIS JKH Assistant" / "browser-helper"


def _plain_directory(path: Path) -> None:
    if path.is_symlink() or path.is_junction():
        raise ValueError("Каталог помощника перенаправлен. Проверьте его расположение.")


def verify_prepared(destination: Path, bundle: HelperBundle) -> None:
    _plain_directory(destination)
    if not destination.is_dir() or {entry.name for entry in destination.iterdir()} != set(HELPER_FILES):
        raise ValueError("Подготовленная папка изменена. Сохраните её отдельно и повторите подготовку.")
    for name, data in bundle.content:
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise ValueError("Файлы подготовленного помощника изменены; перезапись отменена.")


def prepare_helper(bundle: HelperBundle, root: Path | None = None) -> Path:
    """Publish a verified snapshot; preserve every previously prepared version."""
    root = Path(root) if root is not None else installation_root()
    # Refuse redirecting ancestors as well as a redirected destination.
    for ancestor in (root, *root.parents):
        _plain_directory(ancestor)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / bundle.directory_name
    if destination.exists():
        verify_prepared(destination, bundle)
        return destination
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=root))
    try:
        for name, data in bundle.content:
            (staging / name).write_bytes(data)
        verify_prepared(staging, bundle)
        try:
            staging.rename(destination)
        except OSError:
            # Two application instances can prepare the same snapshot at once.
            if not destination.exists():
                raise
            verify_prepared(destination, bundle)
        return destination
    finally:
        # Only our own disposable staging directory may be removed.
        if staging.exists():
            _plain_directory(staging)
            if staging.resolve().parent != root.resolve():
                raise ValueError("Временная папка находится вне каталога помощника.")
            shutil.rmtree(staging)



def application_directory() -> Path:
    """The real EXE folder, never PyInstaller's temporary _MEIPASS directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # In a source checkout, use the folder that also holds browser-extension.
    return Path(__file__).resolve().parents[3]


def verify_adjacent_helper(destination: Path, bundle: HelperBundle) -> None:
    """Accept the distributed browser-extension folder, including its README."""
    _plain_directory(destination)
    if not destination.is_dir():
        raise ValueError("Папка помощника не найдена.")
    for name, data in bundle.content:
        path = destination / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"В папке помощника отсутствует {name}.")
        # Git checkout on Windows may use CRLF, whereas the embedded ZIP uses LF.
        if path.read_bytes().replace(b"\r\n", b"\n") != data:
            raise ValueError(f"Файл {name} отличается от версии программы.")


def verify_installation(destination: Path, bundle: HelperBundle) -> None:
    """Validate the actual browser extension directory before showing readiness."""
    if destination.name == "browser-extension":
        verify_adjacent_helper(destination, bundle)
    else:
        verify_prepared(destination, bundle)


def _publish_adjacent(bundle: HelperBundle, destination: Path) -> Path:
    """Safely create an unpacked extension beside the EXE without overwriting files."""
    parent = destination.parent
    for ancestor in (parent, *parent.parents):
        _plain_directory(ancestor)
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_prepared(destination, bundle)
        return destination
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=parent))
    try:
        for name, data in bundle.content:
            (staging / name).write_bytes(data)
        verify_prepared(staging, bundle)
        try:
            staging.rename(destination)
        except OSError:
            if not destination.exists():
                raise
            verify_prepared(destination, bundle)
        return destination
    finally:
        if staging.exists():
            _plain_directory(staging)
            if staging.resolve().parent != parent.resolve():
                raise ValueError("Временная папка находится вне каталога программы.")
            shutil.rmtree(staging)


def prepare_helper_near_program(
    bundle: HelperBundle, directory: Path | None = None,
) -> Path:
    """Use the shipped folder; for an EXE alone, prepare a sibling folder.

    Preserve an existing differing browser-extension folder, since installed
    browser profiles can still be using it. A new version gets a new sibling.
    """
    base = Path(directory) if directory is not None else application_directory()
    primary = base / "browser-extension"
    if primary.exists() or primary.is_symlink():
        try:
            verify_adjacent_helper(primary, bundle)
        except (ValueError, OSError):
            # Never silently replace the directory an existing browser uses.
            destination = base / f"browser-extension-{bundle.directory_name}"
        else:
            return primary
    else:
        destination = primary
    if destination.name == "browser-extension":
        # The primary folder may include README only when shipped in the ZIP.
        result = _publish_adjacent(bundle, destination)
        verify_installation(result, bundle)
        return result
    return _publish_adjacent(bundle, destination)



def reveal_helper_folder(path: Path) -> bool:
    """Reveal the actual directory, not its manifest; fall back on other OSes."""
    if os.name != "nt":
        return False
    try:
        subprocess.Popen(
            ["explorer.exe", "/select,", str(path)], shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True


def detect_browsers(environment=None) -> tuple[Browser, ...]:
    env = os.environ if environment is None else environment
    suffixes = {
        "chrome": "Google/Chrome/Application/chrome.exe",
        "edge": "Microsoft/Edge/Application/msedge.exe",
        "yandex": "Yandex/YandexBrowser/Application/browser.exe",
    }
    detected = []
    for browser in BROWSERS:
        executable = None
        for key in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            if env.get(key):
                candidate = Path(env[key]) / suffixes[browser.key]
                if candidate.is_file():
                    executable = candidate.resolve()
                    break
        detected.append(Browser(
            browser.key, browser.name, browser.executable_name, browser.extensions_url, executable,
        ))
    return tuple(detected)


def open_extensions_page(browser: Browser) -> None:
    """Start the selected browser for setup without touching its profile.

    Chrome accepts chrome://extensions/ on the command line in our tested setup.
    Edge and Yandex may silently ignore their internal URLs and open an empty
    window. Start those browsers normally: the wizard provides a copied address
    and explains how to enter it in the address bar.
    """
    executable = browser.executable
    if (
        executable is None or not executable.is_absolute() or not executable.is_file()
        or executable.name.casefold() != browser.executable_name.casefold()
    ):
        raise FileNotFoundError(f"Укажите существующий файл {browser.executable_name}.")
    arguments = [str(executable)]
    if browser.key == "chrome":
        arguments.extend(("--new-tab", browser.extensions_url))
    subprocess.Popen(
        arguments, shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
