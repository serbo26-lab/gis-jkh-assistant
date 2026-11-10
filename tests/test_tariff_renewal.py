"""Yearly continuation is finite, persistent and separate from remote writes."""
from copy import deepcopy
from datetime import date
import sqlite3

import pytest

import test_management_fees as fees
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.tariff_renewal import year_end
from gis_jkh_assistant.fee_bulk import prepare_candidates, create_projects
from gis_jkh_assistant.gis_files.house_data import write_house_data_template, parse_house_data_template

store = fees.store


def setup(store, **kwargs):
    fee_store, house_id = store
    repo = fee_store.repository
    house = repo.get_house(house_id)
    fee_store.save_context(house_id, {"contracts": [deepcopy(fees.CONTRACT)], "protocols": [deepcopy(fees.PROTOCOL)]})
    tariff = repo.create_house_tariff(house_id=house_id, amount="24.41", start_year=2024, start_month=4, **kwargs)
    return repo, fee_store, house, tariff


def test_years_have_dates_and_survive_reopen_without_duplicates(store):
    repo, _, house, tariff = setup(store)
    assert tariff.effective_to_date == date(2025, 3, 31)
    repo.ensure_house_tariff_periods(house.id, date(2026, 11, 10))
    before = repo.list_house_tariffs(house.id)
    assert [(t.effective_from_date, t.effective_to_date) for t in before] == [
        (date(2024, 4, 1), date(2025, 3, 31)),
        (date(2025, 4, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2027, 3, 31)),
    ]
    assert {t.amount for t in before} == {"24.41"}
    reopened = Repository(repo.database_path)
    try:
        reopened.ensure_house_tariff_periods(house.id, date(2026, 11, 10))
        assert reopened.list_house_tariffs(house.id) == before
    finally:
        reopened.close()


def test_new_price_replaces_generated_future_and_has_priority(store):
    repo, _, house, _ = setup(store)
    repo.ensure_house_tariff_periods(house.id, date(2028, 1, 1))
    repo.create_house_tariff(house_id=house.id, amount="30", start_year=2026, start_month=7)
    assert repo.find_house_tariff_at_date(house.id, date(2026, 6, 30)).amount == "24.41"
    assert repo.find_house_tariff_at_date(house.id, date(2026, 7, 1)).amount == "30"
    assert repo.find_house_tariff_at_date(house.id, date(2028, 1, 1)).amount == "30"
    rows = repo.list_house_tariffs(house.id)
    assert all(a.effective_to_date < b.effective_from_date for a, b in zip(rows, rows[1:]))


def test_disabled_renewal_leaves_gap_and_invalid_edit_is_atomic(store):
    repo, _, house, tariff = setup(store, auto_renew=False)
    assert repo.find_house_tariff_at_date(house.id, date(2025, 4, 1)) is None
    repo.update_house_tariff(tariff.id, amount="24.41", start_year=2024, start_month=4, auto_renew=True)
    repo.ensure_house_tariff_periods(house.id, date(2027, 1, 1))
    before = repo.list_house_tariffs(house.id)
    with pytest.raises(ValueError):
        repo.update_house_tariff(tariff.id, amount="", start_year=2024, start_month=4)
    assert repo.list_house_tariffs(house.id) == before
    repo.update_house_tariff(tariff.id, amount="24.41", start_year=2024, start_month=4, auto_renew=False)
    assert repo.find_house_tariff_at_date(house.id, date(2027, 1, 1)) is None


