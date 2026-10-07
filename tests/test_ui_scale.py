"""Interface enlargement and native caption regressions with isolated Qt data."""
from __future__ import annotations

import ctypes

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QPushButton

from gis_jkh_assistant.app import main_window, window_chrome
from gis_jkh_assistant.app.styles import tokens
from gis_jkh_assistant.app.ui_scale import SCALE_SETTING, scale_factor, scale_percent

import test_ui_workflow as workflow_tests

desktop = workflow_tests.desktop
qt_app = workflow_tests.qt_app


def _settle(app):
    for _ in range(4):
        app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("value,expected", [
    (None, 100), ("", 100), ("broken", 100), ("200", 100),
    ("100", 100), (125, 125), ("150", 150),
])
def test_ui_size_preference_rejects_invalid_values(value, expected):
    assert scale_percent(value) == expected
    assert scale_factor(value) == expected / 100


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_caption_color_matches_semantic_theme(theme, monkeypatch):
    calls = []

    def set_attribute(window_id, attribute, pointer, size):
        assert window_id == 54
        assert size == 4
        value = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value
        calls.append((attribute, value))
        return 0

    monkeypatch.setattr(window_chrome.sys, "platform", "win32")
    monkeypatch.setattr(window_chrome.QGuiApplication, "platformName", lambda: "windows")
    monkeypatch.setattr(window_chrome, "_dwm_set_attribute", lambda: set_attribute)
    window = type("NativeWindow", (), {"winId": lambda _self: 54})()
    assert window_chrome.apply_window_theme(window, theme)
    values = tokens(theme)
    expected_caption = int(values["app_background"][1:3], 16)
    expected_caption |= int(values["app_background"][3:5], 16) << 8
    expected_caption |= int(values["app_background"][5:7], 16) << 16
    expected_text = int(values["text"][1:3], 16)
    expected_text |= int(values["text"][3:5], 16) << 8
    expected_text |= int(values["text"][5:7], 16) << 16
    assert calls == [(20, int(theme == "dark")), (35, expected_caption), (36, expected_text)]


@pytest.mark.parametrize("platform,qt_platform", [("linux", "xcb"), ("win32", "offscreen")])
def test_caption_theming_is_noop_on_unsupported_platforms(platform, qt_platform, monkeypatch):
    monkeypatch.setattr(window_chrome.sys, "platform", platform)
    monkeypatch.setattr(window_chrome.QGuiApplication, "platformName", lambda: qt_platform)
    monkeypatch.setattr(window_chrome, "_dwm_set_attribute", lambda: pytest.fail("Unsupported DWM"))
    assert not window_chrome.apply_window_theme(object(), "dark")


def test_caption_unsupported_attributes_do_not_break_the_window(monkeypatch):
    monkeypatch.setattr(window_chrome.sys, "platform", "win32")
    monkeypatch.setattr(window_chrome.QGuiApplication, "platformName", lambda: "windows")
    monkeypatch.setattr(window_chrome, "_dwm_set_attribute", lambda: lambda *_args: -2147024809)
    window = type("NativeWindow", (), {"winId": lambda _self: 54})()
    assert not window_chrome.apply_window_theme(window, "light")


def test_theme_switch_requests_matching_caption_and_preserves_normal_window(desktop, monkeypatch):
    requests = []
    monkeypatch.setattr(main_window, "apply_window_theme", lambda window, theme: requests.append(theme))
    original_flags = desktop.window.windowFlags()
    for theme in ("dark", "light"):
        getattr(desktop.window.settings_page, f"{theme}_radio").click()
        _settle(desktop.app)
    assert requests[-2:] == ["dark", "light"]
    assert desktop.window.windowFlags() == original_flags
    assert not desktop.window.windowFlags() & Qt.WindowType.FramelessWindowHint


def test_runtime_size_preserves_context_saved_edits_and_busy_queue(desktop, monkeypatch):
    window = desktop.window
    window.navigation.setCurrentRow(2)
    planning = window.planning_page
    planning.table.item(0, 3).setText("3")
    plan_item = desktop.repository.list_work_plan_items(planning._current_plan_id)[0]
    snapshot = dict(window._shared_context)
    fixation = window.fixation_page
    fixation._queue_running = True
    fixation._integration_busy = True
    table_identity = id(fixation.table)
    monkeypatch.setattr(fixation, "_load_table", lambda: pytest.fail("Scaling rebuilt a busy page"))
    initial_font_size = planning.font().pointSizeF()
    for percent in (150, 125, 100):
        combo = window.settings_page.scale_combo
        combo.setCurrentIndex(combo.findData(percent))
        _settle(desktop.app)
        assert desktop.repository.get_setting(SCALE_SETTING) == str(percent)
        assert desktop.app.property("uiScale") == percent / 100
        assert window._shared_context == snapshot
        assert planning._current_work_list_id == desktop.work_list.id
        assert desktop.repository.get_work_plan_month(plan_item.id, 1).count == 3
        assert planning.table.item(0, 3).text() == "3"
        assert fixation._queue_running and fixation._integration_busy
        assert id(fixation.table) == table_identity
        assert planning.font().pointSizeF() == pytest.approx(initial_font_size * percent / 100)
        assert not window.styleSheet()  # No resize-specific font override hides the chosen size.
    fixation._queue_running = False
    fixation._integration_busy = False


