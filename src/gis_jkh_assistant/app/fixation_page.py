"""Monthly work/service fixation page."""

from __future__ import annotations

from .error_messages import context_text, error_text
from .queue_controls import preview as queue_preview

import sqlite3

import mimetypes

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.models import WorkFixationPeriod
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
from gis_jkh_assistant.work_lists.gis_fixation_sync import (
    apply_remote_work_fixation,
    local_fixation_has_data,
)
from gis_jkh_assistant.work_fixation import (
    build_fixation_command_payload,
    calculate_fact_total_cost,
    helper_supports_fixations,
    item_has_fact,
    fact_validation_error,
    validate_fact_numbers,
)
from gis_jkh_assistant.work_planning import active_months_for_work_list

from .fixation_dialogs import AddUnplannedWorkDialog, FixationPhotoDialog
from .gis_integration_worker import GisIntegrationWorker, connect_result
from .page_layouts import build_monthly, compact_monthly, resize_table_rows, TableHeightObserver
from .workflow_presenter import refresh_monthly
from .styles import color
from .work_row_rules import WorkRowMode, infer_work_row_mode
from .workflow_widgets import style_danger_button


_MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
_ITEM_ID_ROLE = Qt.ItemDataRole.UserRole
_ROW_MODE_ROLE = Qt.ItemDataRole.UserRole + 1