def test_preview_is_read_only_and_creation_saves_matching_periods_atomically(store, monkeypatch):
    repo, fee_store, house, _ = setup(store)
    before = repo.list_house_tariffs(house.id)
    args = (repo, fee_store, house.organization_id, [house.id], date(2026, 1, 1), date(2026, 12, 31), fees.PROTOCOL)
    candidates = prepare_candidates(*args)
    assert repo.list_house_tariffs(house.id) == before
    assert [(c.document["start"], c.document["end"]) for c in candidates] == [
        ("2025-04-01", "2026-03-31"), ("2026-04-01", "2027-03-31")]
    original = fee_store.save_local
    monkeypatch.setattr(fee_store, "save_local", lambda *_: (_ for _ in ()).throw(sqlite3.IntegrityError("test")))
    with pytest.raises(sqlite3.IntegrityError):
        create_projects(*args, candidates)
    assert repo.list_house_tariffs(house.id) == before
    monkeypatch.setattr(fee_store, "save_local", original)
    create_projects(*args, candidates)
    local = {(t.effective_from_date.isoformat(), t.effective_to_date.isoformat()) for t in repo.list_house_tariffs(house.id)}
    assert all((c.document["start"], c.document["end"]) in local for c in candidates)


def test_excel_transfers_dates_and_renewal_to_another_database(store, tmp_path):
    repo, _, house, _ = setup(store, auto_renew=False)
    path = tmp_path / "tariffs.xlsx"
    write_house_data_template(path, houses=[house], tariffs_by_house={house.id: repo.list_house_tariffs(house.id)})
    row = parse_house_data_template(path).tariffs[0]
    assert row.end.value == date(2025, 3, 31)
    assert row.auto_renew is False
    other = Repository(tmp_path / "other.db")
    try:
        org = other.create_organization(name="TEST", ogrn="1022600000540")
        copied = other.create_house(organization_id=org.id, address=house.address)
        other.upsert_house_tariff_change(house_id=copied.id, amount=row.amount,
            start_year=row.start.value.year, start_month=row.start.value.month,
            end_date=row.end.value, auto_renew=row.auto_renew)
        assert other.find_house_tariff_at_date(copied.id, date(2025, 4, 1)) is None
    finally:
        other.close()


def test_leap_day_renewal_has_no_gaps(store):
    repo, _, house, tariff = setup(store)
    repo.update_house_tariff(tariff.id, amount="24.41", start_year=2024, start_month=2, start_day=29,
                            end_year=2025, end_month=2, end_day=28)
    repo.ensure_house_tariff_periods(house.id, date(2028, 3, 1))
    rows = repo.list_house_tariffs(house.id)
    assert year_end(date(2024, 2, 29)) == date(2025, 2, 28)
    assert all(a.effective_to_date.toordinal() + 1 == b.effective_from_date.toordinal()
               for a, b in zip(rows, rows[1:]))


def test_legacy_open_tariff_migrates_once_and_manual_end_is_preserved(store):
    repo, _, house, tariff = setup(store)
    path = repo.database_path
    repo.connection.execute("UPDATE house_tariffs SET manual_end_date=NULL, end_year=9999, end_month=12, end_day=31 WHERE id=?", (tariff.id,))
    manual = repo.create_house_tariff(house_id=house.id, amount="30", start_year=2027, start_month=4,
                                    end_year=2027, end_month=12, auto_renew=False)
    repo.connection.execute("ALTER TABLE house_tariffs DROP COLUMN auto_renew")
    repo.connection.execute("ALTER TABLE house_tariffs DROP COLUMN renewal_source_id")
    repo.connection.commit()
    reopened = Repository(path)
    try:
        assert reopened.get_house_tariff(tariff.id).effective_to_date == date(2027, 3, 31)
        assert reopened.get_house_tariff(tariff.id).auto_renew
        assert reopened.get_house_tariff(manual.id).effective_to_date == date(2027, 12, 31)
        assert not reopened.get_house_tariff(manual.id).auto_renew
        last_before = reopened.list_house_tariffs(house.id)
    finally:
        reopened.close()
    again = Repository(path)
    try:
        assert again.list_house_tariffs(house.id) == last_before
    finally:
        again.close()


def test_deleting_a_continuation_does_not_recreate_it(store):
    repo, _, house, _ = setup(store)
    repo.ensure_house_tariff_periods(house.id, date(2026, 1, 1))
    continuation = repo.list_house_tariffs(house.id)[-1]
    assert continuation.renewal_source_id
    repo.delete_house_tariff(continuation.id)
    assert repo.find_house_tariff_at_date(house.id, date(2026, 1, 1)) is None
