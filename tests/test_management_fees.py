"""Fee data and Qt behavior against fictional local records and controlled workers."""
from copy import deepcopy
from dataclasses import replace
import sqlite3

import pytest
from PySide6.QtCore import QDate
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

import test_ui_workflow as workflows
from gis_jkh_assistant.app import management_fees_page
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.management_fees import (
    ManagementFeeStore, amount_text, helper_supports_fees, validate_document,
)

desktop, qt_app = workflows.desktop, workflows.qt_app
CONTRACT = {"guid": "contract-root", "versionGuid": "contract-version", "objectRootGuid": "object-root",
            "fiasHouseGuid": "34062768-dec0-4576-86cb-62bb82d854d2", "number": "TEST",
            "date": "2020-01-01", "from": "2020-01-01", "to": "2030-12-31"}
PROTOCOL = {"votingGuid": "old-protocol", "votingVersionGuid": "old-version", "protocolCode": "1",
            "protocolDate": "2020-01-01", "eligible": True, "cancelled": False,
            "fiasHouseCodes": [CONTRACT["fiasHouseGuid"]]}
DOCUMENT = {"contract": CONTRACT, "protocol": PROTOCOL, "amount": "15.12",
            "start": "2026-01-01", "end": "2026-12-31"}


def remote(**updates):
    return {"guid": "fee-root", "agreementRootGuid": CONTRACT["guid"], "managedObjectRootGuid": "object-root",
            "status": "DRAFT", "type": "BY_PROTOCOL", "protocolFromVoting": True,
            "managementPayment": "15.12", "lastUpdate": "revision-1",
            "period": {"from": "2026-01-01", "to": "2026-12-31"},
            "ownerVotingList": [{"votingGuid": "old-protocol", "votingVersionGuid": "old-version",
                                 "number": "1", "date": "2020-01-01"}], **updates}


def context(*records):
    return {"contracts": [deepcopy(CONTRACT)], "protocols": [deepcopy(PROTOCOL)],
            "records": [{"contractVersionGuid": CONTRACT["versionGuid"], "record": r} for r in records]}


@pytest.fixture
def store(tmp_path):
    repo = Repository(tmp_path / "fictional.db")
    org = repo.create_organization(name="TEST UO", ogrn="1022600000540")
    house = repo.create_house(organization_id=org.id, address="TEST house", fias_house_guid=CONTRACT["fiasHouseGuid"])
    yield ManagementFeeStore(repo), house.id
    repo.close()


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "1.234", "", "10000000000", "1e100"])
def test_amount_rejects_invalid_values(amount):
    with pytest.raises(ValueError):
        amount_text(amount)


def test_basis_is_required_only_for_remote_workflow(store):
    fee_store, house_id = store
    document = dict(DOCUMENT, protocol=None)
    record_id = fee_store.save_local(house_id, document)
    assert fee_store.get(record_id)["document"]["protocol"] is None
    with pytest.raises(ValueError, match="Выберите протокол"):
        validate_document(document)
    assert validate_document(dict(DOCUMENT, amount="15,12"))["amount"] == "15.12"
    assert helper_supports_fees("0.1.33")
    assert not helper_supports_fees("0.1.32")
    assert not helper_supports_fees("1.0")


def test_import_is_atomic_and_preserves_local_tariffs_and_history(store):
    fee_store, house_id = store
    repo = fee_store.repository
    repo.create_house_tariff(house_id=house_id, amount="11.50", start_year=2020, start_month=1)
    before = repo.list_house_tariffs(house_id)
    local = fee_store.save_local(house_id, dict(DOCUMENT, amount="16.00"))
    fee_store.import_remote(house_id, context(remote(status="TERMINATED")))
    history_id = next(row["id"] for row in fee_store.list(house_id) if row["remote_guid"])
    fee_store.import_remote(house_id, context())
    assert fee_store.get(history_id)["remote"]["status"] == "TERMINATED"
    assert fee_store.get(local)["document"]["amount"] == "16.00"
    assert repo.list_house_tariffs(house_id) == before
    old_rows = fee_store.list(house_id)
    invalid = context(remote(guid="second"), remote(guid="third", managedObjectRootGuid="wrong-house"))
    with pytest.raises(ValueError, match="не соответствует"):
        fee_store.import_remote(house_id, invalid)
    assert fee_store.list(house_id) == old_rows


