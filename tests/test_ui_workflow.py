"""Desktop workflow regressions against real pages and isolated local data.

No test starts the loopback gateway or opens an existing application database.
The UI is exercised offscreen; live GIS commands are deliberately rejected.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QApplication, QMessageBox, QPushButton, QScrollArea, QTableWidget,
)

from gis_jkh_assistant.app import main_window
from gis_jkh_assistant.app.styles import apply_theme, tokens
from gis_jkh_assistant.app.workflow_state import MonthState, fixation_step, quality_step
from gis_jkh_assistant.app.workflow_widgets import ActionMenuButton, OperationStatus
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.gis_integration import IntegrationStatus


class FakeGateway:
    """Connected helper state without sockets, browser sessions or real requests."""

    def __init__(self):
        self.ready = True
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def status(self):
        return IntegrationStatus(
            running=self.started,
            helper_connected=self.ready,
            gis_page_ready=self.ready,
            helper_version="0.1.27",
        )

    def execute(self, *_args, **_kwargs):
        raise AssertionError("A UI regression must not send a live GIS command")


class SignalWorker(QObject):
    progressed = Signal(dict)
    failed = Signal(str)
    succeeded = Signal(dict)


class ControlledWorker(SignalWorker):
    finished = Signal()

    def __init__(self, gateway, command_type, payload, *, parent=None, **_kwargs):
        super().__init__(parent)
        self.gateway = gateway
        self.command_type = command_type
        self.payload = payload
        self.running = False
        from gis_jkh_assistant.app.operation_presenter import observe_worker
        observe_worker(self, parent)

    def start(self):
        self.running = True

    def isRunning(self):  # noqa: N802 - QThread-compatible test double
        return self.running

    def finish(self):
        self.running = False
        self.finished.emit()


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    # Some Windows offscreen runners cannot enumerate system fonts. Load the
    # shipped desktop font explicitly so Cyrillic metrics match the real app.
    font_path = "C:/Windows/Fonts/segoeui.ttf"
    if os.name == "nt" and os.path.isfile(font_path):
        if "Segoe UI" not in QFontDatabase.families():
            assert QFontDatabase.addApplicationFont(font_path) >= 0
        app.setFont(QFont("Segoe UI", 10))
    yield app
    apply_theme(app, "light")


def _settle(app):
    # A second event pass handles layouts requested by the first resize pass.
    app.processEvents()
    app.processEvents()


def _create_house_and_list(repository, *, year=2026):
    organization = repository.create_organization(
        name="Управляющая организация «Городская служба эксплуатации домов»",
        ogrn="1022600000540",
    )
    house = repository.create_house(
        organization_id=organization.id,
        address="г Ставрополь, ул Тестовая, д. 54, корпус 2",
        house_object_guid="house-object-guid",
        fias_house_guid="34062768-dec0-4576-86cb-62bb82d854d2",
    )
    work_list = repository.create_work_list(
        house_id=house.id,
        base_year=year,
        items=[SimpleNamespace(
            position=1,
            work_code="12",
            work_name="Содержание и техническое обслуживание общего имущества дома",
            work_guid="83592b3d-c928-416b-b6e0-595fa1339821",
            unit_name="усл.",
            price="100",
            volume="1",
            quantity="12",
            cost="1200",
        )],
    )
    repository.update_work_list_gis_state(
        work_list.id, gis_work_list_guid="placed-list-guid", gis_status="APPROVED"
    )
    plan = repository.get_or_create_work_plan(work_list_id=work_list.id, year=year)
    plan_item = repository.list_work_plan_items(plan.id)[0]
    for month in range(1, 13):
        repository.update_work_plan_month(plan_item.id, month=month, count=1)
    fixation = repository.get_or_create_work_fixation_period(
        work_list_id=work_list.id, year=year, month=1
    )
    repository.fill_work_fixation_from_plan(fixation.id)
    return organization, house, work_list


@pytest.fixture
def desktop(qt_app, tmp_path, monkeypatch, request):
    repository = Repository(tmp_path / "ui-test.db")
    organization, house, work_list = _create_house_and_list(repository)
    repository.set_setting("ui.shared_context_enabled", "1")
    for key, value in (
        ("organization_id", organization.id), ("house_id", house.id),
        ("work_list_id", work_list.id), ("year", str(getattr(request, "param", 2026))),
    ):
        repository.set_setting(f"ui.context.{key}", value)
    gateway = FakeGateway()
    monkeypatch.setattr(main_window, "LocalIntegrationGateway", lambda: gateway)
    window = main_window.MainWindow(repository)
    window.integration_status_timer.stop()
    window.resize(1366, 768)
    window.show()
    _settle(qt_app)
    yield SimpleNamespace(
        app=qt_app, window=window, repository=repository, gateway=gateway,
        organization=organization, house=house, work_list=work_list,
    )
    window.close()
    window.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _settle(qt_app)


def _select_page(desktop, index):
    desktop.window.navigation.setCurrentRow(index)
    _settle(desktop.app)
    return desktop.window.pages.widget(index)


def _include_quality(page, month):
    page._select_month(month)
    item = page.store.list_items(page._current_period_id)[0]
    page.store.update_item(
        item.id, included=True, cost=None, days_count=None, cost_reduction=None
    )
    page._month_changed()
    return page.store.get_period(page._current_period_id), item


def _add_acceptance_act(page, item):
    return page.store.add_act(
        item_id=item.id, act_type="ACCEPTANCE_ACT", number="54",
        reg_date="31.01.2026", period_from="01.01.2026", period_to="31.01.2026",
        file_name="acceptance.pdf", description=None,
        content_type="application/pdf", content=b"test PDF attachment",
    )


def test_quality_primary_follows_local_draft_acts_and_placed_states(desktop, monkeypatch):
    page = _select_page(desktop, 4)
    page._select_month(1)
    assert page.workflow.primary.text() == "Сформировать оценку"

    period, item = _include_quality(page, 1)
    assert page.workflow.primary.text() == "Сохранить проект"
    assert page.workflow.primary.isEnabled()
    assert page.store.list_acts(item.id) == []
    assert page._validation_error(period.id, require_acts=False) is None
    assert page._validation_error(period.id, require_acts=True)
    saved = []
    monkeypatch.setattr(page, "_save_project", lambda: saved.append(period.id))
    page.workflow.primary.click()
    assert saved == [period.id], "Saving a project must remain available before adding acts"

    page.store.set_gis_state(period.id, gis_status="DRAFT", estimation_guid="draft-guid")
    page._month_changed()
    assert "акт" in page.workflow.primary.text().lower()
    assert "проект" in page.workflow.title.text().lower()

    _add_acceptance_act(page, item)
    # Saving the updated draft acknowledges the added attachment.
    page.store.set_gis_state(period.id, gis_status="DRAFT")
    page._month_changed()
    assert page._validation_error(period.id, require_acts=True) is None
    assert page.workflow.primary.text() == "Разместить"
    assert page.workflow.primary.isEnabled()
    published = []
    monkeypatch.setattr(page, "_publish", lambda: published.append(period.id))
    page.workflow.primary.click()
    assert published == [period.id]

    page.store.set_gis_state(period.id, gis_status="APPROVED")
    page._month_changed()
    assert "размещена" in page.workflow.title.text().lower()
    assert page.workflow.primary.text() == "Следующий месяц"
    page.workflow.primary.click()
    assert page._current_month == 2


def test_period_recommendation_uses_all_months_not_just_selected_month(desktop, monkeypatch):
    page = _select_page(desktop, 4)
    january, january_item = _include_quality(page, 1)
    _add_acceptance_act(page, january_item)
    page.store.set_gis_state(january.id, gis_status="DRAFT", estimation_guid="january-draft")
    february, _item = _include_quality(page, 2)
    for month in range(3, 13):
        completed, completed_item = _include_quality(page, month)
        _add_acceptance_act(page, completed_item)
        page.store.set_gis_state(completed.id, gis_status="APPROVED")
    page._select_month(1)
    assert page.workflow.primary.text() == "Разместить"
    page.scope_switcher.buttons["period"].click()
    assert page.workflow.primary.text().startswith("Сохранить проект")
    saved = []
    monkeypatch.setattr(page, "_save_year_projects", lambda: saved.append("period"))
    page.workflow.primary.click()
    assert saved == ["period"]
    assert page.store.get_period(february.id).gis_status in {None, "LOCAL"}


def test_period_publication_validates_only_months_that_will_be_sent(desktop):
    page = _select_page(desktop, 4)
    january, _item = _include_quality(page, 1)
    page.store.set_gis_state(january.id, gis_status="APPROVED", estimation_guid="completed-january")
    february, item = _include_quality(page, 2)
    _add_acceptance_act(page, item)
    page.store.set_gis_state(february.id, gis_status="DRAFT", estimation_guid="pending-february")
    page._select_month(1)
    page.scope_switcher.set_scope("period")
    candidates = page._periods_with_quality_for_year("approve_quality_assessment")
    assert [period.id for period in candidates] == [february.id]
    assert all(page._validation_error(period.id, require_acts=True) is None for period in candidates)
    assert page.publish_button.isEnabled()
    assert page.workflow.primary.text() == "Разместить"
    assert page.workflow.primary.isEnabled()
    page.scope_switcher.set_scope("month")
    assert not page.publish_button.isEnabled(), "Completed January is not a new publication candidate"


def test_quality_next_month_returns_to_an_earlier_unfinished_month(desktop):
    page = _select_page(desktop, 4)
    for month in range(1, 13):
        period, _item = _include_quality(page, month)
        page.store.set_gis_state(
            period.id, gis_status="DRAFT" if month == 2 else "APPROVED",
            estimation_guid=f"remote-{month}",
        )
    page._select_month(12)
    assert page.workflow.primary.text() == "Следующий месяц"
    assert "Февраль" in page.workflow.detail.text()
    page.workflow.primary.click()
    assert page._current_month == 2


def test_fixation_period_recommends_empty_month_without_replacing_saved_facts(desktop):
    page = _select_page(desktop, 3)
    for month in range(1, 12):
        period = desktop.repository.get_or_create_work_fixation_period(
            work_list_id=desktop.work_list.id, year=2026, month=month
        )
        desktop.repository.fill_work_fixation_from_plan(period.id)
        if month == 1:
            january = period
            item = desktop.repository.list_work_fixation_items(period.id)[0]
            desktop.repository.update_work_fixation_item(
                item.id, fact_price="999", fact_amount="1", fact_count=1, fact_total_cost="999"
            )
        desktop.repository.update_work_fixation_gis_state(period.id, gis_status="SAVED")
    page._month_changed()
    page.scope_switcher.set_scope("period")
    assert page.workflow.primary.text() == "Открыть Декабрь"
    page.workflow.primary.click()
    assert page._current_month == 12
    assert page.scope_switcher.scope() == "month"
    assert desktop.repository.list_work_fixation_items(january.id)[0].fact_price == "999"
    assert desktop.repository.get_work_fixation_period(january.id).gis_status == "SAVED"


def test_quality_period_recommends_missing_month_without_reforming_approved_data(desktop):
    page = _select_page(desktop, 4)
    january, _item = _include_quality(page, 1)
    page.store.set_gis_state(january.id, gis_status="APPROVED", estimation_guid="approved-january")
    february = desktop.repository.get_or_create_work_fixation_period(
        work_list_id=desktop.work_list.id, year=2026, month=2
    )
    desktop.repository.fill_work_fixation_from_plan(february.id)
    page._month_changed()
    page.scope_switcher.set_scope("period")
    assert page.workflow.primary.text() == "Открыть Февраль"
    page.workflow.primary.click()
    assert page._current_month == 2
    assert page.scope_switcher.scope() == "month"
    assert page.store.get_period(january.id).gis_status == "APPROVED"


@pytest.mark.parametrize("page_index,command_name,month_method,period_method", [
    (3, "fill_month_button", "_fill_month_from_plan", "_fill_year_from_plan"),
    (3, "load_month_button", "_load_selected_month_from_gis", "_load_year_from_gis"),
    (3, "publish_month_button", "_publish_selected_month", "_publish_year"),
    (4, "form_month_button", "_form_month_from_fixation", "_form_year_from_fixation"),
    (4, "load_gis_button", "_load_from_gis", "_load_year_from_gis"),
    (4, "save_button", "_save_project", "_save_year_projects"),
    (4, "publish_button", "_publish", "_publish_year"),
])
def test_same_command_routes_to_selected_month_or_period(
    desktop, monkeypatch, page_index, command_name, month_method, period_method
):
    page = _select_page(desktop, page_index)
    calls = []
    monkeypatch.setattr(page, month_method, lambda: calls.append("month"))
    monkeypatch.setattr(page, period_method, lambda: calls.append("period"))
    button = getattr(page, command_name)
    for scope in ("month", "period"):
        page.scope_switcher.buttons[scope].click()
        button.setEnabled(True)
        button.click()
    assert calls == ["month", "period"]
    assert not any(
        isinstance(value, QPushButton) and name.endswith("year_button")
        for name, value in vars(page).items()
    ), "The scope must replace legacy year-command widgets, including hidden duplicates"


def test_network_block_has_inline_explanation_and_local_actions_still_work(desktop):
    page = _select_page(desktop, 4)
    _include_quality(page, 1)
    desktop.gateway.ready = False
    desktop.window._refresh_integration_status()
    assert not page.workflow.primary.isEnabled()
    assert page.workflow.reason.text().strip()
    assert page.form_month_button.isEnabled()
    assert page.month_acts_button.isEnabled()


def test_shared_context_preserves_chosen_list_through_navigation(desktop):
    alternate = desktop.repository.create_work_list(house_id=desktop.house.id, base_year=2026)
    desktop.repository.update_work_list_gis_state(
        alternate.id, gis_work_list_guid="other-list-guid", gis_status="APPROVED"
    )
    registry = _select_page(desktop, 1)
    registry.refresh_work_lists()
    registry.registry_combo.setCurrentIndex(registry.registry_combo.findData(alternate.id))
    registry.registry_combo.setCurrentIndex(registry.registry_combo.findData(desktop.work_list.id))
    assert registry._current_work_list_id == desktop.work_list.id
    for page_index in (2, 3, 4, 1, 4, 3):
        page = _select_page(desktop, page_index)
        assert page._current_work_list_id == desktop.work_list.id
        assert desktop.window._shared_context["work_list_id"] == desktop.work_list.id
        if hasattr(page, "year_combo"):
            assert page.year_combo.currentData() == 2026


def test_shared_local_list_is_not_silently_replaced_by_a_placed_list(desktop):
    local = desktop.repository.create_work_list(house_id=desktop.house.id, base_year=2026)
    registry = _select_page(desktop, 1)
    registry.refresh_work_lists()
    registry.registry_combo.setCurrentIndex(registry.registry_combo.findData(local.id))
    for page_index in (2, 3, 4):
        page = _select_page(desktop, page_index)
        assert page.work_list_combo.currentIndex() == -1
        assert page._current_work_list_id is None
        assert desktop.window._shared_context["work_list_id"] == local.id
        assert page.context_notice.text().strip()
        assert not page.table.isEnabled()
        if page.workflow.primary.isEnabled():
            assert "переч" in page.workflow.primary.text().lower()
    registry = _select_page(desktop, 1)
    assert registry._current_work_list_id == local.id


@pytest.mark.parametrize("desktop", [2030], indirect=True)
def test_restored_year_outside_list_period_stays_unselected_with_a_reason(desktop):
    for page_index in (2, 3, 4, 2):
        page = _select_page(desktop, page_index)
        assert page.work_list_combo.currentData() == desktop.work_list.id
        assert page.year_combo.currentIndex() == -1
        assert int(desktop.window._shared_context["year"]) == 2030
        assert "2030" in page.context_notice.text()
        assert not page.table.isEnabled()
    assert desktop.repository.get_setting("ui.context.year") == "2030"


def test_new_list_creation_year_does_not_change_the_selected_workflow_context(desktop):
    registry = _select_page(desktop, 1)
    registry.year_spin.setValue(2027)
    planning = _select_page(desktop, 2)
    assert planning.year_combo.currentData() == 2026
    assert planning._current_work_list_id == desktop.work_list.id
    registry = _select_page(desktop, 1)
    assert registry.year_spin.value() == 2027
    registry.template_combo.setCurrentIndex(registry.template_combo.findData(None))
    registry.create_button.click()
    new_list_id = registry._current_work_list_id
    assert new_list_id != desktop.work_list.id
    assert desktop.repository.get_work_list(new_list_id).base_year == 2027
    assert desktop.window._shared_context["work_list_id"] == new_list_id
    assert int(desktop.window._shared_context["year"]) == 2027
    planning = _select_page(desktop, 2)
    assert planning._current_work_list_id is None
    assert desktop.window._shared_context["work_list_id"] == new_list_id


def test_theme_controls_persist_and_keep_autosaved_plan_edits(desktop):
    planning = _select_page(desktop, 2)
    planning.table.item(0, 3).setText("2")
    plan_id = planning._current_plan_id
    plan_item = desktop.repository.list_work_plan_items(plan_id)[0]
    assert desktop.repository.get_work_plan_month(plan_item.id, 1).count == 2
    settings = _select_page(desktop, 5)
    for theme in ("dark", "light"):
        getattr(settings, f"{theme}_radio").click()
        _settle(desktop.app)
        assert desktop.repository.get_setting("ui.theme") == theme
        assert desktop.app.palette().color(QPalette.ColorRole.Text) == QColor(tokens(theme)["text"])
        planning = _select_page(desktop, 2)
        assert planning.table.item(0, 3).text() == "2"
        assert planning._current_work_list_id == desktop.work_list.id
        assert desktop.repository.get_work_plan_month(plan_item.id, 1).count == 2
        settings = _select_page(desktop, 5)


def test_planning_lifecycle_tracks_edits_even_within_the_same_second(desktop, monkeypatch):
    from gis_jkh_assistant.app import planning_page
    from gis_jkh_assistant.database import repository as repository_module

    page = _select_page(desktop, 2)
    plan_id = page._current_plan_id
    item = desktop.repository.list_work_plan_items(plan_id)[0]
    monkeypatch.setattr(repository_module, "_now", lambda: "2026-10-07T10:00:00+00:00")
    desktop.repository.apply_work_plan_distribution(
        plan_id, [(item.id, month, 0) for month in range(1, 13)]
    )
    page._load_table()
    assert page.workflow.primary.text() == "Автораспределить"
    page.table.item(0, 3).setText("12")
    assert page.workflow.primary.text() == "Разместить план"
    workers = []

    def create_worker(*args, **kwargs):
        worker = ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(planning_page, "GisIntegrationWorker", create_worker)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: QMessageBox.Ok)
    page.workflow.primary.click()
    assert len(workers) == 1
    assert workers[0].command_type == "save_work_plan"
    assert not page.table.isEnabled()
    assert not page.work_list_combo.isEnabled()
    workers[0].succeeded.emit({"workPlanGuid": "placed-plan", "gisStatus": "SAVED"})
    workers[0].finish()
    _settle(desktop.app)
    assert page.workflow.primary.text() == "К фиксации"
    assert desktop.repository.get_setting(f"ui.plan.synced.{plan_id}")
    placed = desktop.repository.get_work_plan(plan_id)
    assert placed.gis_last_sync_at == placed.updated_at

    page.table.item(0, 3).setText("11")
    changed = desktop.repository.get_work_plan(plan_id)
    assert changed.updated_at == placed.updated_at
    assert page.workflow.primary.text() == "Обновить план в ГИС"
    assert page.workflow.primary.isEnabled()
    assert "изменён локально" in page.plan_status.text()


def test_historical_plan_explains_why_it_cannot_be_published(desktop):
    page = _select_page(desktop, 2)
    with desktop.repository.connection:
        desktop.repository.connection.execute(
            "UPDATE work_plan_items SET work_list_item_id = NULL WHERE work_plan_id = ?",
            (page._current_plan_id,),
        )
    page._load_table()
    assert not page.publish_button.isEnabled()
    assert not page.workflow.primary.isEnabled()
    assert "Исторический план" in page.workflow.reason.text()
    assert page.load_gis_button.isEnabled()


@pytest.mark.parametrize("page_index", [3, 4])
def test_running_operation_locks_context_scope_and_working_data(desktop, page_index):
    page = _select_page(desktop, page_index)
    worker = ControlledWorker(desktop.gateway, "load_work_fixation", {"month": 1, "year": 2026}, parent=page)
    for name in ("organization_combo", "house_combo", "work_list_combo", "year_combo", "scope_switcher", "table"):
        assert not getattr(page, name).isEnabled(), name
    assert not page.workflow.primary.isEnabled()
    assert page.workflow.reason.text().strip()
    worker.finish()
    _settle(desktop.app)
    for name in ("work_list_combo", "year_combo", "scope_switcher", "table"):
        assert getattr(page, name).isEnabled(), name


def test_quality_without_fixation_guides_to_the_prerequisite(desktop):
    page = _select_page(desktop, 4)
    page._select_month(2)
    assert page.workflow.primary.text() == "К фиксации"
    assert "фактические показатели" in page.workflow.detail.text()
    page.workflow.primary.click()
    assert desktop.window.navigation.currentRow() == 3
    assert desktop.window.fixation_page._current_work_list_id == desktop.work_list.id
    assert desktop.window.fixation_page._current_month == 2


@pytest.mark.parametrize("page_index,busy_flag", [(3, "_queue_running"), (4, "_integration_busy")])
def test_shared_selection_waits_for_busy_page_before_changing_its_data(
    desktop, page_index, busy_flag
):
    target = _select_page(desktop, page_index)
    original_period = target._current_period_id
    original_name = target.table.item(0, 1 if page_index == 4 else 0).text()
    setattr(target, busy_flag, True)
    alternate = desktop.repository.create_work_list(house_id=desktop.house.id, base_year=2026)
    desktop.repository.update_work_list_gis_state(
        alternate.id, gis_work_list_guid="new-selected-list", gis_status="APPROVED"
    )
    registry = _select_page(desktop, 1)
    registry.refresh_work_lists()
    registry.registry_combo.setCurrentIndex(registry.registry_combo.findData(alternate.id))
    assert desktop.window._shared_context["work_list_id"] == alternate.id
    target = _select_page(desktop, page_index)
    assert target._current_work_list_id == desktop.work_list.id
    assert target._current_period_id == original_period
    assert target.table.item(0, 1 if page_index == 4 else 0).text() == original_name
    assert target.context_notice.text().strip()
    setattr(target, busy_flag, False)
    _select_page(desktop, 1)
    target = _select_page(desktop, page_index)
    assert target._current_work_list_id == alternate.id
    assert target._current_period_id != original_period


@pytest.mark.parametrize("user_switches_context", [False, True])
def test_async_registry_creation_joins_shared_context_only_if_the_user_kept_it(
    desktop, user_switches_context
):
    alternate = desktop.repository.create_work_list(house_id=desktop.house.id, base_year=2026)
    desktop.repository.update_work_list_gis_state(
        alternate.id, gis_work_list_guid="alternate-placed-list", gis_status="APPROVED"
    )
    registry = _select_page(desktop, 1)
    worker = ControlledWorker(desktop.gateway, "list_organization_work_options", {}, parent=registry)
    worker.start()
    registry._integration_worker = worker
    registry._integration_busy = True
    created = desktop.repository.create_work_list(house_id=desktop.house.id, base_year=2027)
    registry.refresh_work_lists(select_id=created.id)
    assert registry._current_work_list_id == created.id
    assert desktop.window._shared_context["work_list_id"] == desktop.work_list.id
    if user_switches_context:
        planning = _select_page(desktop, 2)
        planning.work_list_combo.setCurrentIndex(planning.work_list_combo.findData(alternate.id))
        assert desktop.window._shared_context["work_list_id"] == alternate.id
    registry._integration_busy = False
    registry._integration_worker = None
    worker.finish()
    _settle(desktop.app)
    expected = alternate.id if user_switches_context else created.id
    assert desktop.window._shared_context["work_list_id"] == expected
    assert desktop.repository.get_setting("ui.context.work_list_id") == expected
    if not user_switches_context:
        assert int(desktop.window._shared_context["year"]) == 2027
        planning = _select_page(desktop, 2)
        assert planning._current_work_list_id is None
        assert desktop.window._shared_context["work_list_id"] == created.id


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(1366, 768), (1600, 900), (1920, 1080)])
def test_all_pages_fit_desktop_width_with_one_recommended_action(desktop, theme, size):
    apply_theme(desktop.app, theme)
    desktop.window.resize(*size)
    for page_index in range(6):
        page = _select_page(desktop, page_index)
        page_scrolls = [
            scroll for scroll in page.findChildren(QScrollArea)
            if scroll.objectName() == "PageScroll"
        ]
        for scroll in page_scrolls:
            assert scroll.horizontalScrollBar().maximum() == 0, (
                theme, size, page_index, scroll.widget().size(), scroll.viewport().size()
            )
            assert scroll.widget().width() <= scroll.viewport().width()
        primary = [
            button for button in page.findChildren(QPushButton)
            if button.objectName() == "PrimaryButton" and button.isVisibleTo(page)
        ]
        assert len(primary) == (0 if page_index == 5 else 1), (theme, size, page_index)
        for button in page.findChildren(QPushButton):
            if not button.isVisibleTo(page):
                continue
            ancestor = button.parentWidget()
            inside_table = False
            while ancestor is not None and ancestor is not page:
                if isinstance(ancestor, QTableWidget):
                    inside_table = True
                    break
                ancestor = ancestor.parentWidget()
            if inside_table:
                continue  # Wide table contents have their own permitted horizontal scroll.
            point = button.mapTo(page, QPoint(0, 0))
            assert point.x() >= 0, (theme, size, page_index, button.text())
            assert point.x() + button.width() <= page.width(), (
                theme, size, page_index, button.text(), button.width(), page.width()
            )
        if page_index in (1, 2, 3, 4):
            table = page.items_table if page_index == 1 else page.table
            name_column = 1 if page_index == 4 else 0
            assert table.columnWidth(name_column) >= 250, (
                "Work names must remain readable when numeric columns are wide",
                theme, size, page_index, table.columnWidth(name_column),
            )
            assert table.width() >= page.width() * 0.85, (
                "The working table must span the page, outside a narrow master/detail column",
                theme, size, page_index, table.width(), page.width(),
            )
    assert desktop.app.palette().color(QPalette.ColorRole.Text) == QColor(tokens(theme)["text"])
    assert desktop.app.palette().color(QPalette.ColorRole.Base) == QColor(tokens(theme)["surface"])


def test_operation_status_reports_progress_failure_and_ignores_old_worker(qt_app):
    status = OperationStatus()
    first = SignalWorker()
    second = SignalWorker()
    status.bind_worker(first, "Загрузка фиксации", "Май 2026")
    first.progressed.emit({"processed": 4, "total": 6})
    assert "Загрузка фиксации" in status.label.text()
    assert "Май 2026" in status.label.text()
    assert "4 из 6" in status.label.text()
    assert status.progress.value() == 4
    assert status.progress.maximum() == 6

    status.bind_worker(second, "Сохранение проектов", "Июль 2026", queue=(4, 8))
    assert "5 из 8" in status.label.text()
    first.failed.emit("Stale worker error")
    assert "Stale worker" not in status.label.text()
    second.failed.emit("Сессия ГИС ЖКХ завершена")
    assert "Остановлено" in status.label.text()
    assert "Июль 2026" in status.label.text()
    assert "Сессия ГИС ЖКХ завершена" in status.label.text()
    assert status.property("tone") == "danger"
    assert status.progress.isHidden()
    status.deleteLater()
    _settle(qt_app)


def test_period_operation_keeps_month_progress_when_worker_reports_inner_data(qt_app):
    status = OperationStatus()
    worker = SignalWorker()
    status.bind_worker(worker, "Сохранение проектов", "Июль 2026", queue=(4, 8))
    worker.progressed.emit({"processed": 2, "total": 3})
    assert "5 из 8" in status.label.text()
    assert "2 из 3" in status.label.text()
    assert status.progress.maximum() == 8
    assert status.progress.value() == 4
    worker.succeeded.emit({})
    assert status.progress.maximum() == 8
    assert status.progress.value() == 5
    assert "5 из 8" in status.label.text()
    status.deleteLater()
    _settle(qt_app)


def test_menu_explains_disabled_commands_and_hides_irrelevant_danger(qt_app):
    command = QPushButton("Аннулировать оценку")
    command.setEnabled(False)
    command.setToolTip("Оценка ещё не сохранена в ГИС ЖКХ")
    menu = ActionMenuButton("Опасные действия")
    action = menu.add_command(command, when=lambda: command.isEnabled())
    menu.sync()
    assert not action.isVisible()
    command.setEnabled(True)
    menu.sync()
    assert action.isVisible() and action.isEnabled()
    command.setEnabled(False)
    regular = menu.add_command(command)
    menu.sync()
    assert regular.isVisible() and not regular.isEnabled()
    assert regular.toolTip() == "Оценка ещё не сохранена в ГИС ЖКХ"
    menu.deleteLater()
    command.deleteLater()
    _settle(qt_app)


@pytest.mark.parametrize("status", ["LOCAL", "MODIFIED", "CANCELLED", "ERROR"])
def test_quality_recovery_keeps_project_saving_before_act_validation(status):
    step = quality_step([MonthState(1, status, has_data=True, publish_error="Нужен акт")])
    assert step.key == "save"
    assert "без актов" in step.detail


def test_fixation_period_with_one_modified_month_recommends_publishing():
    months = [
        MonthState(1, "APPROVED", has_data=True),
        MonthState(2, "MODIFIED", has_data=True),
    ]
    assert fixation_step(months).key == "publish"
    assert fixation_step([months[0]]).key == "next"


@pytest.mark.parametrize("decide,key", [(quality_step, "form"), (fixation_step, "fill")])
def test_empty_month_in_period_prevents_claiming_the_whole_period_is_completed(decide, key):
    months = [MonthState(1, "APPROVED", has_data=True), MonthState(2)]
    step = decide(months)
    assert step.key == key
    assert "размещены все" not in step.title.lower()


@pytest.mark.parametrize("page_index", [3, 4])
def test_month_badge_keeps_a_sync_error_visible_even_with_remote_status(desktop, page_index):
    page = _select_page(desktop, page_index)
    page._select_month(1)
    if page_index == 4:
        period, _item = _include_quality(page, 1)
        page.store.set_gis_state(
            period.id, gis_status="DRAFT", gis_last_error="Сессия завершена"
        )
    else:
        desktop.repository.update_work_fixation_gis_state(
            page._current_period_id, gis_status="SAVED", gis_last_error="Сессия завершена"
        )
    page._month_changed()
    assert "ошибка" in page.month_buttons[1].text().lower()


@pytest.mark.parametrize("page_index", [3, 4])
def test_failed_period_queue_names_the_month_and_resumes_from_that_month(
    desktop, monkeypatch, page_index
):
    from gis_jkh_assistant.app import fixation_page, quality_page

    page = _select_page(desktop, page_index)
    page.scope_switcher.set_scope("period")
    periods = []
    for month in (6, 7, 8):
        if page_index == 4:
            period, _item = _include_quality(page, month)
        else:
            period = desktop.repository.get_or_create_work_fixation_period(
                work_list_id=desktop.work_list.id, year=2026, month=month
            )
            desktop.repository.fill_work_fixation_from_plan(period.id)
        periods.append(period)
    page._queue_period_ids = [period.id for period in periods]
    page._queue_index = 1
    page._queue_failed_period_id = None
    if page_index == 4:
        page._queue_running = True
        page._queue_command = "save_quality_assessment"
        page._queue_title = "Сохранение проектов"
    else:
        page._queue_mode = "publish"
    workers = []

    def create_worker(*args, **kwargs):
        worker = ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker

    module = quality_page if page_index == 4 else fixation_page
    monkeypatch.setattr(module, "GisIntegrationWorker", create_worker)
    if page_index == 4:
        page._send_queue_period(periods[1].id)
    else:
        page._send_period(periods[1].id, queue_mode=True)
    assert len(workers) == 1
    workers[0].failed.emit("Сессия ГИС ЖКХ завершена")
    workers[0].finish()
    _settle(desktop.app)
    assert "Июль 2026" in page.operation_status.label.text()
    assert "Сессия ГИС ЖКХ завершена" in page.operation_status.label.text()
    assert page.continue_queue_button.isVisibleTo(page)
    assert page.continue_queue_button.isEnabled()
    assert "Июль 2026" in page.continue_queue_button.text()
    assert "Продолжить" in page.workflow.primary.text()
    resumed = []
    monkeypatch.setattr(page, "_start_next_queue_item", lambda: resumed.append(page._queue_index))
    page.workflow.primary.click()
    assert resumed == [1]
    assert page._queue_failed_period_id is None
