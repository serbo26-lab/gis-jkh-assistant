from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.work_fixation import (
    build_fixation_command_payload,
    helper_supports_fixations,
    proportional_month_cost,
)


def _fixture(repository: Repository):
    organization = repository.create_organization(
        name="УК Фиксация",
        ogrn="1022600000300",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, пр-д Братский, д. 6",
        fias_house_guid="34062768-dec0-4576-86cb-62bb82d854d2",
    )
    template = repository.create_work_template(
        organization_id=organization.id,
        name="Фиксация",
        items=[
            type(
                "Item",
                (),
                {
                    "position": 1,
                    "work_code": "12",
                    "work_name": "Услуга по управлению",
                    "work_guid": "83592b3d-c928-416b-b6e0-595fa1339821",
                    "price": None,
                    "volume": None,
                    "quantity": "12",
                    "cost": "26151.38",
                },
            )(),
            type(
                "Item",
                (),
                {
                    "position": 2,
                    "work_code": "13",
                    "work_name": "Техническое обслуживание",
                    "work_guid": "98a9a390-d7bb-455b-b0a7-79422b18b252",
                    "price": "10",
                    "volume": "2",
                    "quantity": "12",
                    "cost": "11886.99",
                },
            )(),
        ],
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
    )
    repository.update_work_list_gis_state(
        work_list.id,
        gis_work_list_guid="f505f0ff-8769-4732-9d21-185884a789aa",
        gis_status="APPROVED",
    )
    work_list = repository.get_work_list(work_list.id)
    assert work_list is not None

    plan = repository.get_or_create_work_plan(
        work_list_id=work_list.id,
        year=2026,
    )
    plan_items = repository.list_work_plan_items(plan.id)
    for item in plan_items:
        repository.update_work_plan_month(
            item.id,
            month=1,
            count=1,
            dates=[],
        )
        repository.update_work_plan_month(
            item.id,
            month=2,
            count=1,
            dates=[],
        )
    return organization, house, work_list, plan


def test_month_cost_matches_real_monthly_allocation() -> None:
    assert proportional_month_cost("26151.38", "12", 1) == "2179.28"
    assert proportional_month_cost("11886,99", "12", 1) == "990.58"
    assert proportional_month_cost("100", "0", 1) is None
    assert proportional_month_cost(None, "12", 1) is None


def test_fixation_period_snapshots_plan_and_list_values(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list, _plan = _fixture(repository)

    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    items = repository.list_work_fixation_items(period.id)

    assert [item.planned_count for item in items] == [1, 1]
    assert items[0].planned_total_cost == "2179.28"
    assert items[1].planned_total_cost == "990.58"
    assert items[1].planned_price == "10"
    assert items[1].planned_amount == "2"

    repository.close()


def test_fill_from_plan_and_manual_change_mark_period(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list, _plan = _fixture(repository)

    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    repository.fill_work_fixation_from_plan(period.id)
    items = repository.list_work_fixation_items(period.id)

    assert items[0].fact_count == 1
    assert items[0].fact_total_cost == "2179.28"
    assert items[1].fact_price == "10"
    assert items[1].fact_amount == "2"

    repository.update_work_fixation_gis_state(
        period.id,
        reporting_period_guid="reporting-period-guid",
        gis_status="SAVED",
    )
    repository.update_work_fixation_item(
        items[0].id,
        fact_price=None,
        fact_amount=None,
        fact_count=2,
        fact_total_cost="4358.56",
    )
    refreshed = repository.get_work_fixation_period(period.id)
    assert refreshed is not None
    assert refreshed.gis_status == "MODIFIED"

    repository.close()


def test_fixation_payload_contains_complete_local_month_state(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, house, work_list, _plan = _fixture(repository)

    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=2,
    )
    repository.fill_work_fixation_from_plan(period.id)
    items = repository.list_work_fixation_items(period.id)

    payload = build_fixation_command_payload(
        fias_house_code=house.fias_house_guid or "",
        work_list_guid=work_list.gis_work_list_guid or "",
        year=period.year,
        month=period.month,
        items=items,
    )

    assert payload["fiasHouseCode"] == house.fias_house_guid
    assert payload["workListGuid"] == work_list.gis_work_list_guid
    assert payload["year"] == 2026
    assert payload["month"] == 2
    assert payload["items"][0]["localItemId"] == items[0].id
    assert payload["items"][0]["factCount"] == 1
    assert payload["items"][0]["factTotalCost"] == "2179.28"
    assert payload["items"][1]["factPrice"] == "10"

    repository.close()


def test_clear_fixation_removes_all_fact_values(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list, _plan = _fixture(repository)

    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    repository.fill_work_fixation_from_plan(period.id)
    repository.clear_work_fixation_period(period.id)

    items = repository.list_work_fixation_items(period.id)
    assert all(item.fact_price is None for item in items)
    assert all(item.fact_amount is None for item in items)
    assert all(item.fact_count is None for item in items)
    assert all(item.fact_total_cost is None for item in items)

    repository.close()


def test_fixation_helper_version() -> None:
    assert not helper_supports_fixations(None)
    assert not helper_supports_fixations("0.1.5")
    assert helper_supports_fixations("0.1.6")
    assert helper_supports_fixations("0.2.0")
