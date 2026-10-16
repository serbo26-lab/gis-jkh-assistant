"""Reviewed fee actions against isolated records and controlled Qt workers."""
from copy import deepcopy

import pytest

import test_fee_bulk_ui as bulk_ui
import test_management_fees as fees
from gis_jkh_assistant.fee_bulk import apply_amendments, prepare_amendments, prepare_jobs

store, desktop, qt_app = fees.store, fees.desktop, fees.qt_app


def _jobs(fee_store, house_id, action):
    repo = fee_store.repository
    house = repo.get_house(house_id)
    return prepare_jobs(repo, fee_store, house.organization_id, [house_id], action)


@pytest.mark.parametrize("status,dirty,save,approve,combined", [
    (None, True, True, False, True),
    ("DRAFT", False, False, True, True),
    ("DRAFT", True, True, False, True),
    ("APPROVED", False, False, False, False),
    ("APPROVED", True, True, False, True),
    ("TERMINATED", False, False, False, False),
])
def test_actions_distinguish_local_changes_saved_projects_and_terminal_records(
        store, status, dirty, save, approve, combined):
    fee_store, house_id = store
    if status is None:
        record_id = fee_store.save_local(house_id, deepcopy(fees.DOCUMENT))
    else:
        fee_store.import_remote(house_id, fees.context(fees.remote(status=status)))
        record_id = fee_store.list(house_id)[0]["id"]
        if dirty:
            if status == "APPROVED":
                fee_store.begin_published_edit(record_id)
            fee_store.save_local(house_id, dict(fees.DOCUMENT, amount="20.00"), record_id)
    for action, enabled in (("save", save), ("approve", approve), ("save_approve", combined)):
        assert (_jobs(fee_store, house_id, action)[0].state == "ready") == enabled
    fee_store.mark_pending(record_id)
    assert all(_jobs(fee_store, house_id, action)[0].state == "blocked"
               for action in ("save", "approve", "save_approve"))


@pytest.mark.parametrize("protocol", [fees.PROTOCOL, None])
def test_local_projects_can_be_updated_by_tariffs_without_remote_identity(store, protocol):
    fee_store, house_id = store
    repo = fee_store.repository
    house = repo.get_house(house_id)
    tariff = repo.create_house_tariff(house_id=house_id, amount="20.00", start_year=2026, start_month=1)
    original = dict(fees.DOCUMENT, amount="15.00", protocol=deepcopy(protocol))
    record_id = fee_store.save_local(house_id, original)
    preview = prepare_amendments(repo, fee_store, house.organization_id, house_id, [record_id])
    assert not preview[0].blocked
    assert preview[0].tariff_id == tariff.id
    assert apply_amendments(repo, fee_store, house.organization_id, house_id, preview) == [record_id]
    saved = fee_store.get(record_id)
    assert saved["document"]["amount"] == "20.00"
    assert saved["document"]["protocol"] == protocol
    assert saved["remote"] is None and saved["remote_guid"] is None
    assert saved["dirty"] and not saved["pending"]
    assert len(fee_store.list(house_id)) == 1


def test_local_tariff_amendments_keep_pending_overlap_and_protocol_scope_guards(store):
    fee_store, house_id = store
    repo = fee_store.repository
    house = repo.get_house(house_id)
    repo.create_house_tariff(house_id=house_id, amount="20.00", start_year=2026, start_month=1)
    record_id = fee_store.save_local(house_id, deepcopy(fees.DOCUMENT))
    other_protocol = dict(fees.PROTOCOL, fiasHouseCodes=["another-house"])
    preview = prepare_amendments(repo, fee_store, house.organization_id, house_id, [record_id], other_protocol)
    assert preview[0].blocked and "другому дому" in preview[0].detail
    fee_store.save_local(house_id, dict(fees.DOCUMENT, start="2026-06-01"))
    preview = prepare_amendments(repo, fee_store, house.organization_id, house_id, [record_id])
    assert preview[0].blocked and "пересекается" in preview[0].detail
    fee_store.mark_pending(record_id)
    preview = prepare_amendments(repo, fee_store, house.organization_id, house_id, [record_id])
    assert preview[0].blocked and "прежней отправки" in preview[0].detail


def test_combined_queue_uses_existing_save_and_approve_command_and_final_confirmation(desktop, monkeypatch):
    page, documents = bulk_ui._setup(desktop, monkeypatch)
    ids = bulk_ui._records(page, documents)
    queue = page.fee_queue
    assert queue.start(bulk_ui._jobs(page, "save_approve"))
    bulk_ui._settle(desktop)
    for index in range(2):
        job = next(job for job in queue.jobs if job.state == "running")
        worker = queue.worker
        assert worker.command_type == "save_and_approve_management_fee"
        assert page.store.get(job.record_id)["pending"]
        worker.succeeded.emit(bulk_ui._reply(job.document, guid=f"combined-{index}", status="APPROVED"))
        assert queue.worker is worker
        worker.finish()
        bulk_ui._settle(desktop)
    assert not queue.running
    assert all(job.state == "done" for job in queue.jobs)
    assert all(page.store.get(record_id)["remote"]["status"] == "APPROVED" for record_id in ids)
    assert all(not page.store.get(record_id)["pending"] for record_id in ids)


@pytest.mark.parametrize("outcome", ["stop", "saved_only", "timeout"])
def test_combined_queue_never_retries_unknown_write_and_resumes_only_remaining_jobs(
        desktop, monkeypatch, outcome):
    page, documents = bulk_ui._setup(desktop, monkeypatch)
    bulk_ui._records(page, documents)
    queue = page.fee_queue
    assert queue.start(bulk_ui._jobs(page, "save_approve"))
    bulk_ui._settle(desktop)
    job = next(job for job in queue.jobs if job.state == "running")
    worker = queue.worker
    if outcome == "stop":
        queue.request_stop()
        worker.succeeded.emit(bulk_ui._reply(job.document, guid="first-combined", status="APPROVED"))
    elif outcome == "saved_only":
        worker.succeeded.emit(bulk_ui._reply(job.document, guid="first-combined", status="DRAFT"))
    else:
        worker.failed.emit("Проект сохранён в ГИС. Размещение не подтверждено.")
    worker.finish()
    bulk_ui._settle(desktop)
    assert job.state == ("done" if outcome == "stop" else "uncertain")
    assert bool(page.store.get(job.record_id)["pending"]) == (outcome != "stop")
    assert queue.resume()
    bulk_ui._settle(desktop)
    remaining = next(other for other in queue.jobs if other.state == "running")
    assert remaining.record_id != job.record_id
    queue.worker.succeeded.emit(bulk_ui._reply(remaining.document, guid="second-combined", status="APPROVED"))
    queue.worker.finish()
    bulk_ui._settle(desktop)
    assert not queue.resume()
    if outcome == "stop":
        return
    # Reconciliation can discover the saved draft after an interrupted placement.
    # It attaches the existing remote identity; the next action is placement only.
    reply = bulk_ui._reply(job.document, guid="first-combined", status="DRAFT")
    page.store.import_remote(job.house_id, {"contracts": [reply["contract"]],
        "protocols": [job.document["protocol"]], "records": [{
            "contractVersionGuid": reply["contract"]["versionGuid"], "record": reply["record"]}]})
    assert not page.store.get(job.record_id)["pending"]
    ready = [candidate for candidate in _jobs(page.store, job.house_id, "approve") if candidate.state == "ready"]
    assert len(ready) == 1 and ready[0].record_id == job.record_id
    assert not any(candidate.state == "ready" for candidate in _jobs(page.store, job.house_id, "save"))
    assert len(page.store.list(job.house_id)) == 1
