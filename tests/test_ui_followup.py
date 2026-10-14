"""The user's second walkthrough: command placement and expandable guidance."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

import test_ui_workflow as workflow_fixtures
from test_ui_workflow import _select_page
from gis_jkh_assistant.app.styles import apply_theme

desktop = workflow_fixtures.desktop
qt_app = workflow_fixtures.qt_app


def settle():
    QTest.qWait(120)


def assert_inside(widget, ancestor):
    position = widget.mapTo(ancestor, QPoint(0, 0))
    assert ancestor.rect().contains(widget.rect().translated(position)), (
        widget.objectName(), widget.geometry(), ancestor.geometry(),
    )


def test_house_commands_follow_the_registry_and_preparation(desktop):
    page = _select_page(desktop, 0)
    settle()
    assert page.add_house_button.text() == "Добавить дом вручную"
    assert page.import_button.text() == "Импортировать дома из XLSX…"
    assert page.add_house_button.parentWidget() is page.house_list_actions
    assert page.import_button.parentWidget() is page.house_list_actions
    assert page.house_list_actions.y() >= page.house_list.geometry().bottom()
    flow = page.house_download_actions.layout()
    commands = [flow.itemAt(i).widget() for i in range(1, flow.count())]
    assert commands == [page.sync_houses_button, page.sync_all_management_button,
                        page.sync_all_work_lists_button, page.sync_all_plans_button,
                        page.sync_all_fixations_button, page.sync_all_quality_button]
    assert flow.itemAt(0).widget().text() == "Импорт из ГИС ЖКХ:"
    local_flow = page.house_local_actions.layout()
    assert local_flow.itemAt(0).widget().text() == "Импорт локальный:"
    assert [local_flow.itemAt(i).widget() for i in (1, 2)] == [
        page.export_house_data_button, page.import_house_data_button,
    ]
    assert page.house_local_actions.y() >= page.house_download_actions.geometry().bottom()
    assert page.export_house_data_button.text() == "Экспорт данных в Excel…"
    assert page.import_house_data_button.text() == "Импорт данных из Excel…"


def test_composition_commands_are_direct_and_beside_the_table(desktop):
    page = _select_page(desktop, 1)
    for width in (1366, 1920, 1366):
        desktop.window.resize(width, 768 if width == 1366 else 1000)
        settle()
        for button in (page.creation_toggle, page.directory_button, page.load_export_template_button,
                       page.load_gis_lists_button, page.delete_button, page.cancel_gis_button,
                       page.add_works_button, page.remove_works_button, page.replace_from_template_button,
                       page.clear_works_button, page.templates_button, page.save_as_template_button,
                       page.lazy_fill_button, page.project_save_button, page.approve_gis_button,
                       page.sync_gis_button):
            assert button.isVisibleTo(page)
            assert button.menu() is None
            assert_inside(button, button.parentWidget())
        assert not page.composition_actions.isVisibleTo(page)
        assert page.preparation_bar.y() < page.details_card.y()
        assert page.editing_bar.geometry().bottom() < page.items_table.y()
        left, right = page.editing_bar.groups
        assert not left.geometry().intersects(right.geometry())
        assert right.geometry().right() <= page.editing_bar.width()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_expanded_guidance_and_metadata_fit_after_resize(desktop, theme):
    apply_theme(desktop.app, theme)
    for year in (2021, 2022, 2023, 2024, 2025):
        other = desktop.repository.clone_work_list(desktop.work_list.id, target_base_year=year)
        desktop.repository.update_work_list_gis_state(other.id, gis_status="APPROVED")
    desktop.window.work_lists_page.refresh_work_lists(select_id=desktop.work_list.id)
    for index in (0, 1, 2, 3, 4):
        page = _select_page(desktop, index)
        page.workflow.help_button.setChecked(True)
        if index == 1:
            page.details_more_toggle.setChecked(True)
            page.gis_state_label.setText("Ошибка загрузки. " * 35)
        for width, height in ((1366, 768), (1920, 1000), (1366, 768)):
            desktop.window.resize(width, height)
            settle()
            workflow = page.workflow
            if index in (1, 2, 3, 4):
                selector = page.list_selector
                selected = selector.currentItem()
                assert selected is not None
                assert selector.viewport().rect().contains(selector.visualItemRect(selected))
            for label in (workflow.title, workflow.detail, workflow.reason):
                if label.isVisibleTo(page):
                    assert label.height() >= label.heightForWidth(label.width())
                    assert_inside(label, workflow)
            for step in workflow.step_widgets:
                assert_inside(step, workflow)
            assert workflow.height() >= workflow.layout().heightForWidth(workflow.contentsRect().width())
            if index == 1:
                assert_inside(page.gis_state_label, page.details_card)
                assert_inside(page.export_template_status, page.details_card)
                workspace = page.workspace_splitter.widget(1)
                assert not page.details_card.geometry().intersects(workflow.geometry()), (
                    width, height, workspace.geometry(), workspace.minimumSize(),
                    workspace.layout().totalMinimumSize(), workspace.heightForWidth(workspace.width()),
                )
            if index == 3:
                assert_inside(page.add_unplanned_button, page.unplanned_card)
                assert_inside(page.unplanned_toggle, page.unplanned_card)
                # Adjacent themed frame strokes may share a pixel; content and
                # the outside toolbar must never occupy the same rectangle.
                fact_content = page.data_card.contentsRect().translated(page.data_card.pos())
                unplanned_content = page.unplanned_card.contentsRect().translated(page.unplanned_card.pos())
                assert not fact_content.intersects(unplanned_content)
                for button in (page.add_unplanned_button, page.unplanned_toggle):
                    position = button.mapTo(page.unplanned_splitter, QPoint(0, 0))
                    assert not button.rect().translated(position).intersects(fact_content)
        page.workflow.help_button.setChecked(False)


def test_month_period_and_acts_commands_keep_separate_scopes(desktop, monkeypatch):
    for index in (3, 4):
        page = _select_page(desktop, index)
        settle()
        assert page.month_commands_group.caption.text() == "Действия с выбранным месяцем"
        assert page.period_commands_group.caption.text() == "Действия с выбранным периодом"
        current = page.load_gis_button if index == 4 else page.load_month_button
        clearing = page.clear_values_button if index == 4 else page.clear_month_button
        clear_period = page.clear_period_values_button if index == 4 else page.clear_period_button
        assert current.parentWidget() is page.month_commands_group
        assert clearing.parentWidget() is page.month_commands_group
        assert clear_period.parentWidget() is page.period_commands_group
        assert clearing.isVisibleTo(page) and clear_period.isVisibleTo(page)
        assert clearing.menu() is None and clear_period.menu() is None
        assert page.load_period_button.text() == "Загрузить период из ГИС"
        assert page.publish_period_button.text() == "Разместить период в ГИС"
        for group in (page.month_commands_group, page.period_commands_group):
            for i in range(group.actions.count()):
                widget = group.actions.itemAt(i).widget()
                if widget.isVisibleTo(page):
                    assert_inside(widget, group)
    page = desktop.window.quality_page
    commands = page.acts_commands_group.actions
    assert [commands.itemAt(i).widget() for i in range(commands.count())] == [
        page.month_acts_button, page.upload_acts_button, page.auto_act_month_button,
        page.auto_act_period_button, page.selected_act_button,
    ]
    assert page.upload_acts_button.text() == "Добавить готовый акт…"
    assert not page.create_act_button.isVisibleTo(page)
    assert not page.selection_actions.isVisibleTo(page)
    opened = []
    monkeypatch.setattr(page, "_open_month_acts", lambda **kwargs: opened.append(kwargs))
    page.upload_acts_button.click()
    assert opened == [{"start_upload": True}]


def test_header_selection_preserves_act_links_and_recalculation(desktop):
    page = _select_page(desktop, 4)
    item = page.store.list_items(page._current_period_id)[0]
    page.store.update_item(item.id, included=False, cost="100", days_count=2, cost_reduction="6.45")
    act = page.store.add_act(
        item_id=item.id, act_type="ACCEPTANCE_ACT", number="TEST", reg_date="2026-01-31",
        period_from="2026-01-01", period_to="2026-01-31", file_name="fictional.pdf",
        description=None, content_type="application/pdf", content=b"fictional local document",
    )
    page._load_table()
    settle()
    before = page.store.get_item(item.id)
    header = page.table.horizontalHeader()
    for command, included in ((page.include_all_button, True), (page.exclude_all_button, False)):
        assert command.parentWidget() is header.viewport()
        assert command.isVisibleTo(page)
        assert command.accessibleName()
        assert not command.icon().isNull()
        assert_inside(command, header)
        command.setFocus()
        QTest.keyClick(command, Qt.Key_Space)
        settle()
        assert replace(page.store.get_item(item.id), included=before.included) == before
        assert page.store.get_item(item.id).included is included
        assert page.store.get_act(act.id).content == act.content
        assert page.store.act_link_item_ids(act.id) == [item.id]
        assert page.table.item(0, 0).checkState() == (Qt.Checked if included else Qt.Unchecked)
    # The commands remain real buttons, rather than a clickable painted glyph.
    assert all(isinstance(command, QPushButton) for command in header.commands)


def test_header_selection_buttons_have_sequential_keyboard_focus(desktop):
    page = _select_page(desktop, 4)
    page.include_all_button.setFocus()
    settle()
    assert desktop.app.focusWidget() is page.include_all_button
    QTest.keyClick(page.include_all_button, Qt.Key_Tab)
    assert desktop.app.focusWidget() is page.exclude_all_button
    QTest.keyClick(page.exclude_all_button, Qt.Key_Backtab)
    assert desktop.app.focusWidget() is page.include_all_button
    QTest.keyClick(page.include_all_button, Qt.Key_Tab)
    assert desktop.app.focusWidget() is page.exclude_all_button
    QTest.keyClick(page.exclude_all_button, Qt.Key_Tab)
    assert desktop.app.focusWidget() is page.table
