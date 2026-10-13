"""Stage two UX contracts: explicit modes and visible context, without GIS calls."""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QDialogButtonBox

import test_ui_workflow as workflow_tests
from gis_jkh_assistant.app.dialogs import HouseDialog
from gis_jkh_assistant.app.fixation_dialogs import AddUnplannedWorkDialog
from gis_jkh_assistant.app.planning_page import MonthDatesDialog
from gis_jkh_assistant.app.quality_bulk_dialogs import SelectActWorksDialog

desktop = workflow_tests.desktop
qt_app = workflow_tests.qt_app


def test_house_creation_starts_with_work_fields_and_has_sticky_footer(desktop):
    dialog = HouseDialog(desktop.window)
    try:
        labels = [dialog.form_content.layout().itemAt(0).layout().labelForField(field).text()
                  for field in (
                      dialog.address_edit,
                      dialog.management_start_edit,
                      dialog.management_agreement_number_edit,
                      dialog.charge_area_edit,
                      dialog.residential_spin,
                  )]
        assert labels == [
            "Адрес *", "Начало управления", "Номер договора управления",
            "Площадь к начислению, м²", "Жилых помещений",
        ]
        assert not dialog.ids_group.isVisible()
        dialog.ids_toggle.setChecked(True)
        assert not dialog.ids_group.isHidden()
        dialog.object_guid_edit.setText("optional-guid")
        dialog.ids_toggle.setChecked(False)
        assert dialog.object_guid_edit.text() == "optional-guid"
        footer = dialog.findChild(QDialogButtonBox)
        assert footer.parentWidget() is dialog
        assert footer.button(QDialogButtonBox.StandardButton.Save).text() == "Добавить дом"
        assert dialog.values()["house_object_guid"] == "optional-guid"
    finally:
        dialog.close()
        dialog.deleteLater()


def test_unplanned_entry_keeps_each_mode_and_submits_only_active_values(desktop):
    dialog = AddUnplannedWorkDialog(
        [{"name": "Текущий ремонт", "code": "12", "guid": "local-test"}],
        desktop.window, house_address="Дом 1", month_label="Январь 2026",
    )
    try:
        assert dialog.mode_combo.currentData() == "detail"
        assert dialog.cost_edit.isReadOnly()
        assert dialog.fact_form.isRowVisible(dialog.price_edit)
        dialog.price_edit.setText("10")
        dialog.amount_edit.setText("2")
        dialog.count_edit.setText("3")
        detail_cost = dialog.cost_edit.text()
        assert detail_cost and detail_cost == "60.00"
        assert dialog.values()["fact_price"] == "10"
        assert dialog.values()["fact_amount"] == "2"
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("total"))
        assert not dialog.cost_edit.isReadOnly()
        assert not dialog.fact_form.isRowVisible(dialog.price_edit)
        assert dialog.values()["fact_price"] is None
        assert dialog.values()["fact_amount"] is None
        dialog.cost_edit.setText("150")
        assert dialog.values()["fact_total_cost"] == "150"
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("detail"))
        assert dialog.price_edit.text() == "10"
        assert dialog.amount_edit.text() == "2"
        assert dialog.cost_edit.text() == detail_cost
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("total"))
        assert dialog.cost_edit.text() == "150"
        assert dialog.values()["fact_count"] == 3
    finally:
        dialog.close()
        dialog.deleteLater()


def test_plan_dates_dialog_names_work_and_month_without_changing_dates(desktop):
    dialog = MonthDatesDialog(
        year=2026, month=2, dates=["2026-02-12"],
        work_name="Длинное название проверяемой работы", parent=desktop.window,
    )
    try:
        assert "Длинное название" in dialog.work_label.text()
        assert "02.2026" in dialog.month_label.text()
        assert dialog.work_label.wordWrap()
        assert dialog.dates() == ["2026-02-12"]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_act_work_selection_shows_document_and_updates_count(desktop):
    items = [SimpleNamespace(id="a", work_name="Услуга A", included=True),
             SimpleNamespace(id="b", work_name="Услуга B", included=False)]
    dialog = SelectActWorksDialog(
        items=items, act_label="Акт № 15 от 31.01.2026",
        parent=desktop.window,
    )
    try:
        assert "№ 15" in dialog.act_context.text()
        assert dialog.selection_count.text() == "Выбрано: 1 из 2"
        dialog._set_checks(set())
        assert dialog.selection_count.text() == "Выбрано: 0 из 2"
        assert not dialog.apply_button.isEnabled()
        dialog._select_included()
        assert dialog.selection_count.text() == "Выбрано: 1 из 2"
        assert dialog.apply_button.isEnabled()
        assert dialog.selected_item_ids() == ["a"]
    finally:
        dialog.close()
        dialog.deleteLater()


def test_house_uo_commands_live_next_to_org_selector(desktop):
    page = desktop.window.houses_page
    assert page.organization_actions_button.parentWidget() is page.organization_combo.parentWidget()
    assert page.add_house_button.text() == "Добавить дом вручную"
    assert page.sync_houses_button.text() == "Загрузить дома"
    assert page.sync_all_management_button.isVisibleTo(page)
    assert page.sync_all_work_lists_button.isVisibleTo(page)
    assert page.bulk_actions_button.text() == "Обновить все дома УО"
    files_menu = page.files_actions_button.menu()
    assert files_menu is not None
    actions = files_menu.actions()
    assert page.import_houses_action in actions
    text = [action.text() for action in actions if not action.isSeparator()]
    assert any("Экспорт Excel" in name for name in text)
    assert any("Импорт Excel" in name for name in text)


def test_quality_shows_table_month_and_period_action_separately(desktop):
    page = desktop.window.quality_page
    page._select_month(1)
    page.scope_switcher.set_scope("period")
    page._refresh_year_status()
    assert "Таблица: Январь" in page.scope_hint.text()
    assert "Действие:" in page.action_scope_hint.text()
    assert "месяцев:" in page.action_scope_hint.text()
    page.scope_switcher.set_scope("month")
    page._refresh_year_status()
    assert "Действие: Январь" in page.action_scope_hint.text()
