"""An explicit tariff end survives normalization and leaves real gaps."""

from datetime import date

import pytest

import test_ui_workflow as workflows
from gis_jkh_assistant.app.dialogs import HouseTariffDialog
from gis_jkh_assistant.database import Repository

qt_app = workflows.qt_app


@pytest.fixture
def tariff_repo(tmp_path):
    repo = Repository(tmp_path / "tariffs.db")
    organization = repo.create_organization(name="Тестовая УО", ogrn="1022600000456")
    house = repo.create_house(organization_id=organization.id, address="Тестовый дом")
    yield repo, house.id
    repo.close()


def test_manual_end_is_preserved_after_restart_and_amount_import(tariff_repo):
    repo, house_id = tariff_repo
    tariff = repo.create_house_tariff(
        house_id=house_id, amount="20", start_year=2024, start_month=8,
        end_year=2025, end_month=7,
    )
    assert tariff.effective_to_date == date(2025, 7, 31)
    reopened = Repository(repo.database_path)
    try:
        assert reopened.get_house_tariff(tariff.id).effective_to_date == date(2025, 7, 31)
        reopened.upsert_house_tariff_change(
            house_id=house_id, amount="21", start_year=2024, start_month=8,
        )
        assert reopened.get_house_tariff(tariff.id).effective_to_date == date(2025, 7, 31)
        assert reopened.find_house_tariff_at_date(house_id, date(2025, 8, 1)) is None
    finally:
        reopened.close()


def test_successor_clips_manual_end_and_deletion_does_not_remove_it(tariff_repo):
    repo, house_id = tariff_repo
    first = repo.create_house_tariff(
        house_id=house_id, amount="20", start_year=2024, start_month=8,
        end_year=2025, end_month=7,
    )
    second = repo.create_house_tariff(
        house_id=house_id, amount="22", start_year=2025, start_month=3,
    )
    assert repo.get_house_tariff(first.id).effective_to_date == date(2025, 2, 28)
    repo.delete_house_tariff(second.id)
    assert repo.get_house_tariff(first.id).effective_to_date == date(2025, 7, 31)


def test_update_can_set_and_clear_end_without_another_tariff(tariff_repo):
    repo, house_id = tariff_repo
    tariff = repo.create_house_tariff(house_id=house_id, amount="20", start_year=2024, start_month=8)
    values = dict(amount="20", start_year=2024, start_month=8)
    repo.update_house_tariff(tariff.id, **values, end_year=2025, end_month=7, end_day=30)
    assert len(repo.list_house_tariffs(house_id)) == 1
    assert repo.get_house_tariff(tariff.id).effective_to_date == date(2025, 7, 30)
    with pytest.raises(ValueError, match="раньше начала"):
        repo.update_house_tariff(tariff.id, **values, end_year=2023, end_month=7)
    assert repo.get_house_tariff(tariff.id).effective_to_date == date(2025, 7, 30)
    repo.update_house_tariff(tariff.id, **values, end_year=9999, end_month=12, end_day=31)
    assert repo.get_house_tariff(tariff.id).effective_to_date is None


def test_dialog_saves_end_inclusively_without_new_tariff(qt_app, tariff_repo):
    repo, house_id = tariff_repo
    tariff = repo.create_house_tariff(house_id=house_id, amount="20", start_year=2024, start_month=8)
    dialog = HouseTariffDialog(tariff=tariff)
    try:
        dialog.end_date_edit.setText("07.2025")
        repo.update_house_tariff(tariff.id, **dialog.values())
        assert len(repo.list_house_tariffs(house_id)) == 1
        saved = repo.get_house_tariff(tariff.id)
        assert saved.effective_to_date == date(2025, 7, 31)
        editing = HouseTariffDialog(tariff=saved)
        try:
            assert editing.end_date_edit.text() == "31.07.2025"
            editing.end_date_edit.setText("31.07.2023")
            with pytest.raises(ValueError, match="раньше начала"):
                editing.values()
        finally:
            editing.close()
    finally:
        dialog.close()
