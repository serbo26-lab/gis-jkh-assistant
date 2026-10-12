"""Diagnostic-only pytest plugin: replace FrozenName in a test subprocess.

Loaded only via: python -m pytest -p tools.qt_diagnostic_plugin ...
Never imported by the application, production tests or Windows packaging.
"""
from PySide6.QtCore import QObject


class DisabledFrozenName(QObject):
    """No additional view, no header/scrollbar signal wiring or style callbacks."""

    def __init__(self, table):
        super().__init__(table)


def pytest_configure(config):
    from gis_jkh_assistant.app import frozen_name

    frozen_name.FrozenName = DisabledFrozenName
    config.pluginmanager.register(_DiagnosticMarker(), "frozen-name-disabled-marker")


class _DiagnosticMarker:
    def pytest_sessionstart(self, session):
        print(
            "QT DIAGNOSTIC: FrozenName temporarily disabled for this test process",
            flush=True,
        )
