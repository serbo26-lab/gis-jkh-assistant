"""Native theme traversal must not lose Python-owned widgets to cyclic GC."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = r'''
import gc
import sys
import weakref

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from gis_jkh_assistant.app.styles import apply_theme

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet("QWidget { color: black; }")
observed_event = getattr(QEvent.Type, sys.argv[1])

class CyclicWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.cycle = self

class Probe(QWidget):
    observing = False

    def event(self, event):
        if self.observing and event.type() == observed_event:
            # Deterministic collection in the native traversal, independent of
            # test order or Python's allocation-based automatic GC threshold.
            gc.collect()
            snapshots.append(sum(ref() is not None and isValid(ref()) for ref in refs))
        return super().event(event)

probe = Probe()
probe.ensurePolished()
gc.disable()
try:
    for theme in ("dark", "light") * 10:
        refs = []
        snapshots = []
        for _ in range(20):
            widget = CyclicWidget()
            widget.ensurePolished()
            refs.append(weakref.ref(widget))
        del widget
        probe.observing = True
        apply_theme(app, theme)
        probe.observing = False
        assert snapshots and all(count == len(refs) for count in snapshots), snapshots
        gc.collect()
        assert all(ref() is None for ref in refs), "theme update retained dead widgets"
finally:
    probe.observing = False
    gc.enable()
'''


@pytest.mark.parametrize("event", ["StyleChange", "PaletteChange"])
def test_theme_change_preserves_widgets_during_native_callbacks(event):
    # A regression must fail this assertion rather than crash the pytest host.
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", SCENARIO, event],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
