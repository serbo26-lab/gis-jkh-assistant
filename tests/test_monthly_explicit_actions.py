"""Regressions for explicit month/period actions and GIS-compatible fact input."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from gis_jkh_assistant.app.fixation_page import FixationPage
from gis_jkh_assistant.app.quality_page import QualityAssessmentPage
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.gis_integration import IntegrationStatus


class OfflineGateway:
    def status(self):
        return IntegrationStatus(running=True, helper_connected=True,
                                 gis_page_ready=True, helper_version="0.1.30")

    def execute(self, *_args, **_kwargs):
        raise AssertionError("These regressions must never send GIS requests")


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pages(qt_app, tmp_path):
    repository = Repository(tmp_path / "monthly-ui.db")
    organization = repository.create_organization(name="Тестовая УО", ogrn="1022600000300")
    house = repository.create_house(organization_id=organization.id, address="Тестовый дом",
                                    fias_house_guid="house-fias", house_object_guid="house-object")
    work_list = repository.create_work_list(house_id=house.id, base_year=2026, items=[
        SimpleNamespace(position=1, work_code="12", work_name="Работа по стоимости",
                        work_guid="total-work", price=None, volume=None, quantity="12", cost="1200"),
        SimpleNamespace(position=2, work_code="13", work_name="Работа по цене и объёму",
                        work_guid="components-work", price="10", volume="2", quantity="12", cost=None),
    ])
    repository.update_work_list_gis_state(work_list.id, gis_work_list_guid="work-list", gis_status="APPROVED")
    plan = repository.get_or_create_work_plan(work_list_id=work_list.id, year=2026)
    for item in repository.list_work_plan_items(plan.id):
        repository.update_work_plan_month(item.id, month=1, count=1)
    fixation = FixationPage(repository, OfflineGateway())
    quality = QualityAssessmentPage(repository, OfflineGateway())
    yield SimpleNamespace(repository=repository, work_list=work_list, fixation=fixation, quality=quality)
    for page in (fixation, quality):
        page.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    repository.close()


@pytest.mark.parametrize("page_name,month_button,period_button,month_method,period_method", [
    ("fixation", "fill_month_button", "fill_period_button", "_fill_month_from_plan", "_fill_year_from_plan"),
    ("fixation", "load_month_button", "load_period_button", "_load_selected_month_from_gis", "_load_year_from_gis"),
    ("fixation", "publish_month_button", "publish_period_button", "_publish_selected_month", "_publish_year"),
    ("quality", "form_month_button", "form_period_button", "_form_month_from_fixation", "_form_year_from_fixation"),
    ("quality", "auto_act_month_button", "auto_act_period_button", "_auto_generate_month_act", "_auto_generate_year_acts"),
    ("quality", "load_gis_button", "load_period_button", "_load_from_gis", "_load_year_from_gis"),
    ("quality", "save_button", "save_period_button", "_save_project", "_save_year_projects"),
    ("quality", "publish_button", "publish_period_button", "_publish", "_publish_year"),
    ("quality", "clear_values_button", "clear_period_values_button", "_clear_month_values", "_clear_year_values"),
])
def test_direct_actions_keep_exact_scope(pages, monkeypatch, page_name, month_button,
                                        period_button, month_method, period_method):
    page = getattr(pages, page_name)
    calls = []
    monkeypatch.setattr(page, month_method, lambda: calls.append("month"))
    monkeypatch.setattr(page, period_method, lambda: calls.append("period"))
    for scope in ("month", "period"):
        page.scope_switcher.set_scope(scope)
        for name in (month_button, period_button):
            button = getattr(page, name)
            assert button.menu() is None
            button.setEnabled(True)
            menus = (page.month_actions, page.period_actions) + (
                (page.acts_actions,) if page_name == "quality" else (page.data_actions,)
            )
            for menu in menus:
                menu.sync()
            action = next(action for menu in menus for action, source, _when in menu.entries if source is button)
            assert action.isVisible() and action.isEnabled()
            action.trigger()
    assert calls == ["month", "period", "month", "period"]


def test_fact_columns_grouped_and_plan_reference_optional(pages):
    page = pages.fixation
    assert [page.table.horizontalHeaderItem(i).text() for i in range(1, 5)] == [
        "Цена факт", "Объём факт", "Кол-во факт", "Стоимость факт",
    ]
    assert all(page.table.isColumnHidden(i) for i in range(5, 9))
    page.plan_reference_checkbox.setChecked(True)
    assert not any(page.table.isColumnHidden(i) for i in range(5, 9))
    assert all(not page.table.item(0, i).flags() & Qt.ItemFlag.ItemIsEditable for i in range(5, 9))


def test_planned_input_modes_exist_even_before_filling(pages):
    page = pages.fixation
    assert not page.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable
    assert not page.table.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable
    assert page.table.item(0, 3).flags() & Qt.ItemFlag.ItemIsEditable
    assert page.table.item(0, 4).flags() & Qt.ItemFlag.ItemIsEditable
    assert page.table.item(1, 1).flags() & Qt.ItemFlag.ItemIsEditable
    assert page.table.item(1, 2).flags() & Qt.ItemFlag.ItemIsEditable
    assert not page.table.item(1, 4).flags() & Qt.ItemFlag.ItemIsEditable
    page._select_month(2)
    assert not page.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable
    assert page.table.item(0, 4).flags() & Qt.ItemFlag.ItemIsEditable


def test_explicit_fill_and_edit_calculate_detailed_cost(pages):
    page = pages.fixation
    page._fill_month_from_plan()
    total, detailed = pages.repository.list_work_fixation_items(page._current_period_id)
    assert (total.fact_price, total.fact_amount, total.fact_count, total.fact_total_cost) == (None, None, 1, "100")
    assert (detailed.fact_price, detailed.fact_amount, detailed.fact_count, detailed.fact_total_cost) == ("10", "2", 1, "20.00")
    page.table.item(1, 3).setText("2")
    assert page.table.item(1, 4).text() == "40.00"
    detailed = pages.repository.list_work_fixation_items(page._current_period_id)[1]
    assert detailed.fact_total_cost == "40.00"
    page.table.item(0, 3).setText("3")
    total = pages.repository.list_work_fixation_items(page._current_period_id)[0]
    assert total.fact_count == 3
    assert total.fact_total_cost == "100"


def test_load_preserves_existing_fact_values(pages):
    page = pages.fixation
    item = pages.repository.list_work_fixation_items(page._current_period_id)[0]
    pages.repository.update_work_fixation_item(item.id, fact_price="1", fact_amount="1",
                                               fact_count=3, fact_total_cost="100")
    page._load_table()
    saved = pages.repository.list_work_fixation_items(page._current_period_id)[0]
    assert (saved.fact_price, saved.fact_amount, saved.fact_count, saved.fact_total_cost) == ("1", "1", 3, "100")
    assert page.table.item(0, 1).text() == "1"
    assert not page.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable


@pytest.mark.parametrize("column,value", [(3, "4"), (4, "125")])
def test_correcting_total_row_removes_incompatible_legacy_components(pages, column, value):
    page = pages.fixation
    item = pages.repository.list_work_fixation_items(page._current_period_id)[0]
    pages.repository.update_work_fixation_item(item.id, fact_price="1", fact_amount="1",
                                               fact_count=3, fact_total_cost="100")
    page._load_table()
    page.table.item(0, column).setText(value)
    corrected = pages.repository.list_work_fixation_items(page._current_period_id)[0]
    assert corrected.fact_price is None
    assert corrected.fact_amount is None
    assert corrected.fact_count == (4 if column == 3 else 3)
    assert corrected.fact_total_cost == ("125" if column == 4 else "100")
    assert page.table.item(0, 1).text() == page.table.item(0, 2).text() == ""


def test_period_save_available_while_displayed_month_is_empty(pages):
    page = pages.quality
    item = page.store.list_items(page._current_period_id)[0]
    page.store.update_item(item.id, included=True, cost=None, days_count=None, cost_reduction=None)
    page._select_month(2)
    for scope in ("month", "period"):
        page.scope_switcher.set_scope(scope)
        page._refresh_gis_buttons()
        assert not page.save_button.isEnabled()
        assert page.save_period_button.isEnabled()
        assert not page.publish_button.isEnabled()
        assert not page.publish_period_button.isEnabled()  # January requires an act.
        assert "акт" in page.publish_period_button.toolTip()


def test_quality_act_choices_explain_violation_and_upload(pages, monkeypatch):
    page = pages.quality
    explanation = page.acts_explanation.text()
    assert "Для нарушения качества добавьте готовый документ" in explanation
    assert "укажите число дней нарушения" in explanation
    assert "оставьте поля перерасчёта пустыми" in explanation
    assert "Проект можно сохранить без актов; для размещения нужны акты" in explanation
    calls = []
    monkeypatch.setattr(page, "_open_month_acts", lambda **kwargs: calls.append(kwargs))
    page.upload_acts_button.click()
    page.create_act_button.click()
    assert calls == [{"start_upload": True}, {"start_generate": True}]


def test_danger_actions_are_direct_and_keep_confirmation(pages, monkeypatch):
    page = pages.fixation
    calls = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: calls.append("confirm") or QMessageBox.StandardButton.No)
    for button in (page.clear_month_button, page.clear_period_button):
        assert button.objectName() == "DangerButton"
        assert button.menu() is None
    page._fill_month_from_plan()
    page.clear_month_button.click()
    page.clear_period_button.click()
    assert calls == ["confirm", "confirm"]
    for name in ("clear_values_button", "clear_period_values_button", "cancel_button"):
        button = getattr(pages.quality, name)
        assert button.objectName() == "DangerButton"
        assert button.menu() is None
