"""Tariff periods, shared basis restrictions and atomic local fee generation."""
from copy import deepcopy
from datetime import date
import sqlite3

import pytest

import test_management_fees as fees
from gis_jkh_assistant.fee_bulk import (
    available_protocols, create_projects, prepare_candidates, prepare_jobs,
)

store = fees.store


def _setup(store):
    fee_store, house_id = store
    repo = fee_store.repository
    house = repo.get_house(house_id)
    fee_store.save_context(house_id, {"contracts": [deepcopy(fees.CONTRACT)], "protocols": [deepcopy(fees.PROTOCOL)]})
    return repo, fee_store, house


def _preview(repo, store, house, *, start=date(2026, 1, 1), end=date(2026, 12, 31), protocol=fees.PROTOCOL):
    return prepare_candidates(repo, store, house.organization_id, [house.id], start, end, protocol)


def test_tariff_days_and_exclusive_house_end_are_preserved(store):
    repo, fee_store, house = _setup(store)
    repo.update_house(house.id, management_start_date="2021-07-01", management_end_date="2023-01-01")
    for day, month, amount in ((15, 6, "10.00"), (21, 7, "11.00")):
        repo.create_house_tariff(house_id=house.id, amount=amount, start_year=2021, start_month=month,
                                 start_day=day, start_precision="day")
    before = repo.list_house_tariffs(house.id)
    candidates = _preview(repo, fee_store, house, start=date(2020, 1, 1), end=date(2026, 12, 31))
    assert [(c.document["start"], c.document["end"], c.document["amount"]) for c in candidates] == [
        ("2021-07-01", "2021-07-20", "10.00"), ("2021-07-21", "2022-12-31", "11.00")]
    created = create_projects(repo, fee_store, house.organization_id, [house.id], date(2020, 1, 1),
                              date(2026, 12, 31), fees.PROTOCOL, candidates)
    assert len(created) == 2
    assert repo.list_house_tariffs(house.id) == before
    again = _preview(repo, fee_store, house, start=date(2020, 1, 1))
    assert all(candidate.blocked for candidate in again)


def test_open_tariff_uses_explicit_end_without_rounding(store):
    repo, fee_store, house = _setup(store)
    repo.create_house_tariff(house_id=house.id, amount="15.12", start_year=2026, start_month=1)
    assert _preview(repo, fee_store, house, end=date(2026, 9, 17))[0].document["end"] == "2026-09-17"


def test_different_contract_periods_split_tariff_and_overlaps_are_not_guessed(store):
    repo, fee_store, house = _setup(store)
    repo.create_house_tariff(house_id=house.id, amount="15.12", start_year=2026, start_month=1)
    first = dict(fees.CONTRACT, to="2026-06-30")
    second = dict(fees.CONTRACT, guid="second", versionGuid="v2", objectRootGuid="o2", **{"from": "2026-07-01"})
    fee_store.save_context(house.id, {"contracts": [first, second], "protocols": [fees.PROTOCOL]})
    candidates = _preview(repo, fee_store, house)
    assert [(c.document["start"], c.document["end"]) for c in candidates] == [
        ("2026-01-01", "2026-06-30"), ("2026-07-01", "2026-12-31")]
    assert [c.document["contract"]["guid"] for c in candidates] == [first["guid"], second["guid"]]
    second["from"] = "2020-01-01"
    fee_store.save_context(house.id, {"contracts": [fees.CONTRACT, second], "protocols": []})
    candidates = _preview(repo, fee_store, house)
    assert candidates[0].document["contract"] is None
    assert "Выберите договор" in candidates[0].detail


def test_default_protocol_never_claims_another_house_or_equal_number(store):
    repo, fee_store, house = _setup(store)
    other = repo.create_house(organization_id=house.organization_id, address="Other house", fias_house_guid="other-fias")
    other_protocol = dict(fees.PROTOCOL, votingGuid="different-guid", fiasHouseCodes=[other.fias_house_guid])
    fee_store.save_context(other.id, {"contracts": [], "protocols": [other_protocol]})
    protocols = available_protocols(repo, fee_store, house.organization_id, [house.id, other.id])
    assert {p["votingGuid"] for p in protocols} == {fees.PROTOCOL["votingGuid"], "different-guid"}
    repo.create_house_tariff(house_id=other.id, amount="10", start_year=2026, start_month=1)
    candidate = _preview(repo, fee_store, other)[0]
    assert candidate.document["protocol"] is None
    assert "Выберите протокол" in candidate.detail


