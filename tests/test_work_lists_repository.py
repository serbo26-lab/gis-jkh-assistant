from dataclasses import dataclass

from gis_jkh_assistant.database import Repository, TemplateSource
from gis_jkh_assistant.work_lists import AllocationResult


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


def test_lazy_distribution_is_persisted_and_cloned(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _org, house, template = _fixture(repository)

    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
    )
    item = repository.list_work_list_items(work_list.id)[0]

    repository.apply_lazy_distribution(
        work_list.id,
        planned_total="100000",
        rows=[
            AllocationResult(
                item_id=item.id,
                allocation_percent="100",
                quantity="12",
                cost="100000",
            )
        ],
    )

    refreshed = repository.get_work_list(work_list.id)
    assert refreshed is not None
    assert refreshed.planned_total == "100000"

    refreshed_item = repository.list_work_list_items(work_list.id)[0]
    assert refreshed_item.price is None
    assert refreshed_item.volume is None
    assert refreshed_item.quantity == "12"
    assert refreshed_item.cost == "100000"
    assert refreshed_item.allocation_percent == "100"

    clone = repository.clone_work_list(work_list.id, 2025)
    assert clone.planned_total == "100000"
    clone_item = repository.list_work_list_items(clone.id)[0]
    assert clone_item.allocation_percent == "100"
    assert clone_item.cost == "100000"

    repository.close()


