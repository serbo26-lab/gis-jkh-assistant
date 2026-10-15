"""House fee download uses the existing read-only queue and real Qt controls."""
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

import test_management_fees as fees
import test_ui_workflow as workflows
from test_house_fee_import import apply, placed
from gis_jkh_assistant.app import houses_page, portfolio_import as imports

desktop, qt_app = workflows.desktop, workflows.qt_app


def setup(desktop, monkeypatch):
    fee_page = fees.fee_page(desktop, monkeypatch)
    window = desktop.window
    window.navigation.setCurrentRow(window.pages.indexOf(window.houses_page))
    window.houses_page._refresh_workflow()
    return window.houses_page, fee_page.store


def response_for_house(house, guid):
    response = fees.context(placed(guid, "2025-07-10", "2026-06-30"))
    response["contracts"][0]["fiasHouseGuid"] = house.fias_house_guid
    response["protocols"][0]["fiasHouseCodes"] = [house.fias_house_guid]
    return response


def test_download_button_needs_houses_and_supported_helper_but_no_work_lists(desktop, monkeypatch):
    page, _ = setup(desktop, monkeypatch)
    desktop.repository.update_work_list_gis_state(desktop.work_list.id, gis_status="DRAFT")
    page._refresh_workflow()
    assert page.sync_all_fees_button.isEnabled()
    assert not page.sync_all_fees_button.icon().isNull()
    assert not page.sync_all_plans_button.isEnabled()
    monkeypatch.setattr(imports.ImportReviewDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    before = list(desktop.repository.connection.iterdump())
    page.sync_all_fees_button.click()
    assert page.portfolio_import.worker is None
    assert list(desktop.repository.connection.iterdump()) == before
    desktop.gateway.ready = False
    page._refresh_workflow()
    assert not page.sync_all_fees_button.isEnabled()


@pytest.mark.parametrize("stop", [False, True])
def test_house_queue_loads_one_house_at_a_time_and_respects_stop(desktop, monkeypatch, stop):
    page, fee_store = setup(desktop, monkeypatch)
    repo = desktop.repository
    other = repo.create_house(organization_id=desktop.organization.id, address="Второй вымышленный дом",
                              fias_house_guid="second-fictional-fias")
    monkeypatch.setattr(imports, "GisIntegrationWorker", workflows.ControlledWorker)
    monkeypatch.setattr(imports.ImportReviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    page.sync_all_fees_button.click()
    queue = page.portfolio_import
    QTest.qWait(30)
    assert queue.running and queue.worker.command_type == "load_management_fees"
    assert not page.sync_all_fees_button.isEnabled()
    assert not desktop.window.organizations_page.organization_combo.isEnabled()
    first = next(job for job in queue.jobs if job.state == "pending")
    assert queue.worker.payload["fiasHouseGuid"] == first.fias
    if stop:
        queue.request_stop()
    queue.worker.succeeded.emit(response_for_house(repo.get_house(first.house_id), "first-fee"))
    queue.worker.finish()
    QTest.qWait(30)
    if stop:
        assert not queue.running and [job.state for job in queue.jobs].count("stopped") == 1
        queue.continue_button.click()
        QTest.qWait(30)
    second = next(job for job in queue.jobs if job.state == "pending")
    assert second.house_id != first.house_id
    assert queue.worker.command_type == "load_management_fees"
    assert queue.worker.payload["fiasHouseGuid"] == second.fias
    queue.worker.succeeded.emit(response_for_house(repo.get_house(second.house_id), "second-fee"))
    queue.worker.finish()
    QTest.qWait(30)
    assert not queue.running and all(job.state == "loaded" for job in queue.jobs)
    assert len(repo.list_effective_house_tariffs(other.id)) == 1
    assert len(fee_store.list(desktop.house.id)) == 1
    assert not desktop.window._active_workers


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_imported_tariff_is_identified_and_explicit_edit_creates_local_override(desktop, monkeypatch, theme):
    page, fee_store = setup(desktop, monkeypatch)
    getattr(desktop.window.settings_page, theme + "_radio").setChecked(True)
    apply(fee_store, desktop.house.id, fees.context(placed()))
    page._refresh_tariffs()
    assert page.tariffs_table.item(0, 0).text() == "01.01.2026"
    assert page.tariffs_table.item(0, 1).text() == "31.12.2026"
    assert page.tariffs_table.item(0, 3).text() == "ГИС ЖКХ"
    assert "№ 1" in page.tariffs_table.item(0, 3).toolTip()
    assert page.edit_tariff_button.text() == "Задать локальный…"
    assert not page.delete_tariff_button.isEnabled()
    remote_before = deepcopy(fee_store.list(desktop.house.id))

    def accept_local(dialog):
        dialog.amount_edit.setText("29.00")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(houses_page.HouseTariffDialog, "exec", accept_local)
    page.edit_tariff_button.click()
    assert page.tariffs_table.item(0, 3).text() == "Локально"
    assert page.delete_tariff_button.isEnabled()
    assert desktop.repository.list_house_tariffs(desktop.house.id)[0].amount == "29.00"
    assert fee_store.list(desktop.house.id) == remote_before
    assert page.tariffs_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == desktop.repository.list_house_tariffs(desktop.house.id)[0].id


def test_close_waits_for_house_fee_read_and_never_starts_next_house(desktop, monkeypatch):
    page, _ = setup(desktop, monkeypatch)
    repo = desktop.repository
    repo.create_house(organization_id=desktop.organization.id, address="Другой вымышленный дом", fias_house_guid="other")
    monkeypatch.setattr(imports, "GisIntegrationWorker", workflows.ControlledWorker)
    monkeypatch.setattr(imports.ImportReviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    page.sync_all_fees_button.click()
    QTest.qWait(30)
    queue = page.portfolio_import
    job = next(job for job in queue.jobs if job.state == "pending")
    event = QCloseEvent()
    desktop.window.closeEvent(event)
    assert not event.isAccepted() and not desktop.window._closing
    queue.worker.succeeded.emit(response_for_house(repo.get_house(job.house_id), "placed-at-close"))
    queue.worker.finish()
    QTest.qWait(180)
    assert not queue.running and queue.worker is None
    assert sum(job.state == "loaded" for job in queue.jobs) == 1
    assert sum(job.state == "stopped" for job in queue.jobs) == 1
