"""Real Qt fee generation and queued exchanges against controlled GIS responses."""
from copy import deepcopy
import sqlite3

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

import test_management_fees as fees
import test_ui_workflow as workflows
from gis_jkh_assistant.app import fee_bulk_queue
from gis_jkh_assistant.app.fee_bulk_dialogs import FeeGenerationDialog, FeeQueueDialog
from gis_jkh_assistant.fee_bulk import prepare_jobs
from gis_jkh_assistant.gis_integration import IntegrationError

desktop, qt_app = workflows.desktop, workflows.qt_app


def _settle(desktop):
    for _ in range(6):
        desktop.app.processEvents()


def _setup(desktop, monkeypatch):
    page = fees.fee_page(desktop, monkeypatch)
    monkeypatch.setattr(fee_bulk_queue, "GisIntegrationWorker", workflows.ControlledWorker)
    repo = desktop.repository
    other = repo.create_house(organization_id=desktop.organization.id, address="Другой вымышленный дом",
                              fias_house_guid="other-fias")
    contract = dict(fees.CONTRACT, guid="other-contract", versionGuid="other-v", objectRootGuid="other-o",
                    fiasHouseGuid=other.fias_house_guid)
    protocol = dict(fees.PROTOCOL, votingGuid="other-protocol", votingVersionGuid="other-protocol-v",
                    fiasHouseCodes=[other.fias_house_guid])
    page.store.save_context(other.id, {"contracts": [contract], "protocols": [protocol]})
    for house_id in (desktop.house.id, other.id):
        repo.create_house_tariff(house_id=house_id, amount="15.12", start_year=2026, start_month=1)
    documents = [(desktop.house.id, deepcopy(fees.DOCUMENT)),
                 (other.id, dict(fees.DOCUMENT, contract=contract, protocol=protocol))]
    return page, documents


def _records(page, documents):
    return [page.store.save_local(house_id, document) for house_id, document in documents]


def _jobs(page, action):
    org = page.organization_combo.currentData()
    return prepare_jobs(page.repository, page.store, org,
                        [house.id for house in page.repository.list_houses(org)], action)


def _reply(document, *, guid, status="DRAFT"):
    contract, protocol = document["contract"], document["protocol"]
    return {"contract": contract, "record": fees.remote(guid=guid, agreementRootGuid=contract["guid"],
        managedObjectRootGuid=contract["objectRootGuid"], status=status, managementPayment=document["amount"],
        period={"from": document["start"], "to": document["end"]},
        ownerVotingList=[{"votingGuid": protocol["votingGuid"], "votingVersionGuid": protocol["votingVersionGuid"],
                          "number": protocol["protocolCode"], "date": protocol["protocolDate"]}])}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_generation_asks_default_basis_and_keeps_per_house_basis_editable(desktop, monkeypatch, theme):
    page, documents = _setup(desktop, monkeypatch)
    getattr(desktop.window.settings_page, theme + "_radio").setChecked(True)
    before = [desktop.repository.list_house_tariffs(house_id) for house_id, _ in documents]
    dialog = FeeGenerationDialog(page)
    dialog.show()
    _settle(desktop)
    assert dialog.protocol_combo.currentIndex() == -1
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled()
    index = next(i for i in range(1, dialog.protocol_combo.count())
                 if dialog.protocol_combo.itemData(i)["votingGuid"] == fees.PROTOCOL["votingGuid"])
    dialog.protocol_combo.setCurrentIndex(index)
    assert ok.isEnabled()
    dialog._create()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert len(dialog.created) == 2
    rows = [page.store.get(record_id) for _, record_id in dialog.created]
    first = next(row for row in rows if row["house_id"] == desktop.house.id)
    other = next(row for row in rows if row["house_id"] != desktop.house.id)
    assert first["document"]["protocol"]["votingGuid"] == fees.PROTOCOL["votingGuid"]
    assert other["document"]["protocol"] is None
    page._refresh_table(first["id"])
    assert page.protocol_button.isEnabled()
    assert [desktop.repository.list_house_tariffs(house_id) for house_id, _ in documents] == before
    dialog.deleteLater()


