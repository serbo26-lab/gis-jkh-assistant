"""Import flawed published data and amend selected periods of one fictional house."""
from copy import deepcopy
from datetime import date
import json
import sqlite3

import pytest
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QInputDialog, QMessageBox

import test_management_fees as fees
import test_fee_bulk_ui as bulk_ui
from test_house_fee_import import apply, placed
from gis_jkh_assistant.app import management_fees_page as page_module
from gis_jkh_assistant.app.fee_bulk_dialogs import FeeAmendmentDialog, FeeGenerationDialog, FeeQueueDialog
from gis_jkh_assistant.fee_bulk import apply_amendments, prepare_amendments, prepare_jobs
from gis_jkh_assistant.management_fees import validate_document

store, desktop, qt_app = fees.store, fees.desktop, fees.qt_app


def amendments_setup(store):
    fee_store, house_id = store
    repo = fee_store.repository
    house = repo.get_house(house_id)
    first = repo.create_house_tariff(house_id=house_id, amount="20.00", start_year=2026, start_month=1)
    second = repo.create_house_tariff(house_id=house_id, amount="22.00", start_year=2026, start_month=7)
    fee_store.import_remote(house_id, fees.context(
        placed("first", "2026-01-01", "2026-05-31", amount="15.00"),
        placed("second", "2026-06-01", "2026-12-31", amount="17.00", ownerVotingList=[], protocolFromVoting=False)))
    records = {r["remote_guid"]: r for r in fee_store.list(house_id)}
    ids = [records[key]["id"] for key in ("first", "second")]
    return repo, fee_store, house, ids, {ids[0]: first.id, ids[1]: second.id}


def test_outside_management_record_is_imported_for_correction_but_not_used_as_a_tariff(store):
    fee_store, house_id = store
    response = fees.context(placed(start="2019-06-01", end="2024-05-31"))
    response["contracts"][0]["from"] = "2019-07-01"
    job = apply(fee_store, house_id, response)
    assert job.state == "review" and "вне срока управления: 1" in job.detail
    assert fee_store.list(house_id)[0]["document"]["start"] == "2019-06-01"
    assert fee_store.repository.list_effective_house_tariffs(house_id) == []
    assert fee_store.repository.list_house_tariffs(house_id) == []
    with pytest.raises(ValueError, match="выходит"):
        validate_document(fee_store.list(house_id)[0]["document"])


def test_partial_agreement_import_retains_previous_snapshot_and_successful_records(store):
    fee_store, house_id = store
    apply(fee_store, house_id, fees.context(placed("old")))
    # A snapshot from 0.1.33 has no per-record warning field.
    snapshot = json.loads(fee_store.connection.execute(
        "SELECT value FROM house_fee_tariff_snapshots WHERE house_id=?", (house_id,)).fetchone()[0])
    for record in snapshot["records"]:
        record.pop("warning", None)
    fee_store.connection.execute("UPDATE house_fee_tariff_snapshots SET value=? WHERE house_id=?",
                                (json.dumps(snapshot), house_id))
    fee_store.connection.commit()
    response = fees.context(placed("new", "2025-01-01", "2025-12-31"))
    response["contractErrors"] = [{"number": "Старый", "message": "Не прочитан"}]
    job = apply(fee_store, house_id, response)
    assert job.state == "review" and "Частичная загрузка" in job.detail
    assert {r["remote_guid"] for r in fee_store.list(house_id)} == {"old", "new"}
    assert fee_store.repository.find_house_tariff_at_date(house_id, date(2026, 2, 1)) is not None
    assert fee_store.repository.find_house_tariff_at_date(house_id, date(2025, 2, 1)) is not None


def test_selected_published_records_are_amended_in_place_without_changing_tariffs(store):
    repo, fee_store, house, ids, choices = amendments_setup(store)
    before_tariffs = repo.list_house_tariffs(house.id)
    originals = [deepcopy(fee_store.get(i)) for i in ids]
    preview = prepare_amendments(repo, fee_store, house.organization_id, house.id, ids, fees.PROTOCOL, choices)
    assert not any(c.blocked for c in preview)
    assert [(c.document["start"], c.document["end"], c.document["amount"]) for c in preview] == [
        ("2026-01-01", "2026-06-30", "20.00"), ("2026-07-01", "2026-12-31", "22.00")]
    assert apply_amendments(repo, fee_store, house.organization_id, house.id, preview, fees.PROTOCOL) == ids
    assert len(fee_store.list(house.id)) == 2
    for record_id, original in zip(ids, originals):
        saved = fee_store.get(record_id)
        assert saved["remote"] == original["remote"] and saved["remote_guid"] == original["remote_guid"]
        assert saved["dirty"] and not saved["pending"]
    assert repo.list_house_tariffs(house.id) == before_tariffs
    assert all(job.state == "ready" for job in prepare_jobs(repo, fee_store, house.organization_id, [house.id], "save"))
    assert all(job.state == "blocked" for job in prepare_jobs(repo, fee_store, house.organization_id, [house.id], "approve"))


def test_ambiguous_mapping_is_not_guessed_and_cancelled_records_cannot_be_amended(store):
    repo, fee_store, house, ids, choices = amendments_setup(store)
    preview = prepare_amendments(repo, fee_store, house.organization_id, house.id, [ids[1]])
    assert preview[0].blocked and preview[0].tariff_id is None
    assert "однозначно" in preview[0].detail
    fee_store.import_remote(house.id, fees.context(dict(placed("second"), status="TERMINATED")))
    preview = prepare_amendments(repo, fee_store, house.organization_id, house.id, [ids[1]], fees.PROTOCOL, choices)
    assert preview[0].blocked


