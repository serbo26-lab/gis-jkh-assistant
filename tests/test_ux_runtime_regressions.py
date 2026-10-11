"""Practical dev58 failures replayed on real Qt pages with isolated data."""
import sqlite3
import threading

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from test_ui_workflow import desktop as _desktop_fixture, qt_app as _qt_fixture, ControlledWorker, _select_page, _settle
from gis_jkh_assistant.app import planning_page
from gis_jkh_assistant.app.gis_integration_worker import GisIntegrationWorker, connect_result
from gis_jkh_assistant.app.workflow_presenter import mark_plan_synced, plan_fingerprint

desktop = _desktop_fixture
qt_app = _qt_fixture


@pytest.fixture
def messages(monkeypatch):
    calls = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *args, k=kind, **kwargs: calls.append((k, args[1], args[2])) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    return calls


@pytest.mark.parametrize("response,accept", [
    ({"found": False}, True),
    ({"found": True, "items": []}, False),
    ({"found": True, "items": [{"workGuid": "83592b3d-c928-416b-b6e0-595fa1339821", "monthlyWorks": [{"month": 13, "count": 1}]}]}, True),
])
def test_unsuccessful_load_does_not_confirm_sync(desktop, messages, monkeypatch, response, accept):
    page = _select_page(desktop, 2)
    plan_id = page._current_plan_id
    mark_plan_synced(page, plan_id)
    baseline = desktop.repository.get_setting(f"ui.plan.synced.{plan_id}")
    page.table.item(0, 3).setText("2")
    before = plan_fingerprint(page, plan_id)
    workers = []
    def create(*args, **kwargs):
        worker = ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(planning_page, "GisIntegrationWorker", create)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes if accept else QMessageBox.No)
    page._load_from_gis()
    workers[0].succeeded.emit(response)
    workers[0].finish()
    _settle(desktop.app)
    assert plan_fingerprint(page, plan_id) == before
    assert desktop.repository.get_setting(f"ui.plan.synced.{plan_id}") == baseline


def test_publication_baseline_is_the_sent_snapshot(desktop, messages, monkeypatch):
    page = _select_page(desktop, 2)
    before = plan_fingerprint(page, page._current_plan_id)
    workers = []
    def create(*args, **kwargs):
        worker = ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker
    monkeypatch.setattr(planning_page, "GisIntegrationWorker", create)
    page._publish_to_gis()
    item = desktop.repository.list_work_plan_items(page._current_plan_id)[0]
    desktop.repository.update_work_plan_month(item.id, month=1, count=2)
    workers[0].succeeded.emit({"workPlanGuid": "fake-guid", "gisStatus": "SAVED"})
    workers[0].finish()
    _settle(desktop.app)
    assert desktop.repository.get_setting(f"ui.plan.synced.{page._current_plan_id}") == before
    assert plan_fingerprint(page, page._current_plan_id) != before
    assert page.workflow._key != "next"


@pytest.mark.parametrize("index", [2, 3, 4])
@pytest.mark.parametrize("shared", [True, False])
def test_navigation_retains_year_month_and_scope(desktop, index, shared):
    desktop.repository.update_work_list_period(desktop.work_list.id, start_year=2026, start_month=1, end_year=2027, end_month=12)
    (desktop.window.settings_page.shared_radio if shared else desktop.window.settings_page.independent_radio).click()
    page = _select_page(desktop, index)
    page.year_combo.setCurrentIndex(page.year_combo.findData(2027))
    if index > 2:
        page._select_month(9)
        page.scope_switcher.set_scope("period")
    for other in (0, 1, 5):
        _select_page(desktop, other)
        page = _select_page(desktop, index)
        assert page.year_combo.currentData() == 2027
        if index > 2:
            assert page._current_month == 9
            assert page.scope_switcher.scope() == "period"


