from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.work_lists.gis_sync import (
    apply_gis_work_list_sync,
    prepare_gis_work_list_sync,
)


def _remote(guid: str, house_guid: str, *, status: str = "APPROVED") -> dict:
    return {
        "guid": guid,
        "houseGuid": house_guid,
        "status": status,
        "period": {
            "from": {"year": 2026, "month": 1},
            "to": {"year": 2026, "month": 12},
        },
        "summary": {
            "totalSquare": "100",
            "monthsCount": 12,
            "worksCount": 1,
            "totalCost": "1200",
        },
        "items": [
            {
                "sortIndex": 1,
                "workGuid": "work-guid",
                "workCode": "1.1",
                "workName": "Работа",
                "unitName": "шт.",
                "price": "100",
                "volume": "1",
                "quantity": "12",
                "cost": None,
            }
        ],
    }


def test_prepare_point_sync_is_limited_to_selected_house(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК", ogrn="1022600000700")
    selected = repository.create_house(
        organization_id=org.id,
        address="Дом 1",
        house_object_guid="house-1",
    )
    repository.create_house(
        organization_id=org.id,
        address="Дом 2",
        house_object_guid="house-2",
    )

    preview = prepare_gis_work_list_sync(
        repository,
        org.id,
        [
            _remote("list-1", "house-1"),
            _remote("list-2", "house-2"),
        ],
        target_house_id=selected.id,
    )

    assert preview.received == 2
    assert len(preview.new_rows) == 1
    assert preview.new_rows[0][0] == selected.id
    assert len(preview.unmatched) == 1
    repository.close()


def test_apply_sync_imports_new_list_and_updates_linked_status(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК", ogrn="1022600000701")
    house = repository.create_house(
        organization_id=org.id,
        address="Дом",
        house_object_guid="house-1",
    )

    first = prepare_gis_work_list_sync(
        repository,
        org.id,
        [_remote("list-1", "house-1", status="DRAFT")],
    )
    applied = apply_gis_work_list_sync(repository, first)

    assert applied.imported == 1
    imported = repository.find_work_list_by_gis_guid("list-1")
    assert imported is not None
    assert imported.gis_status == "DRAFT"

    second = prepare_gis_work_list_sync(
        repository,
        org.id,
        [_remote("list-1", "house-1", status="APPROVED")],
    )
    applied_again = apply_gis_work_list_sync(repository, second)

    assert applied_again.imported == 0
    assert applied_again.updated == 1
    refreshed = repository.find_work_list_by_gis_guid("list-1")
    assert refreshed is not None
    assert refreshed.gis_status == "APPROVED"
    assert refreshed.status.value == "placed"
    repository.close()