def test_ui_size_reopens_from_persisted_preference(qt_app, tmp_path, monkeypatch):
    from gis_jkh_assistant.database import Repository
    from test_ui_workflow import FakeGateway

    repository = Repository(tmp_path / "scale.db")
    repository.set_setting(SCALE_SETTING, "150")
    monkeypatch.setattr(main_window, "LocalIntegrationGateway", FakeGateway)
    window = main_window.MainWindow(repository)
    try:
        assert window.settings_page.scale_combo.currentData() == 150
        assert qt_app.property("uiScale") == 1.5
        assert window._ui_scale == 1.5
        window.settings_page.dark_radio.click()
        _settle(qt_app)
        assert qt_app.property("uiScale") == 1.5
    finally:
        window.close()
        window.deleteLater()
        _settle(qt_app)


def test_table_photo_and_delete_controls_resize_without_reloading_busy_period(desktop, monkeypatch):
    window = desktop.window
    window.navigation.setCurrentRow(3)
    page = window.fixation_page
    period_id = page._current_period_id
    item = desktop.repository.list_work_fixation_items(period_id)[0]
    desktop.repository.add_work_fixation_photo(
        fixation_item_id=item.id, file_name="local.jpg", description="Фото",
        content_type="image/jpeg", content=b"local-test-photo",
    )
    desktop.repository.add_work_fixation_unplanned_item(
        period_id, work_code="20", work_name="Внеплановая работа",
        work_guid="independent-work", fact_count=1, fact_total_cost="42",
    )
    page._load_table()
    page._load_unplanned_table()
    controls = [page.table.cellWidget(0, 9), page.unplanned_table.cellWidget(0, 8),
                page.unplanned_table.cellWidget(0, 9)]
    identities = [id(button) for button in controls]
    page._queue_running = True
    monkeypatch.setattr(page, "_load_table", lambda: pytest.fail("Scale rebuilt the fixation period"))
    monkeypatch.setattr(page, "_load_unplanned_table", lambda: pytest.fail("Scale rebuilt extra work"))
    for percent in (150, 100):
        combo = window.settings_page.scale_combo
        combo.setCurrentIndex(combo.findData(percent))
        _settle(desktop.app)
        assert page._current_period_id == period_id
        assert identities == [id(button) for button in controls]
        for button in controls:
            assert button.width() >= button.minimumSizeHint().width()
            assert button.height() >= button.minimumSizeHint().height()
        assert len(desktop.repository.list_work_fixation_photos(fixation_item_id=item.id)) == 1
        assert desktop.repository.list_work_fixation_unplanned_items(period_id)[0].fact_total_cost == "42"
    page._queue_running = False


def test_quality_act_control_resizes_while_current_period_is_busy(desktop, monkeypatch):
    window = desktop.window
    window.navigation.setCurrentRow(4)
    page = window.quality_page
    period, item = workflow_tests._include_quality(page, 1)
    workflow_tests._add_acceptance_act(page, item)
    page._load_table()
    button = page.table.cellWidget(0, 6)
    page._queue_running = True
    monkeypatch.setattr(page, "_load_table", lambda: pytest.fail("Scale rebuilt a busy quality period"))
    for percent in (150, 100):
        combo = window.settings_page.scale_combo
        combo.setCurrentIndex(combo.findData(percent))
        _settle(desktop.app)
        assert page._current_period_id == period.id
        assert page.table.cellWidget(0, 6) is button
        assert button.width() >= button.minimumSizeHint().width()
        assert button.height() >= button.minimumSizeHint().height()
        assert len(page.store.list_acts(item.id)) == 1
    page._queue_running = False


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("percent", [100, 125, 150])
@pytest.mark.parametrize("size", [(1366, 768), (1600, 900), (1920, 1080)])
def test_pages_fit_desktop_at_selected_ui_size(desktop, theme, percent, size):
    window = desktop.window
    settings = window.settings_page
    getattr(settings, f"{theme}_radio").click()
    settings.scale_combo.setCurrentIndex(settings.scale_combo.findData(percent))
    window.resize(*size)
    _settle(desktop.app)
    assert (window.width(), window.height()) == size
    for index in range(window.pages.count()):
        window.navigation.setCurrentRow(index)
        _settle(desktop.app)
        page = window.pages.widget(index)
        assert page.page_content.width() <= page.page_scroll.viewport().width() + 2
        assert page.page_scroll.horizontalScrollBar().maximum() == 0
        for button in page.findChildren(QPushButton):
            if button.isVisible() and button.text() and "\n" not in button.text():
                assert button.width() >= button.minimumSizeHint().width(), (
                    index, percent, size, button.text(), button.width(), button.minimumSizeHint().width(),
                )
                assert button.height() >= button.minimumSizeHint().height(), (
                    index, percent, size, button.text(), button.height(), button.minimumSizeHint().height(),
                )
        for row in range(window.navigation.count()):
            rect = window.navigation.visualItemRect(window.navigation.item(row))
            text_width = window.navigation.fontMetrics().horizontalAdvance(
                window.navigation.item(row).text(),
            )
            assert rect.width() >= text_width + window.navigation.iconSize().width()