def test_fill_manual_fact_requires_confirmation(desktop, messages, monkeypatch):
    page = _select_page(desktop, 3)
    item = desktop.repository.list_work_fixation_items(page._current_period_id)[0]
    desktop.repository.update_work_fixation_item(item.id, fact_price="77", fact_amount="3", fact_count=9, fact_total_cost="2079")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Cancel)
    page._fill_month_from_plan()
    assert desktop.repository.list_work_fixation_items(page._current_period_id)[0].fact_total_cost == "2079"
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page._fill_month_from_plan()
    assert desktop.repository.list_work_fixation_items(page._current_period_id)[0].fact_total_cost != "2079"


def test_autosave_error_preserves_editor_blocks_navigation_and_close(desktop, messages, monkeypatch):
    page = _select_page(desktop, 0)
    original = desktop.repository.update_house
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("TEST locked")
    monkeypatch.setattr(desktop.repository, "update_house", fail)
    page.address_edit.setText("TEST UNSAVED ADDRESS")
    page._queue_change("address", "TEST UNSAVED ADDRESS")
    assert page.flush_pending_changes() is False
    page.refresh_houses()
    assert page.address_edit.text() == "TEST UNSAVED ADDRESS"
    _select_page(desktop, 2)
    assert desktop.window.pages.currentIndex() == 0
    event = QCloseEvent()
    desktop.window.closeEvent(event)
    assert not event.isAccepted()
    assert desktop.repository.get_house(desktop.house.id).address != "TEST UNSAVED ADDRESS"
    assert page._pending_fields["address"] == "TEST UNSAVED ADDRESS"
    monkeypatch.setattr(desktop.repository, "update_house", original)
    page.retry_save_button.click()
    assert not page._pending_fields
    assert desktop.repository.get_house(desktop.house.id).address == "TEST UNSAVED ADDRESS"


def test_close_drains_real_qthread_callbacks_before_database_close(desktop, messages, monkeypatch):
    page = _select_page(desktop, 2)
    release = threading.Event()
    entered = threading.Event()
    writes = []
    def execute(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"local": True}
    monkeypatch.setattr(desktop.gateway, "execute", execute)
    worker = GisIntegrationWorker(desktop.gateway, "load_work_plan", {}, parent=page)
    def succeeded(result):
        desktop.repository.set_setting("TEST.finished", "yes")
        writes.append(result)
    worker.succeeded.connect(succeeded)
    worker.start()
    assert entered.wait(2)
    try:
        assert desktop.window.close() is False
        assert desktop.repository.get_house(desktop.house.id)
        assert not desktop.window.can_delete_local_data()
    finally:
        release.set()
    for _ in range(200):
        QTest.qWait(10)
        if desktop.window._closing:
            break
    assert writes == [{"local": True}]
    assert desktop.window._closing
    assert not worker.isRunning()
    with sqlite3.connect(desktop.repository.database_path) as reader:
        assert reader.execute("SELECT value FROM app_settings WHERE key='TEST.finished'").fetchone() == ("yes",)


@pytest.mark.parametrize("scale", [100, 125, 150])
@pytest.mark.parametrize("index", [3, 4])
def test_scaled_monthly_first_row_is_visible_and_not_clipped(desktop, index, scale):
    page = _select_page(desktop, index)
    desktop.window._scale_changed(scale)
    # Real monetary input expands ResizeToContents columns and requires a
    # horizontal scrollbar even at 100%; name sizing stays in production mode.
    page.table.item(0, 1 if index == 3 else 2).setText("100000000000000000000000000000000000000000000000.00")
    for _ in range(5):
        QTest.qWait(10)
    assert page.table.rowCount() >= 1
    assert page.table.viewport().height() >= page.table.rowHeight(0)
    bottom = page.table.viewport().mapTo(page.page_scroll.viewport(), QPoint(0, page.table.rowHeight(0))).y()
    assert 0 < bottom <= page.page_scroll.viewport().height(), (scale, index, bottom, page.page_scroll.viewport().height())
    assert page.table.horizontalScrollBar().isVisible()
    assert page.table.horizontalScrollBar().maximum() > 0


