from dataclasses import dataclass

from gis_jkh_assistant.database import Repository, TemplateSource


@dataclass
class Item:
    position: int
    work_code: str
    work_name: str
    work_guid: str
    price: str | None = None
    volume: str | None = None
    quantity: str | None = None
    cost: str | None = None


def test_work_list_defaults_to_full_year_and_copies_template(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Пример", ogrn="1022600000000")
    house = repository.create_house(
        organization_id=org.id,
        address="г Ставрополь, ул Ленина, д. 289",
    )
    template = repository.create_work_template(
        organization_id=org.id,
        name="Базовый",
        source=TemplateSource.IMPORT,
        items=[
            Item(
                position=1,
                work_code="12",
                work_name="Услуга по управлению",
                work_guid="guid-12",
            )
        ],
    )

    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2020,
        template_id=template.id,
    )

    assert (work_list.start_year, work_list.start_month) == (2020, 1)
    assert (work_list.end_year, work_list.end_month) == (2020, 12)
    assert work_list.period_label == "01.2020 — 12.2020"

    items = repository.list_work_list_items(work_list.id)
    assert len(items) == 1
    assert items[0].work_code == "12"
    assert items[0].work_name == "Услуга по управлению"

    repository.update_work_list_period(
        work_list.id,
        start_year=2020,
        start_month=5,
        end_year=2020,
        end_month=12,
    )
    updated = repository.get_work_list(work_list.id)
    assert updated.period_label == "05.2020 — 12.2020"

    repository.close()
