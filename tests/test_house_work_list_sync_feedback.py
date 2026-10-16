"""All skipped work lists remain inspectable before and after a real local sync."""
from copy import deepcopy
import sqlite3

import pytest
from PySide6.QtWidgets import QMessageBox

import test_ui_workflow as workflows
from test_gis_work_list_sync import _remote
from gis_jkh_assistant.app import houses_page

desktop, qt_app = workflows.desktop, workflows.qt_app


@pytest.mark.parametrize("accept, new_period", [(True, True), (False, True), (True, False)])
def test_all_conflicts_are_available_and_cancel_does_not_import(
    desktop, monkeypatch, accept, new_period,
):
    page = desktop.window.houses_page
    monkeypatch.setattr(houses_page, "GisIntegrationWorker", workflows.ControlledWorker)
    messages = []

    def respond(message):
        messages.append((message.text(), message.detailedText(), message.standardButtons()))
        if message.standardButtons() & QMessageBox.StandardButton.Yes:
            assert message.defaultButton() == message.button(QMessageBox.StandardButton.No)
            return QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", respond)
    conflicts = [_remote(f"conflict-{i}", desktop.house.house_object_guid) for i in range(8)]
    unmatched = _remote("unknown-list", "unknown-house")
    received = [*conflicts, unmatched]
    if new_period:
        candidate = _remote("new-list", desktop.house.house_object_guid)
        candidate["period"] = {"from": {"month": 1, "year": 2027}, "to": {"month": 12, "year": 2027}}
        received.append(candidate)
    before = list(desktop.repository.connection.iterdump())
    page._sync_all_work_lists_from_gis()
    worker = page._work_lists_sync_worker
    worker.succeeded.emit({"items": received})
    worker.finish()

    assert len(messages) == (2 if accept and new_period else 1)
    for _, details, _ in messages:
        assert details.count(desktop.house.address) == 8
        assert details.count("01.2026 — 12.2026") == 8
        assert details.count("с другим GUID ГИС ЖКХ") == 8
        assert "unknown-house" in details and "unknown-list" in details
        assert "Нет соответствующего дома у выбранной УО" in details
    if accept and new_period:
        assert desktop.repository.find_work_list_by_gis_guid("new-list") is not None
        assert desktop.repository.list_work_list_items(desktop.work_list.id)[0].work_name == (
            "Содержание и техническое обслуживание общего имущества дома"
        )
    else:
        assert list(desktop.repository.connection.iterdump()) == before
    assert not page._bulk_worker_running()


def test_all_save_errors_remain_available_in_result(desktop, monkeypatch):
    page = desktop.window.houses_page
    monkeypatch.setattr(houses_page, "GisIntegrationWorker", workflows.ControlledWorker)
    details = []

    def respond(message):
        if message.standardButtons() & QMessageBox.StandardButton.Yes:
            return QMessageBox.StandardButton.Yes
        details.append(message.detailedText())
        return QMessageBox.StandardButton.Ok

    def fail_import(*, remote, **_kwargs):
        raise sqlite3.OperationalError(f"Тестовый отказ {remote['guid']}")

    monkeypatch.setattr(QMessageBox, "exec", respond)
    monkeypatch.setattr(desktop.repository, "import_gis_work_list", fail_import)
    received = []
    for year in range(2027, 2035):
        candidate = deepcopy(_remote(f"failed-{year}", desktop.house.house_object_guid))
        candidate["period"] = {"from": {"month": 1, "year": year}, "to": {"month": 12, "year": year}}
        received.append(candidate)
    page._sync_all_work_lists_from_gis()
    worker = page._work_lists_sync_worker
    worker.succeeded.emit({"items": received})
    worker.finish()
    assert len(details) == 1
    assert "Ошибки сохранения" in details[0]
    for year in range(2027, 2035):
        assert f"Тестовый отказ failed-{year}" in details[0]
