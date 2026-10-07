"""Work and service lists page."""

from __future__ import annotations

import sqlite3
import base64
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QListWidgetItem,
    QMessageBox,
    QTableWidgetItem,
    QWidget,
)

from gis_jkh_assistant.database.paths import gis_export_template_dir
from gis_jkh_assistant.database.models import TemplateSource
from gis_jkh_assistant.database.repository import Repository
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
from gis_jkh_assistant.gis_files.work_lists import (
    WorkListExportError,
    WorkTemplateImportError,
    export_work_list_xlsx,
    parse_work_template,
)
from gis_jkh_assistant.work_lists.gis_sync import (
    apply_gis_work_list_sync,
    prepare_gis_work_list_sync,
)

from .gis_integration_worker import GisIntegrationWorker
from .lazy_distribution_dialog import LazyDistributionDialog
from .registry_layouts import build_work_lists, mirror_registry
from .registry_workflow import (
    can_initialize_draft_baseline,
    mark_work_list_saved,
    refresh_work_lists,
    work_list_fingerprint,
)
from .styles import color
from .work_directory_dialog import WorkDirectorySelectionDialog
from .work_row_rules import editable_columns_for_mode, infer_work_row_mode
from .work_templates_dialog import WorkTemplatesDialog


_MONTHS = (
    "01 — Январь",
    "02 — Февраль",
    "03 — Март",
    "04 — Апрель",
    "05 — Май",
    "06 — Июнь",
    "07 — Июль",
    "08 — Август",
    "09 — Сентябрь",
    "10 — Октябрь",
    "11 — Ноябрь",
    "12 — Декабрь",
)

_ORIGINAL_VALUE_ROLE = Qt.ItemDataRole.UserRole + 1

_EXPORT_TEMPLATE_PATH_KEY = "gis_work_list_export_template_path"
_EXPORT_TEMPLATE_NAME_KEY = "gis_work_list_export_template_name"
_EXPORT_TEMPLATE_TYPE_KEY = "gis_work_list_export_template_type"
_EXPORT_TEMPLATE_VERSION_KEY = "gis_work_list_export_template_version"
_EXPORT_TEMPLATE_UPDATED_KEY = "gis_work_list_export_template_updated_at"


