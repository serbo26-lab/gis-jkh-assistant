"""UX implementation stage 1: full-width quality guidance and direct month fill.

Real Qt pages with isolated local state; no GIS requests or browser processes.
"""
from __future__ import annotations

import test_ui_workflow as workflow_tests

desktop = workflow_tests.desktop
qt_app = workflow_tests.qt_app


def _settle(app):
    for _ in range(4):
        app.processEvents()


def test_quality_expanded_guidance_uses_own_full_width_row_at_150_percent(desktop):
    window = desktop.window
    window.navigation.setCurrentRow(4)
    window.resize(1366, 800)
    window.settings_page.scale_combo.setCurrentIndex(
        window.settings_page.scale_combo.findData(150)
    )
    page = window.quality_page
    page.scope_switcher.set_scope("period")
    _settle(desktop.app)

    workflow = page.workflow
    assert workflow.help_panel.isHidden()
    workflow.help_button.click()
    _settle(desktop.app)

    assert workflow.help_panel.isVisibleTo(window)
    assert workflow.title.parentWidget() is workflow.help_panel
    assert workflow.detail.parentWidget() is workflow.help_panel
    assert workflow.title.isVisibleTo(window)
    assert workflow.detail.isVisibleTo(window)
    assert workflow.help_panel.y() >= workflow.action_row.geometry().bottom()
    assert workflow.help_panel.width() >= workflow.width() - 20
    assert workflow.detail.width() >= workflow.help_panel.width() - 2
    assert workflow.detail.height() + 2 >= workflow.detail.heightForWidth(
        workflow.detail.width()
    )
    assert workflow.primary.isVisibleTo(window)
    assert workflow.primary.geometry().right() < workflow.width()

    workflow.help_button.click()
    _settle(desktop.app)
    assert workflow.help_panel.isHidden()
    assert workflow.primary.isVisibleTo(window)


def test_quality_narrow_actions_wrap_as_groups_but_not_help_text(desktop):
    window = desktop.window
    window.navigation.setCurrentRow(4)
    page = window.quality_page
    page.scope_switcher.set_scope("period")
    page.workflow.help_button.setChecked(True)
    window.resize(970, 800)
    _settle(desktop.app)
    assert page.workflow.help_panel.width() >= page.workflow.width() - 20
    assert page.workflow.detail.isVisibleTo(window)
    assert not page.workflow.primary.geometry().intersects(
        page.workflow.help_button.geometry()
    )
    assert not page.workflow.primary.geometry().intersects(page.scope_row.geometry())
    assert page.workflow.primary.geometry().right() < page.workflow.width()


def test_month_fill_first_level_stays_in_month_scope_and_uses_existing_command(desktop, monkeypatch):
    window = desktop.window
    window.navigation.setCurrentRow(3)
    page = window.fixation_page
    menu = page.data_actions
    menu.sync()
    first_command = next(
        action for action in menu.menu_model.actions()
        if not action.isSeparator() and action.menu() is None
    )
    assert first_command is page.month_fill_menu_action
    assert first_command.text() == "Заполнить текущий месяц по плану…"
    assert any(action.isSeparator() for action in menu.menu_model.actions())
    assert all(
        source is not page.fill_month_button
        for _action, source, _when in page.month_actions.entries
    )
    assert any(source is page.fill_period_button for _action, source, _when in page.period_actions.entries)

    calls = []
    monkeypatch.setattr(page, "_fill_month_from_plan", lambda: calls.append(page._current_month))
    page.fill_month_button.setEnabled(True)
    for scope in ("month", "period"):
        page.scope_switcher.set_scope(scope)
        menu.sync()
        assert first_command.isEnabled()
        first_command.trigger()
    assert calls == [page._current_month, page._current_month]
    assert page.fill_month_button.menu() is None

    page.fill_month_button.setEnabled(False)
    menu.sync()
    assert not first_command.isEnabled()