@pytest.mark.parametrize("failure", ["tariff", "remote", "database"])
def test_amendment_rechecks_review_and_rolls_back_the_whole_selection(store, monkeypatch, failure):
    repo, fee_store, house, ids, choices = amendments_setup(store)
    preview = prepare_amendments(repo, fee_store, house.organization_id, house.id, ids, fees.PROTOCOL, choices)
    if failure == "tariff":
        tariff = repo.get_house_tariff(choices[ids[0]])
        repo.update_house_tariff(tariff.id, amount="99.00", start_year=2026, start_month=1)
    elif failure == "remote":
        fee_store.import_remote(house.id, fees.context(placed("first", "2026-01-01", "2026-05-31", lastUpdate="changed")))
    else:
        repo.connection.execute("CREATE TRIGGER fail_second_fee BEFORE UPDATE ON management_fee_records "
            "WHEN OLD.remote_guid='second' BEGIN SELECT RAISE(ABORT, 'TEST'); END")
    before = list(repo.connection.iterdump())
    with pytest.raises((ValueError, sqlite3.Error)):
        apply_amendments(repo, fee_store, house.organization_id, house.id, preview, fees.PROTOCOL)
    assert list(repo.connection.iterdump()) == before


def test_single_house_scope_for_generation_protocols_and_export_selection(desktop, monkeypatch):
    page, documents = bulk_ui._setup(desktop, monkeypatch)
    ids = bulk_ui._records(page, documents)
    page._refresh_table(ids[0])
    dialog = FeeGenerationDialog(page)
    try:
        assert dialog.house_ids() == [desktop.house.id]
        assert not hasattr(dialog, "houses_table")
        assert all(dialog.protocol_combo.itemData(i)["votingGuid"] == fees.PROTOCOL["votingGuid"]
                   for i in range(1, dialog.protocol_combo.count()))
        assert {c.house_id for c in dialog.candidates} <= {desktop.house.id}
    finally:
        dialog.deleteLater()
    captured = []
    monkeypatch.setattr(FeeQueueDialog, "exec", lambda dialog: captured.extend(dialog.jobs) or QDialog.DialogCode.Rejected)
    page._start_bulk("save")
    assert {job.house_id for job in captured} == {desktop.house.id}
    assert ids[1] not in {job.record_id for job in captured}
    assert "дома" in page.load_button.text()
    assert not hasattr(page, "bulk_load_button")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_amendment_dialog_shows_old_and_new_values_and_preserves_identity(desktop, monkeypatch, theme):
    page = fees.fee_page(desktop, monkeypatch)
    getattr(desktop.window.settings_page, theme + "_radio").setChecked(True)
    repo = desktop.repository
    repo.create_house_tariff(house_id=desktop.house.id, amount="21.00", start_year=2026, start_month=1)
    page.store.import_remote(desktop.house.id, fees.context(placed("published", amount="15.00", ownerVotingList=[])))
    record = page.store.list(desktop.house.id)[0]
    page._refresh_table(record["id"])
    assert page.edit_button.isEnabled()
    dialog = FeeAmendmentDialog(page, [record["id"]])
    try:
        dialog.show()
        desktop.app.processEvents()
        assert dialog.candidates[0].document["amount"] == "21.00"
        assert dialog.preview.item(0, 2).text() == "15.00"
        dialog.protocol_combo.setCurrentIndex(1)
        desktop.app.processEvents()
        assert len([field for field in dialog.preview.findChildren(QComboBox) if field.isVisible()]) == 1
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        dialog._apply()
        assert dialog.result() == QDialog.DialogCode.Accepted
        saved = page.store.get(record["id"])
        assert saved["document"]["amount"] == "21.00" and saved["remote_guid"] == "published"
        assert saved["document"]["protocol"]["votingGuid"] == fees.PROTOCOL["votingGuid"]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_annulment_does_not_require_protocol_or_valid_new_period(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    page.store.import_remote(desktop.house.id, fees.context(placed("published", "2019-06-01", "2024-05-31",
        ownerVotingList=[], protocolFromVoting=False, hasOtherDetails=True)))
    record = page.store.list(desktop.house.id)[0]
    page._refresh_table(record["id"])
    assert page.annul_button.isEnabled()
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *args: ("Вымышленная причина", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(page_module, "GisIntegrationWorker", fees.workflows.ControlledWorker)
    page.annul_button.click()
    worker = page._worker
    assert worker.command_type == "annul_management_fee"
    assert worker.payload["remoteGuid"] == "published" and worker.payload["reason"]
    worker.succeeded.emit({"record": dict(placed("published", "2019-06-01", "2024-05-31"), status="TERMINATED"),
                           "contract": fees.CONTRACT})
    worker.finish()
    desktop.app.processEvents()
    assert page.store.get(record["id"])["remote"]["status"] == "TERMINATED"


def test_unchanged_published_amendment_is_not_reported_as_uncertain(desktop, monkeypatch):
    page, documents = bulk_ui._setup(desktop, monkeypatch)
    house_id, document = documents[0]
    page.store.import_remote(house_id, fees.context(placed()))
    record = page.store.list(house_id)[0]
    page.store.begin_published_edit(record["id"])
    jobs = prepare_jobs(page.repository, page.store, desktop.organization.id, [house_id], "save")
    page.fee_queue.start(jobs)
    bulk_ui._settle(desktop)
    worker = page.fee_queue.worker
    worker.succeeded.emit(bulk_ui._reply(document, guid=record["remote_guid"], status="APPROVED"))
    worker.finish()
    bulk_ui._settle(desktop)
    assert page.fee_queue.jobs[0].state == "done"
    assert not page.store.get(record["id"])["pending"]
    assert not page.store.get(record["id"])["dirty"]
