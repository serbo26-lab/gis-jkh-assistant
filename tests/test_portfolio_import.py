"""Initial GIS population: real SQLite importers, socket-free worker doubles."""

from dataclasses import replace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

import test_gis_downstream_sync as sync_tests
import test_ui_workflow as workflows
from gis_jkh_assistant.app import portfolio_import as imports
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore

desktop = workflows.desktop
qt_app = workflows.qt_app


@pytest.fixture
def source(tmp_path):
    repository, work_list = sync_tests._setup(tmp_path)
    store = QualityAssessmentStore(repository)
    house = repository.get_house(work_list.house_id)
    yield repository, store, house.organization_id, work_list
    repository.close()


def response(kind):
    if kind == "plan":
        return {"found": True, "gisStatus": "SAVED", "workPlanGuid": "plan-guid", "items": [
            {"workGuid": "work-guid", "workCode": "12", "monthlyWorks": [{"month": 1, "count": 2, "dates": []}]},
        ]}
    if kind == "fixation":
        return {"found": True, "gisStatus": "SAVED", "reportingPeriodGuid": "report-guid", "plannedItems": [
            {"workGuid": "work-guid", "workCode": "12", "factCount": 2, "factTotalCost": "150"},
        ], "unplannedItems": []}
    return {"found": True, "gisStatus": "APPROVED", "reportingPeriodGuid": "report-guid", "estimationGuid": "quality-guid", "items": [
        {"workGuid": "work-guid", "workCode": "12", "cost": "150", "daysPerMonth": 31, "acts": [
            {"guid": "act-guid", "number": "ТЕСТ-1", "regDate": "2026-01-31", "contentGuid": "file-guid", "fileName": "test.pdf"},
        ]},
    ]}


@pytest.mark.parametrize("kind", ["plan", "fixation", "quality"])
def test_initial_import_loads_placed_data_without_upstream_dependencies(source, kind):
    repo, store, org, work_list = source
    job = imports.prepare_jobs(repo, store, org, kind)[0]
    assert job.state == "pending"
    assert repo.get_work_plan_for_list_year(work_list.id, 2026) is None
    assert repo.get_work_fixation_period_for_month(work_list.id, year=2026, month=1) is None
    imports.apply_job(repo, store, job, response(kind))
    assert job.state == "loaded"
    if kind == "plan":
        plan = repo.get_work_plan_for_list_year(work_list.id, 2026)
        assert repo.list_work_plan_months(plan.id)[0].count == 2
    elif kind == "fixation":
        period = repo.get_work_fixation_period_for_month(work_list.id, year=2026, month=1)
        assert repo.list_work_fixation_items(period.id)[0].fact_total_cost == "150"
    else:
        period = store.get_period_for_month(work_list.id, year=2026, month=1)
        assert store.list_items(period.id)[0].cost == "150"
        act = store.list_period_acts(period.id)[0]
        assert act.remote_act_guid == "act-guid" and act.remote_content_guid == "file-guid"
        assert act.content is None  # Existing helper imports metadata, not file bytes.


def test_scope_covers_actual_month_boundaries_and_only_selected_organization(source):
    repo, store, org, work_list = source
    repo.update_work_list_period(work_list.id, start_year=2025, start_month=11, end_year=2026, end_month=2)
    other_org = repo.create_organization(name="Другая тестовая УО", ogrn="0000000000000")
    other_house = repo.create_house(organization_id=other_org.id, address="Другой дом")
    other = repo.create_work_list(house_id=other_house.id, base_year=2026)
    repo.update_work_list_gis_state(other.id, gis_work_list_guid="other-guid", gis_status="APPROVED")
    draft = repo.clone_work_list(work_list.id, target_base_year=2028)
    repo.update_work_list_gis_state(draft.id, gis_work_list_guid="draft-guid", gis_status="DRAFT")
    assert [(j.year, j.month) for j in imports.prepare_jobs(repo, store, org, "plan")] == [(2025, None), (2026, None)]
    for kind in ("fixation", "quality"):
        jobs = imports.prepare_jobs(repo, store, org, kind)
        assert [(j.year, j.month) for j in jobs] == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]
        assert all(j.work_list_id == work_list.id for j in jobs)
    assert not repo.list_work_fixation_periods(work_list.id)
    assert not store.list_periods(work_list.id, year=2026)


@pytest.mark.parametrize("result", [{"found": False}, {"found": True, "gisStatus": "DRAFT"}, {"found": True, "gisStatus": "SAVED"}])
def test_absent_or_unplaced_quality_does_not_create_local_data(source, result):
    repo, store, org, work_list = source
    job = imports.prepare_jobs(repo, store, org, "quality")[0]
    imports.apply_job(repo, store, job, result)
    assert job.state == ("missing" if not result["found"] else "unplaced")
    assert not store.list_periods(work_list.id, year=2026)