class FixationPage(QWidget):
    """Create factual monthly indicators and publish them sequentially to ГИС ЖКХ."""

    def __init__(
        self,
        repository: Repository,
        integration_gateway: LocalIntegrationGateway,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.integration_gateway = integration_gateway
        self._loading = False
        self._integration_worker: GisIntegrationWorker | None = None
        self._integration_busy = False
        self._current_work_list_id: str | None = None
        self._current_period_id: str | None = None
        self._current_month: int | None = None
        self._month_button_columns = 12
        self._queue_period_ids: list[str] = []
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_skipped = 0
        self._queue_failed_period_id: str | None = None
        self._queue_mode = "publish"
        self._queue_running = False

        self._build_ui()
        from .local_history import FactHistory
        self.fact_history = FactHistory(self)
        self.refresh_organizations()
        self._refresh_integration_status()

    def _build_ui(self) -> None:
        build_monthly(self, "fixation")
        self._height_observer = TableHeightObserver(self.table, self._resize_planned_table)
        self._unplanned_height_observer = TableHeightObserver(self.unplanned_table, self._resize_unplanned_table)

    def refresh_organizations(self) -> None:
        current = self.organization_combo.currentData()
        self.organization_combo.blockSignals(True)
        self.organization_combo.clear()
        for organization in self.repository.list_organizations():
            self.organization_combo.addItem(organization.name, organization.id)
        if current:
            index = self.organization_combo.findData(current)
            if index >= 0:
                self.organization_combo.setCurrentIndex(index)
        self.organization_combo.blockSignals(False)
        self._organization_changed()

    def _organization_changed(self) -> None:
        organization_id = self.organization_combo.currentData()
        current = self.house_combo.currentData()
        self.house_combo.blockSignals(True)
        self.house_combo.clear()
        if organization_id:
            for house in self.repository.list_houses(str(organization_id)):
                self.house_combo.addItem(house.address, house.id)
        if current:
            index = self.house_combo.findData(current)
            if index >= 0:
                self.house_combo.setCurrentIndex(index)
        self.house_combo.blockSignals(False)
        self._house_changed()

    def _house_changed(self) -> None:
        house_id = self.house_combo.currentData()
        current = self.work_list_combo.currentData()
        self.work_list_combo.blockSignals(True)
        self.work_list_combo.clear()
        if house_id:
            for work_list in self.repository.list_placed_work_lists(str(house_id)):
                self.work_list_combo.addItem(
                    f"{work_list.period_label} • размещен",
                    work_list.id,
                )
        if current:
            index = self.work_list_combo.findData(current)
            if index >= 0:
                self.work_list_combo.setCurrentIndex(index)
        self.work_list_combo.blockSignals(False)

        has_lists = self.work_list_combo.count() > 0
        self.empty_hint.setVisible(not has_lists)
        if has_lists and self.work_list_combo.currentIndex() < 0:
            self.work_list_combo.setCurrentIndex(0)
        self._work_list_changed()

    def _work_list_changed(self) -> None:
        from .selection_memory import rebuild_years
        rebuild_years(self, self.work_list_combo.currentData())
        self._current_period_id = None
        self._year_changed()

    def _year_changed(self) -> None:
        from .selection_memory import select_year
        select_year(self)
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if self._current_work_list_id
            else None
        )
        year = self.year_combo.currentData()
        active_months = (
            active_months_for_work_list(work_list, int(year))
            if work_list and year is not None
            else ()
        )

        for month, button in self.month_buttons.items():
            button.setEnabled(month in active_months)

        if self._current_month not in active_months:
            self._current_month = active_months[0] if active_months else None

        if self._current_month is None:
            self._current_period_id = None
            self.table.setRowCount(0)
            self.unplanned_table.setRowCount(0)
            self._resize_planned_table()
            self._resize_unplanned_table()
            self.month_status.setText("Период: —")
            self._set_enabled(False)
            self._refresh_year_status()
            return

        self._month_changed()
        self._refresh_year_status()

    def _select_month(self, month: int) -> None:
        button = self.month_buttons.get(int(month))
        if button is None or not button.isEnabled():
            return
        self._current_month = int(month)
        self._month_changed()

    def _month_changed(self) -> None:
        if (
            not self._current_work_list_id
            or self.year_combo.currentData() is None
            or self._current_month is None
        ):
            self._current_period_id = None
            self.table.setRowCount(0)
            self.unplanned_table.setRowCount(0)
            self._resize_planned_table()
            self._resize_unplanned_table()
            self.month_status.setText("Период: —")
            self._set_enabled(False)
            self._refresh_year_status()
            return

        month = int(self._current_month)
        try:
            period = self.repository.get_or_create_work_fixation_period(
                work_list_id=self._current_work_list_id,
                year=int(self.year_combo.currentData()),
                month=month,
            )
            self.repository.refresh_work_fixation_plan_snapshot(period.id)
        except (sqlite3.DatabaseError, ValueError) as exc:
            self._current_period_id = None
            self.table.setRowCount(0)
            self.unplanned_table.setRowCount(0)
            self._resize_planned_table()
            self._resize_unplanned_table()
            self.month_status.setText(str(exc))
            self._set_enabled(False)
            self._refresh_year_status()
            return

        self._current_period_id = period.id
        self._set_enabled(True)
        self._load_table()
        self._load_unplanned_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _relayout_month_buttons(self, columns: int | None = None) -> None:
        if columns is not None:
            self._month_button_columns = max(1, int(columns))
        while self.month_buttons_layout.count():
            self.month_buttons_layout.takeAt(0)
        for column in range(12):
            self.month_buttons_layout.setColumnStretch(column, 0)
        for index, month in enumerate(range(1, 13)):
            row, column = divmod(index, self._month_button_columns)
            self.month_buttons_layout.addWidget(
                self.month_buttons[month],
                row,
                column,
            )
            self.month_buttons_layout.setColumnStretch(column, 1)

    def _resize_table_to_rows(
        self,
        table: QTableWidget,
        *,
        max_rows: int,
        minimum_rows: int = 1,
    ) -> None:
        resize_table_rows(table, max_rows)

    def _resize_planned_table(self) -> None:
        self._resize_table_to_rows(self.table, max_rows=15)

    def _resize_unplanned_table(self) -> None:
        self._resize_table_to_rows(self.unplanned_table, max_rows=8)


    def _load_table(self) -> None:
        if not self._current_period_id:
            self.table.setRowCount(0)
            self._resize_planned_table()
            return

        items = self.repository.list_work_fixation_items(self._current_period_id)
        list_items = {
            item.id: item
            for item in self.repository.list_work_list_items(self._current_work_list_id)
        } if self._current_work_list_id else {}
        self._loading = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        try:
            for row, item in enumerate(items):
                values = (
                    item.work_name,
                    item.fact_price,
                    item.fact_amount,
                    str(item.fact_count) if item.fact_count is not None else "",
                    item.fact_total_cost,
                    item.planned_price,
                    item.planned_amount,
                    str(item.planned_count) if item.planned_count else "",
                    item.planned_total_cost,
                    "заполнено" if item_has_fact(item) else "—",
                )
                for column, value in enumerate(values[:9]):
                    cell = QTableWidgetItem(value or "")
                    if column == 0:
                        cell.setToolTip(item.work_name)
                        cell.setData(_ITEM_ID_ROLE, item.id)
                        source = list_items.get(item.work_list_item_id)
                        cell.setData(_ROW_MODE_ROLE, str(infer_work_row_mode(
                            price=source.price if source else item.planned_price,
                            volume=source.volume if source else item.planned_amount,
                            cost=source.cost if source else item.planned_total_cost,
                        )))
                    if column not in (1, 2, 3, 4):
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if column in (5, 6, 7, 8):
                        cell.setBackground(QBrush(QColor(color("readonly"))))
                        cell.setToolTip("Плановый показатель для сравнения. Изменяется в перечне или планировании.")
                    self.table.setItem(row, column, cell)

                photos = self.repository.list_work_fixation_photos(
                    fixation_item_id=item.id
                )
                photo_button = QPushButton(
                    f"Фото ({len(photos)})" if photos else "Фото"
                )
                photo_button.clicked.connect(
                    lambda _checked=False, item_id=item.id: self._open_planned_photos(item_id)
                )
                self.table.setCellWidget(row, 9, photo_button)

                error = fact_validation_error(item)
                status = QTableWidgetItem("Ошибка данных" if error else values[9] or "")
                status.setToolTip(error or "")
                status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if item_has_fact(item) and not error:
                    status.setBackground(QBrush(QColor(color("success_surface"))))
                self.table.setItem(row, 10, status)
                self._apply_planned_row_mode(row)
        finally:
            self.table.blockSignals(False)
            self._loading = False
        self._resize_planned_table()

    def _show_plan_reference(self, visible: bool) -> None:
        for column in (5, 6, 7, 8):
            self.table.setColumnHidden(column, not visible)
        self.table.name_column_sizer.resize_name()

    def _planned_row_mode(self, row: int) -> WorkRowMode:
        name = self.table.item(row, 0)
        source_mode = name.data(_ROW_MODE_ROLE) if name else None
        if source_mode and source_mode != WorkRowMode.EMPTY:
            return WorkRowMode(source_mode)
        return infer_work_row_mode(
            price=self.table.item(row, 1).text(),
            volume=self.table.item(row, 2).text(),
            cost=self.table.item(row, 4).text(),
        )

    def _apply_planned_row_mode(self, row: int) -> None:
        """Keep the input scheme of the placed list; preserve existing saved values."""
        mode = self._planned_row_mode(row)
        editable_columns = ({1, 2, 3} if mode == WorkRowMode.COMPONENTS
                            else {3, 4} if mode == WorkRowMode.TOTAL else {1, 2, 3, 4})
        was_blocked = self.table.blockSignals(True)
        try:
            for column in (1, 2, 3, 4):
                cell = self.table.item(row, column)
                editable = column in editable_columns
                flags = cell.flags()
                cell.setFlags(flags | Qt.ItemFlag.ItemIsEditable if editable
                              else flags & ~Qt.ItemFlag.ItemIsEditable)
                cell.setBackground(QBrush() if editable else QBrush(QColor(color("readonly"))))
                cell.setToolTip("" if editable else (
                    "В перечне выбран вариант «Количество + Стоимость»: цена и объём не используются."
                    if mode == WorkRowMode.TOTAL else
                    "Стоимость рассчитывается автоматически: цена × объём × количество."
                ))
        finally:
            self.table.blockSignals(was_blocked)

    def _load_unplanned_table(self) -> None:
        if not self._current_period_id:
            self.unplanned_table.setRowCount(0)
            self._resize_unplanned_table()
            return
        items = self.repository.list_work_fixation_unplanned_items(
            self._current_period_id
        )
        self._loading = True
        self.unplanned_table.blockSignals(True)
        self.unplanned_table.setRowCount(len(items))
        try:
            for row, item in enumerate(items):
                values = (
                    item.work_name,
                    item.work_type_name or "—",
                    item.unit_name or "—",
                    item.fact_price,
                    item.fact_amount,
                    str(item.fact_count) if item.fact_count is not None else "",
                    item.fact_total_cost,
                    item.comment,
                )
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value or "")
                    if column == 0:
                        cell.setData(_ITEM_ID_ROLE, item.id)
                    if column in (0, 1, 2):
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.unplanned_table.setItem(row, column, cell)

                photos = self.repository.list_work_fixation_photos(
                    unplanned_item_id=item.id
                )
                photo_button = QPushButton(
                    f"Фото ({len(photos)})" if photos else "Фото"
                )
                photo_button.clicked.connect(
                    lambda _checked=False, item_id=item.id: self._open_unplanned_photos(item_id)
                )
                self.unplanned_table.setCellWidget(row, 8, photo_button)

                remove_button = QPushButton("Удалить")
                style_danger_button(remove_button)
                remove_button.clicked.connect(
                    lambda _checked=False, item_id=item.id: self._delete_unplanned(item_id)
                )
                self.unplanned_table.setCellWidget(row, 9, remove_button)
                self._apply_unplanned_row_mode(row)
        finally:
            self.unplanned_table.blockSignals(False)
            self._loading = False
        self._resize_unplanned_table()

    def _apply_unplanned_row_mode(self, row: int) -> None:
        price_cell = self.unplanned_table.item(row, 3)
        amount_cell = self.unplanned_table.item(row, 4)
        cost_cell = self.unplanned_table.item(row, 6)
        if not price_cell or not amount_cell or not cost_cell:
            return

        price = price_cell.text().strip()
        amount = amount_cell.text().strip()
        cost = cost_cell.text().strip()
        detailed_mode = bool(price or amount)
        total_mode = bool(cost) and not detailed_mode

        editable = Qt.ItemFlag.ItemIsEditable
        readonly_background = QBrush(QColor(color("readonly")))
        normal_background = QBrush()

        was_blocked = self.unplanned_table.blockSignals(True)
        try:
            for cell in (price_cell, amount_cell):
                if total_mode:
                    cell.setFlags(cell.flags() & ~editable)
                    cell.setBackground(readonly_background)
                else:
                    cell.setFlags(cell.flags() | editable)
                    cell.setBackground(normal_background)

            if detailed_mode:
                cost_cell.setFlags(cost_cell.flags() & ~editable)
                cost_cell.setBackground(readonly_background)
            else:
                cost_cell.setFlags(cost_cell.flags() | editable)
                cost_cell.setBackground(normal_background)
        finally:
            self.unplanned_table.blockSignals(was_blocked)

    def _open_planned_photos(self, item_id: str) -> None:
        work_name = next((self.table.item(row, 0).text() for row in range(self.table.rowCount())
                          if self.table.item(row, 0) and self.table.item(row, 0).data(_ITEM_ID_ROLE) == item_id), "")
        dialog = FixationPhotoDialog(
            self.repository,
            fixation_item_id=item_id,
            work_name=work_name,
            parent=self,
        )
        dialog.exec()
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _open_unplanned_photos(self, item_id: str) -> None:
        work_name = next((self.unplanned_table.item(row, 0).text() for row in range(self.unplanned_table.rowCount())
                          if self.unplanned_table.item(row, 0) and self.unplanned_table.item(row, 0).data(_ITEM_ID_ROLE) == item_id), "")
        dialog = FixationPhotoDialog(
            self.repository,
            unplanned_item_id=item_id,
            work_name=work_name,
            parent=self,
        )
        dialog.exec()
        self._load_unplanned_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _delete_unplanned(self, item_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "Удалить внеплановую работу?",
            "Работа будет удалена из локальной фиксации. Если она уже была размещена "
            "через приложение, при следующем размещении месяца она будет удалена и из ГИС ЖКХ.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.delete_work_fixation_unplanned_item(item_id)
        self._load_unplanned_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _unplanned_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.column() not in (3, 4, 5, 6, 7):
            return
        row = cell.row()
        name_cell = self.unplanned_table.item(row, 0)
        item_id = name_cell.data(_ITEM_ID_ROLE) if name_cell else None
        if not item_id:
            return

        def text_at(column: int) -> str | None:
            item = self.unplanned_table.item(row, column)
            value = item.text().strip() if item else ""
            return value or None

        count_text = text_at(5)
        try:
            fact_count = int(count_text) if count_text else None
            if fact_count is not None and fact_count < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self,
                "Проверьте количество",
                "Количество должно быть целым числом не меньше нуля.",
            )
            self._load_unplanned_table()
            return

        price = text_at(3)
        amount = text_at(4)
        cost = text_at(6)
        try:
            validate_fact_numbers(price, amount, fact_count, cost)
        except ValueError as exc:
            work_name = name_cell.text()
            self._load_unplanned_table()
            QMessageBox.warning(self, "Проверьте числовые показатели", f"{work_name}\n{exc}\nПрежние значения сохранены.")
            return
        if price or amount:
            calculated = calculate_fact_total_cost(price, amount, fact_count)
            if calculated is None:
                cost = None
            else:
                cost = calculated
            cost_cell = self.unplanned_table.item(row, 6)
            if cost_cell is not None:
                self.unplanned_table.blockSignals(True)
                try:
                    cost_cell.setText(cost or "")
                finally:
                    self.unplanned_table.blockSignals(False)

        try:
            self.repository.update_work_fixation_unplanned_item(
                str(item_id),
                fact_price=price,
                fact_amount=amount,
                fact_count=fact_count,
                fact_total_cost=cost,
                comment=text_at(7),
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось сохранить", error_text(exc, context=context_text(self)))
            self._load_unplanned_table()
            return

        self._apply_unplanned_row_mode(row)
        self._refresh_period_status()
        self._refresh_year_status()

    def _request_add_unplanned(self) -> None:
        if not self._current_period_id:
            return
        status = self.integration_gateway.status()
        if not (
            status.running
            and status.helper_connected
            and status.gis_page_ready
            and helper_supports_fixations(status.helper_version)
        ):
            QMessageBox.information(
                self,
                "Нет подключения к ГИС ЖКХ",
                "Для выбора внеплановой работы нужен подключенный браузерный помощник 0.1.9+.",
            )
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        planned = self.repository.list_work_fixation_items(self._current_period_id)
        payload = {
            "plannedWorkGuids": [item.work_guid for item in planned if item.work_guid],
            "plannedWorkCodes": [item.work_code for item in planned if item.work_code],
        }
        self._integration_busy = True
        self._refresh_gis_buttons()
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "list_unplanned_work_options",
            payload,
            timeout=90.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            options = list(result.get("items") or [])
            if not options:
                QMessageBox.information(
                    self,
                    "Нет доступных работ",
                    "В справочнике ГИС ЖКХ нет работ, которые можно добавить как внеплановые.",
                )
                return
            dialog = AddUnplannedWorkDialog(options, parent=self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            try:
                added = self.repository.add_work_fixation_unplanned_item(
                    self._current_period_id,
                    **dialog.values(),
                )
                for path in dialog.photo_paths():
                    content_type = (
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream"
                    )
                    self.repository.add_work_fixation_photo(
                        unplanned_item_id=added.id,
                        file_name=path.name,
                        description=path.name,
                        content_type=content_type,
                        content=path.read_bytes(),
                    )
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Не удалось добавить работу", error_text(exc, context=context_text(self)))
                return
            self._load_unplanned_table()
            self._refresh_period_status()
            self._refresh_year_status()

        def failed(message: str) -> None:
            QMessageBox.critical(self, "Не удалось получить справочник работ", error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."))

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_integration_status()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.start()

    def _table_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.column() not in (1, 2, 3, 4):
            return
        if not cell.flags() & Qt.ItemFlag.ItemIsEditable:
            self._load_table()
            return
        name_cell = self.table.item(cell.row(), 0)
        item_id = name_cell.data(_ITEM_ID_ROLE) if name_cell else None
        if not item_id:
            return

        count_text = self.table.item(cell.row(), 3).text().strip()
        try:
            fact_count = int(count_text) if count_text else None
            if fact_count is not None and fact_count < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self,
                "Проверьте количество",
                "Фактическое количество должно быть целым числом не меньше нуля.",
            )
            self._load_table()
            return

        def text_at(column: int) -> str | None:
            value = self.table.item(cell.row(), column).text().strip()
            return value or None

        cost = text_at(4)
        price, amount = text_at(1), text_at(2)
        try:
            validate_fact_numbers(price, amount, fact_count, cost)
        except ValueError as exc:
            work_name, row, column = name_cell.text(), cell.row(), cell.column()
            self._load_table()
            self.table.setCurrentCell(row, column)
            blocked = self.table.blockSignals(True)
            self.table.item(row, column).setToolTip(str(exc))
            self.table.blockSignals(blocked)
            QMessageBox.warning(self, "Проверьте числовые показатели", f"{work_name}\n{exc}\nПрежние значения сохранены.")
            return
        previous = next(item for item in self.repository.list_work_fixation_items(self._current_period_id)
                        if item.id == str(item_id))
        mode = self._planned_row_mode(cell.row())
        if mode == WorkRowMode.COMPONENTS:
            cost = calculate_fact_total_cost(price, amount, fact_count)
            was_blocked = self.table.blockSignals(True)
            try:
                self.table.item(cell.row(), 4).setText(cost or "")
            finally:
                self.table.blockSignals(was_blocked)
        elif mode == WorkRowMode.TOTAL:
            # A deliberate edit repairs incompatible values left by older UI.
            # Opening the page still preserves every historical field.
            price = amount = None
            was_blocked = self.table.blockSignals(True)
            try:
                self.table.item(cell.row(), 1).setText("")
                self.table.item(cell.row(), 2).setText("")
            finally:
                self.table.blockSignals(was_blocked)
        try:
            self.repository.update_work_fixation_item(
                str(item_id),
                fact_price=price,
                fact_amount=amount,
                fact_count=fact_count,
                fact_total_cost=cost,
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось сохранить", error_text(exc, context=context_text(self)))
            self._load_table()
            return

        status = self.table.item(cell.row(), 10)
        self.fact_history.record(previous, (price, amount, fact_count, cost))
        if status:
            filled = any((text_at(1), text_at(2), fact_count is not None, cost))
            status.setText("заполнено" if filled else "—")
            status.setBackground(QBrush(QColor(color("success_surface"))) if filled else QBrush())
        self._apply_planned_row_mode(cell.row())
        self._refresh_period_status()
        self._refresh_year_status()

    def _scope_is_period(self) -> bool:
        return self.scope_switcher.scope() == "period"

    def _validation_error(self, period_id: str) -> str | None:
        items = self.repository.list_work_fixation_items(period_id)
        items += self.repository.list_work_fixation_unplanned_items(period_id)
        return next((error for item in items if (error := fact_validation_error(item))), None)

    def _scope_changed(self, _scope: str) -> None:
        self._set_enabled(bool(self._current_period_id))

    def _fill_scope_from_plan(self) -> None:
        if self._scope_is_period():
            self._fill_year_from_plan()
        else:
            self._fill_month_from_plan()

    def _load_scope_from_gis(self) -> None:
        if self._scope_is_period():
            self._load_year_from_gis()
        else:
            self._load_selected_month_from_gis()

    def _publish_scope(self) -> None:
        if self._scope_is_period():
            self._publish_year()
        else:
            self._publish_selected_month()

    def _clear_scope(self) -> None:
        if not self._scope_is_period():
            self._clear_month()
            return
        self._clear_period()

    def _clear_period(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return
        year = int(self.year_combo.currentData())
        periods = {
            period.month: period
            for period in self.repository.list_work_fixation_periods(
                self._current_work_list_id,
                year=year,
            )
        }
        targets = [
            periods[month]
            for month in active_months_for_work_list(work_list, year)
            if month in periods and self._period_has_data(periods[month].id)
        ]
        if not targets:
            QMessageBox.information(
                self,
                "Нет данных для очистки",
                "В доступном периоде нет заполненных месяцев.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Очистить фактические показатели периода?",
            f"Будут очищены фактические показатели плановых работ в "
            f"{len(targets)} заполненных месяцах. Внеплановые работы и фотографии "
            "останутся без изменений. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for period in targets:
            self.repository.clear_work_fixation_period(period.id)
        self._month_changed()
        self._refresh_year_status()

    def _fill_month_from_plan(self) -> None:
        if not self._current_period_id:
            return
        if any(item_has_fact(item) for item in
               self.repository.list_work_fixation_items(self._current_period_id)):
            answer = QMessageBox.question(
                self, "Заменить фактические показатели месяца?",
                "В этом месяце уже есть фактические показатели. "
                "Они будут заменены значениями плана. "
                "Внеплановые работы и фотографии останутся без изменений. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._copy_plan_to_fact(self._current_period_id)
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _copy_plan_to_fact(self, period_id: str) -> None:
        self.repository.fill_work_fixation_from_plan(period_id)
        for item in self.repository.list_work_fixation_items(period_id):
            if item.fact_price or item.fact_amount:
                cost = calculate_fact_total_cost(item.fact_price, item.fact_amount, item.fact_count)
                self.repository.update_work_fixation_item(
                    item.id, fact_price=item.fact_price, fact_amount=item.fact_amount,
                    fact_count=item.fact_count, fact_total_cost=cost,
                )

    def _fill_year_from_plan(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        year = int(self.year_combo.currentData())
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return

        answer = QMessageBox.question(
            self,
            "Заполнить доступный период по плану?",
            "Фактические показатели доступных месяцев в границах перечня будут заменены "
            "соответствующими плановыми показателями. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        for month in active_months_for_work_list(work_list, year):
            period = self.repository.get_or_create_work_fixation_period(
                work_list_id=work_list.id,
                year=year,
                month=month,
            )
            self.repository.refresh_work_fixation_plan_snapshot(period.id)
            self._copy_plan_to_fact(period.id)

        self._month_changed()
        self._refresh_year_status()

    def _clear_month(self) -> None:
        if not self._current_period_id:
            return
        answer = QMessageBox.question(
            self,
            "Очистить фактические показатели?",
            "Фактические цена, объем, количество и стоимость плановых работ "
            "за выбранный месяц будут очищены. Внеплановые работы и фотографии "
            "останутся без изменений.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.clear_work_fixation_period(self._current_period_id)
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _period_has_data(self, period_id: str) -> bool:
        planned = self.repository.list_work_fixation_items(period_id)
        if any(item_has_fact(item) for item in planned):
            return True
        if self.repository.list_work_fixation_unplanned_items(period_id):
            return True
        for item in planned:
            if self.repository.list_work_fixation_photos(fixation_item_id=item.id):
                return True
        return False

    def _refresh_period_status(self) -> None:
        period = (
            self.repository.get_work_fixation_period(self._current_period_id)
            if self._current_period_id
            else None
        )
        if period is None:
            self.month_status.setText("Период: —")
            return

        items = self.repository.list_work_fixation_items(period.id)
        filled = sum(1 for item in items if item_has_fact(item))
        unplanned = self.repository.list_work_fixation_unplanned_items(period.id)
        photo_count = sum(
            len(self.repository.list_work_fixation_photos(fixation_item_id=item.id))
            for item in items
        ) + sum(
            len(self.repository.list_work_fixation_photos(unplanned_item_id=item.id))
            for item in unplanned
        )
        text = (
            f"{_MONTHS[period.month - 1]} {period.year}: плановых строк с фактом "
            f"{filled} из {len(items)}"
        )
        if unplanned:
            text += f" • внеплановых: {len(unplanned)}"
        if photo_count:
            text += f" • фото: {photo_count}"
        status = str(period.gis_status or "").upper()
        if status == "SAVED":
            text += " • размещено в ГИС ЖКХ"
        elif status == "MODIFIED":
            text += " • изменено после размещения"
        elif status == "ERROR":
            text += " • ошибка размещения"
        elif filled:
            text += " • готово локально"
        if period.gis_last_error:
            text += f" • {period.gis_last_error}"
        self.month_status.setText(text)

    def _refresh_year_status(self) -> None:
        refresh_monthly(self, "fixation")


    def _periods_with_data_for_year(self) -> list[WorkFixationPeriod]:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return []
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return []
        year = int(self.year_combo.currentData())
        active_months = set(active_months_for_work_list(work_list, year))
        periods = self.repository.list_work_fixation_periods(
            self._current_work_list_id,
            year=year,
        )
        return [
            period
            for period in periods
            if period.month in active_months and self._period_has_data(period.id)
        ]

    def _load_selected_month_from_gis(self) -> None:
        if not self._current_period_id or not self._current_work_list_id:
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        period = self.repository.get_work_fixation_period(self._current_period_id)
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if period is None or work_list is None:
            return
        house = self.repository.get_house(work_list.house_id)
        if house is None:
            return
        fias = house.fias_house_guid or house.gis_house_guid
        if not fias or not work_list.gis_work_list_guid:
            QMessageBox.warning(
                self,
                "Не хватает идентификаторов",
                "Для загрузки фиксации нужны ФИАС GUID дома и GUID размещенного перечня.",
            )
            return

        period_id = period.id
        self._integration_busy = True
        self._refresh_gis_buttons()
        self.month_status.setText(
            f"{_MONTHS[period.month - 1]} {period.year}: загрузка из ГИС ЖКХ…"
        )

        worker = GisIntegrationWorker(
            self.integration_gateway,
            "load_work_fixation",
            {
                "fiasHouseCode": fias,
                "workListGuid": work_list.gis_work_list_guid,
                "year": period.year,
                "month": period.month,
            },
            timeout=120.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            if not result.get("found"):
                self.operation_status.outcome("Фиксация в ГИС не найдена. Локальные данные сохранены.")
                QMessageBox.information(
                    self,
                    "Фиксация в ГИС ЖКХ не найдена",
                    "Для выбранного перечня и месяца сохраненная фиксация "
                    "в ГИС ЖКХ не найдена.",
                )
                return

            planned = list(result.get("plannedItems") or [])
            unplanned = list(result.get("unplannedItems") or [])
            filled = sum(
                1
                for row in planned
                if isinstance(row, dict)
                and any(
                    row.get(key) not in (None, "")
                    for key in (
                        "factPrice",
                        "factAmount",
                        "factCount",
                        "factTotalCost",
                    )
                )
            )
            question = (
                f"Найдено плановых строк: {len(planned)}.\n"
                f"Строк с фактическими показателями: {filled}.\n"
                f"Внеплановых работ: {len(unplanned)}.\n\n"
            )
            if local_fixation_has_data(self.repository, period_id):
                question += (
                    "Локальные фактические показатели и внеплановые работы "
                    "этого месяца будут заменены данными из ГИС ЖКХ. "
                    "Фото плановых работ останутся локально. Продолжить?"
                )
            else:
                question += "Загрузить эти данные в программу?"

            answer = QMessageBox.question(
                self,
                "Загрузить фиксацию из ГИС ЖКХ?",
                question,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.operation_status.outcome("Загрузка фиксации отменена. Локальные данные сохранены.")
                return

            try:
                applied = apply_remote_work_fixation(
                    self.repository,
                    work_list_id=self._current_work_list_id,
                    year=period.year,
                    month=period.month,
                    remote=result,
                )
            except (sqlite3.DatabaseError, ValueError) as exc:
                self.operation_status.outcome("Не удалось применить фиксацию. Изменения импорта отменены; повторите загрузку после устранения ошибки.", tone="danger")
                QMessageBox.critical(
                    self,
                    "Не удалось применить фиксацию",
                    error_text(exc, context=context_text(self)),
                )
                return

            self._load_table()
            self._load_unplanned_table()
            self._refresh_period_status()
            self._refresh_year_status()
            self.operation_status.outcome(f"Фиксация загружена: плановых строк {applied['planned']}, внеплановых {applied['unplanned']}.", tone="success")
            QMessageBox.information(
                self,
                "Фиксация загружена",
                f"Плановых строк: {applied['planned']}.\n"
                f"Строк с фактом: {applied['filled']}.\n"
                f"Внеплановых работ: {applied['unplanned']}."
                + ("\nЛокальные работы, отсутствующие в ответе ГИС, сохранены. Месяц помечен изменённым."
                   if applied.get("preserved_local") else ""),
            )

        def progressed(progress: dict) -> None:
            if str(progress.get("phase") or "") == "queued":
                self.month_status.setText(
                    "Ожидание предыдущей операции с ГИС ЖКХ…"
                )

        def failed(message: str) -> None:
            QMessageBox.critical(
                self,
                "Не удалось загрузить фиксацию из ГИС ЖКХ",
                error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."),
            )

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_period_status()
            self._refresh_integration_status()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.progressed.connect(progressed)
        worker.finished.connect(finished)
        worker.start()

    def _load_year_from_gis(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return
        months = active_months_for_work_list(
            work_list,
            int(self.year_combo.currentData()),
        )
        if not months:
            return

        answer = QMessageBox.question(
            self,
            "Загрузить фиксацию за доступные месяцы года?",
            f"Будут последовательно проверены {len(months)} мес. выбранного "
            "периода. Найденная в ГИС ЖКХ фиксация заменит локальные "
            "фактические показатели и внеплановые работы соответствующего "
            "месяца. Месяцы без фиксации будут пропущены. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        year = int(self.year_combo.currentData())
        periods = []
        for month in months:
            period = self.repository.get_or_create_work_fixation_period(
                work_list_id=self._current_work_list_id,
                year=year,
                month=month,
            )
            self.repository.refresh_work_fixation_plan_snapshot(period.id)
            periods.append(period)

        self._queue_period_ids = [period.id for period in periods]
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_skipped = 0
        self._queue_failed_period_id = None
        self._stop_queue_requested = False
        self._queue_mode = "load"
        self.continue_queue_button.setVisible(False)
        self.queue_status_strip.setVisible(True)
        self.queue_progress.setVisible(True)
        self.queue_progress.setRange(0, len(self._queue_period_ids))
        self.queue_progress.setValue(0)
        self.queue_label.setText("Очередь загрузки запущена…")
        self._start_next_queue_item()

    def _publish_selected_month(self) -> None:
        if not self._current_period_id:
            return
        period = self.repository.get_work_fixation_period(self._current_period_id)
        if period is None:
            return
        if not self._period_has_data(period.id):
            QMessageBox.information(
                self,
                "Нет фактических показателей",
                "Сначала заполните выбранный месяц.",
            )
            return

        answer = QMessageBox.warning(
            self,
            "Разместить выбранный месяц?",
            f"Будет размещена фиксация за {_MONTHS[period.month - 1]} {period.year}. "
            "Если фиксация уже существует в ГИС ЖКХ, управляемые приложением "
            "фактические показатели будут обновлены.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._send_period(period.id, queue_mode=False)

    def _publish_year(self) -> None:
        periods = self._periods_with_data_for_year()
        if not periods:
            QMessageBox.information(
                self,
                "Нет заполненных месяцев",
                "Сначала заполните хотя бы один месяц или примените план к году.",
            )
            return

        answer = QMessageBox.warning(
            self,
            "Разместить заполненные месяцы года?",
            f"Будет последовательно обработано месяцев: {len(periods)}. "
            "Подтверждение потребуется только один раз. При первой ошибке очередь "
            "остановится, уже успешно размещенные месяцы останутся размещенными."
            + "\n\n" + queue_preview(self, periods),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._queue_period_ids = [period.id for period in periods]
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_skipped = 0
        self._queue_failed_period_id = None
        self._stop_queue_requested = False
        self._queue_mode = "publish"
        self.continue_queue_button.setVisible(False)
        self.queue_status_strip.setVisible(True)
        self.queue_progress.setVisible(True)
        self.queue_progress.setRange(0, len(self._queue_period_ids))
        self.queue_progress.setValue(0)
        self.queue_label.setText("Очередь запущена…")
        self._start_next_queue_item()

    def _continue_queue(self) -> None:
        if not self._queue_failed_period_id:
            return
        try:
            failed_index = self._queue_period_ids.index(self._queue_failed_period_id)
        except ValueError:
            return
        self._queue_index = failed_index
        self._queue_failed_period_id = None
        self._stop_queue_requested = False
        self.continue_queue_button.setVisible(False)
        self.queue_label.setText("Продолжаем очередь…")
        self._start_next_queue_item()

    def _start_next_queue_item(self) -> None:
        from .queue_controls import stop_before_next
        if stop_before_next(self):
            return
        if getattr(self.window(), "_close_requested", False):
            self._queue_running = False
            self.queue_label.setText(f"Очередь остановлена при закрытии. Обработано месяцев: {self._queue_successes}.")
            return
        self._queue_running = True
        if self._queue_index >= len(self._queue_period_ids):
            self._queue_running = False
            self.queue_progress.setValue(len(self._queue_period_ids))
            if self._queue_mode == "load":
                self.queue_label.setText(
                    f"Очередь завершена. Загружено месяцев: "
                    f"{self._queue_successes} • не найдено в ГИС ЖКХ: "
                    f"{self._queue_skipped}."
                )
            else:
                self.queue_label.setText(
                    f"Очередь завершена. Успешно размещено месяцев: "
                    f"{self._queue_successes}."
                )
            self._refresh_year_status()
            return

        period_id = self._queue_period_ids[self._queue_index]
        period = self.repository.get_work_fixation_period(period_id)
        if period:
            action = "Загружается" if self._queue_mode == "load" else "Размещается"
            self.queue_label.setText(
                f"{action} {_MONTHS[period.month - 1]} {period.year} "
                f"({self._queue_index + 1} из {len(self._queue_period_ids)})…"
            )
        if self._queue_mode == "load":
            self._send_load_period(period_id)
        else:
            self._send_period(period_id, queue_mode=True)

    def _send_load_period(self, period_id: str) -> None:
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        period = self.repository.get_work_fixation_period(period_id)
        if period is None:
            return
        work_list = self.repository.get_work_list(period.work_list_id)
        house = (
            self.repository.get_house(work_list.house_id)
            if work_list is not None
            else None
        )
        fias = (
            (house.fias_house_guid or house.gis_house_guid)
            if house is not None
            else None
        )
        if (
            work_list is None
            or not work_list.gis_work_list_guid
            or not fias
        ):
            self._queue_failed_period_id = period_id
            self._queue_running = False
            self.queue_label.setText(
                "Очередь остановлена: не хватает идентификаторов ГИС ЖКХ."
            )
            self.continue_queue_button.setVisible(True)
            return

        self._integration_busy = True
        self._refresh_gis_buttons()
        command_succeeded = {"value": False}
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "load_work_fixation",
            {
                "fiasHouseCode": fias,
                "workListGuid": work_list.gis_work_list_guid,
                "year": period.year,
                "month": period.month,
            },
            timeout=120.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            command_succeeded["value"] = True
            if result.get("found"):
                try:
                    apply_remote_work_fixation(
                        self.repository,
                        work_list_id=period.work_list_id,
                        year=period.year,
                        month=period.month,
                        remote=result,
                    )
                except (sqlite3.DatabaseError, ValueError) as exc:
                    command_succeeded["value"] = False
                    failed(str(exc))
                    return
                self._queue_successes += 1
            else:
                self._queue_skipped += 1

            self._queue_index += 1
            self.queue_progress.setValue(self._queue_index)
            self._refresh_year_status()
            if self._current_period_id == period_id:
                self._load_table()
                self._load_unplanned_table()
                self._refresh_period_status()

        def failed(message: str) -> None:
            self.repository.update_work_fixation_gis_state(
                period_id,
                gis_status="ERROR",
                gis_last_error=message,
            )
            self._queue_failed_period_id = period_id
            self._queue_running = False
            self.queue_label.setText(
                f"Очередь остановлена на {_MONTHS[period.month - 1]} "
                f"{period.year}: {message}\nУспешно: {self._queue_successes} из {len(self._queue_period_ids)}. "
                "Успешные месяцы повторно не отправляются. Устраните причину и продолжите с проблемного месяца."
            )
            self.continue_queue_button.setText(
                f"Продолжить с {_MONTHS[period.month - 1]} {period.year}"
            )
            self.continue_queue_button.setVisible(True)
            self._refresh_year_status()
            if self._current_period_id == period_id:
                self._refresh_period_status()

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_integration_status()
            if command_succeeded["value"] and self._queue_failed_period_id is None:
                QTimer.singleShot(250, self._start_next_queue_item)

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.start()

    def _send_period(self, period_id: str, *, queue_mode: bool) -> None:
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        period = self.repository.get_work_fixation_period(period_id)
        if period is None:
            return
        work_list = self.repository.get_work_list(period.work_list_id)
        if work_list is None:
            return
        house = self.repository.get_house(work_list.house_id)
        if house is None:
            return

        fias = house.fias_house_guid or house.gis_house_guid
        planned_items = self.repository.list_work_fixation_items(period.id)
        unplanned_items = self.repository.list_work_fixation_unplanned_items(period.id)
        photos_by_planned = {
            item.id: self.repository.list_work_fixation_photos(
                fixation_item_id=item.id
            )
            for item in planned_items
        }
        photos_by_unplanned = {
            item.id: self.repository.list_work_fixation_photos(
                unplanned_item_id=item.id
            )
            for item in unplanned_items
        }
        try:
            payload = build_fixation_command_payload(
                fias_house_code=fias or "",
                work_list_guid=work_list.gis_work_list_guid or "",
                year=period.year,
                month=period.month,
                items=planned_items,
                unplanned_items=unplanned_items,
                photos_by_planned=photos_by_planned,
                photos_by_unplanned=photos_by_unplanned,
                managed_unplanned_guids_json=period.managed_unplanned_guids_json,
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            message = str(exc)
            self.repository.update_work_fixation_gis_state(
                period_id,
                gis_status="ERROR",
                gis_last_error=message,
            )
            if queue_mode:
                self._queue_failed_period_id = period_id
                self._queue_running = False
                self.queue_label.setText(
                    f"Очередь остановлена на периоде {_MONTHS[period.month - 1]} "
                    f"{period.year}: {message}\nУспешно: {self._queue_successes} из {len(self._queue_period_ids)}. "
                "Успешные месяцы повторно не отправляются. Устраните причину и продолжите с проблемного месяца."
                )
                self.continue_queue_button.setText(
                    f"Продолжить с {_MONTHS[period.month - 1]} {period.year}"
                )
                self.continue_queue_button.setVisible(True)
                self._refresh_period_status()
                self._refresh_year_status()
            else:
                QMessageBox.warning(self, "Нельзя разместить фиксацию", message)
            return

        self._integration_busy = True
        self._refresh_gis_buttons()
        command_succeeded = {"value": False}
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "save_work_fixation",
            payload,
            timeout=90.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            command_succeeded["value"] = True
            fixed_guids = {
                str(item.get("localItemId")): str(item.get("guid"))
                for item in result.get("fixedItems", [])
                if item.get("localItemId") and item.get("guid")
            }
            unplanned_guids = {
                str(item.get("localItemId")): str(item.get("guid"))
                for item in result.get("unplannedItems", [])
                if item.get("localItemId") and item.get("guid")
            }
            unplanned_metadata = {
                str(item.get("localItemId")): {
                    "work_type_code": (
                        str(item.get("workTypeCode"))
                        if item.get("workTypeCode")
                        else None
                    ),
                    "work_type_guid": (
                        str(item.get("workTypeGuid"))
                        if item.get("workTypeGuid")
                        else None
                    ),
                    "work_type_name": (
                        str(item.get("workTypeName"))
                        if item.get("workTypeName")
                        else None
                    ),
                    "unit_name": (
                        str(item.get("unitName"))
                        if item.get("unitName")
                        else None
                    ),
                }
                for item in result.get("unplannedItems", [])
                if item.get("localItemId")
            }
            photo_guids = {
                str(item.get("localPhotoId")): (
                    str(item.get("contentGuid")) if item.get("contentGuid") else None,
                    str(item.get("photoGuid")) if item.get("photoGuid") else None,
                )
                for item in result.get("photoResults", [])
                if item.get("localPhotoId")
            }
            self.repository.update_work_fixation_gis_state(
                period_id,
                reporting_period_guid=result.get("reportingPeriodGuid"),
                gis_status="SAVED",
                gis_last_error=None,
                fixed_item_guids=fixed_guids,
                unplanned_item_guids=unplanned_guids,
                unplanned_item_metadata=unplanned_metadata,
                photo_guids=photo_guids,
            )
            if self._current_period_id == period_id:
                self._load_table()
                self._load_unplanned_table()
            if queue_mode:
                self._queue_successes += 1
                self._queue_index += 1
                self.queue_progress.setValue(self._queue_index)
            self._refresh_period_status()
            self._refresh_year_status()

        def failed(message: str) -> None:
            self.repository.update_work_fixation_gis_state(
                period_id,
                gis_status="ERROR",
                gis_last_error=message,
            )
            self._refresh_period_status()
            self._refresh_year_status()
            if queue_mode:
                self._queue_failed_period_id = period_id
                self._queue_running = False
                period_now = self.repository.get_work_fixation_period(period_id)
                label = (
                    f"{_MONTHS[period_now.month - 1]} {period_now.year}"
                    if period_now
                    else "текущий месяц"
                )
                self.queue_label.setText(
                    f"Очередь остановлена на периоде {label}: {message}\nУспешно: {self._queue_successes} из {len(self._queue_period_ids)}. "
                    "Устраните причину и продолжите; успешные месяцы повторно не отправляются."
                )
                self.continue_queue_button.setText(f"Продолжить с {label}")
                self.continue_queue_button.setVisible(True)
            else:
                QMessageBox.critical(self, "Ошибка обмена с ГИС ЖКХ", error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."))

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_integration_status()
            if queue_mode and self._queue_failed_period_id is None:
                QTimer.singleShot(250, self._start_next_queue_item)
            elif not queue_mode and command_succeeded["value"] and not getattr(worker, "result_apply_failed", False):
                self.operation_status.outcome(f"Фиксация за {_MONTHS[period.month - 1]} {period.year} размещена в ГИС ЖКХ.", tone="success")
                QMessageBox.information(
                    self,
                    "Фиксация размещена",
                    f"Фиксация за {_MONTHS[period.month - 1]} {period.year} "
                    "успешно сохранена в ГИС ЖКХ.",
                )

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.start()

    def _refresh_integration_status(self) -> None:
        """Refresh GIS-dependent actions; visible status lives in the sidebar."""

        self._refresh_gis_buttons()

    def _refresh_gis_buttons(self) -> None:
        refresh_monthly(self, "fixation")
        if hasattr(self, "fact_history"):
            self.fact_history.refresh()

    def _set_enabled(self, enabled: bool) -> None:
        self.table.setEnabled(enabled)
        self.unplanned_table.setEnabled(enabled)
        period_scope = self._scope_is_period()
        local_enabled = (
            bool(self._current_work_list_id)
            if period_scope
            else enabled
        )
        self.fill_month_button.setEnabled(local_enabled)
        self.clear_month_button.setEnabled(local_enabled)
        self._refresh_gis_buttons()

    def set_view_mode(self, mode: str) -> None:
        compact_monthly(self, mode != "normal")
        """Adapt month navigation density to the available window size."""

        columns = 12 if mode == "normal" else 6
        if columns != self._month_button_columns:
            self._relayout_month_buttons(columns)
        self._resize_planned_table()
        self._resize_unplanned_table()

    def flush_pending_changes(self) -> None:
        """Cells are saved immediately; kept for the main-window hook."""
