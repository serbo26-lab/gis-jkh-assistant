"""Package only the desktop executable, validated helper files and user guide."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
import zipfile

from gis_jkh_assistant.browser.helper_installation import read_bundle


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "GIS JKH Assistant.exe"


def package_windows_build(executable: Path, output: Path, *, root: Path = ROOT,
                          include_readme: bool = True) -> tuple[str, ...]:
    executable, output = Path(executable).resolve(), Path(output).resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Executable not found: {executable}")
    if executable == output:
        raise ValueError("Archive and executable paths must differ.")
    bundle = read_bundle()
    readme = (root / "README.md").read_bytes() if include_readme else None
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".windows-package-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(executable, EXECUTABLE_NAME)
            for filename, content in bundle.content:
                archive.writestr("browser-extension/" + filename, content)
            if readme is not None:
                archive.writestr("README.md", readme)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("The Windows archive failed its integrity check.")
            members = tuple(archive.namelist())
        os.replace(temporary, output)
        return members
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / EXECUTABLE_NAME)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "GIS.JKH.Assistant-windows.zip")
    parser.add_argument("--no-readme", action="store_true")
    args = parser.parse_args()
    members = package_windows_build(args.exe, args.output, include_readme=not args.no_readme)
    print(str(args.output))
    for name in members:
        print("  " + name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
