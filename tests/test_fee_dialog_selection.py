"""Fee review selections use fictional local data and never start a GIS worker."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QWidget

import test_management_fees as fees
from gis_jkh_assistant.app.fee_bulk_dialogs import (
    FeeAmendmentDialog, FeeQueueDialog, selected_rows,
)
from gis_jkh_assistant.app.widgets import SafeComboBox
from gis_jkh_assistant.fee_bulk import prepare_jobs

qt_app, store = fees.qt_app, fees.store


@pytest.fixture
def page(qt_app, store):
    fee_store, house_id = store
    widget = QWidget()
    widget.repository, widget.store, widget._house_id = fee_store.repository, fee_store, house_id
    widget.organization_combo = SafeComboBox(widget)
    house = widget.repository.get_house(house_id)
    widget.organization_combo.addItem("TEST UO", house.organization_id)
    fee_store.save_context(house_id, fees.context())
    yield widget
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def _jobs(page, action):
    return prepare_jobs(page.repository, page.store, page.organization_combo.currentData(),
                        [page._house_id], action)


def test_queue_select_available_checks_only_eligible_rows_and_explains_empty_selection(page, qt_app):
    ready = page.store.save_local(page._house_id, deepcopy(fees.DOCUMENT))
    blocked = page.store.save_local(page._house_id, dict(fees.DOCUMENT, protocol=None))
    dialog = FeeQueueDialog(page, _jobs(page, "save"))
    try:
        dialog.clear_button.click()
        assert dialog.selected_jobs() == []
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        dialog.select_button.click()
        assert [job.record_id for job in dialog.selected_jobs()] == [ready]
        blocked_row = next(i for i, job in enumerate(dialog.jobs) if job.record_id == blocked)
        item = dialog.table.item(blocked_row, 0)
        assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
        assert "протокол" in item.toolTip().lower()
        assert item.checkState() == Qt.CheckState.Unchecked
    finally:
        dialog.deleteLater()
        qt_app.processEvents()

    dialog = FeeQueueDialog(page, _jobs(page, "approve"))
    try:
        assert not dialog.select_button.isEnabled()
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        assert "Нет проектов, готовых к размещению" in dialog.selection_hint.text()
        assert "Сохранить и разместить" in dialog.selection_hint.text()
        assert all(not dialog.table.item(i, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
                   for i in range(dialog.table.rowCount()))
        assert dialog.selected_jobs() == []
    finally:
        dialog.deleteLater()


def test_combined_queue_review_names_both_steps_and_warns_about_basis_replacement(page):
    page.store.import_remote(page._house_id, fees.context(fees.remote(
        status="APPROVED", ownerVotingList=[], protocolFromVoting=False, hasScanBasis=True)))
    record = page.store.list(page._house_id)[0]
    page.store.begin_published_edit(record["id"])
    page.store.save_local(page._house_id, deepcopy(fees.DOCUMENT), record["id"])
    dialog = FeeQueueDialog(page, _jobs(page, "save_approve"))
    try:
        assert len(dialog.selected_jobs()) == 1
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text() == "Сохранить и разместить проекты в ГИС"
        caption = dialog.layout().itemAt(0).widget().text()
        assert "затем разместить" in caption
        assert "прежнее основание будет заменено" in caption
    finally:
        dialog.deleteLater()


def test_amendment_select_available_handles_local_projects_and_keeps_cancelled_unchecked(page, qt_app):
    page.repository.create_house_tariff(house_id=page._house_id, amount="21.00",
                                       start_year=2026, start_month=1)
    local_id = page.store.save_local(page._house_id, deepcopy(fees.DOCUMENT))
    page.store.import_remote(page._house_id, fees.context(fees.remote(
        guid="cancelled", status="TERMINATED", period={"from": "2025-01-01", "to": "2025-12-31"})))
    cancelled = next(record for record in page.store.list(page._house_id) if record["remote_guid"])
    dialog = FeeAmendmentDialog(page, [local_id, cancelled["id"]])
    try:
        assert isinstance(dialog.protocol_combo, SafeComboBox)
        assert isinstance(dialog.preview.cellWidget(0, 3), SafeComboBox)
        assert [dialog.candidates[row].record_id for row in selected_rows(dialog.preview)] == [local_id]
        dialog.clear_button.click()
        assert selected_rows(dialog.preview) == []
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        dialog.select_button.click()
        assert [dialog.candidates[row].record_id for row in selected_rows(dialog.preview)] == [local_id]
        dialog.show()
        qt_app.processEvents()
        cell = dialog.preview.item(1, 0)
        QTest.mouseClick(dialog.preview.viewport(), Qt.MouseButton.LeftButton,
                         pos=dialog.preview.visualItemRect(cell).center())
        assert cell.checkState() == Qt.CheckState.Unchecked
        dialog._apply()
        assert dialog.updated == [local_id]
        assert page.store.get(local_id)["document"]["amount"] == "21.00"
        assert page.store.get(cancelled["id"])["remote"]["status"] == "TERMINATED"
    finally:
        dialog.close()
        dialog.deleteLater()


def test_amendment_with_only_cancelled_records_explains_why_selection_is_unavailable(page):
    page.repository.create_house_tariff(house_id=page._house_id, amount="21.00",
                                       start_year=2026, start_month=1)
    page.store.import_remote(page._house_id, fees.context(fees.remote(status="TERMINATED")))
    record_id = page.store.list(page._house_id)[0]["id"]
    dialog = FeeAmendmentDialog(page, [record_id])
    try:
        assert not dialog.select_button.isEnabled()
        assert "Нет доступных изменений" in dialog.selection_hint.text()
        assert "аннулированные" in dialog.selection_hint.text()
        assert selected_rows(dialog.preview) == []
    finally:
        dialog.deleteLater()