@pytest.mark.parametrize("version,ready", [("0.1.26", False), ("0.1.27", False), ("0.1.29", False), ("0.1.30", True)])
def test_network_commands_follow_routing_version(desktop, version, ready, monkeypatch):
    from dataclasses import replace
    status = desktop.gateway.status()
    monkeypatch.setattr(desktop.gateway, "status", lambda: replace(status, helper_version=version))
    for index, button_name in ((1, "load_gis_lists_button"), (2, "load_gis_button"), (3, "load_month_button"), (4, "load_gis_button")):
        page = _select_page(desktop, index)
        desktop.window._refresh_integration_status()
        assert getattr(page, button_name).isEnabled() == ready
        if not ready:
            assert getattr(page, button_name).toolTip()


def test_partial_generation_reports_failure_and_resumes_without_replacing_completed_month(desktop, messages, monkeypatch):
    from types import SimpleNamespace
    page = _select_page(desktop, 4)
    periods = []
    for month in (1, 2, 3):
        period = page.store.get_or_create_period(work_list_id=desktop.work_list.id, year=2026, month=month)
        item = page.store.list_items(period.id)[0]
        page.store.update_item(item.id, included=True, cost=None, days_count=None, cost_reduction=None)
        periods.append(period.id)
    monkeypatch.setattr(page, "_sync_work_units_for_act", lambda *args: False)
    calls = []
    def generate(period_id):
        calls.append(period_id)
        if period_id == periods[1]:
            raise RuntimeError("TEST PDF failure")
        return SimpleNamespace(created=1, linked_items=1, protected_items=0, missing_costs=0)
    monkeypatch.setattr(page, "_auto_generate_act_for_period", generate)
    page._auto_generate_year_acts()
    assert calls == periods[:2]
    assert messages[-1][0] == "warning"
    assert "частично" in messages[-1][1]
    assert "TEST PDF failure" in messages[-1][2]
    calls.clear()
    monkeypatch.setattr(page, "_auto_generate_act_for_period", lambda id_: calls.append(id_) or SimpleNamespace(created=1, linked_items=1, protected_items=0, missing_costs=0))
    page._auto_generate_year_acts()
    assert calls == periods[1:]
    assert page._auto_act_resume is None
    assert messages[-1][0] == "information"


def test_modal_result_handler_keeps_database_open_after_worker_finishes(desktop, messages, monkeypatch):
    page = _select_page(desktop, 2)
    monkeypatch.setattr(desktop.gateway, "execute", lambda *args, **kwargs: {})
    worker = GisIntegrationWorker(desktop.gateway, "load_work_plan", {}, parent=page)
    completed = []
    def handle(result):
        desktop.window.close()
        # QTest enters an event loop as QMessageBox.exec does; finished and timers
        # are processed while the success handler is still active.
        QTest.qWait(150)
        assert not desktop.window._closing
        desktop.repository.set_setting("TEST.modal", "saved")
        completed.append(True)
    connect_result(worker, worker.succeeded, handle)
    worker.start()
    for _ in range(200):
        QTest.qWait(10)
        if desktop.window._closing:
            break
    assert completed == [True]
    assert desktop.window._closing


def test_house_switch_and_search_keep_pending_editor_and_valid_list_items(desktop, messages, monkeypatch):
    from PySide6.QtCore import Qt
    page = _select_page(desktop, 0)
    other = desktop.repository.create_house(organization_id=desktop.organization.id, address="TEST OTHER B")
    page.refresh_houses()
    page._queue_change("address", "TEST PENDING A")
    original = desktop.repository.update_house
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("TEST database error")
    monkeypatch.setattr(desktop.repository, "update_house", fail)
    page.search_edit.setText("TEST OTHER B")
    assert page.house_list.currentItem().data(Qt.UserRole) == desktop.house.id
    assert page._current_house_id == desktop.house.id
    for row in range(page.house_list.count()):
        if page.house_list.item(row).data(Qt.UserRole) == other.id:
            page.house_list.setCurrentRow(row)
            break
    assert page.house_list.currentItem().data(Qt.UserRole) == desktop.house.id
    assert page._pending_fields["address"] == "TEST PENDING A"
    monkeypatch.setattr(desktop.repository, "update_house", original)
    # Other Qt listeners must still receive valid items after the save handler.
    import shiboken6
    observed = []
    page.house_list.currentItemChanged.connect(
        lambda current, previous: observed.append((
            shiboken6.isValid(current),
            previous is None or shiboken6.isValid(previous),
        ))
    )
    for row in range(page.house_list.count()):
        if page.house_list.item(row).data(Qt.UserRole) == other.id:
            page.house_list.setCurrentRow(row)
            break
    assert not page._pending_fields
    assert desktop.repository.get_house(desktop.house.id).address == "TEST PENDING A"
    assert page._current_house_id == other.id
    assert page.house_list.currentItem().data(Qt.UserRole) == other.id
    assert page.address_edit.text() == other.address
    assert observed and all(all(pair) for pair in observed)
    _settle(desktop.app)
    assert page.house_list.count() == 1
    assert page.house_list.currentItem().data(Qt.UserRole) == other.id


