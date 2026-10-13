"""Nielsen iteration: retained edits, truthful outcomes and safe attachments."""
from __future__ import annotations

import sqlite3

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

import test_ui_workflow as workflows
from gis_jkh_assistant.app.fixation_dialogs import FixationPhotoDialog
from gis_jkh_assistant.app.gis_integration_worker import connect_result
from gis_jkh_assistant.app.workflow_widgets import OperationStatus

desktop = workflows.desktop
qt_app = workflows.qt_app


@pytest.mark.parametrize("setting", ["theme", "scale", "context"])
def test_failed_setting_restores_selection_without_applying(desktop, monkeypatch, setting):
    page = desktop.window.settings_page
    messages, applied = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: messages.append(a[2]))
    page.themeChanged.connect(applied.append)
    page.scaleChanged.connect(applied.append)
    page.sharedContextChanged.connect(applied.append)
    def fail(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    with monkeypatch.context() as patch:
        patch.setattr(desktop.repository, "set_setting", fail)
        if setting == "theme":
            page.dark_radio.click()
            assert page.light_radio.isChecked() and not page.dark_radio.isChecked()
        elif setting == "scale":
            page._scale_changed(150)
            assert page.scale_combo.currentData() == 100
            assert page.scale_combo.isHidden() and not page.scale_combo.isEnabled()
            assert not applied and not messages
            return  # Suspended scale has no persistence operation to fail.
        else:
            page.independent_radio.click()
            assert page.shared_radio.isChecked() and not page.independent_radio.isChecked()
        assert not applied
        assert messages and "не применено" in messages[0]
        assert "ошибка" in page.save_status.text()


def test_failed_list_edit_keeps_input_and_gates_refresh_until_retry(desktop, monkeypatch):
    repo, window = desktop.repository, desktop.window
    repo.update_work_list_gis_state(desktop.work_list.id, gis_status="DRAFT")
    window.navigation.setCurrentRow(1)
    page = window.work_lists_page
    page.refresh_work_lists(select_id=desktop.work_list.id)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: messages.append(a[2]))
    cell = page.items_table.item(0, 1)
    item_id = repo.list_work_list_items(desktop.work_list.id)[0].id
    original = repo.list_work_list_items(desktop.work_list.id)[0].price
    update = repo.update_work_list_item
    def fail(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    with monkeypatch.context() as patch:
        patch.setattr(repo, "update_work_list_item", fail)
        cell.setText("321.45")
        assert cell.text() == "321.45"
        assert repo.list_work_list_items(desktop.work_list.id)[0].price == original
        assert page.has_pending_save_error()
        window.navigation.setCurrentRow(2)
        assert window.pages.currentIndex() == window.navigation.currentRow() == 1
        assert page.flush_pending_changes() is False
        assert "Изменения остались в таблице, но ещё не сохранены" in messages[0]
        assert page.items_table.isEnabled()
        assert not page.registry_combo.isEnabled()
        page.refresh_work_lists(select_id=desktop.work_list.id)
        assert page.items_table.item(0, 1).text() == "321.45"
    assert page.flush_pending_changes() is True
    assert repo.list_work_list_items(desktop.work_list.id)[0].price == "321.45"
    assert not page.has_pending_save_error()
    assert page.registry_combo.isEnabled()
    assert update is not None and item_id
    window.navigation.setCurrentRow(2)
    assert window.pages.currentIndex() == 2


def test_failed_period_retains_input_and_retry_saves(desktop, monkeypatch):
    repo, window = desktop.repository, desktop.window
    repo.update_work_list_gis_state(desktop.work_list.id, gis_status="DRAFT")
    window.navigation.setCurrentRow(1)
    page = window.work_lists_page
    page.refresh_work_lists(select_id=desktop.work_list.id)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    def fail(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    with monkeypatch.context() as patch:
        patch.setattr(repo, "update_work_list_period", fail)
        page.end_month.setCurrentIndex(10)
        assert page.flush_pending_changes() is False
        assert repo.get_work_list(desktop.work_list.id).end_month == 12
        assert page.end_month.currentIndex() == 10
        window.navigation.setCurrentRow(2)
        assert window.pages.currentIndex() == window.navigation.currentRow() == 1
    assert page.flush_pending_changes() is True
    assert repo.get_work_list(desktop.work_list.id).end_month == 11


@pytest.mark.parametrize("selector", ["organization", "house", "work_list"])
@pytest.mark.parametrize("failure", ["sqlite", "invalid_period"])
def test_first_flush_error_restores_owner_selection(desktop, monkeypatch, selector, failure):
    repo, window = desktop.repository, desktop.window
    extra_org = repo.create_organization(name="Another fixture organization", ogrn="0000000000059")
    extra_house = repo.create_house(organization_id=desktop.organization.id, address="Another fixture house")
    extra_list = repo.create_work_list(house_id=desktop.house.id, base_year=2027, items=[])
    repo.update_work_list_gis_state(desktop.work_list.id, gis_status="DRAFT")
    window.navigation.setCurrentRow(1)
    page = window.work_lists_page
    page.refresh_organizations()
    page.refresh_work_lists(select_id=desktop.work_list.id)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    def fail(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    page.end_month.setCurrentIndex(10)  # Pending timer, no prior failure/lock.
    if failure == "invalid_period":
        page.start_month.setCurrentIndex(11)
        page.end_month.setCurrentIndex(0)
    with monkeypatch.context() as patch:
        if failure == "sqlite":
            patch.setattr(repo, "update_work_list_period", fail)
        if selector == "organization":
            page.organization_combo.setCurrentIndex(page.organization_combo.findData(extra_org.id))
        elif selector == "house":
            page.house_combo.setCurrentIndex(page.house_combo.findData(extra_house.id))
        else:
            page.registry_combo.setCurrentIndex(page.registry_combo.findData(extra_list.id))
        assert page.organization_combo.currentData() == desktop.organization.id
        assert page.house_combo.currentData() == desktop.house.id
        assert page.registry_combo.currentData() == page._current_work_list_id == desktop.work_list.id
        assert page.end_month.currentIndex() == (0 if failure == "invalid_period" else 10)
        assert page.has_pending_save_error()
        assert repo.get_work_list(desktop.work_list.id).end_month == 12
    if failure == "invalid_period":
        page.end_month.setCurrentIndex(11)
    assert page.flush_pending_changes()
    assert repo.get_work_list(desktop.work_list.id).end_month == (12 if failure == "invalid_period" else 11)


def test_photo_cancel_and_missing_file_preserve_existing_attachment(desktop, monkeypatch, tmp_path):
    window = desktop.window
    window.navigation.setCurrentRow(3)
    page = window.fixation_page
    item = desktop.repository.list_work_fixation_items(page._current_period_id)[0]
    photo = desktop.repository.add_work_fixation_photo(fixation_item_id=item.id,
        file_name="existing.jpg", content_type="image/jpeg", content=b"fixture", description="fixture")
    dialog = FixationPhotoDialog(desktop.repository, fixation_item_id=item.id, work_name=item.work_name, parent=page)
    assert item.work_name in dialog.context_label.text()
    assert page.house_combo.currentText() in dialog.context_label.text()
    assert page.month_combo.currentText() in dialog.context_label.text()
    dialog.list.setCurrentRow(0)
    questions, warnings = [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    dialog._remove()
    assert questions and questions[0][-1] == QMessageBox.StandardButton.No
    assert desktop.repository.list_work_fixation_photos(fixation_item_id=item.id)[0].id == photo.id
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a: ([str(tmp_path / "missing.jpg")], ""))
    dialog._add()
    assert "больше не доступен" in warnings[0]
    assert len(desktop.repository.list_work_fixation_photos(fixation_item_id=item.id)) == 1
    dialog.close()


def test_transport_success_is_neutral_and_uncaught_apply_error_is_visible(desktop, monkeypatch):
    class Worker(QObject):
        succeeded = Signal(dict)
        failed = Signal(str)
        progressed = Signal(dict)
    page = desktop.window.houses_page
    worker = Worker(page)
    worker.payload = {}
    strip = OperationStatus(parent=page)
    page.operation_status = strip
    strip.bind_worker(worker, "Загрузка домов")
    worker.succeeded.emit({"items": []})
    assert "проверка результата" in strip.label.text()
    assert strip.property("tone") != "success"
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: messages.append(a[2]))
    def fail(result):
        raise sqlite3.OperationalError("database is locked")
    connect_result(worker, worker.succeeded, fail)
    worker.succeeded.emit({"items": [{"address": "fixture"}]})
    assert strip.property("tone") == "danger"
    assert "Не удалось применить" in strip.label.text()
    assert messages and "изменения в ГИС не отменяются автоматически" in messages[0]
    assert desktop.window._result_callbacks == 0


def test_empty_house_card_does_not_display_previous_object(desktop):
    page = desktop.window.houses_page
    page._load_house(desktop.house.id)
    assert page.address_edit.text()
    page._load_house(None)
    assert page.address_edit.text() == ""
    assert page.card_org_combo.currentIndex() == -1
    assert not page._pending_fields
    assert desktop.repository.get_house(desktop.house.id).address == desktop.house.address


def test_fixed_scope_menu_names_actual_targets_without_changing_view(desktop):
    for index in (3, 4):
        desktop.window.navigation.setCurrentRow(index)
        page = desktop.window.pages.currentWidget()
        page._select_month(9)
        page.scope_switcher.set_scope("period")
        page._refresh_gis_buttons()
        assert page.month_combo.currentData() == 9
        assert "Сентябрь 2026" in page.month_actions.menu_model.title()
        assert "2026 года — 01–12 (12)" in page.period_actions.menu_model.title()
        assert page.data_actions.submenus == [page.month_actions, page.period_actions]
