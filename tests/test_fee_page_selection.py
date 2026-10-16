"""Explicit checked periods, action availability and local removal on fictional data."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

import test_management_fees as fees
from gis_jkh_assistant.app.fee_bulk_dialogs import FeeAmendmentDialog, FeeQueueDialog
from gis_jkh_assistant.app.widgets import SafeComboBox

desktop, qt_app, store = fees.desktop, fees.qt_app, fees.store


def _check(page, record_id, checked=True):
    for row in range(page.table.rowCount()):
        item = page.table.item(row, 0)
        if item.data(Qt.ItemDataRole.UserRole) == record_id:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            return row
    raise AssertionError("Missing fictional record")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_checks_control_bulk_commands_independently_of_editor(desktop, monkeypatch, theme):
    page = fees.fee_page(desktop, monkeypatch)
    getattr(desktop.window.settings_page, theme + "_radio").setChecked(True)
    page.store.import_remote(desktop.house.id, fees.context(fees.remote()))
    draft = page.store.list(desktop.house.id)[0]["id"]
    local = page.store.save_local(desktop.house.id, dict(fees.DOCUMENT, start="2025-01-01", end="2025-12-31"))
    page._refresh_table(local)
    assert not page._selected_record_ids()
    assert not page.bulk_save_button.isEnabled()
    assert not page.bulk_approve_button.isEnabled()
    _check(page, local)
    assert page.bulk_save_button.isEnabled()
    assert page.bulk_save_approve_button.isEnabled()
    assert not page.bulk_approve_button.isEnabled()
    draft_row = next(row for row in range(page.table.rowCount())
                     if page.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == draft)
    page.table.setCurrentCell(draft_row, 1)
    assert page._record_id == draft
    assert page._selected_record_ids() == [local]
    page.clear_selection_button.click()
    _check(page, draft)
    assert not page.bulk_save_button.isEnabled()
    assert page.bulk_approve_button.isEnabled()
    assert page.bulk_save_approve_button.isEnabled()
    page.amount_edit.setText("20.00")
    assert not page.bulk_approve_button.isEnabled()  # The open editor has unsaved changes.
    assert page.bulk_save_button.isEnabled()
    page.select_all_button.click()
    assert set(page._selected_record_ids()) == {local, draft}
    assert page.flush_pending_changes()
    page._refresh_table(local)
    assert set(page._selected_record_ids()) == {local, draft}
    assert page.store.get(draft)["document"]["amount"] == "20.00"
    page.clear_selection_button.click()
    assert not page._selected_record_ids()


def test_mouse_checkbox_keyboard_all_and_house_switch(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    first = page.store.save_local(desktop.house.id, fees.DOCUMENT)
    second = page.store.save_local(desktop.house.id, dict(fees.DOCUMENT, start="2025-01-01", end="2025-12-31"))
    page._refresh_table(first)
    desktop.app.processEvents()
    rect = page.table.visualItemRect(page.table.item(0, 0))
    QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                    pos=rect.topLeft() + QPoint(10, rect.height() // 2))
    assert page._selected_record_ids() == [first]
    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert set(page._selected_record_ids()) == {first, second}
    other = desktop.repository.create_house(organization_id=desktop.organization.id,
        address="Другой тестовый дом", fias_house_guid="fictional-other-house")
    page.refresh_organizations()
    page.house_combo.setCurrentIndex(page.house_combo.findData(other.id))
    assert not page._selected_record_ids()


def test_export_uses_only_checked_periods_and_current_action(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    first = page.store.save_local(desktop.house.id, fees.DOCUMENT)
    second = page.store.save_local(desktop.house.id, dict(fees.DOCUMENT, start="2025-01-01", end="2025-12-31"))
    page._refresh_table(second)
    _check(page, first)
    captured = []
    monkeypatch.setattr(FeeQueueDialog, "exec", lambda dialog: captured.extend(dialog.jobs) or QDialog.DialogCode.Rejected)
    page.bulk_save_approve_button.click()
    assert [(job.record_id, job.action) for job in captured] == [(first, "save_approve")]
    assert page._record_id == second


def test_edit_open_row_does_not_amend_other_checked_periods(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    page.store.import_remote(desktop.house.id, fees.context(*[
        fees.remote(guid=f"published-{year}", status="APPROVED",
                    period={"from": f"{year}-01-01", "to": f"{year}-12-31"})
        for year in (2024, 2025, 2026)]))
    rows = page.store.list(desktop.house.id)
    current = rows[0]["id"]
    page._refresh_table(current)
    for record in rows[1:]:
        _check(page, record["id"])
    monkeypatch.setattr(FeeAmendmentDialog, "exec", lambda *_: pytest.fail("Single-row edit must not open bulk amendment"))
    page.edit_button.click()
    assert page.store.get(current)["dirty"]
    assert all(not page.store.get(record["id"])["dirty"] for record in rows[1:])
    assert set(page._selected_record_ids()) == {record["id"] for record in rows[1:]}


def test_house_popup_reuses_window_bounded_component(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    assert isinstance(page.house_combo, SafeComboBox)
    for number in range(60):
        page.house_combo.addItem(f"Вымышленный длинный адрес дома {number}", f"unused-{number}")
    desktop.window.resize(1180, 780)
    desktop.app.processEvents()
    page.house_combo.showPopup()
    try:
        desktop.app.processEvents()
        popup = page.house_combo.view().window()
        window_top = desktop.window.mapToGlobal(QPoint(0, 0))
        assert popup.y() >= window_top.y()
        assert popup.y() + popup.height() <= window_top.y() + desktop.window.height()
        assert popup.height() < 600
        assert page.house_combo.view().verticalScrollBar().maximum() > 0
    finally:
        page.house_combo.hidePopup()


def test_all_available_local_projects_can_be_checked_and_updated_in_place(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    ids = []
    for year, amount in ((2025, "20.00"), (2026, "21.00")):
        desktop.repository.create_house_tariff(house_id=desktop.house.id, amount=amount, start_year=year, start_month=1)
        ids.append(page.store.save_local(desktop.house.id,
            dict(fees.DOCUMENT, start=f"{year}-01-01", end=f"{year}-12-31")))
    dialog = FeeAmendmentDialog(page, ids)
    try:
        assert all(not candidate.blocked for candidate in dialog.candidates)
        dialog.clear_button.click()
        assert all(dialog.preview.item(row, 0).checkState() == Qt.CheckState.Unchecked for row in range(2))
        dialog.select_button.click()
        assert all(dialog.preview.item(row, 0).checkState() == Qt.CheckState.Checked for row in range(2))
        dialog._apply()
        assert set(dialog.updated) == set(ids)
        assert [page.store.get(record_id)["document"]["amount"] for record_id in ids] == ["20.00", "21.00"]
        assert all(not page.store.get(record_id)["remote_guid"] for record_id in ids)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_periods_sort_by_dates_not_creation_order(store):
    fee_store, house_id = store
    for start in ("2020-07-01", "2026-07-01", "2021-07-01", "2026-01-01"):
        fee_store.save_local(house_id, dict(fees.DOCUMENT, start=start))
    assert [row["document"]["start"] for row in fee_store.list(house_id)] == [
        "2026-07-01", "2026-01-01", "2021-07-01", "2020-07-01"]


def test_annulled_copy_can_be_removed_without_changing_context_or_tariffs(store):
    fee_store, house_id = store
    source = fees.context(fees.remote(status="TERMINATED"))
    fee_store.import_remote(house_id, source)
    record = fee_store.list(house_id)[0]
    tariff = fee_store.repository.create_house_tariff(house_id=house_id, amount="21.00", start_year=2026, start_month=1)
    before_context = deepcopy(fee_store.context(house_id))
    fee_store.delete_local(record["id"])
    assert not fee_store.list(house_id)
    assert fee_store.context(house_id) == before_context
    assert fee_store.repository.get_house_tariff(tariff.id).amount == "21.00"
    fee_store.import_remote(house_id, source)
    restored = fee_store.list(house_id)[0]
    assert restored["remote"]["status"] == "TERMINATED"
    assert restored["remote_guid"] == record["remote_guid"]


@pytest.mark.parametrize("status,pending", [("DRAFT", False), ("APPROVED", False), ("TERMINATED", True)])
def test_active_or_unverified_remote_copy_cannot_be_removed(store, status, pending):
    fee_store, house_id = store
    fee_store.import_remote(house_id, fees.context(fees.remote(status=status)))
    record = fee_store.list(house_id)[0]
    if pending:
        fee_store.mark_pending(record["id"])
    with pytest.raises(ValueError):
        fee_store.delete_local(record["id"])
    assert fee_store.get(record["id"])


def test_annulled_row_delete_confirmation_explains_reload(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    page.store.import_remote(desktop.house.id, fees.context(fees.remote(status="TERMINATED")))
    page._refresh_table()
    assert page.delete_button.isEnabled()
    confirmations = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: confirmations.append(args[2]) or QMessageBox.StandardButton.Yes)
    page.delete_button.click()
    assert not page.store.list(desktop.house.id)
    assert "повторной загрузке" in confirmations[0]
