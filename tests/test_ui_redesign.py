"""Menu reachability and dense desktop composition on isolated mock data."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
from gis_jkh_assistant.gis_integration import IntegrationStatus

import test_ui_workflow as workflows

desktop = workflows.desktop
qt_app = workflows.qt_app


@pytest.mark.parametrize("ready,error,expected", [
    (False, None, "Откройте вкладку ГИС ЖКХ"),
    (True, "QA renewal error", "Ошибка продления сессии"),
])
def test_compact_connection_keeps_readiness_and_session_errors_explicit(desktop, monkeypatch, ready, error, expected):
    status = IntegrationStatus(running=True, helper_connected=True, gis_page_ready=ready,
                               helper_version="0.1.30", session_keep_alive_error=error)
    monkeypatch.setattr(desktop.gateway, "status", lambda: status)
    desktop.window._refresh_integration_status()
    assert desktop.window.integration_status.text() == expected
    assert desktop.window.integration_status.toolTip() == desktop.window.settings_page.connection_status.text()


def test_sidebar_explains_tab_mode_and_tracks_connected_browsers(desktop, monkeypatch):
    window = desktop.window
    assert window.context_mode_label.text() == "Режим вкладок:\nОбщий выбор"
    window.settings_page.independent_radio.click()
    assert window.context_mode_label.text() == "Режим вкладок:\nНезависимый выбор"
    window.settings_page.shared_radio.click()
    assert window.context_mode_label.text() == "Режим вкладок:\nОбщий выбор"
    status = IntegrationStatus(True, True, True, helper_version="0.1.31",
                               browser_names=("Google Chrome", "Microsoft Edge"))
    monkeypatch.setattr(desktop.gateway, "status", lambda: status)
    window._refresh_integration_status()
    settle(desktop.app)
    assert window.integration_browser.isVisibleTo(window)
    assert window.integration_browser.text() == "Браузеры: Google Chrome, Microsoft Edge"
    assert window.integration_browser.text() in window.settings_page.connection_status.text()
    assert window.context_card.geometry().bottom() < window.integration_card.geometry().top()
    assert window.integration_card.geometry().bottom() < window.height()
    status = IntegrationStatus(True, False, False)
    window._refresh_integration_status()
    assert not window.integration_browser.isVisibleTo(window)


def settle(app):
    for _ in range(5):
        app.processEvents()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_quality_footer_keeps_complete_status_and_long_error_readable(desktop, theme):
    window = desktop.window
    getattr(window.settings_page, theme + "_radio").click()
    window.settings_page.scale_combo.setCurrentIndex(window.settings_page.scale_combo.findData(150))
    window.navigation.setCurrentRow(4)
    page = window.quality_page
    for width in (1366, 1600):
        window.resize(width, 900)
        for status in ("Январь 2026: локально", "Ошибка обмена: проверьте подключение помощника и состояние сведений в ГИС перед повторной отправкой. " * 5):
            page.month_status.setText(status)
            page.month_acts_summary.setText("Акты месяца: 1 • связи с работами: 1")
            settle(desktop.app)
            for label in (page.month_status, page.month_acts_summary):
                assert label.height() >= label.heightForWidth(label.width())
                assert label.parentWidget().rect().contains(label.geometry())
            if len(status) < 40:
                assert page.month_status.width() >= page.month_status.fontMetrics().horizontalAdvance(status)
                assert page.month_acts_summary.width() >= page.month_acts_summary.fontMetrics().horizontalAdvance(page.month_acts_summary.text())


@pytest.mark.parametrize("state", ["DRAFT", "APPROVED"])
@pytest.mark.parametrize("ready,busy", [(True, False), (False, False), (True, True)])
def test_registry_menu_follows_live_command_state_without_stray_buttons(desktop, state, ready, busy):
    window = desktop.window
    window.navigation.setCurrentRow(1)
    page = window.work_lists_page
    desktop.repository.update_work_list_gis_state(desktop.work_list.id, gis_status=state)
    page.refresh_work_lists(select_id=desktop.work_list.id)
    desktop.gateway.ready = ready
    page._integration_busy = busy
    page._refresh_workflow()
    for menu in (page.gis_actions, page.files_actions_button, page.composition_actions):
        menu.sync()
        assert menu.isVisibleTo(page) if menu is page.files_actions_button else not menu.isVisibleTo(page)
        for action, source, when in menu.entries:
            assert action.isEnabled() == source.isEnabled()
            if source.toolTip():
                assert action.toolTip() == source.toolTip()
            assert action.isVisible() == (when is None or bool(when()))
    assert page.cancel_gis_button.isVisibleTo(page) == (state == "APPROVED")
    assert page.delete_button.isVisibleTo(page)
    cancellation = next(action for action, source, _when in page.gis_actions.entries if source is page.cancel_gis_button)
    assert cancellation.isVisible() == (state == "APPROVED")
    page._integration_busy = False


def test_collapsed_unplanned_counter_tracks_database_without_erasing_rows(desktop):
    window = desktop.window
    window.navigation.setCurrentRow(3)
    page = window.fixation_page
    period_id = page._current_period_id
    item = desktop.repository.add_work_fixation_unplanned_item(
        period_id, work_code="QA", work_guid="QA-EXTRA", work_name="QA extra",
        fact_count=1, fact_total_cost="42",
    )
    page._load_unplanned_table()
    settle(desktop.app)
    assert page.unplanned_toggle.text() == "Внеплановые работы: 1"
    before = desktop.repository.list_work_fixation_unplanned_items(period_id)
    for _ in range(2):
        page.unplanned_toggle.click()
        settle(desktop.app)
    assert desktop.repository.list_work_fixation_unplanned_items(period_id) == before
    desktop.repository.delete_work_fixation_unplanned_item(item.id)
    page._load_unplanned_table()
    assert page.unplanned_toggle.text() == "Внеплановые работы: 0"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_month_context_and_recommendation_remain_visible_without_month_matrix(desktop, theme):
    window = desktop.window
    getattr(window.settings_page, theme + "_radio").click()
    window.settings_page.scale_combo.setCurrentIndex(window.settings_page.scale_combo.findData(150))
    for index in (3, 4):
        window.navigation.setCurrentRow(index)
        page = window.pages.currentWidget()
        page._select_month(9)
        settle(desktop.app)
        assert page.month_details_button.isChecked()
        assert page.months_card.isVisibleTo(page)
        assert page.current_commands_label.isVisibleTo(page)
        assert page.period_commands_label.isVisibleTo(page)
        assert page.month_combo.currentText() == "Сентябрь"
        assert page.month_combo.width() >= page.month_combo.fontMetrics().horizontalAdvance("Сентябрь") + 45
        option = QStyleOptionComboBox()
        page.month_combo.initStyleOption(option)
        edit_rect = page.month_combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, page.month_combo)
        assert edit_rect.width() >= page.month_combo.fontMetrics().horizontalAdvance("Сентябрь")
        for field in page.context_card.fields:
            assert field.isVisibleTo(page)
            rect = field.rect().translated(field.mapTo(page.page_scroll.viewport(), QPoint(0, 0)))
            assert page.page_scroll.viewport().rect().contains(rect)
        page.scope_switcher.set_scope("period")
        assert page.current_commands_label.isVisibleTo(page)
        assert page.period_commands_label.isVisibleTo(page)
        assert "2026" in page.scope_hint.text()
