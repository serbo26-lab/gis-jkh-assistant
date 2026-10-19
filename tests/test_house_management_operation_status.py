"""A management lookup finishes only after its local result is applied."""

import sqlite3

import pytest
from PySide6.QtWidgets import QMessageBox

import test_ui_workflow as workflows
from gis_jkh_assistant.app import houses_page


desktop, qt_app = workflows.desktop, workflows.qt_app

MANAGEMENT = {
    "managementStartDate": "01.07.2020",
    "managementEndDate": None,
    "agreementNumber": "TEST-MANAGEMENT-1",
    "agreementDate": "01.06.2020",
    "organizationGuid": "test-organization-guid",
    "organizationRootGuid": "test-organization-root-guid",
}


def _start_lookup(desktop, monkeypatch):
    page = workflows._select_page(desktop, 0)
    monkeypatch.setattr(houses_page, "GisIntegrationWorker", workflows.ControlledWorker)
    page._sync_management_from_gis()
    worker = page._management_worker
    assert worker is not None
    assert worker.isRunning()
    assert worker.command_type == "get_house_management_info"
    assert not page.sync_management_button.isEnabled()
    return page, worker


def test_success_is_shown_before_confirmation_and_survives_worker_finish(desktop, monkeypatch):
    confirmations = []

    def confirm(_parent, title, _message):
        # The acknowledgement opens after the data and the visible outcome agree.
        house = desktop.repository.get_house(desktop.house.id)
        organization = desktop.repository.get_organization(desktop.organization.id)
        assert house.management_agreement_number == MANAGEMENT["agreementNumber"]
        assert house.management_data_source == "gis_public"
        assert organization.gis_organization_guid == MANAGEMENT["organizationGuid"]
        assert page.management_agreement_number_edit.text() == MANAGEMENT["agreementNumber"]
        assert page.operation_status.property("tone") == "success"
        assert page.operation_status.label.text() == (
            "Сведения об управлении обновлены. Локальная карточка сохранена."
        )
        assert "проверка результата" not in page.operation_status.label.text()
        confirmations.append(title)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", confirm)
    page, worker = _start_lookup(desktop, monkeypatch)
    worker.succeeded.emit(MANAGEMENT)
    assert confirmations == ["Сведения обновлены"]
    outcome = page.operation_status.label.text()
    worker.finish()
    workflows._settle(desktop.app)
    desktop.window._refresh_integration_status()
    assert page._management_worker is None
    assert not page._workflow_busy
    assert page.sync_management_button.isEnabled()
    assert page.operation_status.property("tone") == "success"
    assert page.operation_status.label.text() == outcome


@pytest.mark.parametrize("failure", ["network", "update_house", "update_organization", "load_house"])
def test_failure_is_not_replaced_by_success_or_worker_cleanup(desktop, monkeypatch, failure):
    confirmations, errors = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: confirmations.append(args[1]))

    def show_error(_parent, title, _message):
        assert page.operation_status.property("tone") == "danger"
        errors.append(title)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "critical", show_error)
    page, worker = _start_lookup(desktop, monkeypatch)

    def cannot_apply(*_args, **_kwargs):
        raise sqlite3.OperationalError("isolated management apply failure")

    if failure == "network":
        worker.failed.emit("isolated management network failure")
    else:
        owner, method = (page, "_load_house") if failure == "load_house" else (desktop.repository, failure)
        monkeypatch.setattr(owner, method, cannot_apply)
        worker.succeeded.emit(MANAGEMENT)
        assert worker.result_apply_failed
    assert len(errors) == 1
    assert not confirmations
    outcome = page.operation_status.label.text()
    assert "Локальная карточка сохранена" not in outcome
    worker.finish()
    workflows._settle(desktop.app)
    assert page._management_worker is None
    assert not page._workflow_busy
    assert page.sync_management_button.isEnabled()
    assert page.operation_status.property("tone") == "danger"
    assert page.operation_status.label.text() == outcome
