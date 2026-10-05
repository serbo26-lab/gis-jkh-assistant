from gis_jkh_assistant.acceptance_act_pdf import build_acceptance_act_pdf
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.quality_assessment import (
    build_quality_command_payload,
    helper_supports_quality_assessment,
    quality_reduction,
)


def _fixture(repository: Repository):
    organization = repository.create_organization(
        name="УК Оценка качества",
        ogrn="1022600000400",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Тестовая, д. 30",
        house_object_guid="house-object-guid",
        fias_house_guid="34062768-dec0-4576-86cb-62bb82d854d2",
    )
    template = repository.create_work_template(
        organization_id=organization.id,
        name="Оценка качества",
        items=[
            type(
                "Item",
                (),
                {
                    "position": 1,
                    "work_code": "12",
                    "work_name": "Услуга по управлению",
                    "work_guid": "83592b3d-c928-416b-b6e0-595fa1339821",
                    "price": None,
                    "volume": None,
                    "quantity": "12",
                    "cost": "26151.38",
                    "allocation_percent": None,
                },
            )(),
            type(
                "Item",
                (),
                {
                    "position": 2,
                    "work_code": "13",
                    "work_name": "Техническое обслуживание",
                    "work_guid": "98a9a390-d7bb-455b-b0a7-79422b18b252",
                    "price": None,
                    "volume": None,
                    "quantity": "12",
                    "cost": "11886.99",
                    "allocation_percent": None,
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
        gis_work_list_guid="f505f0ff-8769-4732-9d21-185884a789aa",
        gis_status="APPROVED",
    )
    work_list = repository.get_work_list(work_list.id)
    assert work_list is not None
    return organization, house, work_list


def test_quality_helper_version() -> None:
    assert not helper_supports_quality_assessment(None)
    assert not helper_supports_quality_assessment("0.1.9")
    assert not helper_supports_quality_assessment("0.1.10")
    assert not helper_supports_quality_assessment("0.1.11")
    assert not helper_supports_quality_assessment("0.1.12")
    assert not helper_supports_quality_assessment("0.1.13")
    assert not helper_supports_quality_assessment("0.1.14")
    assert helper_supports_quality_assessment("0.1.15")
    assert helper_supports_quality_assessment("0.2.0")


def test_quality_reduction_matches_gis_formula() -> None:
    assert quality_reduction("1", 1, 31).startswith("0.03225806451612903")
    assert quality_reduction("31", 1, 31) == "1"
    assert quality_reduction("62,00", 2, 31) == "4.00"
    assert quality_reduction(None, 1, 31) is None
    assert quality_reduction("1", None, 31) is None
    assert quality_reduction("1", 32, 31) is None


def test_quality_period_snapshots_work_list_and_month_days(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list = _fixture(repository)
    store = QualityAssessmentStore(repository)

    period = store.get_or_create_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    items = store.list_items(period.id)

    assert len(items) == 2
    assert [item.work_name for item in items] == [
        "Услуга по управлению",
        "Техническое обслуживание",
    ]
    assert all(item.days_per_month == 31 for item in items)
    assert all(not item.included for item in items)

    repository.close()


def test_multiple_quality_acts_are_kept_for_one_work(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, house, work_list = _fixture(repository)
    store = QualityAssessmentStore(repository)
    period = store.get_or_create_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    item = store.list_items(period.id)[0]
    store.update_item(
        item.id,
        included=True,
        cost=None,
        days_count=None,
        cost_reduction=None,
    )

    first = store.add_act(
        item_id=item.id,
        act_type="ACCEPTANCE_ACT",
        number="№3",
        reg_date="01.01.2026",
        period_from="01.01.2026",
        period_to="01.01.2026",
        file_name="act-1.pdf",
        description="act-1.pdf",
        content_type="application/pdf",
        content=b"pdf-one",
    )
    second = store.add_act(
        item_id=item.id,
        act_type="ACCEPTANCE_ACT",
        number="№4",
        reg_date="02.01.2026",
        period_from="01.01.2026",
        period_to="01.01.2026",
        file_name="act-2.pdf",
        description="act-2.pdf",
        content_type="application/pdf",
        content=b"pdf-two",
    )

    acts = store.list_acts(item.id)
    assert [act.id for act in acts] == [first.id, second.id]

    refreshed = store.list_items(period.id)[0]
    payload = build_quality_command_payload(
        fias_house_code=house.fias_house_guid or "",
        house_object_guid=house.house_object_guid or "",
        work_list_guid=work_list.gis_work_list_guid or "",
        year=2026,
        month=1,
        items=[refreshed],
        acts_by_item={refreshed.id: acts},
    )
    assert payload["houseObjectGuid"] == "house-object-guid"
    assert len(payload["items"]) == 1
    assert len(payload["items"][0]["acts"]) == 2
    assert payload["items"][0]["acts"][0]["actType"] == "ACCEPTANCE_ACT"
    assert payload["items"][0]["acts"][1]["number"] == "№4"
    assert payload["items"][0]["acts"][0]["fileBase64"]

    repository.close()


def test_remote_quality_act_is_marked_for_removal_and_sent_in_payload(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, house, work_list = _fixture(repository)
    store = QualityAssessmentStore(repository)
    period = store.get_or_create_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    item = store.list_items(period.id)[0]
    store.update_item(
        item.id,
        included=True,
        cost=None,
        days_count=None,
        cost_reduction=None,
    )
    act = store.add_act(
        item_id=item.id,
        act_type="ACCEPTANCE_ACT",
        number="10",
        reg_date="01.01.2026",
        period_from="01.01.2026",
        period_to="10.01.2026",
        file_name="quality.pdf",
        description=None,
        content_type="application/pdf",
        content=b"quality",
    )
    store.set_gis_state(
        period.id,
        reporting_period_guid="reporting-guid",
        estimation_guid="estimation-guid",
        gis_status="APPROVED",
        act_guids={act.id: ("content-guid", "act-guid")},
    )

    store.delete_act(act.id)

    assert store.list_acts(item.id) == []
    all_acts = store.list_acts(item.id, include_deleted=True)
    assert len(all_acts) == 1
    assert all_acts[0].link_deleted is True

    refreshed = store.list_items(period.id)[0]
    payload = build_quality_command_payload(
        fias_house_code=house.fias_house_guid or "",
        house_object_guid=house.house_object_guid or "",
        work_list_guid=work_list.gis_work_list_guid or "",
        year=2026,
        month=1,
        items=[refreshed],
        acts_by_item={refreshed.id: all_acts},
    )
    row = payload["items"][0]
    assert row["acts"] == []
    assert row["removedRemoteActGuids"] == ["act-guid"]
    assert row["removedRemoteContentGuids"] == ["content-guid"]

    period_after = store.get_period(period.id)
    assert period_after is not None
    assert period_after.gis_status == "MODIFIED"
    repository.close()


def test_cancelled_quality_assessment_keeps_local_data_for_reuse(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, _house, work_list = _fixture(repository)
    store = QualityAssessmentStore(repository)
    period = store.get_or_create_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    item = store.list_items(period.id)[0]
    act = store.add_act(
        item_id=item.id,
        act_type="ACCEPTANCE_ACT",
        number="11",
        reg_date="01.01.2026",
        period_from="01.01.2026",
        period_to="11.01.2026",
        file_name="act.pdf",
        description="act.pdf",
        content_type="application/pdf",
        content=b"act",
    )
    store.set_gis_state(
        period.id,
        reporting_period_guid="reporting-guid",
        estimation_guid="estimation-guid",
        gis_status="APPROVED",
        item_guids={item.id: "item-guid"},
        act_guids={act.id: ("content-guid", "act-guid")},
    )

    store.mark_cancelled(period.id)

    cancelled = store.get_period(period.id)
    assert cancelled is not None
    assert cancelled.estimation_guid is None
    assert cancelled.gis_status == "CANCELLED"

    refreshed_item = store.get_item(item.id)
    assert refreshed_item is not None
    assert refreshed_item.gis_estimation_item_guid is None

    kept_act = store.list_acts(item.id)[0]
    assert kept_act.remote_content_guid == "content-guid"
    assert kept_act.remote_act_guid is None
    assert kept_act.content == b"act"
    repository.close()


def test_one_quality_act_can_be_linked_to_multiple_works(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, house, work_list = _fixture(repository)
    store = QualityAssessmentStore(repository)
    period = store.get_or_create_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    items = store.list_items(period.id)
    for item in items:
        store.update_item(
            item.id,
            included=True,
            cost=None,
            days_count=None,
            cost_reduction=None,
        )

    act = store.add_act(
        item_id=items[0].id,
        act_type="ACCEPTANCE_ACT",
        number="1",
        reg_date="31.01.2026",
        period_from="01.01.2026",
        period_to="31.01.2026",
        file_name="shared.pdf",
        description="shared.pdf",
        content_type="application/pdf",
        content=b"shared-pdf",
    )
    store.set_act_links(act.id, [item.id for item in items])

    assert store.count_act_links(act.id) == 2
    assert [row.id for row in store.list_acts(items[0].id)] == [act.id]
    assert [row.id for row in store.list_acts(items[1].id)] == [act.id]

    refreshed = store.list_items(period.id)
    acts = {
        item.id: store.list_acts(item.id, include_deleted=True)
        for item in refreshed
    }
    payload = build_quality_command_payload(
        fias_house_code=house.fias_house_guid or "",
        house_object_guid=house.house_object_guid or "",
        work_list_guid=work_list.gis_work_list_guid or "",
        year=2026,
        month=1,
        items=refreshed,
        acts_by_item=acts,
    )
    assert len(payload["items"]) == 2
    assert payload["items"][0]["acts"][0]["localActId"] == act.id
    assert payload["items"][1]["acts"][0]["localActId"] == act.id
    assert (
        payload["items"][0]["acts"][0]["fileBase64"]
        == payload["items"][1]["acts"][0]["fileBase64"]
    )
    repository.close()


def test_unlinking_remote_shared_act_removes_only_one_work_link(tmp_path) -> None:
    repository = Repository(tmp_path / "app.db")
    _organization, house, work_list = _fixture(repository)
    store = QualityAssessmentStore(repository)
    period = store.get_or_create_period(
        work_list_id=work_list.id,
        year=2026,
        month=1,
    )
    items = store.list_items(period.id)
    for item in items:
        store.update_item(
            item.id,
            included=True,
            cost=None,
            days_count=None,
            cost_reduction=None,
        )

    act = store.add_act(
        item_id=items[0].id,
        act_type="ACCEPTANCE_ACT",
        number="1",
        reg_date="31.01.2026",
        period_from="01.01.2026",
        period_to="31.01.2026",
        file_name="shared.pdf",
        description=None,
        content_type="application/pdf",
        content=b"shared",
    )
    store.set_act_links(act.id, [item.id for item in items])
    store.set_gis_state(
        period.id,
        reporting_period_guid="reporting-guid",
        estimation_guid="estimation-guid",
        gis_status="APPROVED",
        act_guids={act.id: ("content-guid", "act-guid")},
    )

    store.unlink_act(act.id, items[1].id)

    assert store.count_act_links(act.id) == 1
    removed = store.list_acts(items[1].id, include_deleted=True)
    assert len(removed) == 1
    assert removed[0].link_deleted is True

    refreshed = store.list_items(period.id)
    payload = build_quality_command_payload(
        fias_house_code=house.fias_house_guid or "",
        house_object_guid=house.house_object_guid or "",
        work_list_guid=work_list.gis_work_list_guid or "",
        year=2026,
        month=1,
        items=refreshed,
        acts_by_item={
            item.id: store.list_acts(item.id, include_deleted=True)
            for item in refreshed
        },
    )
    by_id = {row["localItemId"]: row for row in payload["items"]}
    assert by_id[items[0].id]["acts"][0]["remoteActGuid"] == "act-guid"
    assert by_id[items[1].id]["acts"] == []
    assert by_id[items[1].id]["removedRemoteActGuids"] == ["act-guid"]
    repository.close()


def test_generated_acceptance_act_pdf_is_valid_pdf() -> None:
    content = build_acceptance_act_pdf(
        organization_name='ООО "УК Тест"',
        house_address="г Ставрополь, ул Тестовая, д. 30",
        act_number="1",
        act_date="31.01.2026",
        period_from="01.01.2026",
        period_to="31.01.2026",
        work_names=[
            "Услуга по управлению",
            "Техническое обслуживание инженерных сетей входящих в состав общего имущества",
        ],
    )

    assert content.startswith(b"%PDF-")
    assert len(content) > 2000
