"""Placed fee snapshots fill gaps without editing the user's tariff history."""
from copy import deepcopy
from datetime import date
import sqlite3

import pytest

import test_management_fees as fees
from gis_jkh_assistant.app import portfolio_import as imports
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.house_fee_tariffs import save_snapshot
from gis_jkh_assistant.fee_bulk import prepare_candidates

store = fees.store


def placed(guid="published", start="2026-01-01", end="2026-12-31", amount="15.12", **updates):
    return fees.remote(guid=guid, status="APPROVED", period={"from": start, "to": end},
                       managementPayment=amount, **updates)


def apply(fee_store, house_id, response):
    repo = fee_store.repository
    org = repo.get_house(house_id).organization_id
    job = next(job for job in imports.prepare_jobs(repo, fee_store, org, "fees") if job.house_id == house_id)
    imports.apply_job(repo, fee_store, job, response)
    return job


def intervals(repo, house_id):
    return [(item.effective_from_date, item.effective_to_date, item.amount, bool(item.gis_fee_guid))
            for item in repo.list_effective_house_tariffs(house_id)]


def test_import_reads_only_placed_fees_and_keeps_basis_and_local_projects(store):
    fee_store, house_id = store
    repo = fee_store.repository
    local_id = fee_store.save_local(house_id, dict(fees.DOCUMENT, amount="19.00"))
    job = apply(fee_store, house_id, fees.context(placed(), fees.remote(guid="draft"),
                                               fees.remote(guid="cancelled", status="TERMINATED")))
    assert job.state == "loaded" and "Размещённых сведений: 1" in job.detail
    assert len(repo.list_effective_house_tariffs(house_id)) == 1
    assert repo.list_house_tariffs(house_id) == []
    assert fee_store.get(local_id)["document"]["amount"] == "19.00"
    assert {row["remote_guid"] for row in fee_store.list(house_id)} == {None, "published"}
    assert fee_store.context(house_id)["protocols"] == [fees.PROTOCOL]
    imported = repo.list_effective_house_tariffs(house_id)[0]
    assert imported.basis_label == "№ 1 от 2020-01-01"
    assert repo.find_house_tariff_at_date(house_id, date(2026, 2, 1)).amount == "15.12"


def test_local_history_has_priority_over_full_or_partial_overlap_without_any_updates(store):
    fee_store, house_id = store
    repo = fee_store.repository
    repo.create_house_tariff(house_id=house_id, amount="21.00", start_year=2025, start_month=1)
    repo.create_house_tariff(house_id=house_id, amount="23.00", start_year=2026, start_month=7)
    before = [tuple(row) for row in repo.connection.execute("SELECT * FROM house_tariffs")]
    apply(fee_store, house_id, fees.context(placed(start="2024-07-10", end="2026-12-31")))
    assert [tuple(row) for row in repo.connection.execute("SELECT * FROM house_tariffs")] == before
    assert intervals(repo, house_id) == [
        (date(2024, 7, 10), date(2024, 12, 31), "15.12", True),
        (date(2025, 1, 1), date(2026, 6, 30), "21.00", False),
        (date(2026, 7, 1), None, "23.00", False),
    ]
    for target in (date(2025, 3, 1), date(2026, 9, 1), date(2028, 1, 1)):
        assert repo.find_house_tariff_at_date(house_id, target).gis_fee_guid is None


def test_exact_dates_and_gaps_survive_reopen_and_do_not_supply_incomplete_months(store):
    fee_store, house_id = store
    repo = fee_store.repository
    apply(fee_store, house_id, fees.context(
        placed("one", "2026-01-10", "2026-01-20"), placed("two", "2026-03-01", "2026-03-31")))
    assert repo.find_house_tariff_at_date(house_id, date(2026, 1, 21)) is None
    assert repo.find_house_tariff_covering_period(house_id, start_year=2026, start_month=1,
                                                 end_year=2026, end_month=1) is None
    assert repo.find_house_tariff_covering_period(house_id, start_year=2026, start_month=3,
                                                 end_year=2026, end_month=3).amount == "15.12"
    reopened = Repository(repo.database_path)
    try:
        assert intervals(reopened, house_id) == intervals(repo, house_id)
        assert reopened.list_house_tariffs(house_id) == []
    finally:
        reopened.close()
    house = repo.get_house(house_id)
    suggestions = prepare_candidates(repo, fee_store, house.organization_id, [house_id],
                                     date(2026, 1, 1), date(2026, 12, 31))
    assert [(item.document["start"], item.document["end"]) for item in suggestions] == [
        ("2026-01-10", "2026-01-20"), ("2026-03-01", "2026-03-31")]
    assert all(item.blocked for item in suggestions)  # Existing published records are not recreated.