@pytest.mark.parametrize("change", ["tariff", "existing", "failure"])
def test_generation_rechecks_review_and_is_atomic(store, monkeypatch, change):
    repo, fee_store, house = _setup(store)
    tariff = repo.create_house_tariff(house_id=house.id, amount="15.12", start_year=2026, start_month=1)
    repo.create_house_tariff(house_id=house.id, amount="16", start_year=2026, start_month=7)
    candidates = _preview(repo, fee_store, house)
    if change == "tariff":
        repo.update_house_tariff(tariff.id, amount="17", start_year=2026, start_month=1)
    elif change == "existing":
        fee_store.save_local(house.id, fees.DOCUMENT)
    else:
        original = fee_store.save_local
        count = []
        def fail_second(*args, **kwargs):
            count.append(1)
            if len(count) == 2:
                raise sqlite3.OperationalError("TEST database failure")
            return original(*args, **kwargs)
        monkeypatch.setattr(fee_store, "save_local", fail_second)
    before = fee_store.list(house.id)
    with pytest.raises((ValueError, sqlite3.Error)):
        create_projects(repo, fee_store, house.organization_id, [house.id], date(2026, 1, 1),
                        date(2026, 12, 31), fees.PROTOCOL, candidates)
    assert fee_store.list(house.id) == before


@pytest.mark.parametrize("status", ["APPROVED", "TERMINATED"])
def test_generation_and_bulk_transfer_preserve_remote_history(store, status):
    repo, fee_store, house = _setup(store)
    repo.create_house_tariff(house_id=house.id, amount="15.12", start_year=2026, start_month=1)
    fee_store.import_remote(house.id, fees.context(fees.remote(status=status)))
    before = fee_store.list(house.id)
    assert _preview(repo, fee_store, house)[0].blocked is (status == "APPROVED")
    for action in ("save", "approve"):
        assert all(job.state == "blocked" for job in prepare_jobs(repo, fee_store, house.organization_id, [house.id], action))
    assert fee_store.list(house.id) == before


def test_save_and_approve_are_distinct_and_pending_is_not_retryable(store):
    repo, fee_store, house = _setup(store)
    local = fee_store.save_local(house.id, fees.DOCUMENT)
    def jobs(action):
        return prepare_jobs(repo, fee_store, house.organization_id, [house.id], action)
    assert jobs("save")[0].state == "ready"
    assert jobs("approve")[0].state == "blocked"
    fee_store.mark_pending(local)
    assert jobs("save")[0].state == "blocked"
    fee_store.import_remote(house.id, fees.context(fees.remote()))
    assert jobs("save")[0].state == "blocked"
    assert jobs("approve")[0].state == "ready"
    fee_store.save_local(house.id, dict(fees.DOCUMENT, amount="20"), local)
    assert jobs("save")[0].state == "ready"
    assert jobs("approve")[0].state == "blocked"


def test_cross_organization_scope_is_rejected(store):
    repo, fee_store, house = _setup(store)
    other = repo.create_organization(name="Other UO", ogrn="1022600000541")
    with pytest.raises(ValueError, match="УО"):
        prepare_candidates(repo, fee_store, other.id, [house.id], date(2026, 1, 1), date(2026, 12, 31))


def test_same_gis_protocol_can_cover_two_houses_without_changing_tariffs(store):
    repo, fee_store, house = _setup(store)
    other = repo.create_house(organization_id=house.organization_id, address="Other house", fias_house_guid="other-fias")
    shared = dict(fees.PROTOCOL, fiasHouseCodes=[house.fias_house_guid, other.fias_house_guid])
    for item in (house, other):
        contract = dict(fees.CONTRACT, objectRootGuid=item.id, fiasHouseGuid=item.fias_house_guid)
        fee_store.save_context(item.id, {"contracts": [contract], "protocols": [shared]})
        repo.create_house_tariff(house_id=item.id, amount="15.12", start_year=2026, start_month=1)
    choices = available_protocols(repo, fee_store, house.organization_id, [house.id, other.id])
    assert len(choices) == 1
    candidates = prepare_candidates(repo, fee_store, house.organization_id, [house.id, other.id],
                                     date(2026, 1, 1), date(2026, 12, 31), choices[0])
    assert len(candidates) == 2
    assert all(fees.validate_document(c.document) for c in candidates)


def test_invalid_precision_blocks_only_affected_tariff_without_rounding(store):
    repo, fee_store, house = _setup(store)
    repo.create_house_tariff(house_id=house.id, amount="15.123", start_year=2026, start_month=1)
    repo.create_house_tariff(house_id=house.id, amount="16.00", start_year=2026, start_month=7)
    candidates = _preview(repo, fee_store, house)
    assert candidates[0].blocked and candidates[0].document["amount"] == "15.123"
    assert not candidates[1].blocked