def test_queue_save_then_approve_is_serial_and_tracks_correct_houses(desktop, monkeypatch):
    page, documents = _setup(desktop, monkeypatch)
    ids = _records(page, documents)
    queue = page.fee_queue
    assert queue.start(_jobs(page, "save"))
    _settle(desktop)
    assert queue.worker is not None and page._queue_running
    assert not page.house_combo.isEnabled()
    assert not desktop.window.organizations_page.organization_combo.isEnabled()
    for step in range(2):
        job = next(job for job in queue.jobs if job.state == "running")
        worker = queue.worker
        assert worker.command_type == "save_management_fee_project"
        assert worker.payload["fiasHouseGuid"] == desktop.repository.get_house(job.house_id).fias_house_guid
        assert page.store.get(job.record_id)["pending"]
        worker.succeeded.emit(_reply(job.document, guid=f"created-{step}"))
        assert queue.worker is worker  # Never advance before finished.
        worker.finish()
        _settle(desktop)
    assert not queue.running and all(job.state == "done" for job in queue.jobs)
    assert not desktop.window._active_workers
    assert all(page.store.get(record_id)["remote"]["status"] == "DRAFT" for record_id in ids)
    assert all(not page.store.get(record_id)["pending"] for record_id in ids)
    assert queue.start(_jobs(page, "approve"))
    _settle(desktop)
    for _ in range(2):
        job = next(job for job in queue.jobs if job.state == "running")
        assert queue.worker.command_type == "approve_management_fee"
        reply = _reply(job.document, guid=page.store.get(job.record_id)["remote_guid"], status="APPROVED")
        queue.worker.succeeded.emit(reply)
        queue.worker.finish()
        _settle(desktop)
    assert all(page.store.get(record_id)["remote"]["status"] == "APPROVED" for record_id in ids)


@pytest.mark.parametrize("outcome", ["stop", "network", "wrong_reply"])
def test_stop_and_uncertain_errors_do_not_retry_completed_or_unknown_writes(desktop, monkeypatch, outcome):
    page, documents = _setup(desktop, monkeypatch)
    _records(page, documents)
    queue = page.fee_queue
    queue.start(_jobs(page, "save"))
    _settle(desktop)
    job, worker = next(job for job in queue.jobs if job.state == "running"), queue.worker
    if outcome == "stop":
        queue.request_stop()
        worker.succeeded.emit(_reply(job.document, guid="created-first"))
    elif outcome == "network":
        worker.failed.emit("TEST timeout after request")
    else:
        reply = _reply(job.document, guid="created-first")
        reply["record"]["managementPayment"] = "99.00"
        worker.succeeded.emit(reply)
    worker.finish()
    _settle(desktop)
    assert not queue.running
    assert job.state == ("done" if outcome == "stop" else "uncertain")
    assert sum(other.state == "stopped" for other in queue.jobs) == 1
    pending = page.store.get(job.record_id)["pending"]
    assert bool(pending) == (outcome != "stop")
    assert queue.resume()
    _settle(desktop)
    remaining = next(other for other in queue.jobs if other.state == "running")
    assert remaining.record_id != job.record_id
    queue.worker.succeeded.emit(_reply(remaining.document, guid="created-second"))
    queue.worker.finish()
    _settle(desktop)
    assert job.state == ("done" if outcome == "stop" else "uncertain")
    assert not queue.resume()


@pytest.mark.parametrize("change", ["review", "database", "constructor"])
def test_changed_review_and_database_failure_never_dispatch_a_write(desktop, monkeypatch, change):
    page, documents = _setup(desktop, monkeypatch)
    _records(page, documents)
    job = _jobs(page, "save")[0]
    if change == "review":
        page.store.save_local(job.house_id, dict(job.document, amount="20.00"), job.record_id)
    elif change == "database":
        def fail_marker(*_):
            raise sqlite3.OperationalError("TEST database locked")
        monkeypatch.setattr(page.store, "mark_pending", fail_marker)
    if change == "constructor":
        def fail_constructor(*args, **kwargs):
            raise IntegrationError("TEST helper routing unavailable")
        monkeypatch.setattr(fee_bulk_queue, "GisIntegrationWorker", fail_constructor)
    else:
        monkeypatch.setattr(fee_bulk_queue, "GisIntegrationWorker",
                            lambda *args, **kwargs: pytest.fail("Invalid review must never dispatch"))
    page.fee_queue.start([job])
    _settle(desktop)
    assert job.state == "error"
    assert not page.store.get(job.record_id)["pending"]
    assert not desktop.window._active_workers


