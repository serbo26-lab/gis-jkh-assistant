"""Session warnings stay distinct from browser connectivity on an isolated desktop."""

from dataclasses import replace

import pytest

import test_ui_workflow as workflows
from gis_jkh_assistant.app.settings_page import SESSION_CONNECTION_GUIDANCE
from gis_jkh_assistant.gis_integration import IntegrationStatus
from gis_jkh_assistant.gis_integration.organization_context import (
    MIN_ORGANIZATION_ROUTING_HELPER_VERSION,
)
from gis_jkh_assistant.quality_assessment import MIN_QUALITY_HELPER_VERSION

desktop = workflows.desktop
qt_app = workflows.qt_app

READY = IntegrationStatus(
    running=True, helper_connected=True, gis_page_ready=True,
    helper_version="0.1.34", session_keep_alive_enabled=True,
    browser_names=("Google Chrome",),
)


def _status(desktop, monkeypatch, **changes):
    status = replace(READY, **changes)
    monkeypatch.setattr(desktop.gateway, "status", lambda: status)
    desktop.window._refresh_integration_status()
    return desktop.window, desktop.window.settings_page


@pytest.mark.parametrize("error", [
    "Failed to fetch",
    "Service Unavailable: Госуслуги — технические работы",
    "Session expired",
    "Unauthorized",
])
def test_session_failure_preserves_browser_connection_and_requires_checking_the_cabinet(
    desktop, monkeypatch, error,
):
    before = list(desktop.repository.connection.iterdump())
    window, settings = _status(desktop, monkeypatch, session_keep_alive_error=error)

    assert window.integration_status.text() == "Ошибка продления сессии"
    details = window.integration_status.toolTip()
    assert details == settings.connection_status.text()
    assert "Состояние сессии ГИС ЖКХ не подтверждено" in details
    assert "ГИС ЖКХ помощник 0.1.34 подключён" in details
    assert "ГИС ЖКХ подключен" not in details
    assert "Сессия: активна" not in details
    assert "Сессия: автопродление вкл." not in details
    assert SESSION_CONNECTION_GUIDANCE in details
    assert "Если сайт показывает форму входа, войдите снова." in details
    assert "технические работы" in details
    assert "сессия истекла" not in details.casefold()
    assert error not in details
    assert window.integration_browser.isVisibleTo(window)
    assert window.integration_browser.text() == "Браузер: Google Chrome"
    assert window.integration_browser.text() in details
    assert not settings.recover_connection_button.isHidden()
    assert settings.recover_connection_button.text() == "Как проверить сессию ГИС ЖКХ…"
    settings.recover_connection_button.setChecked(True)
    assert not settings.recovery_steps.isHidden()
    assert SESSION_CONNECTION_GUIDANCE in settings.recovery_steps.text()
    assert "Локальная работа доступна" in settings.recovery_steps.text()
    assert "Повторная установка помощника не нужна" in settings.recovery_steps.text()
    assert "Установить" not in settings.helper_setup_button.text()
    assert list(desktop.repository.connection.iterdump()) == before


def test_session_recovery_clears_warning_and_keeps_local_work_and_navigation(
    desktop, monkeypatch,
):
    window, settings = _status(desktop, monkeypatch)
    current_page = window.pages.currentWidget()
    house_id = window.houses_page._current_house_id
    local_add_enabled = window.houses_page.add_house_button.isEnabled()
    before = list(desktop.repository.connection.iterdump())
    assert settings.recover_connection_button.isHidden()

    _status(desktop, monkeypatch, session_keep_alive_error="Temporary network failure")
    settings.recover_connection_button.setChecked(True)
    assert window.houses_page.add_house_button.isEnabled() == local_add_enabled
    assert window.pages.currentWidget() is current_page
    assert window.houses_page._current_house_id == house_id

    _status(desktop, monkeypatch, last_keep_alive_success_at="2026-10-17T00:00:00Z")

    assert window.integration_status.text() == "ГИС ЖКХ подключен"
    assert "Сессия: автопродление вкл." in settings.connection_status.text()
    assert "не подтверждено" not in settings.connection_status.text()
    assert settings.recover_connection_button.isHidden()
    assert not settings.recover_connection_button.isChecked()
    assert settings.recovery_steps.isHidden()
    assert SESSION_CONNECTION_GUIDANCE not in settings.recovery_steps.text()
    assert window.integration_browser.isVisibleTo(window)
    assert window.pages.currentWidget() is current_page
    assert window.houses_page._current_house_id == house_id
    assert window.houses_page.add_house_button.isEnabled() == local_add_enabled
    assert list(desktop.repository.connection.iterdump()) == before


@pytest.mark.parametrize("version,brief,minimum", [
    ("0.1.19", "Помощник устарел", MIN_QUALITY_HELPER_VERSION),
    ("0.1.29", "Обновите браузерный помощник", MIN_ORGANIZATION_ROUTING_HELPER_VERSION),
])
def test_session_warning_keeps_helper_version_and_routing_requirements(
    desktop, monkeypatch, version, brief, minimum,
):
    window, settings = _status(
        desktop, monkeypatch, helper_version=version,
        session_keep_alive_error="Session renewal failed",
    )

    assert window.integration_status.text() == brief
    details = settings.connection_status.text()
    assert ".".join(map(str, minimum)) + "+" in details
    assert "Сессия: ошибка продления" in details
    assert "ГИС ЖКХ подключен" not in details
    assert SESSION_CONNECTION_GUIDANCE in details
    assert not settings.recover_connection_button.isHidden()


@pytest.mark.parametrize("connected,ready,brief", [
    (True, False, "Откройте вкладку ГИС ЖКХ"),
    (False, False, "Помощник не подключён"),
])
def test_stale_session_error_does_not_replace_missing_helper_or_tab_guidance(
    desktop, monkeypatch, connected, ready, brief,
):
    window, settings = _status(
        desktop, monkeypatch, helper_connected=connected, gis_page_ready=ready,
        session_keep_alive_error="Previous session error",
    )

    assert window.integration_status.text() == brief
    assert window.integration_status.toolTip() == settings.connection_status.text()
    assert SESSION_CONNECTION_GUIDANCE not in settings.connection_status.text()
    assert not settings.recover_connection_button.isHidden()
    assert "сессию" not in settings.recover_connection_button.text()
    assert settings.helper_setup_button.text().startswith("Настроить" if connected else "Установить")
    assert window.integration_browser.isVisibleTo(window) == connected
