from gis_jkh_assistant.database import Repository


def test_planned_calculator_values_are_persisted_and_cloned(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    organization = repository.create_organization(
        name="УК Пример",
        ogrn="1022600000000",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Примерная, д. 10",
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
    )

    repository.apply_lazy_distribution(
        work_list.id,
        planned_total="118869.89",
        planned_tariff="25.04",
        planned_area="395.6",
        planned_months="12",
        rows=[],
    )

    saved = repository.get_work_list(work_list.id)
    assert saved is not None
    assert saved.planned_total == "118869.89"
    assert saved.planned_tariff == "25.04"
    assert saved.planned_area == "395.6"
    assert saved.planned_months == "12"

    cloned = repository.clone_work_list(work_list.id, 2025)
    assert cloned.planned_total == "118869.89"
    assert cloned.planned_tariff == "25.04"
    assert cloned.planned_area == "395.6"
    assert cloned.planned_months == "12"

    repository.close()
