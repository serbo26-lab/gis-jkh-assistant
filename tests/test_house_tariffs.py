from gis_jkh_assistant.database import Repository


def test_house_charge_area_and_tariff_periods(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК Пример",
        ogrn="1022600000000",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Примерная, д. 1",
        charge_area="395,6",
    )

    assert repository.get_house(house.id).charge_area == "395,6"

    tariff_2025 = repository.create_house_tariff(
        house_id=house.id,
        amount="24,15",
        start_year=2025,
        start_month=1,
        end_year=2025,
        end_month=12,
    )
    tariff_2026 = repository.create_house_tariff(
        house_id=house.id,
        amount="25,04",
        start_year=2026,
        start_month=1,
        end_year=2026,
        end_month=12,
    )

    assert [item.id for item in repository.list_house_tariffs(house.id)] == [
        tariff_2025.id,
        tariff_2026.id,
    ]

    selected = repository.find_house_tariff_covering_period(
        house.id,
        start_year=2026,
        start_month=3,
        end_year=2026,
        end_month=10,
    )
    assert selected is not None
    assert selected.id == tariff_2026.id
    assert selected.amount == "25,04"

    repository.close()


def test_tariff_prefill_requires_one_tariff_to_cover_whole_period(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК Пример",
        ogrn="1022600000000",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Примерная, д. 2",
    )

    repository.create_house_tariff(
        house_id=house.id,
        amount="25",
        start_year=2026,
        start_month=1,
        end_year=2026,
        end_month=6,
    )
    repository.create_house_tariff(
        house_id=house.id,
        amount="27",
        start_year=2026,
        start_month=7,
        end_year=2026,
        end_month=12,
    )

    assert (
        repository.find_house_tariff_covering_period(
            house.id,
            start_year=2026,
            start_month=1,
            end_year=2026,
            end_month=12,
        )
        is None
    )

    repository.close()


def test_house_delete_cascades_tariffs(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК Пример",
        ogrn="1022600000000",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Примерная, д. 3",
    )
    repository.create_house_tariff(
        house_id=house.id,
        amount="25",
        start_year=2026,
        start_month=1,
        end_year=2026,
        end_month=12,
    )

    repository.delete_house(house.id)

    assert repository.list_house_tariffs(house.id) == []
    repository.close()


def test_last_tariff_continues_until_next_explicit_change(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК Пример",
        ogrn="1022600000000",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Примерная, д. 4",
    )

    repository.create_house_tariff(
        house_id=house.id,
        amount="22",
        start_year=2020,
        start_month=8,
    )
    repository.create_house_tariff(
        house_id=house.id,
        amount="23",
        start_year=2021,
        start_month=8,
    )

    tariffs = repository.list_house_tariffs(house.id)
    assert tariffs[0].period_label == "08.2020 — 07.2021"
    assert tariffs[1].period_label == "с 08.2021"

    selected = repository.find_house_tariff_covering_period(
        house.id,
        start_year=2024,
        start_month=1,
        end_year=2024,
        end_month=12,
    )
    assert selected is not None
    assert selected.amount == "23"

    repository.close()


def test_exact_day_tariff_change_recalculates_previous_period(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК Пример",
        ogrn="1022600000001",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Примерная, д. 5",
    )

    first = repository.create_house_tariff(
        house_id=house.id,
        amount="23",
        start_year=2025,
        start_month=8,
        start_day=1,
        start_precision="month",
    )
    second = repository.create_house_tariff(
        house_id=house.id,
        amount="24",
        start_year=2026,
        start_month=8,
        start_day=10,
        start_precision="day",
    )

    first = repository.get_house_tariff(first.id)
    second = repository.get_house_tariff(second.id)
    assert first is not None
    assert second is not None
    assert first.effective_from_label == "08.2025"
    assert first.effective_to_date.isoformat() == "2026-08-09"
    assert first.period_label == "08.2025 — 09.08.2026"
    assert first.period_full_label == "01.08.2025 — 09.08.2026"
    assert second.effective_from_label == "10.08.2026"

    assert repository.find_house_tariff_covering_period(
        house.id,
        start_year=2026,
        start_month=1,
        end_year=2026,
        end_month=7,
    ).amount == "23"
    assert repository.find_house_tariff_covering_period(
        house.id,
        start_year=2026,
        start_month=8,
        end_year=2026,
        end_month=8,
    ) is None

    repository.close()


def test_tariffs_can_change_twice_in_one_month_when_dates_differ(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК Пример",
        ogrn="1022600000002",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Примерная, д. 6",
    )

    repository.create_house_tariff(
        house_id=house.id,
        amount="20",
        start_year=2025,
        start_month=8,
        start_day=1,
        start_precision="month",
    )
    repository.create_house_tariff(
        house_id=house.id,
        amount="21",
        start_year=2025,
        start_month=8,
        start_day=10,
        start_precision="day",
    )

    tariffs = repository.list_house_tariffs(house.id)
    assert len(tariffs) == 2
    assert tariffs[0].effective_to_date.isoformat() == "2025-08-09"

    repository.close()
