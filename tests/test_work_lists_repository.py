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


def _fixture(repository: Repository):
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
                price="10",
                volume="2",
                quantity="1",
                cost="20",
            )
        ],
    )
    return org, house, template


def test_work_list_defaults_to_full_selected_year_and_copies_template(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _org, house, template = _fixture(repository)

    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
    )

    assert (work_list.start_year, work_list.start_month) == (2026, 1)
    assert (work_list.end_year, work_list.end_month) == (2026, 12)
    assert work_list.period_label == "01.2026 — 12.2026"

    items = repository.list_work_list_items(work_list.id)
    assert len(items) == 1
    assert items[0].work_code == "12"
    assert items[0].price == "10"

    repository.close()


def test_clone_shifts_period_and_keeps_current_values(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _org, house, template = _fixture(repository)

    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
    )
    repository.update_work_list_period(
        work_list.id,
        start_year=2025,
        start_month=6,
        end_year=2026,
        end_month=5,
    )
    source_item = repository.list_work_list_items(work_list.id)[0]
    repository.update_work_list_item(source_item.id, price="17.25")

    clone = repository.clone_work_list(work_list.id, 2025)

    assert clone.base_year == 2025
    assert clone.period_label == "06.2024 — 05.2025"
    cloned_item = repository.list_work_list_items(clone.id)[0]
    assert cloned_item.price == "17.25"

    repository.close()


def test_all_house_lists_are_returned_in_period_order(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _org, house, template = _fixture(repository)

    later = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
    )
    earlier = repository.create_work_list(
        house_id=house.id,
        base_year=2024,
        template_id=template.id,
    )
    transition = repository.create_work_list(
        house_id=house.id,
        base_year=2025,
        template_id=template.id,
    )
    repository.update_work_list_period(
        transition.id,
        start_year=2025,
        start_month=6,
        end_year=2026,
        end_month=5,
    )

    listed = repository.list_work_lists(house.id)

    assert [item.id for item in listed] == [earlier.id, transition.id, later.id]
    repository.close()
