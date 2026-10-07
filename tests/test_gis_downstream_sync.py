from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.work_lists.gis_fixation_sync import apply_remote_work_fixation
from gis_jkh_assistant.work_lists.gis_plan_sync import apply_remote_work_plan
from gis_jkh_assistant.work_lists.gis_quality_sync import (
    apply_remote_quality_assessment,
)


def _setup(tmp_path):
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК", ogrn="1022600000799")
    house = repository.create_house(
        organization_id=org.id,
        address="Дом",
        house_object_guid="house-object",
        gis_house_guid="fias-house",
        fias_house_guid="fias-house",
    )
    work_list = repository.import_gis_work_list(
        house_id=house.id,
        remote={
            "guid": "list-guid",
            "houseGuid": "house-object",
            "status": "APPROVED",
            "period": {
                "from": {"year": 2026, "month": 1},
                "to": {"year": 2026, "month": 12},
            },
            "summary": {},
            "items": [
                {
                    "sortIndex": 1,
                    "workGuid": "work-guid",
                    "workCode": "12",
                    "workName": "Работа",
                    "unitName": "шт.",
                    "price": "100",
                    "volume": "2",
                    "quantity": "12",
                    "cost": "1200",
                }
            ],
        },
    )
    return repository, work_list


def test_apply_remote_work_plan(tmp_path) -> None:
    repository, work_list = _setup(tmp_path)

    applied = apply_remote_work_plan(
        repository,
        work_list_id=work_list.id,
        year=2026,
        remote={
            "found": True,
            "workPlanGuid": "plan-guid",
            "gisStatus": "SAVED",
            "items": [
                {
                    "sortIndex": 1,
                    "workGuid": "work-guid",
                    "workCode": "12",
                    "monthlyWorks": [
                        {
                            "month": 1,
                            "count": 1,
                            "dates": ["2026-01-15"],
                        }
                    ],
                }
            ],
        },
    )

    plan = repository.get_work_plan_for_list_year(work_list.id, 2026)
    assert plan is not None
    assert plan.gis_work_plan_guid == "plan-guid"
    assert applied == {"items": 1, "months": 1, "historical": 0}
    months = repository.list_work_plan_months(plan.id)
    assert len(months) == 1
    assert months[0].month == 1
    assert months[0].count == 1
    assert "2026-01-15" in str(months[0].dates_json)
    repository.close()


def test_apply_remote_fixation_month(tmp_path) -> None:
    repository, work_list = _setup(tmp_path)
    apply_remote_work_plan(
        repository,
        work_list_id=work_list.id,
        year=2026,
        remote={
            "found": True,
            "workPlanGuid": "plan-guid",
            "items": [
                {
                    "sortIndex": 1,
                    "workGuid": "work-guid",
                    "workCode": "12",
                    "monthlyWorks": [{"month": 1, "count": 1, "dates": []}],
                }
            ],
        },
    )

    applied = apply_remote_work_fixation(
        repository,
        work_list_id=work_list.id,
        year=2026,
        month=1,
        remote={
            "found": True,
            "reportingPeriodGuid": "period-guid",
            "gisStatus": "SAVED",
            "plannedItems": [
                {
                    "position": 1,
                    "workGuid": "work-guid",
                    "workCode": "12",
                    "guid": "fixed-guid",
                    "factCount": 1,
                    "factTotalCost": "100.00",
                }
            ],
            "unplannedItems": [],
        },
    )

    period = repository.get_work_fixation_period_for_month(
        work_list.id,
        year=2026,
        month=1,
    )
    assert period is not None
    assert period.reporting_period_guid == "period-guid"
    assert period.gis_status == "SAVED"
    rows = repository.list_work_fixation_items(period.id)
    assert rows[0].fact_count == 1
    assert rows[0].fact_total_cost == "100.00"
    assert rows[0].gis_fixed_work_guid == "fixed-guid"
    assert applied["filled"] == 1
    repository.close()


