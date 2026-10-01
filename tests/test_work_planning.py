import json

import pytest

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.work_planning import (
    active_months_for_work_list,
    balanced_month_distribution,
    balanced_period_distribution,
    integral_quantity,
)


def _placed_fixture(repository: Repository):
    organization = repository.create_organization(
        name="УК План",
        ogrn="1022600000200",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Плановая, д. 1",
    )
    template = repository.create_work_template(
        organization_id=organization.id,
        name="Плановый",
        items=[
            type(
                "Item",
                (),
                {
                    "position": 1,
                    "work_code": "1",
                    "work_name": "Уборка",
                    "work_guid": "work-guid-1",
                    "quantity": "12",
                },
            )(),
            type(
                "Item",
                (),
                {
                    "position": 2,
                    "work_code": "2",
                    "work_name": "Осмотр",
                    "work_guid": "work-guid-2",
                    "quantity": "3",
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
        gis_work_list_guid="placed-list-guid",
        gis_status="APPROVED",
    )
    return organization, house, repository.get_work_list(work_list.id)


def test_planning_is_available_only_for_placed_lists(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК План",
        ogrn="1022600000201",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Плановая, д. 2",
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
    )

    with pytest.raises(ValueError, match="только после размещения"):
        repository.get_or_create_work_plan(
            work_list_id=work_list.id,
            year=2026,
        )

    assert repository.list_placed_work_lists(house.id) == []
    repository.close()


def test_create_plan_snapshots_placed_list_items(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, house, work_list = _placed_fixture(repository)
    assert work_list is not None

    placed = repository.list_placed_work_lists(house.id)
    assert [item.id for item in placed] == [work_list.id]

    plan = repository.get_or_create_work_plan(
        work_list_id=work_list.id,
        year=2026,
    )
    same_plan = repository.get_or_create_work_plan(
        work_list_id=work_list.id,
        year=2026,
    )
    assert same_plan.id == plan.id

    items = repository.list_work_plan_items(plan.id)
    assert [item.work_name for item in items] == ["Уборка", "Осмотр"]
    assert [item.list_quantity for item in items] == ["12", "3"]
    assert [item.work_guid for item in items] == ["work-guid-1", "work-guid-2"]

    repository.close()


def test_plan_month_supports_count_without_dates_and_optional_dates(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list = _placed_fixture(repository)
    assert work_list is not None

    plan = repository.get_or_create_work_plan(
        work_list_id=work_list.id,
        year=2026,
    )
    item = repository.list_work_plan_items(plan.id)[0]

    repository.update_work_plan_month(
        item.id,
        month=1,
        count=1,
        dates=["2026-01-31"],
    )
    january = repository.get_work_plan_month(item.id, 1)
    assert january is not None
    assert january.count == 1
    assert json.loads(january.dates_json) == ["2026-01-31"]

    repository.update_work_plan_month(
        item.id,
        month=2,
        count=1,
        dates=[],
    )
    february = repository.get_work_plan_month(item.id, 2)
    assert february is not None
    assert february.count == 1
    assert february.dates_json is None

    with pytest.raises(ValueError, match="меньше числа выбранных"):
        repository.update_work_plan_month(
            item.id,
            month=1,
            count=0,
            dates=["2026-01-31"],
        )

    with pytest.raises(ValueError, match="выбранному месяцу"):
        repository.update_work_plan_month(
            item.id,
            month=1,
            count=1,
            dates=["2026-02-01"],
        )

    repository.close()


def test_balanced_distribution_matches_common_yearly_case() -> None:
    assert balanced_month_distribution(12, tuple(range(1, 13))) == {
        month: 1 for month in range(1, 13)
    }
    seasonal = balanced_month_distribution(3, tuple(range(1, 13)))
    assert sum(seasonal.values()) == 3
    assert seasonal[1] == 1
    assert seasonal[12] == 1


def test_transition_period_distribution_uses_full_list_period(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list = _placed_fixture(repository)
    assert work_list is not None
    repository.update_work_list_period(
        work_list.id,
        start_year=2025,
        start_month=6,
        end_year=2026,
        end_month=5,
    )
    work_list = repository.get_work_list(work_list.id)
    assert work_list is not None

    assert active_months_for_work_list(work_list, 2025) == tuple(range(6, 13))
    assert active_months_for_work_list(work_list, 2026) == tuple(range(1, 6))

    distribution = balanced_period_distribution(12, work_list)
    assert len(distribution) == 12
    assert set(distribution.values()) == {1}
    assert distribution[(2025, 6)] == 1
    assert distribution[(2026, 5)] == 1

    repository.close()


def test_integral_quantity_accepts_localized_integer_values() -> None:
    assert integral_quantity("12") == 12
    assert integral_quantity("12,00") == 12
    assert integral_quantity("1 200") == 1200
    assert integral_quantity("3,5") is None


def test_multi_year_distribution_supports_more_than_twelve_months(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list = _placed_fixture(repository)
    assert work_list is not None

    repository.update_work_list_period(
        work_list.id,
        start_year=2025,
        start_month=1,
        end_year=2027,
        end_month=12,
    )
    work_list = repository.get_work_list(work_list.id)
    assert work_list is not None

    distribution = balanced_period_distribution(36, work_list)
    assert len(distribution) == 36
    assert set(distribution.values()) == {1}

    repository.close()
