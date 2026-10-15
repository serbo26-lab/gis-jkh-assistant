"""Execute the Windows build step with competing Python commands in PATH."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell command discovery")
def test_build_uses_one_python_and_keeps_executable_name_as_one_argument(tmp_path):
    powershell = shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell 7 is unavailable")
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/build-windows.yml"
    section = workflow.read_text(encoding="utf-8").split(
        "      - name: Build executable\n", 1)[1].split("      - name:", 1)[0]
    command = textwrap.dedent(section.split("        run: |\n", 1)[1])
    fallback = tmp_path / "another Python with spaces"
    fallback.mkdir()
    # A valid second executable is enough for native Get-Command discovery.
    # If selected, cmd.exe cannot perform the requested Python module invocation.
    shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32/cmd.exe", fallback / "python.exe")
    capture = tmp_path / "invocation.json"
    (tmp_path / "PyInstaller.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path(os.environ['BUILD_INVOCATION_CAPTURE']).write_text(json.dumps({"
        "'python': sys.executable, 'argv': sys.argv, 'path': os.environ['PATH']}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), str(fallback),
                                  str(Path(env["SYSTEMROOT"]) / "System32")))
    env["PYTHONPATH"] = str(tmp_path)
    env["BUILD_INVOCATION_CAPTURE"] = str(capture)
    script = tmp_path / "build step.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "if (@(Get-Command python -CommandType Application).Count -lt 2) "
        "{ throw 'Fixture must expose two Python applications' }\n" + command,
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = json.loads(capture.read_text(encoding="utf-8"))
    assert Path(invocation["python"]).resolve() == Path(sys.executable).resolve()
    assert invocation["argv"][invocation["argv"].index("--name") + 1] == "GIS JKH Assistant"
    assert str(fallback) not in invocation["path"].split(os.pathsep)