def test_refresh_preserves_dirty_draft_and_uncertain_update(store):
    fee_store, house_id = store
    fee_store.import_remote(house_id, context(remote()))
    record_id = fee_store.list(house_id)[0]["id"]
    fee_store.save_local(house_id, dict(DOCUMENT, amount="16.00"), record_id)
    fee_store.import_remote(house_id, context(remote()))
    assert fee_store.get(record_id)["document"]["amount"] == "16.00"
    fee_store.mark_pending(record_id)
    fee_store.import_remote(house_id, context(remote()))
    row = fee_store.get(record_id)
    assert row["pending"] and row["document"]["amount"] == "16.00"
    fee_store.import_remote(house_id, context(remote(managementPayment="16.00")))
    assert not fee_store.get(record_id)["pending"]
    assert not fee_store.get(record_id)["dirty"]


def test_uncertain_create_is_reconciled_without_duplicate(store):
    fee_store, house_id = store
    record_id = fee_store.save_local(house_id, DOCUMENT)
    fee_store.mark_pending(record_id)
    fee_store.import_remote(house_id, context())
    assert fee_store.get(record_id)["pending"]
    fee_store.import_remote(house_id, context(remote()))
    assert len(fee_store.list(house_id)) == 1
    assert fee_store.get(record_id)["remote_guid"] == "fee-root"
    assert not fee_store.get(record_id)["pending"]


def test_ambiguous_remote_duplicates_do_not_select_an_arbitrary_result(store):
    fee_store, house_id = store
    record_id = fee_store.save_local(house_id, DOCUMENT)
    fee_store.mark_pending(record_id)
    fee_store.import_remote(house_id, context(remote(), remote(guid="another-fee-root")))
    assert fee_store.get(record_id)["pending"]
    assert fee_store.get(record_id)["remote_guid"] is None
    assert len(fee_store.list(house_id)) == 3


def fee_page(desktop, monkeypatch):
    window = desktop.window
    page = window.management_fees_page
    original = desktop.gateway.status
    monkeypatch.setattr(desktop.gateway, "status", lambda: replace(original(), helper_version="0.1.33"))
    window.navigation.setCurrentRow(window.pages.indexOf(page))
    page.store.import_remote(desktop.house.id, context())
    page._refresh_table()
    return page


def test_fee_section_preserves_shared_work_list_and_year(desktop, monkeypatch):
    window = desktop.window
    before = dict(window._shared_context)
    page = fee_page(desktop, monkeypatch)
    assert page.house_combo.currentData() == desktop.house.id
    assert window._shared_context == before
    assert window.navigation.item(2).text() == "Информация о размере платы"
    for index, target in enumerate((window.houses_page, window.work_lists_page, window.planning_page,
                                    window.fixation_page, window.quality_page, window.settings_page)):
        window._navigate_workflow_target(index)
        assert window.pages.currentWidget() is target