def test_common_month_carries_to_monthly_neighbor(desktop):
    page = _select_page(desktop, 3)
    page._select_month(9)
    page.scope_switcher.set_scope("period")
    quality = _select_page(desktop, 4)
    assert quality._current_month == 9
    assert quality.scope_switcher.scope() == "period"


def test_switch_house_after_pending_organization_move_keeps_clicked_target(desktop, messages):
    from PySide6.QtCore import Qt
    page = _select_page(desktop, 0)
    other = desktop.repository.create_house(organization_id=desktop.organization.id, address="TEST STAYS HERE")
    destination = desktop.repository.create_organization(name="TEST DESTINATION", ogrn="0000000000060")
    page.refresh_houses()
    page._queue_change("organization_id", destination.id)
    for row in range(page.house_list.count()):
        if page.house_list.item(row).data(Qt.UserRole) == other.id:
            page.house_list.setCurrentRow(row)
            break
    _settle(desktop.app)
    assert desktop.repository.get_house(desktop.house.id).organization_id == destination.id
    assert desktop.repository.get_house(other.id).organization_id == desktop.organization.id
    assert page.organization_combo.currentData() == desktop.organization.id
    assert page.card_org_combo.currentData() == desktop.organization.id
    assert page._current_house_id == other.id
    assert page.house_list.currentItem().data(Qt.UserRole) == other.id
    assert page.house_list.count() == 1


@pytest.mark.parametrize("index", [3, 4])
def test_close_stops_already_queued_next_month(desktop, messages, monkeypatch, index):
    from PySide6.QtCore import QTimer
    page = _select_page(desktop, index)
    called = []
    method = "_send_period" if index == 3 else "_send_queue_period"
    monkeypatch.setattr(page, method, lambda *args, **kwargs: called.append(True))
    page._queue_period_ids = [page._current_period_id]
    page._queue_running = True
    QTimer.singleShot(10, page._start_next_queue_item)
    assert desktop.window.close() is False
    for _ in range(100):
        QTest.qWait(10)
        if desktop.window._closing:
            break
    assert not called
    assert desktop.window._closing


def test_close_stops_project_upload_after_basis_download(desktop, messages, monkeypatch):
    from gis_jkh_assistant.app.registry_layouts import prepare_project
    page = _select_page(desktop, 1)
    workers = []
    obtained = False
    uploaded = []
    monkeypatch.setattr(page, "_export_template_path", lambda: "TEST BASIS" if obtained else None)
    monkeypatch.setattr(page, "_upload_current_work_list_to_gis", lambda: uploaded.append(True))
    def download():
        worker = ControlledWorker(desktop.gateway, "download_work_list_export_template", {}, parent=page)
        page._integration_worker = worker
        workers.append(worker)
        worker.start()
    monkeypatch.setattr(page, "_download_export_template_from_gis", download)
    prepare_project(page)
    assert desktop.window.close() is False
    obtained = True
    workers[0].succeeded.emit({})
    workers[0].finish()
    for _ in range(100):
        QTest.qWait(10)
        if desktop.window._closing:
            break
    assert not uploaded
    assert desktop.window._closing
