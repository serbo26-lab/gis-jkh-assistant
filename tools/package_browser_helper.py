"""Build/check the deterministic helper resource from the canonical extension sources."""

from io import BytesIO
from pathlib import Path
import sys
import zipfile


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    output = root / "src/gis_jkh_assistant/browser/assets/browser-helper.zip"
    buffer = BytesIO()
    expected = {}
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "background.js"):
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            expected[name] = (root / "browser-extension" / name).read_bytes().replace(b"\r\n", b"\n")
            archive.writestr(entry, expected[name])
    content = buffer.getvalue()
    if "--check" in sys.argv:
        try:
            with zipfile.ZipFile(output) as archive:
                actual = {name: archive.read(name) for name in archive.namelist()}
        except (OSError, zipfile.BadZipFile):
            actual = None
        if actual != expected:
            print("Helper bundle is stale. Run: python -B tools/package_browser_helper.py")
            return 1
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
