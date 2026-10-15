"""Section groups remain one keyboard/mouse selection and preserve save guards."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest

import test_ui_workflow as workflows
from gis_jkh_assistant.app.sidebar_navigation import SidebarNavigation

qt_app = workflows.qt_app
desktop = workflows.desktop


def test_keyboard_crosses_groups_and_bottom_settings_without_separator_stops(qt_app):
    navigation = SidebarNavigation([
        [(str(row), QIcon(), str(row), str(row)) for row in group]
        for group in ((0, 1), (2, 3, 4, 5), (6,), (7,))
    ])
    navigation.resize(228, 650)
    navigation.show()
    workflows._settle(qt_app)
    navigation.refresh_metrics(20)
    navigation.setCurrentRow(0)
    navigation.setFocus()
    changed = []
    navigation.currentRowChanged.connect(changed.append)
    try:
        for target in range(1, 8):
            QTest.keyClick(qt_app.focusWidget(), Qt.Key.Key_Down)
            assert navigation.currentRow() == target
            assert navigation.selectedItems() == [navigation.item(target)]
            assert qt_app.focusWidget() is navigation.item_view(target)
        assert changed == list(range(1, 8))
        for target in range(6, -1, -1):
            QTest.keyClick(qt_app.focusWidget(), Qt.Key.Key_Up)
            assert navigation.currentRow() == target
        QTest.keyClick(qt_app.focusWidget(), Qt.Key.Key_End)
        assert navigation.currentRow() == 7
        QTest.keyClick(qt_app.focusWidget(), Qt.Key.Key_Home)
        assert navigation.currentRow() == 0
        assert len(navigation.dividers) == 2
        for divider in navigation.dividers:
            assert divider.focusPolicy() == Qt.FocusPolicy.NoFocus
            QTest.mouseClick(divider, Qt.MouseButton.LeftButton)
            assert navigation.currentRow() == 0
    finally:
        navigation.close()
        navigation.deleteLater()
        workflows._settle(qt_app)


def test_sidebar_order_bottom_alignment_clicks_and_save_guard(desktop, monkeypatch):
    window, navigation = desktop.window, desktop.window.navigation
    targets = (window.organizations_page, window.houses_page, window.work_lists_page,
               window.planning_page, window.fixation_page, window.quality_page,
               window.management_fees_page, window.settings_page)
    assert [navigation.item(row).text() for row in range(navigation.count())] == [
        "УО", "Дома", "Перечни работ и услуг", "Планирование", "Фиксация работ и услуг",
        "Оценка качества", "Информация о размере платы", "Настройки",
    ]
    for theme in ("light", "dark"):
        window._theme_changed(theme)
        for height in (600, 768, 900):
            window.resize(1366, height)
            workflows._settle(desktop.app)
            views = navigation.views
            for row in range(navigation.count()):
                view = navigation.item_view(row)
                rect = view.visualItemRect(navigation.item(row))
                assert view.viewport().rect().contains(rect)
                assert view.verticalScrollBar().maximum() == 0
            assert views[-1].geometry().bottom() == navigation.height() - 1
            assert views[-1].mapTo(window.sidebar, QPoint()).y() < window.context_card.y()
            for upper, divider, lower in zip(views, navigation.dividers, views[1:]):
                assert divider.height() == 1
                assert divider.y() - upper.geometry().bottom() == 8
                assert lower.y() - divider.y() == 8
    for row, page in enumerate(targets):
        view = navigation.item_view(row)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                        pos=view.visualItemRect(navigation.item(row)).center())
        workflows._settle(desktop.app)
        assert window.pages.currentWidget() is page
        assert navigation.selectedItems() == [navigation.item(row)]
    navigation.setFocus()
    QTest.keyClick(desktop.app.focusWidget(), Qt.Key.Key_Tab)
    assert desktop.app.focusWidget() not in navigation.views
    QTest.keyClick(desktop.app.focusWidget(), Qt.Key.Key_Backtab)
    assert desktop.app.focusWidget() is navigation.item_view(7)
    navigation.setCurrentRow(1)
    monkeypatch.setattr(window.houses_page, "flush_pending_changes", lambda: False)
    view = navigation.item_view(7)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                    pos=view.visualItemRect(navigation.item(7)).center())
    workflows._settle(desktop.app)
    assert window.pages.currentWidget() is window.houses_page
    assert navigation.currentRow() == 1
    assert navigation.selectedItems() == [navigation.item(1)]
    navigation.setFocus()
    QTest.keyClick(desktop.app.focusWidget(), Qt.Key.Key_End)
    workflows._settle(desktop.app)
    assert window.pages.currentWidget() is window.houses_page
    assert navigation.selectedItems() == [navigation.item(1)]
    monkeypatch.setattr(window.houses_page, "flush_pending_changes", lambda: True)