def test_photo_only_and_unlinked_act_are_protected(source):
    repo, store, org, work_list = source
    fixation = repo.get_or_create_work_fixation_period(work_list_id=work_list.id, year=2026, month=1)
    item = repo.list_work_fixation_items(fixation.id)[0]
    photo = repo.add_work_fixation_photo(fixation_item_id=item.id, file_name="test.jpg", description=None,
                                        content_type="image/jpeg", content=b"fictional photo")
    quality = store.get_or_create_period(work_list_id=work_list.id, year=2026, month=1)
    quality_item = store.list_items(quality.id)[0]
    act = store.add_act(item_id=quality_item.id, act_type="ACCEPTANCE_ACT", number="TEST", reg_date="2026-01-31",
                        period_from="2026-01-01", period_to="2026-01-31", file_name="test.pdf", description=None,
                        content_type="application/pdf", content=b"fictional act")
    store.set_gis_state(quality.id, gis_status="APPROVED", act_guids={act.id: ("remote-file", "remote-act")})
    store.delete_act(act.id)
    assert imports.prepare_jobs(repo, store, org, "fixation")[0].state == "protected"
    assert imports.prepare_jobs(repo, store, org, "quality")[0].state == "protected"
    assert repo.list_work_fixation_photos(fixation_item_id=item.id)[0].content == photo.content
    assert store.get_act(act.id).content == act.content
    assert not store.act_link_item_ids(act.id)
    assert store.list_period_acts(quality.id, include_deleted=True)[0].link_deleted


def test_edits_while_response_is_pending_are_not_overwritten(source):
    repo, store, org, work_list = source
    job = imports.prepare_jobs(repo, store, org, "plan")[0]
    plan = repo.get_or_create_work_plan(work_list_id=work_list.id, year=2026)
    item = repo.list_work_plan_items(plan.id)[0]
    repo.update_work_plan_month(item.id, month=1, count=7)
    imports.apply_job(repo, store, job, response("plan"))
    assert job.state == "protected"
    assert repo.list_work_plan_months(plan.id)[0].count == 7


def test_local_apply_failure_rolls_back_entire_period(source):
    repo, store, org, work_list = source
    job = imports.prepare_jobs(repo, store, org, "quality")[0]
    remote = response("quality")
    remote["items"][0]["daysPerMonth"] = "invalid"
    with pytest.raises(ValueError):
        imports.apply_job(repo, store, job, remote)
    assert store.get_period_for_month(work_list.id, year=2026, month=1) is None


def test_wrong_response_identity_is_rejected(source):
    repo, store, org, work_list = source
    job = imports.prepare_jobs(repo, store, org, "plan")[0]
    with pytest.raises(ValueError, match="другому перечню"):
        imports.apply_job(repo, store, job, dict(response("plan"), workListGuid="wrong"))
    assert repo.get_work_plan_for_list_year(work_list.id, 2026) is None


def test_bad_historical_plan_date_is_explained_without_creating_a_plan(source):
    repo, store, org, work_list = source
    repo.update_work_list_period(work_list.id, start_year=2022, start_month=1,
                                 end_year=2022, end_month=12)
    job = imports.prepare_jobs(repo, store, org, "plan")[0]
    remote = response("plan")
    remote.update(year=2022, workListGuid=job.work_list_guid)
    remote["items"][0].update(workName="Обслуживание оборудования", monthlyWorks=[
        {"month": 1, "count": 1, "dates": ["2022-01-15"]},
        {"month": 3, "count": 1, "dates": ["2001-01-01"]},
    ])
    before = list(repo.connection.iterdump())
    with pytest.raises(ValueError) as raised:
        imports.apply_job(repo, store, job, remote)
    detail = str(raised.value)
    for expected in ("Обслуживание оборудования", "строка 1", "01.01.2001", "03.2022"):
        assert expected in detail
    assert list(repo.connection.iterdump()) == before
    assert repo.get_work_plan_for_list_year(work_list.id, 2022) is None


