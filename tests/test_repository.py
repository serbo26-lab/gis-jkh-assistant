from gis_jkh_assistant.database import RecordSource, Repository


def test_organization_and_house_crud(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Пример", ogrn="1022600000000")
    house = repository.create_house(
        organization_id=org.id,
        address="г Ставрополь, ул Ленина, д. 289",
        house_object_guid="bfaf0062-af27-4e7c-b4d6-1ad379239b51",
        residential_premises_count=67,
        non_residential_premises_count=25,
        source=RecordSource.IMPORT,
    )

    assert house.total_premises_count == 92
    assert repository.list_houses(org.id)[0].address.endswith("д. 289")

    repository.update_house(house.id, residential_premises_count=68)
    assert repository.get_house(house.id).residential_premises_count == 68

    repository.delete_organization(org.id)
    assert repository.list_organizations() == []
    repository.close()
