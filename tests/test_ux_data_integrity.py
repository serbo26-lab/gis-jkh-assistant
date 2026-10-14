"""Regression cases from the dev58 practical audit, using isolated SQLite."""

import sqlite3
from copy import deepcopy

import pytest

from test_gis_downstream_sync import _setup
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.quality_automation import generate_acceptance_act_for_period
from gis_jkh_assistant.work_lists.gis_fixation_sync import apply_remote_work_fixation
from gis_jkh_assistant.work_lists.gis_plan_sync import apply_remote_work_plan
from gis_jkh_assistant.work_lists.gis_quality_sync import apply_remote_quality_assessment


def snapshot(repo, *tables):
    with sqlite3.connect(repo.database_path) as reader:
        return {table: reader.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in tables}


def plan_response():
    return {"found": True, "workPlanGuid": "test-new-plan", "gisStatus": "SAVED",
            "items": [{"workGuid": "work-guid", "workCode": "12", "sortIndex": 1,
                       "monthlyWorks": [{"month": 1, "count": 3, "dates": []},
                                        {"month": 2, "count": 4, "dates": []}]}]}


@pytest.mark.parametrize("bad", [
    {"month": 13, "count": 1}, {"month": 2, "count": -1},
    {"month": 2, "count": 1, "dates": ["2026-03-01"]},
    {"month": 2, "count": 0, "dates": ["2026-02-01"]},
    {"month": 2, "count": 1, "dates": ["not-a-date"]},
])
def test_invalid_plan_preserves_complete_snapshot(tmp_path, bad):
    repo, wl = _setup(tmp_path)
    plan = repo.get_or_create_work_plan(work_list_id=wl.id, year=2026)
    item = repo.list_work_plan_items(plan.id)[0]
    repo.update_work_plan_month(item.id, month=2, count=2, dates=["2026-02-10"])
    before = snapshot(repo, "work_plans", "work_plan_items", "work_plan_months")
    response = plan_response()
    response["items"][0]["monthlyWorks"][1] = bad
    with pytest.raises(ValueError):
        apply_remote_work_plan(repo, work_list_id=wl.id, year=2026, remote=response)
    assert snapshot(repo, *before) == before
    repo.close()


