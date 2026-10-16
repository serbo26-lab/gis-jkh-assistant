"""Connection onboarding follows local gateway status without contacting GIS."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QPushButton

from gis_jkh_assistant.app import main_window
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.gis_integration import IntegrationStatus

import test_ui_workflow as workflows

qt_app = workflows.qt_app

READY = IntegrationStatus(True, True, True, helper_version="0.1.34")
DISCONNECTED = IntegrationStatus(True, False, False)


@pytest.fixture
def intro_window(qt_app, tmp_path, monkeypatch):
    windows = []

    def create(status, *, seen=False):
        repository = Repository(tmp_path / f"intro-{len(windows)}.db")
        if seen:
            repository.set_setting("ui.helper.intro_seen", "1")
        state = SimpleNamespace(status=status)

        def reject_command(*_args, **_kwargs):
            pytest.fail("Onboarding must not send GIS commands")

        gateway = SimpleNamespace(
            start=lambda: None, stop=lambda: None,
            status=lambda: state.status, execute=reject_command,
        )
        monkeypatch.setattr(main_window, "LocalIntegrationGateway", lambda: gateway)
        window = main_window.MainWindow(repository)
        window.integration_status_timer.stop()
        startup_hidden = not hasattr(window, "helper_intro") or window.helper_intro.isHidden()
        window.show()
        workflows._settle(qt_app)
        windows.append(window)
        return SimpleNamespace(
            window=window, repository=repository, state=state, startup_hidden=startup_hidden,
        )

    yield create
    for window in reversed(windows):
        window.close()
        window.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    workflows._settle(qt_app)


def test_ready_helper_dismisses_intro_during_startup(intro_window):
    desktop = intro_window(READY)
    assert desktop.startup_hidden
    assert desktop.window.helper_intro.isHidden()
    assert desktop.repository.get_setting("ui.helper.intro_seen") == "1"


def test_helper_connection_dismisses_intro_once(intro_window, monkeypatch):
    desktop = intro_window(DISCONNECTED)
    assert not desktop.startup_hidden
    assert desktop.window.helper_intro.isVisibleTo(desktop.window)
    writes = []
    original = desktop.repository.set_setting

    def record_setting(key, value):
        if key == "ui.helper.intro_seen":
            writes.append(value)
        return original(key, value)

    monkeypatch.setattr(desktop.repository, "set_setting", record_setting)
    desktop.state.status = READY
    desktop.window._refresh_integration_status()
    desktop.window._refresh_integration_status()
    assert desktop.window.helper_intro.isHidden()
    assert desktop.repository.get_setting("ui.helper.intro_seen") == "1"
    assert writes == ["1"]


@pytest.mark.parametrize("status", [
    replace(READY, running=False), DISCONNECTED,
    replace(READY, gis_page_ready=False),
    replace(READY, helper_version="0.1.27"),
    replace(READY, helper_version=None),
])
def test_incomplete_or_obsolete_connection_keeps_intro(intro_window, status):
    desktop = intro_window(status)
    desktop.window._refresh_integration_status()
    assert not desktop.startup_hidden
    assert desktop.window.helper_intro.isVisibleTo(desktop.window)
    assert not desktop.repository.get_setting("ui.helper.intro_seen")


def test_disconnect_does_not_restore_dismissed_intro(intro_window):
    desktop = intro_window(READY)
    desktop.state.status = DISCONNECTED
    desktop.window._refresh_integration_status()
    assert desktop.window.helper_intro.isHidden()
    assert desktop.repository.get_setting("ui.helper.intro_seen") == "1"


def test_explicit_intro_dismissal_stays_saved(intro_window):
    desktop = intro_window(DISCONNECTED)
    dismiss = next(button for button in desktop.window.helper_intro.findChildren(QPushButton)
                   if button.text() == "Продолжить локально")
    dismiss.click()
    desktop.window._refresh_integration_status()
    assert desktop.window.helper_intro.isHidden()
    assert desktop.repository.get_setting("ui.helper.intro_seen") == "1"


def test_seen_intro_is_not_recreated_when_disconnected(intro_window):
    desktop = intro_window(DISCONNECTED, seen=True)
    assert not hasattr(desktop.window, "helper_intro")
    assert desktop.repository.get_setting("ui.helper.intro_seen") == "1"