def test_apply_remote_quality_assessment_with_act_metadata(tmp_path) -> None:
    repository, work_list = _setup(tmp_path)
    store = QualityAssessmentStore(repository)

    applied = apply_remote_quality_assessment(
        store,
        work_list_id=work_list.id,
        year=2026,
        month=1,
        remote={
            "found": True,
            "reportingPeriodGuid": "period-guid",
            "estimationGuid": "estimation-guid",
            "gisStatus": "DRAFT",
            "items": [
                {
                    "position": 1,
                    "workGuid": "work-guid",
                    "workCode": "12",
                    "guid": "estimation-item-guid",
                    "cost": "100",
                    "daysCount": 2,
                    "daysPerMonth": 31,
                    "costReduction": "6.45",
                    "acts": [
                        {
                            "guid": "act-guid",
                            "actType": "QUALITY_VIOLATION_ACT",
                            "number": "1",
                            "regDate": "05.01.2026",
                            "periodFrom": "01.01.2026",
                            "periodTo": "02.01.2026",
                            "contentGuid": "content-guid",
                            "fileName": "act.pdf",
                            "contentType": "application/pdf",
                            "fileSize": 123,
                        }
                    ],
                }
            ],
        },
    )

    period = store.get_period_for_month(work_list.id, year=2026, month=1)
    assert period is not None
    assert period.estimation_guid == "estimation-guid"
    assert period.gis_status == "DRAFT"
    items = store.list_items(period.id)
    assert items[0].included is True
    assert items[0].days_count == 2
    acts = store.list_acts(items[0].id)
    assert len(acts) == 1
    assert acts[0].remote_act_guid == "act-guid"
    assert acts[0].remote_content_guid == "content-guid"
    assert applied == {"items": 1, "acts": 1}
    repository.close()


def test_historical_plan_and_fixation_rows_can_be_imported(tmp_path) -> None:
    repository, work_list = _setup(tmp_path)

    applied_plan = apply_remote_work_plan(
        repository,
        work_list_id=work_list.id,
        year=2026,
        remote={
            "found": True,
            "workPlanGuid": "plan-guid",
            "items": [
                {
                    "sortIndex": 1,
                    "workGuid": "work-guid",
                    "workCode": "12",
                    "workName": "Работа",
                    "monthlyWorks": [{"month": 1, "count": 1, "dates": []}],
                },
                {
                    "sortIndex": 2,
                    "workGuid": "historical-guid",
                    "workCode": "7.2",
                    "workName": "Историческая работа",
                    "monthlyWorks": [{"month": 1, "count": 1, "dates": []}],
                },
            ],
        },
    )

    assert applied_plan["historical"] == 1
    plan = repository.get_work_plan_for_list_year(work_list.id, 2026)
    assert plan is not None
    plan_rows = repository.list_work_plan_items(plan.id)
    historical = [row for row in plan_rows if row.work_guid == "historical-guid"]
    assert len(historical) == 1
    assert historical[0].work_list_item_id is None

    applied_fixation = apply_remote_work_fixation(
        repository,
        work_list_id=work_list.id,
        year=2026,
        month=1,
        remote={
            "found": True,
            "reportingPeriodGuid": "period-guid",
            "plannedItems": [
                {
                    "position": 1,
                    "workGuid": "work-guid",
                    "workCode": "12",
                    "workName": "Работа",
                    "guid": "fixed-1",
                    "factCount": 1,
                },
                {
                    "position": 2,
                    "workGuid": "historical-guid",
                    "workCode": "7.2",
                    "workName": "Историческая работа",
                    "guid": "fixed-2",
                    "factCount": 1,
                },
            ],
            "unplannedItems": [],
        },
    )

    assert applied_fixation["planned"] == 2
    period = repository.get_work_fixation_period_for_month(
        work_list.id, year=2026, month=1
    )
    assert period is not None
    fixation_rows = repository.list_work_fixation_items(period.id)
    imported = [row for row in fixation_rows if row.work_guid == "historical-guid"]
    assert len(imported) == 1
    assert imported[0].fact_count == 1
    repository.close()


def test_historical_quality_row_can_be_imported(tmp_path) -> None:
    repository, work_list = _setup(tmp_path)
    store = QualityAssessmentStore(repository)

    applied = apply_remote_quality_assessment(
        store,
        work_list_id=work_list.id,
        year=2026,
        month=1,
        remote={
            "found": True,
            "reportingPeriodGuid": "period-guid",
            "estimationGuid": "estimation-guid",
            "gisStatus": "APPROVED",
            "items": [
                {
                    "position": 2,
                    "workGuid": "historical-guid",
                    "workCode": "7.2",
                    "workName": "Историческая работа",
                    "guid": "quality-guid",
                    "cost": "10",
                    "daysCount": 0,
                    "daysPerMonth": 31,
                    "costReduction": "0",
                    "acts": [],
                }
            ],
        },
    )

    assert applied["items"] == 1
    period = store.get_period_for_month(work_list.id, year=2026, month=1)
    assert period is not None
    rows = store.list_items(period.id)
    historical = [row for row in rows if row.work_guid == "historical-guid"]
    assert len(historical) == 1
    assert historical[0].work_list_item_id is None
    assert historical[0].gis_estimation_item_guid == "quality-guid"
    repository.close()