def test_plan_sql_failure_rolls_back_all_rows_and_metadata(tmp_path):
    repo, wl = _setup(tmp_path)
    plan = repo.get_or_create_work_plan(work_list_id=wl.id, year=2026)
    item = repo.list_work_plan_items(plan.id)[0]
    repo.update_work_plan_month(item.id, month=2, count=2, dates=["2026-02-10"])
    before = snapshot(repo, "work_plans", "work_plan_items", "work_plan_months")
    with repo.connection:
        repo.connection.execute("CREATE TRIGGER test_plan_failure BEFORE INSERT ON work_plan_months "
                                "WHEN NEW.month=2 BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        apply_remote_work_plan(repo, work_list_id=wl.id, year=2026, remote=plan_response())
    assert snapshot(repo, *before) == before
    repo.close()


def test_atomic_scope_owns_nested_method_commits(tmp_path):
    repo, wl = _setup(tmp_path)
    before = snapshot(repo, "work_plans", "work_plan_items", "work_plan_months")
    with pytest.raises(RuntimeError):
        with repo.connection.atomic():
            plan = repo.get_or_create_work_plan(work_list_id=wl.id, year=2026)
            item = repo.list_work_plan_items(plan.id)[0]
            repo.update_work_plan_month(item.id, month=1, count=2)
            repo.update_work_plan_gis_state(plan.id, gis_status="SAVED")
            raise RuntimeError("rollback")
    assert snapshot(repo, *before) == before
    repo.close()


@pytest.mark.parametrize("remote_has_work", [True, False])
def test_fixation_sync_preserves_unplanned_identity_and_photos(tmp_path, remote_has_work):
    repo, wl = _setup(tmp_path)
    period = repo.get_or_create_work_fixation_period(work_list_id=wl.id, year=2026, month=1)
    item = repo.add_work_fixation_unplanned_item(period.id, work_code="TEST-U",
              work_name="TEST WORK", work_guid="test-u", fact_count=1, fact_total_cost="25")
    photo = repo.add_work_fixation_photo(unplanned_item_id=item.id, file_name="test.png",
              description="test", content_type="image/png", content=b"TEST LOCAL PHOTO")
    remote_row = {"guid": "test-remote-u", "workGuid": "test-u", "workCode": "TEST-U",
                  "workName": "TEST WORK", "factCount": 2, "factTotalCost": "50"}
    response = {"found": True, "plannedItems": [],
                "unplannedItems": [remote_row] if remote_has_work else []}
    for _ in range(2):
        apply_remote_work_fixation(repo, work_list_id=wl.id, year=2026, month=1, remote=response)
        rows = repo.list_work_fixation_unplanned_items(period.id)
        assert len(rows) == 1 and rows[0].id == item.id
        assert repo.list_work_fixation_photos(unplanned_item_id=item.id)[0].content == photo.content
    assert snapshot(repo, "work_fixation_photos")["work_fixation_photos"][0][0] == photo.id
    repo.close()


def add_act(store, item, *, generated=False):
    return store.add_act(item_id=item.id, act_type="ACCEPTANCE_ACT", number="TEST-1",
                        reg_date="31.01.2026", period_from="01.01.2026",
                        period_to="31.01.2026", file_name="test-local.pdf", description="TEST",
                        content_type="application/pdf", content=b"TEST LOCAL FILE", generated=generated)


def quality_response(act=None):
    return {"found": True, "gisStatus": "DRAFT", "items": [
        {"workGuid": "work-guid", "workCode": "12", "position": 1,
         "guid": "test-quality-row", "acts": [act] if act else []}]}


def remote_act(local):
    return {"guid": "test-remote-act", "contentGuid": "test-remote-file",
            "actType": local.act_type, "number": local.number, "regDate": local.reg_date,
            "periodFrom": local.period_from, "periodTo": local.period_to,
            "fileName": local.file_name, "fileSize": local.file_size}


@pytest.mark.parametrize("remote_has_act", [True, False])
def test_quality_import_preserves_local_file_and_links(tmp_path, remote_has_act):
    repo, wl = _setup(tmp_path)
    store = QualityAssessmentStore(repo)
    period = store.get_or_create_period(work_list_id=wl.id, year=2026, month=1)
    item = store.list_items(period.id)[0]
    act = add_act(store, item)
    response = quality_response(remote_act(act) if remote_has_act else None)
    for _ in range(2):
        apply_remote_quality_assessment(store, work_list_id=wl.id, year=2026, month=1,
                                        remote=deepcopy(response))
        acts = store.list_period_acts(period.id)
        assert len(acts) == 1 and acts[0].id == act.id and acts[0].content == act.content
        assert store.act_link_item_ids(act.id) == [item.id]
    repo.close()


@pytest.mark.parametrize("failure", ["pdf", "insert", "link"])
def test_generation_failure_preserves_previous_generated_act(tmp_path, monkeypatch, failure):
    repo, wl = _setup(tmp_path)
    from act_test_data import populate_act_requisites
    populate_act_requisites(repo, wl)
    fixation = repo.get_or_create_work_fixation_period(work_list_id=wl.id, year=2026, month=1)
    repo.update_work_fixation_item(repo.list_work_fixation_items(fixation.id)[0].id,
        fact_price=None, fact_amount=None, fact_count=1, fact_total_cost="100")
    store = QualityAssessmentStore(repo)
    period = store.get_or_create_period(work_list_id=wl.id, year=2026, month=1)
    item = store.list_items(period.id)[0]
    store.update_item(item.id, included=True, cost=None, days_count=None, cost_reduction=None)
    previous = add_act(store, item, generated=True)
    before = snapshot(repo, "quality_assessment_acts", "quality_assessment_act_links",
                      "quality_assessment_periods", "acceptance_act_snapshots")
    def fail(*args, **kwargs):
        raise RuntimeError("TEST GENERATION FAILURE")
    if failure == "pdf":
        monkeypatch.setattr("gis_jkh_assistant.quality_automation.build_acceptance_act_pdf", fail)
    elif failure == "insert":
        monkeypatch.setattr(store, "add_act", fail)
    else:
        original = store.set_act_links
        def fail_new_link(act_id, links):
            if act_id != previous.id:
                fail()
            return original(act_id, links)
        monkeypatch.setattr(store, "set_act_links", fail_new_link)
    with pytest.raises(RuntimeError, match="TEST GENERATION FAILURE"):
        generate_acceptance_act_for_period(store, period_id=period.id,
                   organization_name="TEST ONLY", house_address="TEST HOUSE")
    assert snapshot(repo, *before) == before
    repo.close()


@pytest.mark.parametrize("kind", ["plan", "fixation", "quality"])
def test_historical_rows_keep_identity_and_values_when_absent_or_same_position_conflicts(tmp_path, kind):
    repo, wl = _setup(tmp_path)
    store = QualityAssessmentStore(repo)
    row = {"workGuid": "TEST-HISTORICAL", "workCode": "TEST-H", "workName": "TEST history", "position": 1,
           "monthlyWorks": [{"month": 2, "count": 4}], "factCount": 4, "factTotalCost": "444",
           "guid": "TEST-row-guid", "cost": "123", "daysCount": 2, "daysPerMonth": 31, "costReduction": "7"}
    if kind == "plan":
        def apply(items):
            apply_remote_work_plan(repo, work_list_id=wl.id, year=2026, remote={"found": True, "items": items})
        tables = ("work_plan_items", "work_plan_months")
    elif kind == "fixation":
        def apply(items):
            apply_remote_work_fixation(repo, work_list_id=wl.id, year=2026, month=1,
                remote={"found": True, "plannedItems": items, "unplannedItems": []})
        tables = ("work_fixation_items",)
    else:
        def apply(items):
            apply_remote_quality_assessment(store, work_list_id=wl.id, year=2026, month=1, remote={"found": True, "items": items})
        tables = ("quality_assessment_items",)
    apply([row])
    before = snapshot(repo, *tables)
    historical = [value for value in before[tables[0]] if "TEST-HISTORICAL" in value]
    assert len(historical) == 1
    assert any("work-guid" in value for value in before[tables[0]]), "Explicit GUID conflict must not rename local A as historical B"
    for _ in range(2):
        apply([])
        after = snapshot(repo, *tables)
        assert [value for value in after[tables[0]] if "TEST-HISTORICAL" in value] == historical
        if kind == "plan":
            assert after["work_plan_months"] == before["work_plan_months"]
    repo.close()


def test_quality_conflicting_remote_file_rolls_back_items_and_all_links(tmp_path):
    repo, wl = _setup(tmp_path)
    store = QualityAssessmentStore(repo)
    period = store.get_or_create_period(work_list_id=wl.id, year=2026, month=1)
    item = store.list_items(period.id)[0]
    act = add_act(store, item)
    store.set_gis_state(period.id, act_guids={act.id: ("TEST-original-file", "TEST-act")})
    before = snapshot(repo, "quality_assessment_items", "quality_assessment_acts", "quality_assessment_act_links", "quality_assessment_periods")
    remote = remote_act(act)
    remote.update(guid="TEST-act", contentGuid="TEST-different-file")
    with pytest.raises(ValueError, match="Файл акта"):
        apply_remote_quality_assessment(store, work_list_id=wl.id, year=2026, month=1, remote=quality_response(remote))
    assert snapshot(repo, *before) == before
    repo.close()


def test_unacknowledged_deletion_keeps_file_and_only_covered_links_are_removed(tmp_path):
    repo, wl = _setup(tmp_path)
    store = QualityAssessmentStore(repo)
    period = store.get_or_create_period(work_list_id=wl.id, year=2026, month=1)
    item = store.list_items(period.id)[0]
    act = add_act(store, item)
    store.set_gis_state(period.id, act_guids={act.id: ("TEST-file", "TEST-act")})
    store.delete_act(act.id)
    before = snapshot(repo, "quality_assessment_acts", "quality_assessment_act_links")
    store.set_gis_state(period.id, gis_status="DRAFT")
    assert snapshot(repo, *before) == before
    store.set_gis_state(period.id, gis_status="DRAFT", acknowledged_deletions={(act.id, item.id): ("TEST-file", "TEST-act")})
    assert store.get_act(act.id) is None
    repo.close()


@pytest.mark.parametrize("late_change", [None, "relink", "remote_ids"])
def test_shared_act_cleanup_obeys_sent_links_and_later_changes(tmp_path, late_change):
    from gis_jkh_assistant.quality_assessment import build_quality_command_payload
    repo, wl = _setup(tmp_path)
    store = QualityAssessmentStore(repo)
    response = quality_response()
    response["items"].append({"workGuid": "TEST-HIST", "workCode": "TEST-H", "position": 2})
    apply_remote_quality_assessment(store, work_list_id=wl.id, year=2026, month=1, remote=response)
    period = store.get_period_for_month(wl.id, year=2026, month=1)
    first, second = store.list_items(period.id)
    store.update_item(second.id, included=False, cost=None, days_count=None, cost_reduction=None)
    act = add_act(store, first)
    store.set_act_links(act.id, [first.id, second.id])
    store.set_gis_state(period.id, act_guids={act.id: ("TEST-file", "TEST-act")})
    store.delete_act(act.id)
    payload = build_quality_command_payload(fias_house_code="TEST", house_object_guid="TEST", work_list_guid="TEST", year=2026, month=1,
        items=store.list_items(period.id), acts_by_item={item.id: store.list_acts(item.id, include_deleted=True) for item in store.list_items(period.id)})
    covered = store.deletion_snapshot(payload)
    assert set(covered) == {(act.id, first.id)}
    if late_change == "relink":
        store.set_act_links(act.id, [first.id])
    elif late_change == "remote_ids":
        store.set_gis_state(period.id, act_guids={act.id: ("TEST-new-file", "TEST-new-act")})
    store.set_gis_state(period.id, gis_status="DRAFT", acknowledged_deletions=covered)
    assert store.get_act(act.id).content == act.content
    links = store.act_link_item_ids(act.id, include_deleted=True)
    assert second.id in links
    assert (first.id in links) == bool(late_change)
    if late_change is None:
        store.set_gis_state(period.id, acknowledged_deletions={(act.id, second.id): ("TEST-file", "TEST-act")})
        assert store.get_act(act.id) is None
    repo.close()
