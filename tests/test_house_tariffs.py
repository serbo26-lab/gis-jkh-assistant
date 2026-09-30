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