class WorkListsPage(QWidget):
    """Local work-list registry grouped by organization and house."""
    contextSelectionChanged = Signal()

    def __init__(
        self,
        repository: Repository,
        integration_gateway: LocalIntegrationGateway,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.integration_gateway = integration_gateway
        self._integration_worker: GisIntegrationWorker | None = None
        self._integration_busy = False
        self._current_work_list_id: str | None = None
        self._loading = False
        self._period_dirty = False
        self._loaded_period: tuple[int, int, int, int] | None = None

        self._period_timer = QTimer(self)
        self._period_timer.setSingleShot(True)
        self._period_timer.setInterval(650)
        self._period_timer.timeout.connect(self._save_period)

        self._build_ui()
        self._refresh_export_template_status()
        self._refresh_integration_status()
        self.refresh_organizations()

    def _build_ui(self) -> None:
        build_work_lists(self)

    def _refresh_workflow(self) -> None:
        self._refresh_gis_action_buttons()

    def refresh_organizations(self) -> None:
        self._save_period()
        current = self.organization_combo.currentData()
        organizations = self.repository.list_organizations()

        # Returning to this page used to rebuild a potentially very large
        # QTableWidget even when the organization/house context had not changed.
        # Keep the existing widgets intact when the source context is identical.
        current_org_rows = [
            (
                str(self.organization_combo.itemData(index)),
                self.organization_combo.itemText(index),
            )
            for index in range(self.organization_combo.count())
        ]
        repository_org_rows = [
            (str(organization.id), organization.name)
            for organization in organizations
        ]
        current_house_id = self.house_combo.currentData()
        if current and current_org_rows == repository_org_rows:
            houses = self.repository.list_houses(str(current))
            current_house_rows = [
                (
                    str(self.house_combo.itemData(index)),
                    self.house_combo.itemText(index),
                )
                for index in range(self.house_combo.count())
            ]
            repository_house_rows = [
                (str(house.id), house.address)
                for house in houses
            ]
            if current_house_rows == repository_house_rows and current_house_id:
                self._refresh_gis_action_buttons()
                return

        self.organization_combo.blockSignals(True)
        self.organization_combo.clear()
        for organization in organizations:
            self.organization_combo.addItem(organization.name, organization.id)
        if current:
            index = self.organization_combo.findData(current)
            if index >= 0:
                self.organization_combo.setCurrentIndex(index)
        self.organization_combo.blockSignals(False)

        self._organization_changed()

    def _organization_changed(self) -> None:
        self._save_period()
        organization_id = self.organization_combo.currentData()

        current_house = self.house_combo.currentData()
        self.house_combo.blockSignals(True)
        self.house_combo.clear()
        if organization_id:
            for house in self.repository.list_houses(organization_id):
                self.house_combo.addItem(house.address, house.id)
        if current_house:
            index = self.house_combo.findData(current_house)
            if index >= 0:
                self.house_combo.setCurrentIndex(index)
        self.house_combo.blockSignals(False)

        self._refresh_templates()
        self._house_changed()

    def _refresh_templates(self) -> None:
        organization_id = self.organization_combo.currentData()
        current = self.template_combo.currentData()

        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem("Пустой перечень", None)
        if organization_id:
            for template in self.repository.list_work_templates(organization_id):
                legacy_basic = (
                    template.name.strip().casefold() == "базовый"
                    and template.source in {TemplateSource.IMPORT, TemplateSource.BUILTIN}
                )
                if template.source == TemplateSource.BUILTIN or legacy_basic:
                    continue
                self.template_combo.addItem(template.name, template.id)
        if current:
            index = self.template_combo.findData(current)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
        self.template_combo.blockSignals(False)

    def _house_changed(self) -> None:
        self._save_period()
        selected_house_id = self.house_combo.currentData()
        current = (
            self.repository.get_work_list(self._current_work_list_id)
            if self._current_work_list_id
            else None
        )
        if current is not None and current.house_id != selected_house_id:
            self._current_work_list_id = None
        self.refresh_work_lists()

    def refresh_work_lists(self, select_id: str | None = None) -> None:
        """Show every work list for the selected house, independent of creation year."""

        house_id = self.house_combo.currentData()
        selected_id = select_id or self._current_work_list_id
        if select_id:
            self.creation_toggle.setChecked(False)

        self.work_lists.blockSignals(True)
        self.work_lists.clear()
        selected_row = -1
        lists = self.repository.list_work_lists(house_id) if house_id else []
        organization_id = self.organization_combo.currentData()
        templates = {
            template.id: template
            for template in (
                self.repository.list_work_templates(str(organization_id))
                if organization_id
                else []
            )
        }

        for row, work_list in enumerate(lists):
            template = (
                templates.get(work_list.template_id)
                if work_list.template_id
                else None
            )
            if template:
                template_name = template.name
            elif work_list.gis_work_list_guid:
                template_name = "Из ГИС ЖКХ"
            else:
                template_name = "Без шаблона"
            status = self._work_list_status_text(work_list)
            item = QListWidgetItem(
                f"{work_list.period_label}\n{template_name} • {status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, work_list.id)
            self.work_lists.addItem(item)
            if work_list.id == selected_id:
                selected_row = row

        self.list_count.setText(str(len(lists)))
        if selected_row >= 0:
            self.work_lists.setCurrentRow(selected_row)
        self.work_lists.blockSignals(False)

        current = self.work_lists.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        mirror_registry(self)
        self._load_work_list(current_id)
        if select_id and current_id == select_id:
            self.contextSelectionChanged.emit()

    def _creation_year(self) -> int:
        self.year_spin.interpretText()
        return self.year_spin.value()

    @staticmethod
    def _directory_option_item(
        option: dict,
        *,
        position: int,
        template_item=None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            position=position,
            work_code=str(option.get("code") or "").strip() or None,
            work_name=str(option.get("name") or "").strip(),
            work_guid=str(option.get("guid") or "").strip() or None,
            unit_name=str(option.get("unitName") or "").strip() or None,
            price=getattr(template_item, "price", None),
            volume=getattr(template_item, "volume", None),
            quantity=getattr(template_item, "quantity", None),
            cost=getattr(template_item, "cost", None),
            allocation_percent=getattr(template_item, "allocation_percent", None),
        )

    def _directory_ready(self) -> bool:
        status = self.integration_gateway.status()
        return bool(
            status.running
            and status.helper_connected
            and status.gis_page_ready
        )

    def _load_organization_directory(
        self,
        on_success,
        *,
        busy_text: str,
    ) -> None:
        if self._integration_worker is not None and self._integration_worker.isRunning():
            QMessageBox.information(
                self,
                "Операция уже выполняется",
                "Дождитесь завершения текущей операции с ГИС ЖКХ.",
            )
            return
        if not self._directory_ready():
            QMessageBox.information(
                self,
                "Нет подключения к ГИС ЖКХ",
                "Для получения справочника работ откройте личный кабинет ГИС ЖКХ "
                "и убедитесь, что ГИС ЖКХ помощник подключен.",
            )
            return

        self._integration_busy = True
        self.save_status.setText(busy_text)
        self._refresh_gis_action_buttons()
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "list_organization_work_options",
            {},
            timeout=60.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            options = list(result.get("items") or [])
            on_success(options)

        def failed(message: str) -> None:
            QMessageBox.critical(
                self,
                "Не удалось получить справочник",
                message,
            )

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self.save_status.setText("Сохранено")
            self._refresh_gis_action_buttons()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _create_work_list(self) -> None:
        self._save_period()
        house_id = self.house_combo.currentData()
        if not house_id:
            QMessageBox.information(
                self,
                "Не выбран дом",
                "Выберите управляющую организацию и дом.",
            )
            return

        template_id = self.template_combo.currentData()
        if not template_id:
            try:
                work_list = self.repository.create_work_list(
                    house_id=house_id,
                    base_year=self._creation_year(),
                    template_id=None,
                )
            except ValueError as exc:
                QMessageBox.warning(self, "Нельзя создать перечень", str(exc))
                return
            self.refresh_work_lists(select_id=work_list.id)
            return

        template = self.repository.get_work_template(template_id)
        template_items = self.repository.list_work_template_items(template_id)
        if not template or not template_items:
            QMessageBox.warning(
                self,
                "Пустой шаблон",
                "В выбранном шаблоне нет работ. Создайте пустой перечень "
                "и добавьте работы из справочника ГИС ЖКХ.",
            )
            return

        def create_from_directory(options: list[dict]) -> None:
            by_guid = {
                str(option.get("guid") or ""): option
                for option in options
                if option.get("guid")
            }
            by_code = {
                str(option.get("code") or ""): option
                for option in options
                if option.get("code")
            }

            matched = []
            missing = []
            for item in template_items:
                option = (
                    by_guid.get(str(item.work_guid or ""))
                    or by_code.get(str(item.work_code or ""))
                )
                if option is None:
                    missing.append(item)
                    continue
                matched.append(
                    self._directory_option_item(
                        option,
                        position=len(matched) + 1,
                        template_item=item,
                    )
                )

            if not matched:
                QMessageBox.warning(
                    self,
                    "Работы шаблона недоступны",
                    "Ни одна работа выбранного шаблона не найдена в актуальном "
                    "справочнике этой управляющей организации.",
                )
                return

            if missing:
                names = "\n".join(f"• {item.work_name}" for item in missing[:8])
                if len(missing) > 8:
                    names += f"\n… ещё: {len(missing) - 8}"
                message = QMessageBox(self)
                message.setWindowTitle("Часть работ шаблона недоступна")
                message.setIcon(QMessageBox.Icon.Warning)
                message.setText(
                    f"В справочнике организации найдено {len(matched)} из "
                    f"{len(template_items)} работ шаблона «{template.name}»."
                )
                message.setInformativeText(
                    "Не найдены:\n"
                    + names
                    + "\n\nСоздать перечень только из доступных работ?"
                )
                create_button = message.addButton(
                    "Создать из доступных",
                    QMessageBox.ButtonRole.AcceptRole,
                )
                message.addButton(
                    "Отмена",
                    QMessageBox.ButtonRole.RejectRole,
                )
                message.exec()
                if message.clickedButton() is not create_button:
                    return

            try:
                work_list = self.repository.create_work_list(
                    house_id=house_id,
                    base_year=self._creation_year(),
                    template_id=template_id,
                    items=matched,
                )
            except ValueError as exc:
                QMessageBox.warning(self, "Нельзя создать перечень", str(exc))
                return
            self.refresh_work_lists(select_id=work_list.id)

        self._load_organization_directory(
            create_from_directory,
            busy_text="Проверка шаблона по справочнику ГИС ЖКХ…",
        )

    def _clone_current_work_list(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            QMessageBox.information(
                self,
                "Не выбран перечень",
                "Сначала выберите перечень, который нужно использовать как основу.",
            )
            return

        source = self.repository.get_work_list(self._current_work_list_id)
        if not source:
            return

        target_year, ok = QInputDialog.getInt(
            self,
            "Создать на основе выбранного",
            "Год нового перечня:",
            self._creation_year(),
            2015,
            2100,
            1,
        )
        if not ok:
            return

        try:
            cloned = self.repository.clone_work_list(source.id, target_year)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "Нельзя создать перечень",
                str(exc),
            )
            return
        self.year_spin.setValue(target_year)
        self.refresh_work_lists(select_id=cloned.id)

    def _work_list_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        self._save_period()
        work_list_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        mirror_registry(self)
        self._load_work_list(work_list_id)

    def _load_work_list(self, work_list_id: str | None) -> None:
        self._period_timer.stop()
        self._period_dirty = False
        self._current_work_list_id = work_list_id
        self._composition_review_error = ""
        mirror_registry(self)

        if not work_list_id:
            self._loaded_period = None
            self._set_editor_enabled(False)
            self.items_table.setRowCount(0)
            self.template_label.setText("—")
            self.status_label.setText("—")
            self.planned_total_label.setText("—")
            self.gis_state_label.setText("—")
            self.selected_label.setText("Выбранный перечень: —")
            self.save_status.setText("Сохранено")
            self._refresh_gis_action_buttons()
            return

        work_list = self.repository.get_work_list(work_list_id)
        if not work_list:
            self._loaded_period = None
            self._set_editor_enabled(False)
            return

        if str(work_list.gis_status or "").upper() == "DRAFT":
            if (
                self.repository.get_setting(f"ui_work_list_saved:{work_list.id}") is None
                and can_initialize_draft_baseline(self, work_list)
            ):
                mark_work_list_saved(self, work_list.id)

        self._loading = True
        try:
            self._set_editor_enabled(True)
            self.start_month.setCurrentIndex(work_list.start_month - 1)
            self.start_year.setValue(work_list.start_year)
            self.end_month.setCurrentIndex(work_list.end_month - 1)
            self.end_year.setValue(work_list.end_year)
            self._loaded_period = (
                work_list.start_year,
                work_list.start_month,
                work_list.end_year,
                work_list.end_month,
            )
            template = (
                self.repository.get_work_template(work_list.template_id)
                if work_list.template_id
                else None
            )
            self.template_label.setText(template.name if template else "Без шаблона")
            self.status_label.setText(self._work_list_status_text(work_list))
            self.planned_total_label.setText(
                f"{work_list.planned_total} руб." if work_list.planned_total else "—"
            )
            self.gis_state_label.setText(self._gis_state_text(work_list))
            self.selected_label.setText(f"Выбранный перечень: {work_list.period_label}")
            self._load_items(work_list_id)
            self._refresh_gis_action_buttons()
        finally:
            self._loading = False

        self.save_status.setText("Сохранено")

    def _load_items(self, work_list_id: str) -> None:
        items = self.repository.list_work_list_items(work_list_id)
        self.items_table.blockSignals(True)
        self.items_table.setUpdatesEnabled(False)
        try:
            self.items_table.setRowCount(len(items))

            for row, item in enumerate(items):
                name_item = QTableWidgetItem(item.work_name)
                name_item.setData(Qt.ItemDataRole.UserRole, item.id)
                name_item.setData(_ORIGINAL_VALUE_ROLE, item.work_name)
                if item.work_code:
                    name_item.setToolTip(f"Код работы: {item.work_code}")
                self.items_table.setItem(row, 0, name_item)

                values = (item.price, item.volume, item.quantity, item.cost)
                for column, value in enumerate(values, start=1):
                    cell = QTableWidgetItem(value or "")
                    cell.setData(_ORIGINAL_VALUE_ROLE, value or "")
                    self.items_table.setItem(row, column, cell)

                self._apply_row_input_mode(row)
        finally:
            self.items_table.setUpdatesEnabled(True)
            self.items_table.blockSignals(False)
        self._refresh_gis_action_buttons()

    def _current_period(self) -> tuple[int, int, int, int]:
        self.start_year.interpretText()
        self.end_year.interpretText()
        return (
            self.start_year.value(),
            self.start_month.currentIndex() + 1,
            self.end_year.value(),
            self.end_month.currentIndex() + 1,
        )

    def _queue_period_save(self) -> None:
        if self._loading or not self._current_work_list_id:
            return

        period = self._current_period()
        if period == self._loaded_period:
            self._period_dirty = False
            self._period_timer.stop()
            self.save_status.setText("Сохранено")
            return

        self._period_dirty = True
        self.save_status.setText("Сохраняется…")
        self._period_timer.start()
        self._refresh_gis_action_buttons()

    def _save_period(self) -> None:
        self._period_timer.stop()
        if self._loading or not self._current_work_list_id or not self._period_dirty:
            return

        start_year, start_month, end_year, end_month = self._current_period()
        if (start_year, start_month) > (end_year, end_month):
            self.save_status.setText("Некорректный период")
            return

        try:
            self.repository.update_work_list_period(
                self._current_work_list_id,
                start_year=start_year,
                start_month=start_month,
                end_year=end_year,
                end_month=end_month,
            )
        except ValueError as exc:
            self.save_status.setText("Период вне срока управления")
            QMessageBox.warning(
                self,
                "Проверьте период перечня",
                str(exc),
            )
            if self._loaded_period:
                (
                    old_start_year,
                    old_start_month,
                    old_end_year,
                    old_end_month,
                ) = self._loaded_period
                self._loading = True
                try:
                    self.start_month.setCurrentIndex(old_start_month - 1)
                    self.start_year.setValue(old_start_year)
                    self.end_month.setCurrentIndex(old_end_month - 1)
                    self.end_year.setValue(old_end_year)
                finally:
                    self._loading = False
            self._period_dirty = False
            return

        self._loaded_period = (start_year, start_month, end_year, end_month)
        self._period_dirty = False
        self.save_status.setText("Сохранено")

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list:
            self.selected_label.setText(f"Выбранный перечень: {work_list.period_label}")
            self._update_list_item_text(work_list.id)
            self._refresh_gis_action_buttons()

    def _update_list_item_text(self, work_list_id: str) -> None:
        work_list = self.repository.get_work_list(work_list_id)
        if not work_list:
            return
        template = (
            self.repository.get_work_template(work_list.template_id)
            if work_list.template_id
            else None
        )
        if template:
            template_name = template.name
        elif work_list.gis_work_list_guid:
            template_name = "Из ГИС ЖКХ"
        else:
            template_name = "Без шаблона"
        status = self._work_list_status_text(work_list)

        for row in range(self.work_lists.count()):
            item = self.work_lists.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == work_list_id:
                item.setText(f"{work_list.period_label}\n{template_name} • {status}")
                break
        mirror_registry(self)

    def _item_changed(self, table_item: QTableWidgetItem) -> None:
        if self._loading or not self._current_work_list_id:
            return

        original = table_item.data(_ORIGINAL_VALUE_ROLE)
        current = table_item.text().strip()
        if (original or "") == current:
            return

        row = table_item.row()
        id_item = self.items_table.item(row, 0)
        if not id_item:
            return
        item_id = id_item.data(Qt.ItemDataRole.UserRole)
        if not item_id:
            return

        field_by_column = {
            0: "work_name",
            1: "price",
            2: "volume",
            3: "quantity",
            4: "cost",
        }
        field = field_by_column.get(table_item.column())
        if not field:
            return

        self.save_status.setText("Сохраняется…")
        self.repository.update_work_list_item(
            item_id,
            **{field: current or None},
        )
        table_item.setData(_ORIGINAL_VALUE_ROLE, current)

        self.items_table.blockSignals(True)
        try:
            self._apply_row_input_mode(row)
        finally:
            self.items_table.blockSignals(False)

        self.save_status.setText("Сохранено")
        self._refresh_gis_action_buttons()

    def _apply_row_input_mode(self, row: int) -> None:
        """Mirror the mutually exclusive input schemes used by ГИС ЖКХ."""

        price_item = self.items_table.item(row, 1)
        volume_item = self.items_table.item(row, 2)
        cost_item = self.items_table.item(row, 4)
        if not price_item or not volume_item or not cost_item:
            return

        mode = infer_work_row_mode(
            price=price_item.text(),
            volume=volume_item.text(),
            cost=cost_item.text(),
        )
        editable_columns = editable_columns_for_mode(mode)

        for column in range(5):
            item = self.items_table.item(row, column)
            if item is None:
                continue

            flags = item.flags()
            if column in editable_columns:
                item.setFlags(flags | Qt.ItemFlag.ItemIsEditable)
                item.setBackground(QBrush())
                if column != 0:
                    item.setToolTip("")
            else:
                item.setFlags(flags & ~Qt.ItemFlag.ItemIsEditable)
                item.setBackground(QBrush(QColor(color("readonly"))))
                if column in (1, 2):
                    item.setToolTip(
                        "Выбран вариант «Кол-во + Стоимость». "
                        "Очистите стоимость, чтобы снова вводить цену и объем."
                    )
                elif column == 4:
                    item.setToolTip(
                        "Выбран вариант «Цена + Объем + Кол-во». "
                        "Очистите цену и объем, чтобы вводить стоимость."
                    )

    @staticmethod
    def _work_list_status_text(work_list) -> str:
        value = work_list.status.value
        if value == "local":
            return "Локальный"
        if value == "gis_draft":
            return "Проект в ГИС ЖКХ"
        if value == "placed":
            return "Размещен в ГИС ЖКХ"
        if value == "cancelled":
            return "Отменён в ГИС ЖКХ"
        return value

    @staticmethod
    def _gis_state_text(work_list) -> str:
        if work_list.gis_status == "DRAFT":
            text = "Проект"
        elif work_list.gis_status == "APPROVED":
            text = "Размещен"
        elif work_list.gis_status == "CANCELLED":
            text = "Отменён"
        elif work_list.gis_status == "IMPORT_SENT":
            text = "Файл отправлен на обработку"
        elif work_list.gis_status == "NOT_FOUND":
            text = "Не найден при последней проверке"
        elif work_list.gis_status:
            text = work_list.gis_status
        else:
            text = "Не загружен"

        details = []
        if work_list.gis_work_list_guid:
            details.append(f"GUID: {work_list.gis_work_list_guid}")
        if work_list.gis_last_sync_at:
            details.append(f"проверено {work_list.gis_last_sync_at}")
        if work_list.gis_last_error:
            details.append(f"ошибка: {work_list.gis_last_error}")
        return text + (f" • {' • '.join(details)}" if details else "")

    def _refresh_integration_status(self) -> None:
        """Refresh GIS-dependent actions; visible status lives in the sidebar."""

        self._refresh_gis_action_buttons()

    def _refresh_gis_action_buttons(self) -> None:
        status = self.integration_gateway.status()
        ready = status.running and status.helper_connected and status.gis_page_ready
        selected = self._current_work_list_id is not None
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if selected
            else None
        )
        gis_status = work_list.gis_status if work_list else None

        house_id = self.house_combo.currentData()
        house = self.repository.get_house(house_id) if house_id else None
        house_ready = house is not None and bool(house.house_object_guid)
        self.load_gis_lists_button.setEnabled(
            ready and house_ready and not self._integration_busy
        )
        self.load_export_template_button.setEnabled(
            ready and house_ready and not self._integration_busy
        )
        self.load_export_template_file_button.setEnabled(not self._integration_busy)
        self.download_template_action.setEnabled(
            self.load_export_template_button.isEnabled()
        )
        self.manual_template_action.setEnabled(
            self.load_export_template_file_button.isEnabled()
        )
        self.export_xlsx_action.setEnabled(
            selected and not self._integration_busy
        )
        self.files_actions_button.setEnabled(
            self.download_template_action.isEnabled()
            or self.manual_template_action.isEnabled()
            or self.export_xlsx_action.isEnabled()
        )

        enabled = ready and selected and not self._integration_busy
        self.sync_gis_button.setEnabled(enabled)
        self.upload_to_gis_button.setEnabled(
            enabled and gis_status in {None, "", "NOT_FOUND"}
        )
        self.cancel_gis_button.setEnabled(enabled and gis_status == "APPROVED")
        self.save_editable_gis_button.setEnabled(
            enabled and gis_status in {"CANCELLED", "DRAFT"}
        )
        self.approve_gis_button.setEnabled(enabled and gis_status == "DRAFT")
        refresh_work_lists(self)

    def _refresh_export_template_status(self) -> None:
        path_text = self.repository.get_setting(_EXPORT_TEMPLATE_PATH_KEY)
        source_name = self.repository.get_setting(_EXPORT_TEMPLATE_NAME_KEY)
        file_type = self.repository.get_setting(_EXPORT_TEMPLATE_TYPE_KEY)
        version = self.repository.get_setting(_EXPORT_TEMPLATE_VERSION_KEY)

        if path_text and Path(path_text).exists():
            details = [source_name or Path(path_text).name]
            if file_type:
                details.append(file_type)
            if version:
                details.append(f"v{version}")
            self.export_template_status.setText(
                "Шаблон экспорта ГИС ЖКХ: " + " • ".join(details)
            )
            return

        self.export_template_status.setText(
            "Шаблон экспорта ГИС ЖКХ: не загружен. "
            "Он нужен один раз как техническая основа для формирования XLSX."
        )

    def _install_export_template(
        self,
        source: Path,
        *,
        source_name: str | None = None,
    ):
        imported = parse_work_template(source)

        suffix = source.suffix.lower()
        if suffix not in {".xlsx", ".xlsm"}:
            suffix = ".xlsx"

        directory = gis_export_template_dir()
        stored_path = directory / f"work-list-export-template{suffix}"

        for old_file in directory.glob("work-list-export-template.*"):
            if old_file != stored_path:
                old_file.unlink(missing_ok=True)
        if source.resolve() != stored_path.resolve():
            shutil.copy2(source, stored_path)

        self.repository.set_setting(_EXPORT_TEMPLATE_PATH_KEY, str(stored_path))
        self.repository.set_setting(
            _EXPORT_TEMPLATE_NAME_KEY,
            source_name or source.name,
        )
        self.repository.set_setting(_EXPORT_TEMPLATE_TYPE_KEY, imported.file_type)
        self.repository.set_setting(_EXPORT_TEMPLATE_VERSION_KEY, imported.version)
        self.repository.set_setting(
            _EXPORT_TEMPLATE_UPDATED_KEY,
            datetime.now().isoformat(timespec="seconds"),
        )
        self._refresh_export_template_status()
        return imported

    def _load_export_template_file(self) -> None:
        message = QMessageBox(self)
        message.setWindowTitle("Ручной выбор шаблона экспорта")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText("Выбрать официальный XLSX-шаблон вручную?")
        message.setInformativeText(
            "Это резервный способ. Обычно шаблон можно получить автоматически "
            "кнопкой «Получить / обновить шаблон из ГИС ЖКХ».\n\n"
            "Выберите официальный файл «Экспорт перечня работ …xlsx», "
            "ранее выгруженный из ГИС ЖКХ."
        )
        message.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        if message.exec() != QMessageBox.StandardButton.Ok:
            return

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите шаблон экспорта, выгруженный из ГИС ЖКХ",
            "",
            "Excel (*.xlsx *.xlsm)",
        )
        if not filename:
            return

        source = Path(filename)
        try:
            imported = self._install_export_template(source)
        except (WorkTemplateImportError, OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Файл не распознан",
                "Выбранный файл не похож на шаблон экспорта перечня ГИС ЖКХ.\n\n"
                + str(exc),
            )
            return

        details = []
        if imported.file_type:
            details.append(f"TYPE: {imported.file_type}")
        if imported.version:
            details.append(f"VERSION: {imported.version}")

        text = "Шаблон экспорта сохранен и будет использоваться при формировании XLSX."
        if details:
            text += "\n\n" + "\n".join(details)
        QMessageBox.information(self, "Шаблон экспорта обновлен", text)

    def _download_export_template_from_gis(self) -> None:
        self._save_period()

        house_id = self.house_combo.currentData()
        house = self.repository.get_house(house_id) if house_id else None
        if house is None:
            QMessageBox.information(
                self,
                "Не выбран дом",
                "Сначала выберите дом. ГИС ЖКХ формирует технический шаблон "
                "в контексте конкретного МКД.",
            )
            return
        if not house.house_object_guid:
            QMessageBox.warning(
                self,
                "Нет GUID дома",
                "В разделе «Дома» загрузите сведения о доме из ГИС ЖКХ.",
            )
            return
        if not self._directory_ready():
            QMessageBox.information(
                self,
                "Нет подключения к ГИС ЖКХ",
                "Откройте личный кабинет ГИС ЖКХ и убедитесь, что "
                "ГИС ЖКХ помощник подключен.",
            )
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            QMessageBox.information(
                self,
                "Операция уже выполняется",
                "Дождитесь завершения текущей операции с ГИС ЖКХ.",
            )
            return

        self._set_integration_busy(True, "Запрос шаблона в ГИС ЖКХ…")
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "download_work_list_export_template",
            {
                "houseObjectGuid": house.house_object_guid,
                "year": int(self.year_spin.value()),
                "month": 1,
            },
            timeout=120.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            encoded = str(result.get("fileBase64") or "").strip()
            file_name = str(
                result.get("fileName") or "Экспорт перечня работ.xlsx"
            ).strip()
            if not encoded:
                QMessageBox.critical(
                    self,
                    "Не удалось получить шаблон",
                    "ГИС ЖКХ завершил выгрузку, но готовый файл не был получен.",
                )
                return

            try:
                payload = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                QMessageBox.critical(
                    self,
                    "Не удалось получить шаблон",
                    "Помощник вернул поврежденные данные XLSX.\n\n" + str(exc),
                )
                return

            suffix = Path(file_name).suffix.lower()
            if suffix not in {".xlsx", ".xlsm"}:
                suffix = ".xlsx"

            directory = gis_export_template_dir()
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix="gis-work-list-template-",
                    suffix=suffix,
                    dir=directory,
                    delete=False,
                ) as tmp:
                    tmp.write(payload)
                    temporary_path = Path(tmp.name)

                imported = self._install_export_template(
                    temporary_path,
                    source_name=file_name,
                )
            except (WorkTemplateImportError, OSError, ValueError) as exc:
                QMessageBox.critical(
                    self,
                    "Полученный файл не распознан",
                    "ГИС ЖКХ вернул файл, который не удалось использовать "
                    "как шаблон перечня.\n\n"
                    + str(exc),
                )
                return
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

            details = []
            if imported.file_type:
                details.append(f"TYPE: {imported.file_type}")
            if imported.version:
                details.append(f"VERSION: {imported.version}")

            text = (
                "Официальный шаблон получен напрямую из ГИС ЖКХ и сохранен "
                "как техническая основа для формирования XLSX."
            )
            if details:
                text += "\n\n" + "\n".join(details)
            if not getattr(self, "_preparing_project", False):
                QMessageBox.information(self, "Шаблон экспорта обновлен", text)

        def progressed(progress: dict) -> None:
            phase = str(progress.get("phase") or "")
            if phase == "queued":
                self.save_status.setText("Ожидание предыдущей операции с ГИС ЖКХ…")
                return
            if phase != "template_export":
                return
            status = str(progress.get("status") or "")
            labels = {
                "requesting": "Запрос выгрузки шаблона…",
                "processing": "ГИС ЖКХ формирует шаблон…",
                "downloading": "Скачивание готового шаблона…",
            }
            self.save_status.setText(labels.get(status, "Получение шаблона…"))

        def failed(message: str) -> None:
            QMessageBox.critical(
                self,
                "Не удалось получить шаблон из ГИС ЖКХ",
                message
                + "\n\nРезервный путь: «Файлы и шаблоны» → «Выбрать XLSX вручную…».",
            )

        def finished() -> None:
            self._integration_worker = None
            self._set_integration_busy(False)
            self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.progressed.connect(progressed)
        worker.finished.connect(finished)
        worker.start()

    def _export_template_path(self) -> Path | None:
        path_text = self.repository.get_setting(_EXPORT_TEMPLATE_PATH_KEY)
        if not path_text:
            return None
        path = Path(path_text)
        return path if path.exists() else None

    def _export_current_work_list(self) -> None:
        """Build an XLSX from the currently selected local work list."""

        self._save_period()
        if not self._current_work_list_id:
            QMessageBox.information(
                self,
                "Не выбран перечень",
                "Сначала выберите перечень, который нужно сформировать в XLSX.",
            )
            return

        source_path = self._export_template_path()
        if source_path is None:
            QMessageBox.information(
                self,
                "Не загружен шаблон экспорта",
                "В меню «Файлы и шаблоны» выберите «Получить официальный шаблон». "
                "Резервный путь в этом же меню: «Выбрать XLSX вручную…».",
            )
            self._refresh_export_template_status()
            return

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return

        house = self.repository.get_house(work_list.house_id)
        if not house:
            QMessageBox.critical(
                self,
                "Дом не найден",
                "Карточка дома отсутствует в локальной базе.",
            )
            return
        if not house.house_object_guid:
            QMessageBox.warning(
                self,
                "Нет GUID дома",
                "Для формирования файла нужен GUID объекта жилищного фонда.",
            )
            return

        extension = source_path.suffix.lower()
        if extension not in {".xlsx", ".xlsm"}:
            extension = ".xlsx"

        safe_house = house.gis_unique_house_number or house.house_object_guid[:8]
        default_name = (
            f"Перечень_{safe_house}_"
            f"{work_list.start_month:02d}.{work_list.start_year}-"
            f"{work_list.end_month:02d}.{work_list.end_year}{extension}"
        )
        default_path = Path.home() / "Downloads" / default_name

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сформировать файл для ГИС ЖКХ",
            str(default_path),
            "Excel (*.xlsx *.xlsm)",
        )
        if not filename:
            return

        output_path = Path(filename)
        if output_path.suffix.lower() not in {".xlsx", ".xlsm"}:
            output_path = output_path.with_suffix(extension)

        items = self.repository.list_work_list_items(work_list.id)
        self.save_status.setText("Формируется XLSX…")
        try:
            result = export_work_list_xlsx(
                source_template_path=source_path,
                output_path=output_path,
                house_guid=house.house_object_guid,
                start_year=work_list.start_year,
                start_month=work_list.start_month,
                end_year=work_list.end_year,
                end_month=work_list.end_month,
                items=items,
            )
        except (WorkListExportError, OSError, zipfile.BadZipFile) as exc:
            self.save_status.setText("Ошибка формирования")
            QMessageBox.critical(self, "Не удалось сформировать XLSX", str(exc))
            return

        self.save_status.setText("Сохранено")
        QMessageBox.information(
            self,
            "XLSX сформирован",
            f"Файл готов для загрузки в ГИС ЖКХ.\n\n"
            f"Строк выгружено: {result.exported_rows}\n"
            f"{result.output_path}",
        )

    def _current_gis_context(self):
        self._save_period()
        if not self._current_work_list_id:
            QMessageBox.information(
                self,
                "Не выбран перечень",
                "Сначала выберите перечень.",
            )
            return None

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return None

        house = self.repository.get_house(work_list.house_id)
        if not house:
            QMessageBox.critical(
                self,
                "Дом не найден",
                "Карточка дома отсутствует в локальной базе.",
            )
            return None

        period_error = self.repository.work_list_management_period_error(
            work_list.house_id,
            start_year=work_list.start_year,
            start_month=work_list.start_month,
            end_year=work_list.end_year,
            end_month=work_list.end_month,
        )
        if period_error:
            QMessageBox.warning(
                self,
                "Период перечня вне срока управления",
                period_error + "\n\nИсправьте период перечня перед обменом с ГИС ЖКХ.",
            )
            return None

        if not house.house_object_guid:
            QMessageBox.warning(
                self,
                "Нет GUID дома",
                "Для обмена с ГИС ЖКХ нужен GUID объекта жилищного фонда.",
            )
            return None

        return work_list, house

    @staticmethod
    def _gis_command_payload(work_list, house) -> dict:
        return {
            "houseGuid": house.house_object_guid,
            "workListGuid": work_list.gis_work_list_guid,
            "startYear": work_list.start_year,
            "startMonth": work_list.start_month,
            "endYear": work_list.end_year,
            "endMonth": work_list.end_month,
        }

    def _build_xlsx_for_gis(self, work_list, house) -> tuple[str, str] | None:
        source_path = self._export_template_path()
        if source_path is None:
            QMessageBox.information(
                self,
                "Не загружен шаблон экспорта",
                "Сначала загрузите технический шаблон экспорта ГИС ЖКХ.",
            )
            return None

        extension = source_path.suffix.lower()
        if extension not in {".xlsx", ".xlsm"}:
            extension = ".xlsx"

        safe_house = house.gis_unique_house_number or house.house_object_guid[:8]
        filename = (
            f"Перечень_{safe_house}_"
            f"{work_list.start_month:02d}.{work_list.start_year}-"
            f"{work_list.end_month:02d}.{work_list.end_year}{extension}"
        )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="gis-jkh-work-list-",
                suffix=extension,
                delete=False,
            ) as tmp:
                temporary_path = Path(tmp.name)

            export_work_list_xlsx(
                source_template_path=source_path,
                output_path=temporary_path,
                house_guid=house.house_object_guid,
                start_year=work_list.start_year,
                start_month=work_list.start_month,
                end_year=work_list.end_year,
                end_month=work_list.end_month,
                items=self.repository.list_work_list_items(work_list.id),
            )
            encoded = base64.b64encode(temporary_path.read_bytes()).decode("ascii")
            return filename, encoded
        except (WorkListExportError, OSError, zipfile.BadZipFile) as exc:
            QMessageBox.critical(self, "Не удалось подготовить XLSX", str(exc))
            return None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _set_integration_busy(self, busy: bool, text: str | None = None) -> None:
        self._integration_busy = busy
        self._refresh_gis_action_buttons()
        if busy and text:
            self.save_status.setText(text)
        elif not busy:
            self.save_status.setText("Сохранено")

    def _run_integration_command(
        self,
        *,
        work_list_id: str,
        command_type: str,
        payload: dict,
        success_callback,
        busy_text: str,
        timeout: float = 120.0,
    ) -> None:
        if self._integration_worker is not None and self._integration_worker.isRunning():
            QMessageBox.information(
                self,
                "Операция уже выполняется",
                "Дождитесь завершения текущей операции с ГИС ЖКХ.",
            )
            return

        self._set_integration_busy(True, busy_text)
        sent_fingerprint = work_list_fingerprint(self, work_list_id)
        worker = GisIntegrationWorker(
            self.integration_gateway,
            command_type,
            payload,
            timeout=timeout,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            if command_type in {"upload_import_work_list", "save_editable_work_list"}:
                mark_work_list_saved(self, work_list_id, sent_fingerprint)
            success_callback(work_list_id, result)

        def failed(message: str) -> None:
            self.repository.update_work_list_gis_state(
                work_list_id,
                gis_last_error=message,
            )
            if self._current_work_list_id == work_list_id:
                self._load_work_list(work_list_id)
            QMessageBox.critical(self, "Ошибка обмена с ГИС ЖКХ", message)

        def finished() -> None:
            self._integration_worker = None
            self._set_integration_busy(False)
            self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _apply_gis_result(self, work_list_id: str, result: dict) -> None:
        self.repository.update_work_list_gis_state(
            work_list_id,
            gis_work_list_guid=result.get("workListGuid"),
            gis_file_guid=result.get("fileGuid"),
            gis_import_job_guid=result.get("importJobGuid"),
            gis_status=result.get("gisStatus"),
            gis_last_error=None,
        )
        self.refresh_work_lists(select_id=work_list_id)

    def _upload_current_work_list_to_gis(self) -> None:
        context = self._current_gis_context()
        if not context:
            return
        work_list, house = context

        prepared = self._build_xlsx_for_gis(work_list, house)
        if not prepared:
            return
        filename, encoded = prepared

        answer = QMessageBox.question(
            self,
            "Сохранить проект в ГИС ЖКХ?",
            f"Будет создан проект перечня {work_list.period_label}\n"
            f"для дома:\n{house.address}\n\n"
            "После обработки в ГИС он появится со статусом «Проект». "
            "Размещение будет отдельным следующим шагом.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        payload = self._gis_command_payload(work_list, house)
        payload.update({"fileName": filename, "fileBase64": encoded})

        def on_success(work_list_id: str, result: dict) -> None:
            self._apply_gis_result(work_list_id, result)
            if result.get("gisStatus") == "DRAFT":
                QMessageBox.information(
                    self,
                    "Проект сохранён",
                    "Перечень готов в ГИС ЖКХ как проект. Следующий шаг — размещение.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Проект обрабатывается",
                    "ГИС ЖКХ обрабатывает перечень. Через некоторое время "
                    "нажмите «Проверить готовность проекта».",
                )

        self._run_integration_command(
            work_list_id=work_list.id,
            command_type="upload_import_work_list",
            payload=payload,
            success_callback=on_success,
            busy_text="Загрузка в ГИС ЖКХ…",
            timeout=150.0,
        )

    def _load_house_work_lists_from_gis(self) -> None:
        self._save_period()

        organization_id = self.organization_combo.currentData()
        house_id = self.house_combo.currentData()
        if not organization_id or not house_id:
            QMessageBox.information(
                self,
                "Не выбран дом",
                "Сначала выберите дом, для которого нужно загрузить перечни.",
            )
            return

        house = self.repository.get_house(house_id)
        if house is None:
            return
        if not house.house_object_guid:
            QMessageBox.information(
                self,
                "Нет GUID дома",
                "Для загрузки перечней сначала загрузите сведения о МКД из ГИС ЖКХ.",
            )
            return

        if not self._directory_ready():
            QMessageBox.information(
                self,
                "Нет подключения к ГИС ЖКХ",
                "Откройте личный кабинет ГИС ЖКХ и убедитесь, что "
                "ГИС ЖКХ помощник подключен.",
            )
            return

        if self._integration_worker is not None and self._integration_worker.isRunning():
            QMessageBox.information(
                self,
                "Операция уже выполняется",
                "Дождитесь завершения текущей операции с ГИС ЖКХ.",
            )
            return

        self._set_integration_busy(True, "Загрузка перечней выбранного МКД…")
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "list_work_lists",
            {"houseObjectGuid": house.house_object_guid},
            timeout=120.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            remote_items = [
                item for item in list(result.get("items") or [])
                if isinstance(item, dict)
            ]
            preview = prepare_gis_work_list_sync(
                self.repository,
                str(organization_id),
                remote_items,
                target_house_id=str(house_id),
            )

            summary = (
                f"Дом: {house.address}\n\n"
                f"Получено из ГИС ЖКХ: {preview.received}\n"
                f"Новых перечней: {len(preview.new_rows)}\n"
                f"Уже связанных: {len(preview.linked_rows)}\n"
                f"Конфликтов: {len(preview.conflicts)}\n"
                f"Не сопоставлено: {len(preview.unmatched)}"
            )

            if not preview.new_rows and not preview.linked_rows:
                details = ""
                if preview.conflicts:
                    details = "\n\n" + "\n".join(preview.conflicts[:3])
                QMessageBox.information(
                    self,
                    "Перечни выбранного МКД",
                    summary + "\n\nИзменять локальную базу не требуется." + details,
                )
                return

            answer = QMessageBox.question(
                self,
                "Загрузить перечни выбранного МКД?",
                summary
                + "\n\nНовые перечни будут добавлены в локальную базу. "
                "Существующие локальные перечни автоматически не перезаписываются.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            applied = apply_gis_work_list_sync(self.repository, preview)
            # Synchronization updates the registry, keeping the user's chosen object.
            self.refresh_work_lists()

            result_text = (
                f"Добавлено перечней: {applied.imported}\n"
                f"Обновлено статусов: {applied.updated}\n"
                f"Конфликтов пропущено: {len(preview.conflicts)}"
            )
            if applied.errors:
                result_text += (
                    f"\nОшибок при сохранении: {len(applied.errors)}\n\n"
                    + "\n".join(applied.errors[:5])
                )
                QMessageBox.warning(
                    self,
                    "Загрузка завершена с замечаниями",
                    result_text,
                )
            else:
                QMessageBox.information(
                    self,
                    "Перечни выбранного МКД загружены",
                    result_text,
                )

        def failed(message: str) -> None:
            QMessageBox.critical(
                self,
                "Ошибка загрузки перечней выбранного МКД",
                message,
            )

        def finished() -> None:
            self._integration_worker = None
            self._set_integration_busy(False)
            self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _sync_current_work_list_from_gis(self) -> None:
        context = self._current_gis_context()
        if not context:
            return
        work_list, house = context
        payload = self._gis_command_payload(work_list, house)

        def on_success(work_list_id: str, result: dict) -> None:
            self._apply_gis_result(work_list_id, result)
            status = result.get("gisStatus")
            if status == "DRAFT":
                message = "Перечень найден в ГИС ЖКХ со статусом «Проект»."
            elif status == "APPROVED":
                message = "Перечень размещен в ГИС ЖКХ."
            elif status == "CANCELLED":
                message = "Перечень найден в ГИС ЖКХ со статусом «Отменён»."
            else:
                message = "Перечень с таким домом и периодом в ГИС ЖКХ не найден."
            QMessageBox.information(self, "Статус ГИС ЖКХ", message)

        self._run_integration_command(
            work_list_id=work_list.id,
            command_type="sync_work_list",
            payload=payload,
            success_callback=on_success,
            busy_text="Проверка статуса ГИС ЖКХ…",
            timeout=60.0,
        )

    def _approve_current_work_list_in_gis(self) -> None:
        context = self._current_gis_context()
        if not context:
            return
        work_list, house = context

        answer = QMessageBox.warning(
            self,
            "Разместить информацию?",
            f"Разместить перечень {work_list.period_label}\n"
            f"для дома:\n{house.address}\n\n"
            "В ГИС ЖКХ проект будет переведен в размещенное состояние.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        payload = self._gis_command_payload(work_list, house)

        def on_success(work_list_id: str, result: dict) -> None:
            self._apply_gis_result(work_list_id, result)
            QMessageBox.information(
                self,
                "Информация размещена",
                "Перечень размещен в ГИС ЖКХ.",
            )

        self._run_integration_command(
            work_list_id=work_list.id,
            command_type="approve_work_list",
            payload=payload,
            success_callback=on_success,
            busy_text="Размещение в ГИС ЖКХ…",
            timeout=90.0,
        )

    def _cancel_current_work_list_in_gis(self) -> None:
        context = self._current_gis_context()
        if not context:
            return
        work_list, house = context
        if str(work_list.gis_status or "").upper() != "APPROVED":
            QMessageBox.information(
                self,
                "Перечень не размещен",
                "Отменить можно только перечень в статусе «Размещен».",
            )
            return
        if not work_list.gis_work_list_guid:
            return

        message = QMessageBox(self)
        message.setWindowTitle("Отменить перечень в ГИС ЖКХ?")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(
            f"Будет отменён перечень {work_list.period_label} для дома:\n"
            f"{house.address}"
        )
        message.setInformativeText(
            "ГИС ЖКХ удалит связанные с этим перечнем планирование, фиксацию "
            "и оценку качества. После успешной отмены эти разделы будут очищены "
            "и в локальной базе программы. Сам перечень и его текущий состав "
            "останутся доступны для редактирования.\n\nПродолжить?"
        )
        cancel_button = message.addButton(
            "Отменить перечень",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        message.addButton("Назад", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is not cancel_button:
            return

        payload = self._gis_command_payload(work_list, house)
        payload["workListGuid"] = work_list.gis_work_list_guid

        def on_success(work_list_id: str, result: dict) -> None:
            self._apply_gis_result(work_list_id, result)
            if str(result.get("gisStatus") or "").upper() == "CANCELLED":
                self.repository.clear_work_list_downstream_data(work_list_id)
            self.refresh_work_lists(select_id=work_list_id)
            QMessageBox.information(
                self,
                "Перечень отменён",
                "Перечень отменён в ГИС ЖКХ. Планирование, фиксация и оценка "
                "для него очищены. Теперь можно удалить старые работы, добавить "
                "актуальные и сохранить изменённый перечень как проект.",
            )

        self._run_integration_command(
            work_list_id=work_list.id,
            command_type="cancel_work_list",
            payload=payload,
            success_callback=on_success,
            busy_text="Отмена перечня в ГИС ЖКХ…",
            timeout=90.0,
        )

    def _save_editable_work_list_to_gis(self) -> None:
        context = self._current_gis_context()
        if not context:
            return
        work_list, house = context
        status = str(work_list.gis_status or "").upper()
        if status not in {"CANCELLED", "DRAFT"}:
            QMessageBox.information(
                self,
                "Перечень недоступен для изменения",
                "Прямое сохранение состава доступно для отменённого перечня "
                "или проекта ГИС ЖКХ.",
            )
            return
        if not work_list.gis_work_list_guid:
            return

        items = self.repository.list_work_list_items(work_list.id)
        if not items:
            QMessageBox.warning(
                self,
                "Пустой состав",
                "Сначала добавьте актуальные работы и услуги. Пустой перечень "
                "в ГИС ЖКХ не сохраняется.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Сохранить изменения в ГИС ЖКХ?",
            f"В ГИС ЖКХ будет сохранён текущий состав перечня "
            f"{work_list.period_label}.\n\n"
            f"Работ и услуг: {len(items)}.\n"
            "Каждая строка будет проверена по актуальному справочнику организации.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        payload = self._gis_command_payload(work_list, house)
        payload["workListGuid"] = work_list.gis_work_list_guid
        payload["items"] = [
            {
                "workGuid": item.work_guid,
                "workCode": item.work_code,
                "workName": item.work_name,
                "price": item.price,
                "volume": item.volume,
                "quantity": item.quantity,
                "cost": item.cost,
            }
            for item in items
        ]

        def on_success(work_list_id: str, result: dict) -> None:
            self._apply_gis_result(work_list_id, result)
            self.repository.clear_work_list_downstream_data(work_list_id)
            self.refresh_work_lists(select_id=work_list_id)
            QMessageBox.information(
                self,
                "Изменения сохранены",
                "Новый состав сохранён в ГИС ЖКХ как проект. После проверки "
                "значений его можно разместить кнопкой «Разместить информацию».",
            )

        self._run_integration_command(
            work_list_id=work_list.id,
            command_type="save_editable_work_list",
            payload=payload,
            success_callback=on_success,
            busy_text="Сохранение изменённого перечня в ГИС ЖКХ…",
            timeout=120.0,
        )

    def _clear_all_works(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return
        items = self.repository.list_work_list_items(work_list.id)
        if not items:
            return

        answer = QMessageBox.warning(
            self,
            "Очистить состав перечня?",
            f"Из локального перечня будут удалены все работы и услуги: "
            f"{len(items)}.\n\n"
            "Это удобно перед полной актуализацией старого перечня. "
            "Изменения попадут в ГИС ЖКХ только после нажатия "
            "«Сохранить изменения в ГИС ЖКХ».",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self.repository.clear_work_list_downstream_data(work_list.id)
            deleted = self.repository.clear_work_list_items(work_list.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Нельзя очистить состав", str(exc))
            return
        self._load_work_list(work_list.id)
        self.save_status.setText(f"Удалено работ: {deleted}")

    def _replace_current_from_template(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return

        template_id = self.template_combo.currentData()
        if not template_id:
            QMessageBox.information(
                self,
                "Не выбран шаблон",
                "В поле «Шаблон перечня» выберите шаблон, которым нужно "
                "полностью заменить текущий состав.",
            )
            return

        template = self.repository.get_work_template(template_id)
        template_items = self.repository.list_work_template_items(template_id)
        if template is None or not template_items:
            QMessageBox.warning(
                self,
                "Пустой шаблон",
                "В выбранном шаблоне нет работ и услуг.",
            )
            return

        def replace_from_directory(options: list[dict]) -> None:
            by_guid = {
                str(option.get("guid") or ""): option
                for option in options
                if option.get("guid")
            }
            by_code = {
                str(option.get("code") or ""): option
                for option in options
                if option.get("code")
            }
            matched = []
            missing = []
            for item in template_items:
                option = (
                    by_guid.get(str(item.work_guid or ""))
                    or by_code.get(str(item.work_code or ""))
                )
                if option is None:
                    missing.append(item)
                    continue
                matched.append(
                    self._directory_option_item(
                        option,
                        position=len(matched) + 1,
                        template_item=item,
                    )
                )

            if not matched:
                QMessageBox.warning(
                    self,
                    "Работы шаблона недоступны",
                    "Ни одна работа выбранного шаблона не найдена в актуальном "
                    "справочнике этой управляющей организации.",
                )
                return

            details = (
                f"Текущий состав будет полностью заменён шаблоном "
                f"«{template.name}».\n"
                f"Доступно актуальных работ: {len(matched)} из "
                f"{len(template_items)}."
            )
            if missing:
                names = "\n".join(
                    f"• {item.work_name}" for item in missing[:8]
                )
                if len(missing) > 8:
                    names += f"\n… ещё: {len(missing) - 8}"
                details += (
                    "\n\nРаботы, отсутствующие в актуальном справочнике, "
                    "не будут перенесены:\n" + names
                )

            answer = QMessageBox.warning(
                self,
                "Заменить состав перечня?",
                details + "\n\nПродолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            try:
                self.repository.clear_work_list_downstream_data(work_list.id)
                added = self.repository.replace_work_list_items(
                    work_list.id,
                    matched,
                    template_id=template_id,
                )
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    "Не удалось заменить состав",
                    str(exc),
                )
                return
            self._load_work_list(work_list.id)
            self.save_status.setText(f"Состав заменён: {added} работ")

        self._load_organization_directory(
            replace_from_directory,
            busy_text="Сопоставление шаблона с актуальным справочником…",
        )

    def _add_works_from_directory(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return
        if str(work_list.gis_status or "").upper() not in {
            "",
            "NOT_FOUND",
            "CANCELLED",
            "DRAFT",
        }:
            QMessageBox.information(
                self,
                "Состав сейчас недоступен для изменения",
                "Добавление и удаление работ доступно у локального, отменённого "
                "перечня либо проекта ГИС ЖКХ.",
            )
            return

        existing = self.repository.list_work_list_items(work_list.id)
        guids = {str(item.work_guid) for item in existing if item.work_guid}
        codes = {str(item.work_code) for item in existing if item.work_code}

        def show_directory(options: list[dict]) -> None:
            dialog = WorkDirectorySelectionDialog(
                options,
                existing_guids=guids,
                existing_codes=codes,
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected = dialog.selected_options()
            if not selected:
                return
            rows = [
                self._directory_option_item(option, position=index)
                for index, option in enumerate(selected, start=1)
            ]
            try:
                added = self.repository.add_work_list_items(work_list.id, rows)
            except ValueError as exc:
                QMessageBox.warning(self, "Не удалось добавить работы", str(exc))
                return
            self._load_items(work_list.id)
            self.save_status.setText(
                f"Добавлено работ: {added}" if added else "Новых работ нет"
            )

        self._load_organization_directory(
            show_directory,
            busy_text="Получение справочника работ…",
        )

    def _remove_selected_works(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return
        selected_rows = sorted(
            {item.row() for item in self.items_table.selectedItems()}
        )
        if not selected_rows:
            QMessageBox.information(
                self,
                "Не выбраны работы",
                "Выделите одну или несколько строк таблицы.",
            )
            return

        item_ids = []
        for row in selected_rows:
            item = self.items_table.item(row, 0)
            if not item:
                continue
            item_id = item.data(Qt.ItemDataRole.UserRole)
            if item_id:
                item_ids.append(str(item_id))

        if not item_ids:
            return
        answer = QMessageBox.question(
            self,
            "Удалить выбранные работы?",
            f"Из перечня будут удалены строки: {len(item_ids)}. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repository.delete_work_list_items(
                self._current_work_list_id,
                item_ids,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось удалить работы", str(exc))
            return
        self._load_items(self._current_work_list_id)
        self.save_status.setText("Сохранено")

    def _save_current_as_template(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return
        items = self.repository.list_work_list_items(self._current_work_list_id)
        if not items:
            QMessageBox.information(
                self,
                "Пустой перечень",
                "Сначала добавьте работы из справочника ГИС ЖКХ.",
            )
            return

        name, ok = QInputDialog.getText(
            self,
            "Сохранить состав как шаблон",
            "Название шаблона:",
        )
        if not ok or not name.strip():
            return
        try:
            template = self.repository.create_work_template_from_work_list(
                work_list_id=self._current_work_list_id,
                name=name.strip(),
            )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Название уже используется",
                "У этой управляющей организации уже есть шаблон с таким названием.",
            )
            return
        except sqlite3.Error as exc:
            QMessageBox.critical(
                self,
                "Не удалось сохранить шаблон",
                f"Ошибка локальной базы данных: {exc}",
            )
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось сохранить шаблон", str(exc))
            return

        self._refresh_templates()
        index = self.template_combo.findData(template.id)
        if index >= 0:
            self.template_combo.setCurrentIndex(index)
        QMessageBox.information(
            self,
            "Шаблон сохранён",
            f"Состав перечня сохранён как шаблон «{template.name}».",
        )

    def _open_lazy_distribution(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return

        items = self.repository.list_work_list_items(work_list.id)
        if not items:
            QMessageBox.information(
                self,
                "Нет позиций для распределения",
                "В выбранном перечне нет работ и услуг. "
                "Нажмите «Добавить работы…» и выберите позиции из справочника ГИС ЖКХ.",
            )
            return

        dialog = LazyDistributionDialog(self.repository, work_list, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        refreshed = self.repository.get_work_list(work_list.id)
        if refreshed:
            self.planned_total_label.setText(
                f"{refreshed.planned_total} руб." if refreshed.planned_total else "—"
            )
        self._load_items(work_list.id)
        self.save_status.setText("Сохранено")

    def _manage_templates(self) -> None:
        self._save_period()
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            QMessageBox.information(
                self,
                "Не выбрана УО",
                "Сначала выберите управляющую организацию.",
            )
            return
        dialog = WorkTemplatesDialog(self.repository, organization_id, self)
        dialog.exec()
        self._refresh_templates()
        self.refresh_work_lists(select_id=self._current_work_list_id)

    def _delete_work_list(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return

        answer = QMessageBox.question(
            self,
            "Удалить перечень?",
            f"Удалить перечень {work_list.period_label} из локальной базы?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.repository.delete_work_list(work_list.id)
        self._current_work_list_id = None
        self.refresh_work_lists()
        self.contextSelectionChanged.emit()

    def _set_editor_enabled(self, enabled: bool) -> None:
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if enabled and self._current_work_list_id
            else None
        )
        composition_editable = bool(
            work_list
            and str(work_list.gis_status or "").upper()
            in {"", "NOT_FOUND", "CANCELLED", "DRAFT"}
        )
        for widget in (
            self.delete_button,
            self.create_from_current_button,
            self.export_button,
            self.save_as_template_button,
        ):
            widget.setEnabled(enabled)

        for widget in (
            self.start_month,
            self.start_year,
            self.end_month,
            self.end_year,
            self.items_table,
            self.lazy_fill_button,
        ):
            widget.setEnabled(enabled and composition_editable)

        self.add_works_button.setEnabled(enabled and composition_editable)
        self.remove_works_button.setEnabled(enabled and composition_editable)
        self.clear_works_button.setEnabled(enabled and composition_editable)
        self.replace_from_template_button.setEnabled(
            enabled and composition_editable
        )
        self._refresh_gis_action_buttons()

    def set_view_mode(self, mode: str) -> None:
        """Switch between normal, compact and narrow responsive layouts."""

        if mode not in {"normal", "compact", "narrow"}:
            mode = "normal"
        if mode == self._view_mode:
            return
        self._apply_view_mode(mode)

    def _apply_view_mode(self, mode: str) -> None:
        self._view_mode = mode
        margins = (18, 14, 18, 18) if mode == "normal" else (12, 10, 12, 12)
        self.page_layout.setContentsMargins(*margins)
        self.page_layout.setSpacing(10 if mode == "normal" else 8)
        self.items_table.setMinimumHeight(300 if mode == "normal" else 260)

    def set_compact_mode(self, compact: bool) -> None:
        """Compatibility shim for older callers."""

        self.set_view_mode("compact" if compact else "normal")

    def flush_pending_changes(self) -> None:
        self._save_period()
