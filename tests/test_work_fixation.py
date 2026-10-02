import pytest

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.work_fixation import (
    build_fixation_command_payload,
    calculate_fact_total_cost,
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
    assert not helper_supports_fixations("0.1.8")
    assert helper_supports_fixations("0.1.9")
    assert helper_supports_fixations("0.2.0")


def test_unplanned_fact_total_cost_matches_gis_formula() -> None:
    assert calculate_fact_total_cost("1", "1", 1) == "1.00"
    assert calculate_fact_total_cost("12,50", "2", 3) == "75.00"
    assert calculate_fact_total_cost("0.1", "0.2", 1) == "0.02"
    assert calculate_fact_total_cost("", "2", 3) is None
    assert calculate_fact_total_cost("2", None, 3) is None
    assert calculate_fact_total_cost("2", "3", None) is None
    assert calculate_fact_total_cost("-1", "2", 3) is None


def test_unplanned_work_cannot_duplicate_planned_work(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list, _plan = _fixture(repository)
    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    with pytest.raises(ValueError, match="является плановой"):
        repository.add_work_fixation_unplanned_item(
            period.id,
            work_code="12",
            work_name="Услуга по управлению",
            work_guid="83592b3d-c928-416b-b6e0-595fa1339821",
            fact_count=1,
        )
    repository.close()


def test_unplanned_work_and_photo_are_in_gis_payload(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, house, work_list, _plan = _fixture(repository)
    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    repository.fill_work_fixation_from_plan(period.id)
    planned = repository.list_work_fixation_items(period.id)
    repository.add_work_fixation_photo(
        fixation_item_id=planned[0].id,
        file_name="plan.jpg",
        description="plan.jpg",
        content_type="image/jpeg",
        content=b"jpeg-bytes",
    )
    unplanned = repository.add_work_fixation_unplanned_item(
        period.id,
        work_code="20",
        work_name="Внеплановая работа",
        work_guid="a63d1b8b-6ca4-439f-8c78-71afe7c004c0",
        work_type_code="1",
        work_type_name="Обслуживание",
        unit_name="ед",
        fact_price="2",
        fact_amount="2",
        fact_count=2,
        fact_total_cost="8.00",
        comment="комментарий",
    )
    repository.add_work_fixation_photo(
        unplanned_item_id=unplanned.id,
        file_name="extra.gif",
        description="extra.gif",
        content_type="image/gif",
        content=b"gif-bytes",
    )

    payload = build_fixation_command_payload(
        fias_house_code=house.fias_house_guid or "",
        work_list_guid=work_list.gis_work_list_guid or "",
        year=2026,
        month=1,
        items=repository.list_work_fixation_items(period.id),
        unplanned_items=repository.list_work_fixation_unplanned_items(period.id),
        photos_by_planned={
            item.id: repository.list_work_fixation_photos(fixation_item_id=item.id)
            for item in repository.list_work_fixation_items(period.id)
        },
        photos_by_unplanned={
            item.id: repository.list_work_fixation_photos(unplanned_item_id=item.id)
            for item in repository.list_work_fixation_unplanned_items(period.id)
        },
        managed_unplanned_guids_json=period.managed_unplanned_guids_json,
    )

    assert payload["items"][0]["photos"][0]["fileName"] == "plan.jpg"
    assert payload["items"][0]["photos"][0]["fileBase64"]
    stored_unplanned = repository.list_work_fixation_unplanned_items(period.id)
    assert stored_unplanned[0].work_type_name == "Обслуживание"
    assert stored_unplanned[0].unit_name == "ед"

    assert payload["unplannedItems"][0]["workCode"] == "20"
    assert payload["unplannedItems"][0]["factCount"] == 2
    assert payload["unplannedItems"][0]["comment"] == "комментарий"
    assert payload["unplannedItems"][0]["photos"][0]["fileName"] == "extra.gif"
    repository.close()


def test_managed_gis_guids_allow_deleting_published_extras(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list, _plan = _fixture(repository)
    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    planned = repository.list_work_fixation_items(period.id)
    photo = repository.add_work_fixation_photo(
        fixation_item_id=planned[0].id,
        file_name="photo.jpg",
        description=None,
        content_type="image/jpeg",
        content=b"photo",
    )
    extra = repository.add_work_fixation_unplanned_item(
        period.id,
        work_code="20",
        work_name="Внеплановая работа",
        work_guid="a63d1b8b-6ca4-439f-8c78-71afe7c004c0",
        fact_count=1,
    )
    repository.update_work_fixation_gis_state(
        period.id,
        gis_status="SAVED",
        unplanned_item_guids={extra.id: "remote-unplanned-guid"},
        unplanned_item_metadata={
            extra.id: {
                "work_type_code": "1",
                "work_type_guid": "work-type-guid",
                "work_type_name": "Обслуживание",
                "unit_name": "ед",
            }
        },
        photo_guids={photo.id: ("remote-content-guid", "remote-photo-guid")},
    )

    refreshed_item = repository.list_work_fixation_items(period.id)[0]
    refreshed_unplanned = repository.list_work_fixation_unplanned_items(period.id)[0]
    assert refreshed_unplanned.work_type_code == "1"
    assert refreshed_unplanned.work_type_guid == "work-type-guid"
    assert refreshed_unplanned.work_type_name == "Обслуживание"
    assert refreshed_unplanned.unit_name == "ед"

    refreshed_period = repository.get_work_fixation_period(period.id)
    assert refreshed_period is not None
    assert "remote-unplanned-guid" in (refreshed_period.managed_unplanned_guids_json or "")
    assert "remote-content-guid" in (
        refreshed_item.managed_photo_content_guids_json or ""
    )

    repository.delete_work_fixation_photo(photo.id)
    repository.delete_work_fixation_unplanned_item(extra.id)
    period_after_delete = repository.get_work_fixation_period(period.id)
    assert period_after_delete is not None
    assert period_after_delete.gis_status == "MODIFIED"
    assert "remote-unplanned-guid" in (
        period_after_delete.managed_unplanned_guids_json or ""
    )
    repository.close()
