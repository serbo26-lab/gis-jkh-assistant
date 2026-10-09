"""Real widgets: registry selection, validation and lifecycle recommendations."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gis_jkh_assistant.app.houses_page import HousesPage  # noqa: E402
from gis_jkh_assistant.app.registry_layouts import prepare_project  # noqa: E402
from gis_jkh_assistant.app.registry_workflow import (  # noqa: E402
    check_work_list,
    mark_work_list_saved,
)
from gis_jkh_assistant.app.work_lists_page import WorkListsPage  # noqa: E402
from gis_jkh_assistant.app.workflow_widgets import OperationStatus  # noqa: E402
from gis_jkh_assistant.database import Repository  # noqa: E402


class Gateway:
    def __init__(self, ready=True):
        self.ready = ready

    def status(self):
        return SimpleNamespace(running=True, helper_connected=self.ready, gis_page_ready=self.ready, helper_version="0.1.30")


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def registry(tmp_path, application):
    repository = Repository(tmp_path / "registry.db")
    org = repository.create_organization(name="УК Пример", ogrn="1022600000000")
    house = repository.create_house(
        organization_id=org.id,
        address="Улица Примерная, дом 1",
        house_object_guid="house-guid",
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=2026,
        items=[
            SimpleNamespace(
                position=1,
                work_code="1",
                work_guid="work-guid",
                work_name="Уборка",
                price=None,
                volume=None,
                quantity="1",
                cost="100",
                allocation_percent=None,
            )
        ],
    )
    page = WorkListsPage(repository, Gateway())
    yield repository, house, work_list, page
    page._period_timer.stop()
    page.deleteLater()
    application.processEvents()
    repository.close()


def select(page, work_list):
    page.refresh_work_lists(select_id=work_list.id)


def test_registry_requires_explicit_selection_and_preserves_it(registry):
    repository, house, first, page = registry
    second = repository.create_work_list(house_id=house.id, base_year=2027)
    page.refresh_work_lists()
    assert page._current_work_list_id is None
    assert page.registry_combo.currentIndex() == -1
    select(page, first)
    assert page.registry_combo.currentData() == first.id
    page.refresh_organizations()
    assert page._current_work_list_id == first.id
    page.refresh_work_lists(select_id="deleted-or-unavailable")
    assert page._current_work_list_id is None
    assert page.registry_combo.currentData() != second.id


def test_stable_context_signal_only_announces_explicit_available_selection(registry):
    _repository, _house, work_list, page = registry
    observed = []
    page.contextSelectionChanged.connect(lambda: observed.append(page._current_work_list_id))
    page.refresh_work_lists()
    assert observed == []
    page.refresh_work_lists(select_id="unavailable-list")
    assert observed == []
    select(page, work_list)
    assert observed == [work_list.id]


def test_local_check_then_project_then_publish_then_planning(registry):
    repository, _house, work_list, page = registry
    select(page, work_list)
    assert page.workflow._key == "check"
    check_work_list(page)
    assert page.workflow._key == "prepare"
    assert page.workflow.primary.text() == "Сохранить проект"
    mark_work_list_saved(page, work_list.id)
    repository.update_work_list_gis_state(
        work_list.id,
        gis_status="DRAFT",
        gis_work_list_guid="list-guid",
    )
    select(page, work_list)
    assert page.workflow._key == "publish"
    repository.update_work_list_gis_state(work_list.id, gis_status="APPROVED")
    select(page, work_list)
    assert page.workflow._key == "next"
    assert not page.items_table.isEnabled()
    assert "Только просмотр" in page.context_status.text()


def test_changed_project_is_checked_and_saved_before_publish(registry):
    repository, _house, work_list, page = registry
    repository.update_work_list_gis_state(
        work_list.id,
        gis_status="DRAFT",
        gis_work_list_guid="list-guid",
    )
    mark_work_list_saved(page, work_list.id)
    select(page, work_list)
    assert page.workflow._key == "publish"
    cell = page.items_table.item(0, 4)
    cell.setText("150")
    assert page.workflow._key == "check"
    check_work_list(page)
    assert page.workflow._key == "save"
    mark_work_list_saved(page, work_list.id)
    page._refresh_workflow()
    assert page.workflow._key == "publish"


@pytest.mark.parametrize("updated", ["2026-10-06T12:00:00+00:00", "2026-10-07T12:00:00+00:00"])
def test_legacy_draft_with_unverified_local_rows_is_saved_before_publish(registry, updated):
    repository, _house, work_list, page = registry
    repository.update_work_list_gis_state(
        work_list.id,
        gis_status="DRAFT",
        gis_work_list_guid="list-guid",
    )
    # dev53 row edits have no own timestamp and do not touch work_lists.updated_at.
    item = repository.list_work_list_items(work_list.id)[0]
    repository.update_work_list_item(item.id, cost="250")
    repository.connection.execute(
        "UPDATE work_lists SET gis_last_sync_at = ?, updated_at = ? WHERE id = ?",
        ("2026-10-06T12:00:00+00:00", updated, work_list.id),
    )
    repository.connection.commit()
    select(page, work_list)
    assert page.workflow._key == "check"
    assert repository.get_setting(f"ui_work_list_saved:{work_list.id}") is None
    check_work_list(page)
    assert page.workflow._key == "save"


def test_status_only_sync_does_not_overwrite_dirty_composition_snapshot(registry):
    repository, _house, work_list, page = registry
    repository.update_work_list_gis_state(
        work_list.id,
        gis_status="DRAFT",
        gis_work_list_guid="list-guid",
    )
    mark_work_list_saved(page, work_list.id)
    original_snapshot = repository.get_setting(f"ui_work_list_saved:{work_list.id}")
    item = repository.list_work_list_items(work_list.id)[0]
    repository.update_work_list_item(item.id, cost="250")
    page._apply_gis_result(work_list.id, {"gisStatus": "DRAFT", "workListGuid": "list-guid"})
    assert page.workflow._key == "check"
    assert repository.get_setting(f"ui_work_list_saved:{work_list.id}") == original_snapshot


def test_metadata_only_imported_draft_can_be_published(registry):
    repository, house, _work_list, page = registry
    imported = repository.create_work_list(house_id=house.id, base_year=2027)
    repository.update_work_list_gis_state(
        imported.id,
        gis_status="DRAFT",
        gis_work_list_guid="remote-list-guid",
    )
    select(page, imported)
    assert page.workflow._key == "publish"


def test_pending_period_edit_blocks_publication_before_autosave(registry):
    repository, _house, work_list, page = registry
    repository.update_work_list_gis_state(
        work_list.id,
        gis_status="DRAFT",
        gis_work_list_guid="list-guid",
    )
    mark_work_list_saved(page, work_list.id)
    select(page, work_list)
    page.end_month.setCurrentIndex(10)
    assert page._period_dirty
    assert page.workflow._key == "check"
    check_work_list(page)
    assert repository.get_work_list(work_list.id).end_month == 11
    assert page.workflow._key == "save"


def test_check_reuses_export_rules_and_does_not_send(registry):
    repository, _house, work_list, page = registry
    item = repository.list_work_list_items(work_list.id)[0]
    repository.update_work_list_item(item.id, quantity=None)
    select(page, work_list)
    check_work_list(page)
    assert page.workflow._key == "check"
    assert "количество" in page.operation_status.label.text().lower()
    assert page._integration_worker is None


def test_invalid_unsaved_period_cannot_be_reported_as_checked(registry):
    _repository, _house, work_list, page = registry
    select(page, work_list)
    page.start_month.setCurrentIndex(11)
    page.end_month.setCurrentIndex(0)
    check_work_list(page)
    assert page.workflow._key == "check"
    assert "Начало периода" in page.operation_status.label.text()


def test_offline_reason_and_busy_editing_restrictions(registry):
    _repository, _house, work_list, page = registry
    select(page, work_list)
    check_work_list(page)
    page.integration_gateway.ready = False
    page._refresh_workflow()
    assert not page.workflow.primary.isEnabled()
    assert "помощник" in page.workflow.reason.text()
    page._set_integration_busy(True)
    assert not page.items_table.isEnabled()
    assert not page.create_button.isEnabled()
    page._set_integration_busy(False)
    assert page.items_table.isEnabled()


def test_houses_has_real_operation_status_and_contextual_next_action(tmp_path, application):
    repository = Repository(tmp_path / "houses.db")
    page = HousesPage(repository, Gateway())
    assert isinstance(page.bulk_status_strip, OperationStatus)
    assert page.workflow._key == "organization_load"
    org = repository.create_organization(name="УК", ogrn="1022600000000")
    page.refresh_organizations(select_id=org.id)
    assert page.workflow._key == "load"
    repository.create_house(organization_id=org.id, address="Дом 1", house_object_guid="guid")
    page.refresh_houses()
    assert page.workflow._key == "next"
    assert page.delete_house_button.parentWidget() is not page.house_list.parentWidget()
    page.search_edit.setText("Неизвестный адрес")
    assert page.workflow._key == "reset_search"
    page.deleteLater()
    application.processEvents()
    repository.close()


class PreparationWorker(QObject):
    succeeded = Signal(dict)
    finished = Signal()


@pytest.mark.parametrize(
    "success,change_selection,expected", [(False, False, 0), (True, True, 0), (True, False, 1)]
)
def test_automatic_preparation_only_continues_successful_original_context(
    registry,
    application,
    monkeypatch,
    tmp_path,
    success,
    change_selection,
    expected,
):
    _repository, _house, work_list, page = registry
    select(page, work_list)
    worker = PreparationWorker()
    available = False
    uploaded = []

    def download():
        page._integration_worker = worker

    monkeypatch.setattr(
        page, "_export_template_path", lambda: tmp_path / "basis.xlsx" if available else None
    )
    monkeypatch.setattr(page, "_download_export_template_from_gis", download)
    monkeypatch.setattr(page, "_upload_current_work_list_to_gis", lambda: uploaded.append(True))
    prepare_project(page)
    if success:
        available = True
        worker.succeeded.emit({})
    if change_selection:
        page._current_work_list_id = "other-list"
    worker.finished.emit()
    application.processEvents()
    assert len(uploaded) == expected
    assert not page._preparing_project
    page._integration_worker = None