def test_template_distribution_percentages_can_be_reused(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _org, house, template = _fixture(repository)

    repository.save_work_template_allocation_percentages(
        template.id,
        {1: "100"},
    )

    template_items = repository.list_work_template_items(template.id)
    assert len(template_items) == 1
    assert template_items[0].allocation_percent == "100"

    second_house = repository.create_house(
        organization_id=template.organization_id,
        address="г Ставрополь, ул Ленина, д. 291",
    )
    second_list = repository.create_work_list(
        house_id=second_house.id,
        base_year=2026,
        template_id=template.id,
    )

    assert second_list.template_id == template.id
    assert repository.list_work_list_items(second_list.id)[0].allocation_percent is None
    assert repository.list_work_template_items(template.id)[0].allocation_percent == "100"

    repository.close()


def test_template_distribution_requires_matching_positions(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _org, _house, template = _fixture(repository)

    import pytest

    with pytest.raises(ValueError, match="Состав перечня отличается"):
        repository.save_work_template_allocation_percentages(
            template.id,
            {2: "100"},
        )

    repository.close()


def test_planned_work_unit_is_persisted_and_propagated_to_fixation(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _org, house, template = _fixture(repository)
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
    )
    repository.update_work_list_gis_state(
        work_list.id,
        gis_work_list_guid="work-list-guid",
        gis_status="APPROVED",
    )
    list_item = repository.list_work_list_items(work_list.id)[0]

    fixation = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    repository.update_work_list_unit_names(
        work_list.id,
        {list_item.id: "мес"},
    )

    refreshed_list_item = repository.list_work_list_items(work_list.id)[0]
    fixation_item = repository.list_work_fixation_items(fixation.id)[0]

    assert refreshed_list_item.unit_name == "мес"
    assert fixation_item.unit_name == "мес"
    repository.close()


def test_work_list_defaults_are_clipped_to_management_period(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Период", ogrn="1022600000300")
    house = repository.create_house(
        organization_id=org.id,
        address="г. М..., ул. Б..., д. 0",
        management_start_date="01.06.2020",
        management_end_date="01.03.2022",
        management_agreement_number="Д-1",
        management_agreement_date="15.05.2020",
        management_data_source="manual",
    )

    first = repository.create_work_list(house_id=house.id, base_year=2020)
    middle = repository.create_work_list(house_id=house.id, base_year=2021)
    last = repository.create_work_list(house_id=house.id, base_year=2022)

    assert first.period_label == "06.2020 — 12.2020"
    assert middle.period_label == "01.2021 — 12.2021"
    assert last.period_label == "01.2022 — 02.2022"

    repository.close()


def test_work_list_period_cannot_leave_management_period(tmp_path) -> None:
    import pytest

    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Границы", ogrn="1022600000301")
    house = repository.create_house(
        organization_id=org.id,
        address="г. М..., ул. Б..., д. 0",
        management_start_date="01.06.2020",
        management_end_date="01.03.2022",
    )
    work_list = repository.create_work_list(house_id=house.id, base_year=2021)

    with pytest.raises(ValueError, match="раньше начала управления"):
        repository.update_work_list_period(
            work_list.id,
            start_year=2020,
            start_month=5,
            end_year=2021,
            end_month=5,
        )

    with pytest.raises(ValueError, match="после окончания управления"):
        repository.update_work_list_period(
            work_list.id,
            start_year=2021,
            start_month=6,
            end_year=2022,
            end_month=3,
        )

    repository.update_work_list_period(
        work_list.id,
        start_year=2020,
        start_month=6,
        end_year=2022,
        end_month=2,
    )
    refreshed = repository.get_work_list(work_list.id)
    assert refreshed is not None
    assert refreshed.period_label == "06.2020 — 02.2022"
    repository.close()


def test_missing_management_dates_do_not_restrict_work_list(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Без дат", ogrn="1022600000302")
    house = repository.create_house(
        organization_id=org.id,
        address="г. М..., ул. Б..., д. 0",
    )
    work_list = repository.create_work_list(house_id=house.id, base_year=2020)
    repository.update_work_list_period(
        work_list.id,
        start_year=2019,
        start_month=6,
        end_year=2021,
        end_month=5,
    )
    refreshed = repository.get_work_list(work_list.id)
    assert refreshed is not None
    assert refreshed.period_label == "06.2019 — 05.2021"
    repository.close()


def test_empty_work_list_can_receive_and_remove_directory_works(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Каталог", ogrn="1022600000500")
    house = repository.create_house(
        organization_id=org.id,
        address="г. М..., ул. Б..., д. 0",
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
    )
    assert repository.list_work_list_items(work_list.id) == []

    first = Item(
        position=1,
        work_code="12",
        work_name="Услуга по управлению",
        work_guid="guid-12",
    )
    second = Item(
        position=2,
        work_code="13",
        work_name="Техническое обслуживание",
        work_guid="guid-13",
    )

    added = repository.add_work_list_items(work_list.id, [first, second, first])
    assert added == 2
    items = repository.list_work_list_items(work_list.id)
    assert [item.work_code for item in items] == ["12", "13"]

    removed = repository.delete_work_list_items(work_list.id, [items[0].id])
    assert removed == 1
    remaining = repository.list_work_list_items(work_list.id)
    assert len(remaining) == 1
    assert remaining[0].work_code == "13"
    assert remaining[0].position == 1
    repository.close()


def test_template_can_be_created_from_current_work_list_composition(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Шаблон", ogrn="1022600000501")
    house = repository.create_house(
        organization_id=org.id,
        address="г. М..., ул. Б..., д. 0",
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
    )
    repository.add_work_list_items(
        work_list.id,
        [
            type(
                "DirectoryItem",
                (),
                {
                    "position": 1,
                    "work_code": "12",
                    "work_name": "Услуга по управлению",
                    "work_guid": "guid-12",
                    "unit_name": "мес",
                    "price": "99",
                    "volume": "2",
                    "quantity": "12",
                    "cost": None,
                    "allocation_percent": "100",
                },
            )()
        ],
    )

    template = repository.create_work_template_from_work_list(
        work_list_id=work_list.id,
        name="Основной",
    )
    template_items = repository.list_work_template_items(template.id)

    assert template.organization_id == org.id
    assert len(template_items) == 1
    assert template_items[0].work_code == "12"
    assert template_items[0].unit_name == "мес"
    assert template_items[0].price is None
    assert template_items[0].volume is None
    assert template_items[0].quantity is None
    assert template_items[0].allocation_percent == "100"
    repository.close()


def test_create_work_list_can_use_validated_template_subset(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org, house, template = _fixture(repository)
    original = repository.list_work_template_items(template.id)[0]
    current_directory_row = type(
        "DirectoryItem",
        (),
        {
            "position": 1,
            "work_code": original.work_code,
            "work_name": original.work_name,
            "work_guid": original.work_guid,
            "unit_name": "мес",
            "price": original.price,
            "volume": original.volume,
            "quantity": original.quantity,
            "cost": original.cost,
            "allocation_percent": original.allocation_percent,
        },
    )()

    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
        items=[current_directory_row],
    )
    items = repository.list_work_list_items(work_list.id)

    assert len(items) == 1
    assert items[0].work_guid == original.work_guid
    assert items[0].unit_name == "мес"
    repository.close()


def test_loaded_or_placed_list_composition_cannot_be_changed(tmp_path) -> None:
    import pytest

    repository = Repository(tmp_path / "app.db")
    _org, house, template = _fixture(repository)
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        template_id=template.id,
    )
    repository.update_work_list_gis_state(
        work_list.id,
        gis_work_list_guid="gis-list",
        gis_status="DRAFT",
    )

    with pytest.raises(ValueError, match="локального перечня"):
        repository.add_work_list_items(
            work_list.id,
            [
                Item(
                    position=2,
                    work_code="13",
                    work_name="Работа",
                    work_guid="guid-13",
                )
            ],
        )

    item = repository.list_work_list_items(work_list.id)[0]
    with pytest.raises(ValueError, match="локального перечня"):
        repository.delete_work_list_items(work_list.id, [item.id])
    repository.close()
