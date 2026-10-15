"""UX stage 3: targeted presentation/regression contracts with local data only."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QHeaderView
from gis_jkh_assistant.app.lazy_distribution_dialog import LazyDistributionDialog
from gis_jkh_assistant.app.work_templates_dialog import WorkTemplatesDialog
from gis_jkh_assistant.database import Repository

import test_ui_workflow as workflows

desktop = workflows.desktop
qt_app = workflows.qt_app


def test_distribution_calculator_has_permanent_field_labels(desktop):
    dialog = LazyDistributionDialog(desktop.repository, desktop.work_list, desktop.window)
    try:
        for text, editor in (
            ("Тариф, руб./м²", dialog.tariff_edit),
            ("Площадь, м²", dialog.area_edit),
            ("Месяцы", dialog.months_edit),
        ):
            assert any(
                label.text() == text and label.buddy() is editor
                for label in dialog.findChildren(QLabel)
            )
        dialog.tariff_edit.setText("32.50")
        dialog.area_edit.setText("1250.50")
        dialog.months_edit.setText("12")
        dialog.calculate_total_button.click()
        total = dialog.total_edit.text().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        assert Decimal(total) == Decimal("487695")
    finally:
        dialog.close()
        dialog.deleteLater()


def test_empty_templates_expose_excel_and_save_current_paths(qt_app, tmp_path):
    repository = Repository(tmp_path / "stage3-empty-templates.db")
    org = repository.create_organization(name="УО без шаблонов", ogrn="1022600000421")
    clicked = []
    dialog = WorkTemplatesDialog(
        repository, org.id, save_current=lambda: clicked.append(True),
        current_ready=True,
    )
    try:
        assert dialog.list_widget.count() == 0
        assert not dialog.empty_guidance.isHidden()
        assert "Excel" in dialog.import_button.text()
        assert dialog.save_current_button.isEnabled()
        dialog.save_current_button.click()
        assert clicked == [True]
    finally:
        dialog.close()
        dialog.deleteLater()
        repository.close()


def test_templates_explain_when_current_list_is_unusable(qt_app, tmp_path):
    repository = Repository(tmp_path / "stage3-no-current-list.db")
    org = repository.create_organization(name="УО с пустым перечнем", ogrn="1022600000422")
    dialog = WorkTemplatesDialog(repository, org.id, current_ready=False)
    try:
        assert dialog.list_widget.count() == 0
        assert not dialog.save_current_button.isEnabled()
        assert "выберите" in dialog.empty_guidance.text().lower()
    finally:
        dialog.close()
        dialog.deleteLater()
        repository.close()


def test_unplanned_columns_prioritize_names_and_numeric_data(desktop):
    page = desktop.window.fixation_page
    table = page.unplanned_table
    page._resize_unplanned_table()
    header = table.horizontalHeader()
    for column in (1, 2, 7):
        assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive
    assert table.columnWidth(1) < 240
    assert table.columnWidth(2) < 160
    assert table.columnWidth(7) < 280
    assert table.columnWidth(0) >= 320


def test_settings_separate_reconnection_and_installation(desktop):
    settings = desktop.window.settings_page
    settings.set_connection_status(
        "Помощник не подключён", helper_connected=False, gis_page_ready=False
    )
    assert not settings.recover_connection_button.isHidden()
    assert "Установить" in settings.helper_setup_button.text()
    settings.recover_connection_button.setChecked(True)
    assert not settings.recovery_steps.isHidden()
    settings.set_connection_status(
        "ГИС ЖКХ подключен", helper_connected=True, gis_page_ready=True
    )
    assert settings.recover_connection_button.isHidden()
    assert not settings.recover_connection_button.isChecked()
    assert "Настроить" in settings.helper_setup_button.text()


def test_work_list_groups_remain_distinct_and_commands_reachable(desktop):
    page = desktop.window.work_lists_page
    assert page.composition_actions.text() == "Состав и шаблоны"
    assert page.lazy_fill_button.text() == "Распределение суммы…"
    assert page.gis_actions.text() == "Обмен с ГИС ЖКХ"
    assert page.files_actions_button.text() == "Файлы Excel"
    sources = [button for _act, button, _when in page.composition_actions.entries]
    assert page.templates_button in sources
    assert page.save_as_template_button in sources
    assert page.remove_works_button in sources


def test_quality_group_labels_and_selected_work_act_action(desktop, monkeypatch):
    window = desktop.window
    window.navigation.setCurrentRow(6)
    page = window.quality_page
    assert page.selection_actions.text().startswith("Оценка:")
    assert page.acts_actions.text().startswith("Документы:")
    assert page.data_actions.text() == "Данные и обмен с ГИС"
    assert page.month_acts_button.text() == "Акты…"
    assert page.table.rowCount() > 0
    page.table.setCurrentCell(0, 1)
    page._update_selected_act_button()
    assert page.selected_act_button.isEnabled()
    calls = []
    monkeypatch.setattr(page, "_open_acts", calls.append)
    page.table.horizontalScrollBar().setValue(
        page.table.horizontalScrollBar().maximum()
    )
    page.selected_act_button.click()
    assert calls == [page.table.item(0, 0).data(Qt.ItemDataRole.UserRole)]
    page.table.setEnabled(False)
    page._update_selected_act_button()
    assert not page.selected_act_button.isEnabled()
    page._refresh_year_status()