def test_conflicting_gis_amounts_are_reported_and_not_guessed(store):
    fee_store, house_id = store
    job = apply(fee_store, house_id, fees.context(
        placed("a", "2026-01-01", "2026-02-28", "10.00"),
        placed("b", "2026-02-01", "2026-03-31", "12.00")))
    assert job.state == "review" and "Конфликтующих пар" in job.detail
    assert intervals(fee_store.repository, house_id) == [
        (date(2026, 1, 1), date(2026, 1, 31), "10.00", True),
        (date(2026, 3, 1), date(2026, 3, 31), "12.00", True),
    ]


def test_repeated_equal_overlaps_are_not_duplicates_and_local_override_wins(store):
    fee_store, house_id = store
    response = fees.context(placed("a"), placed("b"))
    apply(fee_store, house_id, response)
    before = intervals(fee_store.repository, house_id)
    apply(fee_store, house_id, response)
    assert intervals(fee_store.repository, house_id) == before and len(before) == 1
    repo = fee_store.repository
    local = repo.create_house_tariff(house_id=house_id, amount="28.00", start_year=2026, start_month=5)
    apply(fee_store, house_id, response)
    assert repo.find_house_tariff_at_date(house_id, date(2026, 5, 1)).id == local.id


@pytest.mark.parametrize("failure", ["period", "house", "contract", "db"])
def test_failed_house_response_rolls_back_snapshot_basis_and_fee_records(store, failure):
    fee_store, house_id = store
    repo = fee_store.repository
    apply(fee_store, house_id, fees.context(placed("original")))
    response = fees.context(placed("new", amount="22.00"))
    if failure == "period":
        response["records"][0]["record"]["period"]["to"] = "2025-01-01"
    elif failure == "house":
        response["contracts"][0]["fiasHouseGuid"] = "foreign-house"
    elif failure == "contract":
        response["records"][0]["record"]["agreementRootGuid"] = "foreign-contract"
    else:
        repo.connection.execute("CREATE TRIGGER test_fail BEFORE INSERT ON management_fee_records "
                                "BEGIN SELECT RAISE(ABORT, 'test database failure'); END")
    before = list(repo.connection.iterdump())
    with pytest.raises((ValueError, sqlite3.Error)):
        apply(fee_store, house_id, response)
    assert list(repo.connection.iterdump()) == before


def test_reload_clears_only_gis_snapshot_and_never_local_tariffs(store):
    fee_store, house_id = store
    repo = fee_store.repository
    apply(fee_store, house_id, fees.context(placed()))
    local = repo.create_house_tariff(house_id=house_id, amount="25.00", start_year=2026, start_month=7)
    apply(fee_store, house_id, fees.context(fees.remote(status="TERMINATED")))
    assert repo.list_effective_house_tariffs(house_id) == [repo.get_house_tariff(local.id)]


def test_changed_house_identity_does_not_reuse_old_snapshot(store):
    fee_store, house_id = store
    repo = fee_store.repository
    apply(fee_store, house_id, fees.context(placed()))
    house = repo.get_house(house_id)
    job = imports.prepare_jobs(repo, fee_store, house.organization_id, "fees")[0]
    repo.connection.execute("UPDATE houses SET fias_house_guid='new-fias' WHERE id=?", (house_id,))
    repo.connection.commit()
    assert repo.list_effective_house_tariffs(house_id) == []
    with pytest.raises(ValueError, match="изменились"):
        imports.apply_job(repo, fee_store, job, fees.context(placed()))


def test_local_edited_fee_form_is_not_used_as_published_tariff(store):
    fee_store, house_id = store
    fee_store.import_remote(house_id, fees.context(placed()))
    record = fee_store.list(house_id)[0]
    fee_store.begin_published_edit(record["id"])
    fee_store.save_local(house_id, dict(fees.DOCUMENT, amount="99.00"), record["id"])
    apply(fee_store, house_id, fees.context(placed()))
    assert fee_store.get(record["id"])["document"]["amount"] == "99.00"
    assert fee_store.repository.list_effective_house_tariffs(house_id)[0].amount == "15.12"


def test_snapshot_does_not_change_schema_or_data_in_legacy_tariff_table(store):
    fee_store, house_id = store
    repo = fee_store.repository
    schema = repo.connection.execute("SELECT sql FROM sqlite_master WHERE name='house_tariffs'").fetchone()[0]
    response = deepcopy(fees.context(placed()))
    with repo.connection.atomic():
        save_snapshot(repo, house_id, response)
    assert repo.connection.execute("SELECT sql FROM sqlite_master WHERE name='house_tariffs'").fetchone()[0] == schema
    assert repo.list_house_tariffs(house_id) == []
