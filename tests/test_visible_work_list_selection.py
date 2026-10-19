"""Visible registry mouse/keyboard changes reach each page's actual controller."""

import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QListWidget

import test_ui_workflow as workflows


desktop, qt_app = workflows.desktop, workflows.qt_app


@pytest.mark.parametrize("section", ["registry", "planning"])
@pytest.mark.parametrize("interaction", ["mouse", "keyboard"])
def test_visible_selection_updates_hidden_source_and_current_card(desktop, monkeypatch, section, interaction):
    repository = desktop.repository
    for year in (2023, 2024, 2025):
        work_list = repository.create_work_list(
            house_id=desktop.house.id, base_year=year,
            items=[SimpleNamespace(work_name=f"Тестовая работа {year}", quantity="1", cost="100")],
        )
        repository.update_work_list_gis_state(
            work_list.id, gis_work_list_guid=f"placed-visible-selection-{year}", gis_status="APPROVED",
        )
    page = workflows._select_page(desktop, 1 if section == "registry" else 2)
    if section == "registry":
        page.refresh_work_lists()
        source = page.work_lists
        assert isinstance(source, QListWidget)
    else:
        page._house_changed()
        source = page.work_list_combo
        assert isinstance(source, QComboBox)
    workflows._settle(desktop.app)
    selector = page.list_selector
    assert selector.isVisible()
    assert source.isHidden()
    assert selector.rowCount() == 4
    original = page._current_work_list_id
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: errors.append((kind, str(value))))

    if interaction == "mouse":
        target_row = 1
        selector.scrollToItem(selector.item(target_row, 0))
        workflows._settle(desktop.app)
        position = selector.visualRect(selector.model().index(target_row, 0)).center()
        assert selector.viewport().rect().contains(position)
        QTest.mouseClick(selector.viewport(), Qt.MouseButton.LeftButton, pos=position)
    else:
        target_row = selector.currentRow() - 1
        assert target_row >= 0
        selector.setFocus()
        QTest.keyClick(selector, Qt.Key.Key_Up)
    workflows._settle(desktop.app)
    assert not errors, f"The visible selector raised a Qt slot exception: {errors}"
    expected_id = selector.item(target_row, 0).data(Qt.ItemDataRole.UserRole)
    expected = repository.get_work_list(expected_id)
    assert expected_id != original
    assert selector.currentRow() == target_row
    source_id = (source.currentItem().data(Qt.ItemDataRole.UserRole)
                 if isinstance(source, QListWidget) else source.currentData())
    assert source_id == expected_id
    assert page._current_work_list_id == expected_id
    if section == "registry":
        assert page.start_year.value() == expected.start_year
        assert page.end_year.value() == expected.end_year
        assert expected.period_label in page.selected_label.text()
        assert page.items_table.item(0, 0).text() == f"Тестовая работа {expected.base_year}"
    else:
        assert page.year_combo.currentData() == expected.base_year
        assert repository.get_work_plan(page._current_plan_id).work_list_id == expected_id
        assert page.table.item(0, 0).text() == f"Тестовая работа {expected.base_year}"
    assert not errors