def test_new_project_copies_tariff_without_changing_it_and_does_not_select_basis(desktop, monkeypatch):
    repo = desktop.repository
    tariff = repo.create_house_tariff(house_id=desktop.house.id, amount="15.12", start_year=2026, start_month=1)
    page = fee_page(desktop, monkeypatch)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a: (a[3][0], True))
    page.new_button.click()
    row = page._record()
    assert row["document"]["protocol"] is None
    assert row["document"]["amount"] == "15.12"
    assert not page.approve_button.isEnabled()
    assert page.protocol_button.isEnabled()
    assert not any("скан" in button.text().lower() for button in page.findChildren(management_fees_page.QPushButton))
    page.store.save_local(desktop.house.id, dict(DOCUMENT, start="2027-01-01", end="2027-12-31"))
    page._refresh_table(row["id"])
    page.amount_edit.setText("16.00")
    window = desktop.window
    window._navigate_workflow_target(2)
    assert page.store.get(row["id"])["document"]["amount"] == "16.00"
    assert repo.get_house_tariff(tariff.id).amount == "15.12"
    window.navigation.setCurrentRow(window.pages.indexOf(page))
    assert page._record_id == row["id"]


def test_save_failure_retains_form_and_blocks_navigation_close(desktop, monkeypatch):
    page = fee_page(desktop, monkeypatch)
    record_id = page.store.save_local(desktop.house.id, DOCUMENT)
    page._refresh_table(record_id)
    page.amount_edit.setText("21.00")
    original = page.store.save_local
    monkeypatch.setattr(page.store, "save_local", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("TEST locked")))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    desktop.window._navigate_workflow_target(2)
    assert desktop.window.pages.currentWidget() is page
    event = QCloseEvent()
    desktop.window.closeEvent(event)
    assert not event.isAccepted()
    assert page.amount_edit.text() == "21.00"
    monkeypatch.setattr(page.store, "save_local", original)
    assert page.flush_pending_changes()


def test_send_persists_uncertainty_and_readback_enables_placement(desktop, monkeypatch):
    page = fee_page(desktop, monkeypatch)
    record_id = page.store.save_local(desktop.house.id, DOCUMENT)
    page._refresh_table(record_id)
    monkeypatch.setattr(management_fees_page, "GisIntegrationWorker", workflows.ControlledWorker)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page.save_button.click()
    worker = page._worker
    assert worker.command_type == "save_management_fee_project"
    assert page.store.get(record_id)["pending"]
    assert not page.approve_button.isEnabled()
    worker.succeeded.emit({"record": remote(), "contract": CONTRACT})
    worker.finish()
    workflows._settle(desktop.app)
    assert page.approve_button.isEnabled()
    page.approve_button.click()
    assert page._worker.command_type == "approve_management_fee"
    page._worker.succeeded.emit({"record": remote(status="APPROVED"), "contract": CONTRACT})
    page._worker.finish()
    workflows._settle(desktop.app)
    assert page.store.get(record_id)["remote"]["status"] == "APPROVED"
    assert not page.amount_edit.isEnabled()


def test_protocol_dialog_requires_explicit_selection(desktop, monkeypatch):
    page = fee_page(desktop, monkeypatch)
    record_id = page.store.save_local(desktop.house.id, dict(DOCUMENT, protocol=None))
    page._refresh_table(record_id)
    def choose(dialog):
        table = dialog.findChild(management_fees_page.QTableWidget)
        assert table.currentRow() == -1
        table.setCurrentCell(0, 0)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", choose)
    page.protocol_button.click()
    assert page._protocol["votingGuid"] == "old-protocol"
    assert page.flush_pending_changes()
    assert page.store.get(record_id)["document"]["protocol"]["protocolDate"] == "2020-01-01"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_fee_page_fits_100_percent_desktop(desktop, monkeypatch, theme):
    page = fee_page(desktop, monkeypatch)
    record_id = page.store.save_local(desktop.house.id, DOCUMENT)
    page._refresh_table(record_id)
    desktop.window._theme_changed(theme)
    desktop.window.resize(1366, 768)
    workflows._settle(desktop.app)
    assert page.page_scroll.horizontalScrollBar().maximum() == 0
    assert page.table.viewport().height() >= 100
    assert page.save_button.isVisibleTo(page)
    assert page.protocol_button.isVisibleTo(page)
    assert page.start_edit.date() == QDate(2026, 1, 1)
