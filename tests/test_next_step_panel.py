"""Optional workflow guidance never hides the actual editing commands."""

import sqlite3

import pytest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

import test_ui_workflow as workflows
from gis_jkh_assistant.app.main_window import MainWindow
from gis_jkh_assistant.app.settings_page import SettingsPage
from gis_jkh_assistant.database import Repository

desktop = workflows.desktop
qt_app = workflows.qt_app


def _pages(window):
    return (window.houses_page, window.work_lists_page, window.planning_page,
            window.fixation_page, window.quality_page)


def test_toggle_guidance_preserves_commands_context_and_survives_layout_changes(desktop):
    window, app = desktop.window, desktop.app
    settings = window.settings_page
    assert settings.next_step_panel_checkbox.isChecked()
    assert settings.next_step_panel_checkbox.text() == "Включена"
    contexts = [window._context_values(name) for name in
                ("houses", "work_lists", "planning", "fixation", "quality")]
    commands = (window.houses_page.add_house_button, window.work_lists_page.add_works_button,
                window.planning_page.load_gis_button, window.fixation_page.load_month_button,
                window.quality_page.month_acts_button)
    for index, page in enumerate(_pages(window)):
        window.navigation.setCurrentRow(window.pages.indexOf(page))
        workflows._settle(app)
        assert page.workflow.isVisibleTo(page)
        assert page.workflow.help_button.text() == "Подробнее"
        page.workflow.help_button.setChecked(True)

    window.navigation.setCurrentRow(7)
    QTest.keyClick(settings.next_step_panel_checkbox, Qt.Key.Key_Space)
    assert settings.next_step_panel_checkbox.text() == "Выключена"
    for width, height in ((1100, 680), (1366, 768)):
        window.resize(width, height)
        window._update_display_layout(force=True)
        for index, page in enumerate(_pages(window)):
            window.navigation.setCurrentRow(window.pages.indexOf(page))
            workflows._settle(app)
            assert page.workflow.isHidden()
            assert page.workflow.help_button.isHidden()
            assert commands[index].isVisibleTo(page)
            if index in (3, 4):
                assert page.months_card.isVisibleTo(page)
                assert page.month_commands_group.isVisibleTo(page)
                assert page.period_commands_group.isVisibleTo(page)
    settings.dark_radio.click()
    for page in _pages(window):
        assert page.workflow.isHidden()
        assert page.workflow.help_button.isHidden()
    assert contexts == [window._context_values(name) for name in
                        ("houses", "work_lists", "planning", "fixation", "quality")]

    settings.next_step_panel_checkbox.click()
    for index, page in enumerate(_pages(window)):
        window.navigation.setCurrentRow(window.pages.indexOf(page))
        workflows._settle(app)
        assert page.workflow.isVisibleTo(page)
        assert page.workflow.help_button.isVisibleTo(page)
        assert page.workflow.help_button.isChecked()  # Expanded help is retained.


def test_disabled_guidance_is_restored_after_reopening_database(desktop, tmp_path):
    path = tmp_path / "settings.db"
    repository = Repository(path)
    settings = SettingsPage(repository)
    assert settings.next_step_panel_enabled()
    settings.next_step_panel_checkbox.click()
    assert repository.get_setting("ui.next_step_panel_enabled") == "0"
    settings.close()
    repository.close()

    reopened = Repository(path)
    window = MainWindow(reopened)  # Fixture replaces the gateway with a socket-free mock.
    window.integration_status_timer.stop()
    try:
        assert not window.settings_page.next_step_panel_checkbox.isChecked()
        assert all(page.workflow.isHidden() for page in _pages(window))
        window.settings_page.next_step_panel_checkbox.click()
        assert reopened.get_setting("ui.next_step_panel_enabled") == "1"
    finally:
        window.close()
    verify = Repository(path)
    try:
        assert verify.get_setting("ui.next_step_panel_enabled") == "1"
    finally:
        verify.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_failed_guidance_save_keeps_previous_visibility(desktop, monkeypatch, enabled):
    window = desktop.window
    settings = window.settings_page
    if not enabled:
        settings.next_step_panel_checkbox.click()
    messages, applied = [], []
    settings.nextStepPanelChanged.connect(applied.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))

    def fail(*args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(desktop.repository, "set_setting", fail)
    settings.next_step_panel_checkbox.click()
    assert settings.next_step_panel_checkbox.isChecked() is enabled
    assert settings.next_step_panel_checkbox.text() == ("Включена" if enabled else "Выключена")
    assert not applied
    assert messages and "не применено" in messages[0]
    assert all(page.workflow.guidance_enabled is enabled for page in _pages(window))
    assert all(page.workflow.isHidden() is not enabled for page in _pages(window))
