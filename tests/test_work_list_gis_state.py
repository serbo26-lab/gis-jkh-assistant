from gis_jkh_assistant.database import Repository, WorkListStatus


def test_gis_draft_and_approved_statuses_are_persisted(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Пример", ogrn="1022600000000")
    house = repository.create_house(
        organization_id=org.id,
        address="г Ставрополь, ул Примерная, д. 1",
        house_object_guid="house-guid",
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
    )

    repository.update_work_list_gis_state(
        work_list.id,
        gis_work_list_guid="draft-guid",
        gis_file_guid="file-guid",
        gis_import_job_guid="import-guid",
        gis_status="DRAFT",
    )
    draft = repository.get_work_list(work_list.id)
    assert draft is not None
    assert draft.status == WorkListStatus.GIS_DRAFT
    assert draft.gis_status == "DRAFT"
    assert draft.gis_work_list_guid == "draft-guid"
    assert draft.gis_file_guid == "file-guid"
    assert draft.gis_import_job_guid == "import-guid"
    assert draft.gis_last_sync_at is not None
    assert draft.gis_last_error is None

    repository.update_work_list_gis_state(
        work_list.id,
        gis_status="APPROVED",
        gis_last_error=None,
    )
    approved = repository.get_work_list(work_list.id)
    assert approved is not None
    assert approved.status == WorkListStatus.PLACED
    assert approved.gis_status == "APPROVED"

    repository.close()


def test_not_found_does_not_destroy_previous_local_lifecycle(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    org = repository.create_organization(name="УК Пример", ogrn="1022600000000")
    house = repository.create_house(
        organization_id=org.id,
        address="г Ставрополь, ул Примерная, д. 2",
    )
    work_list = repository.create_work_list(house_id=house.id, base_year=2026)

    repository.update_work_list_gis_state(
        work_list.id,
        gis_work_list_guid="deleted-project-guid",
        gis_status="DRAFT",
    )
    draft = repository.get_work_list(work_list.id)
    assert draft is not None
    assert draft.status == WorkListStatus.GIS_DRAFT

    repository.update_work_list_gis_state(
        work_list.id,
        gis_status="NOT_FOUND",
        gis_last_error=None,
    )
    updated = repository.get_work_list(work_list.id)
    assert updated is not None
    assert updated.status == WorkListStatus.LOCAL
    assert updated.gis_status == "NOT_FOUND"
    assert updated.gis_work_list_guid is None

    repository.close()