def test_generation_loads_context_and_cancel_stops_after_current_house(desktop, monkeypatch):
    page, documents = _setup(desktop, monkeypatch)
    dialog = FeeGenerationDialog(page)
    dialog._load_context()
    _settle(desktop)
    assert dialog._loading_context
    assert not dialog.houses_table.isEnabled()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.reject()
    assert dialog._loading_context
    queue = page.fee_queue
    job = next(job for job in queue.jobs if job.state == "running")
    document = next(document for house_id, document in documents if house_id == job.house_id)
    queue.worker.succeeded.emit({"contracts": [document["contract"]], "protocols": [document["protocol"]], "records": []})
    queue.worker.finish()
    _settle(desktop)
    assert not dialog._loading_context
    assert not queue.running
    assert sum(job.state == "stopped" for job in queue.jobs) == 1
    assert dialog.result() == dialog.DialogCode.Rejected
    dialog.deleteLater()


def test_close_waits_for_current_fee_and_does_not_start_remaining_houses(desktop, monkeypatch):
    page, documents = _setup(desktop, monkeypatch)
    _records(page, documents)
    queue = page.fee_queue
    queue.start(_jobs(page, "save"))
    _settle(desktop)
    job = next(job for job in queue.jobs if job.state == "running")
    event = QCloseEvent()
    desktop.window.closeEvent(event)
    assert not event.isAccepted()
    assert not desktop.window._closing
    queue.worker.succeeded.emit(_reply(job.document, guid="created-before-close"))
    queue.worker.finish()
    _settle(desktop)
    assert not queue.running and sum(other.state == "stopped" for other in queue.jobs) == 1
    assert page.store.get(job.record_id)["remote_guid"] == "created-before-close"
    QTest.qWait(120)
    _settle(desktop)
    assert desktop.window._closing


def test_bulk_preview_blocks_pending_and_local_only_approval(desktop, monkeypatch):
    page, documents = _setup(desktop, monkeypatch)
    ids = _records(page, documents)
    page.store.mark_pending(ids[0])
    dialog = FeeQueueDialog(page, _jobs(page, "approve"))
    assert not dialog.selected_jobs()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.deleteLater()


@pytest.mark.parametrize("found", [True, False])
def test_read_only_queue_reconciles_unknown_create_and_reports_remaining_uncertainty(desktop, monkeypatch, found):
    page, documents = _setup(desktop, monkeypatch)
    _records(page, documents)
    job = _jobs(page, "save")[0]
    queue = page.fee_queue
    queue.start([job])
    _settle(desktop)
    queue.worker.failed.emit("TEST uncertain response")
    queue.worker.finish()
    _settle(desktop)
    assert page.store.get(job.record_id)["pending"]
    read_job = prepare_jobs(page.repository, page.store, job.organization_id, [job.house_id], "load")[0]
    queue.start([read_job])
    _settle(desktop)
    assert queue.worker.command_type == "load_management_fees"
    reply = _reply(job.document, guid="found-created-project")
    queue.worker.succeeded.emit({"contracts": [job.document["contract"]],
        "protocols": [job.document["protocol"]], "records": [{
            "contractVersionGuid": job.document["contract"]["versionGuid"],
            "record": reply["record"]}] if found else []})
    queue.worker.finish()
    _settle(desktop)
    row = page.store.get(job.record_id)
    assert bool(row["pending"]) == (not found)
    assert read_job.unresolved == (0 if found else 1)
    if found:
        assert row["remote_guid"] == "found-created-project"
        assert len(page.store.list(job.house_id)) == 1
    else:
        assert "1" in read_job.detail
        assert "результат не установлен: 1" in queue.summary()