def _queue(desktop, monkeypatch):
    page = desktop.window.houses_page
    queue = page.portfolio_import
    calls = []

    def worker(*args, **kwargs):
        result = workflows.ControlledWorker(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(imports, "GisIntegrationWorker", worker)
    job = imports.prepare_jobs(desktop.repository, desktop.window.quality_page.store, desktop.organization.id, "plan")[0]
    # All periods in these doubles are genuine list periods; apply responses may
    # be missing, so no fake local writes or fabricated out-of-range years occur.
    job = replace(job, state="pending", detail="")
    queue.jobs = [job, replace(job)]
    queue.organization_id = desktop.organization.id
    queue.organization_name = desktop.organization.name
    monkeypatch.setattr(imports, "protected_reason", lambda *args: "")
    queue._begin()
    QTest.qWait(30)
    return page, queue, calls


def test_queue_continues_after_failure_and_retries_only_failed_jobs(desktop, monkeypatch):
    page, queue, calls = _queue(desktop, monkeypatch)
    assert len(calls) == 1 and calls[0].command_type == "load_work_plan"
    calls[0].failed.emit("Тестовый сбой сети")
    calls[0].finish()
    QTest.qWait(30)
    assert len(calls) == 2
    calls[1].succeeded.emit({"found": False})
    calls[1].finish()
    QTest.qWait(30)
    assert not queue.running and [j.state for j in queue.jobs] == ["error", "missing"]
    assert queue.retry_button.isVisibleTo(page)
    assert "ошибки: 1" in page.bulk_operation_status.text()
    queue.retry_button.click()
    QTest.qWait(30)
    assert len(calls) == 3
    calls[2].succeeded.emit({"found": False})
    calls[2].finish()
    QTest.qWait(30)
    assert [j.state for j in queue.jobs] == ["missing", "missing"]
    assert not queue.retry_button.isVisibleTo(page)


def test_queue_reports_bad_date_and_continues_without_changing_existing_plan(desktop, monkeypatch):
    page, queue, calls = _queue(desktop, monkeypatch)
    before = list(desktop.repository.connection.iterdump())
    remote = response("plan")
    remote["items"][0].update(workName="Обслуживание оборудования", monthlyWorks=[
        {"month": 3, "count": 1, "dates": ["2001-01-01"]},
    ])
    calls[0].succeeded.emit(remote)
    calls[0].finish()
    QTest.qWait(30)
    assert len(calls) == 2
    assert queue.jobs[0].state == "error"
    assert "01.01.2001" in queue.jobs[0].detail and "03.2026" in queue.jobs[0].detail
    assert list(desktop.repository.connection.iterdump()) == before
    calls[1].succeeded.emit({"found": False})
    calls[1].finish()
    QTest.qWait(30)
    assert not queue.running and [j.state for j in queue.jobs] == ["error", "missing"]
    assert queue.retry_button.isVisibleTo(page)


def test_stop_and_window_close_do_not_start_next_request(desktop, monkeypatch):
    page, queue, calls = _queue(desktop, monkeypatch)
    queue.stop_button.click()
    assert queue.running and not queue.stop_button.isEnabled()
    calls[0].succeeded.emit({"found": False})
    calls[0].finish()
    QTest.qWait(30)
    assert len(calls) == 1 and not queue.running
    assert [j.state for j in queue.jobs] == ["missing", "stopped"]
    queue.jobs.append(replace(queue.jobs[-1]))
    queue.continue_button.click()
    QTest.qWait(30)
    assert len(calls) == 2
    database_path = desktop.repository.connection.execute("PRAGMA database_list").fetchone()[2]
    desktop.window.close()
    assert desktop.window._close_requested and not desktop.window._closing
    calls[1].succeeded.emit({"found": False})
    calls[1].finish()
    QTest.qWait(180)
    assert len(calls) == 2 and not queue.running
    assert queue.jobs[-1].state == "stopped"
    assert desktop.window._closing
    reopened = Repository(database_path)
    reopened.close()


def test_buttons_become_available_after_lists_without_requiring_prior_imports(desktop):
    page = desktop.window.houses_page
    repo = desktop.repository
    repo.update_work_list_gis_state(desktop.work_list.id, gis_status="DRAFT")
    page._refresh_workflow()
    assert all(not page.portfolio_import_buttons[kind].isEnabled() for kind in ("plan", "fixation", "quality"))
    repo.update_work_list_gis_state(desktop.work_list.id, gis_status="APPROVED")
    page._refresh_workflow()
    assert all(page.portfolio_import_buttons[kind].isEnabled() for kind in ("plan", "fixation", "quality"))
    desktop.gateway.ready = False
    page._refresh_workflow()
    assert all(not b.isEnabled() for b in page.portfolio_import_buttons.values())


def test_cancelled_preview_does_not_start_worker_or_create_periods(desktop, monkeypatch):
    page = desktop.window.houses_page
    monkeypatch.setattr(imports.ImportReviewDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    before = desktop.repository.connection.total_changes
    page.sync_all_quality_button.click()
    assert not page.portfolio_import.running
    assert page.portfolio_import.worker is None
    assert desktop.repository.connection.total_changes == before
