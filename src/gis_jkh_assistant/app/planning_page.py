"""Local planning page for placed work and service lists."""

from __future__ import annotations

from .error_messages import context_text, error_text

import sqlite3

import calendar
import json

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCalendarWidget,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
from gis_jkh_assistant.work_lists.gis_plan_sync import (
    apply_remote_work_plan,
    local_plan_has_data,
)
from gis_jkh_assistant.work_planning import (
    active_months_for_work_list,
    balanced_period_distribution,
    build_gis_work_plan_command_payload,
    integral_quantity,
)

from .gis_integration_worker import GisIntegrationWorker, connect_result
from .page_layouts import build_planning
from .workflow_presenter import mark_plan_synced, plan_fingerprint, refresh_planning
from .styles import color


_MONTH_HEADERS = (
    "Янв",
    "Фев",
    "Мар",
    "Апр",
    "Май",
    "Июн",
    "Июл",
    "Авг",
    "Сен",
    "Окт",
    "Ноя",
    "Дек",
)

_PLAN_ITEM_ID_ROLE = Qt.ItemDataRole.UserRole
_DATES_ROLE = Qt.ItemDataRole.UserRole + 1
_ORIGINAL_COUNT_ROLE = Qt.ItemDataRole.UserRole + 2


class PlanningTable(QTableWidget):
    """Monthly table with spreadsheet-like Delete handling."""

    delete_requested = Signal()
    context_menu_requested = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Menu or (event.key() == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.context_menu_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MonthDatesDialog(QDialog):
    """Pick optional concrete dates for one monthly plan cell."""

    def __init__(
        self,
        *,
        year: int,
        month: int,
        dates: list[str],
        work_name: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.year = int(year)
        self.month = int(month)
        self.setWindowTitle(f"Плановые даты — {self.month:02d}.{self.year}")
        self.setMinimumWidth(520)

        self.work_label = QLabel(work_name or "Работа не выбрана")
        self.work_label.setObjectName("SectionTitle")
        self.work_label.setWordWrap(True)
        self.work_label.setToolTip(work_name)
        self.month_label = QLabel(f"Плановые даты: {self.month:02d}.{self.year}")
        self.month_label.setObjectName("Muted")

        explanation = QLabel(
            "Конкретные даты необязательны. Можно оставить только количество "
            "в месяце. Если даты нужны, выберите день в календаре и нажмите "
            "«Добавить дату»."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("Muted")

        self.calendar = QCalendarWidget()
        first = QDate(self.year, self.month, 1)
        last = QDate(
            self.year,
            self.month,
            calendar.monthrange(self.year, self.month)[1],
        )
        self.calendar.setMinimumDate(first)
        self.calendar.setMaximumDate(last)
        self.calendar.setSelectedDate(first)

        self.dates_list = QListWidget()
        self.dates_list.setMinimumHeight(140)

        for value in sorted(set(dates)):
            parsed = QDate.fromString(value, Qt.DateFormat.ISODate)
            if parsed.isValid():
                self._append_date(parsed)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Добавить дату")
        self.remove_button = QPushButton("Удалить выбранную")
        self.clear_button = QPushButton("Очистить даты")
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")

        layout = QVBoxLayout(self)
        layout.addWidget(self.work_label)
        layout.addWidget(self.month_label)
        layout.addWidget(explanation)
        layout.addWidget(self.calendar)
        layout.addWidget(QLabel("Выбранные даты"))
        layout.addWidget(self.dates_list)
        layout.addLayout(actions)
        layout.addWidget(buttons)

        self.add_button.clicked.connect(self._add_selected_date)
        self.calendar.activated.connect(self._add_selected_date)
        self.remove_button.clicked.connect(self._remove_selected_date)
        self.clear_button.clicked.connect(self.dates_list.clear)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def _add_selected_date(self, *_args) -> None:
        self._append_date(self.calendar.selectedDate())

    def _append_date(self, value: QDate) -> None:
        iso = value.toString(Qt.DateFormat.ISODate)
        for index in range(self.dates_list.count()):
            if self.dates_list.item(index).data(Qt.ItemDataRole.UserRole) == iso:
                return
        item_text = value.toString("dd.MM.yyyy")
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(item_text)
        item.setData(Qt.ItemDataRole.UserRole, iso)
        self.dates_list.addItem(item)

    def _remove_selected_date(self) -> None:
        row = self.dates_list.currentRow()
        if row >= 0:
            self.dates_list.takeItem(row)

    def dates(self) -> list[str]:
        values = [
            self.dates_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.dates_list.count())
        ]
        return sorted(str(value) for value in values if value)


class PlanningPage(QWidget):
    """Plan monthly work counts for already placed work lists."""

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
        self._loading = False
        self._current_plan_id: str | None = None
        self._current_work_list_id: str | None = None

        self._build_ui()
        self.refresh_organizations()
        self._refresh_integration_status()

    def _build_ui(self) -> None:
        build_planning(self, PlanningTable)

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
        self._current_plan_id = None
        self._year_changed()

    def _year_changed(self) -> None:
        from .selection_memory import select_year
        select_year(self)
        work_list_id = self._current_work_list_id
        year = self.year_combo.currentData()
        if not work_list_id or year is None:
            self._current_plan_id = None
            self.table.setRowCount(0)
            self.plan_status.setText("План: —")
            self._set_plan_enabled(False)
            return

        try:
            plan = self.repository.get_or_create_work_plan(
                work_list_id=work_list_id,
                year=int(year),
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            self._current_plan_id = None
            self.table.setRowCount(0)
            self.plan_status.setText(f"План: {exc}")
            self._set_plan_enabled(False)
            return

        self._current_plan_id = plan.id
        self._update_plan_status(plan)
        self._set_plan_enabled(True)
        self._load_table()
        self._refresh_gis_action_button()

    def _load_table(self) -> None:
        if not self._current_plan_id or not self._current_work_list_id:
            self.table.setRowCount(0)
            return

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return
        year = int(self.year_combo.currentData())
        active_months = set(active_months_for_work_list(work_list, year))

        items = self.repository.list_work_plan_items(self._current_plan_id)
        month_rows = self.repository.list_work_plan_months(self._current_plan_id)
        months = {
            (month_row.work_plan_item_id, month_row.month): month_row
            for month_row in month_rows
        }

        self._loading = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        try:
            for row, item in enumerate(items):
                name = QTableWidgetItem(item.work_name)
                name.setData(_PLAN_ITEM_ID_ROLE, item.id)
                name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if item.work_code:
                    name.setToolTip(f"Код работы: {item.work_code}")
                self.table.setItem(row, 0, name)

                list_quantity = QTableWidgetItem(item.list_quantity or "")
                list_quantity.setFlags(
                    list_quantity.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
                list_quantity.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 1, list_quantity)

                plan_quantity = QTableWidgetItem("0")
                plan_quantity.setFlags(
                    plan_quantity.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
                plan_quantity.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 2, plan_quantity)

                total = 0
                for month in range(1, 13):
                    column = month + 2
                    month_row = months.get((item.id, month))
                    count = int(month_row.count) if month_row else 0
                    dates = self._decode_dates(month_row.dates_json if month_row else None)
                    total += count

                    cell = QTableWidgetItem(str(count) if count else "")
                    cell.setData(_PLAN_ITEM_ID_ROLE, item.id)
                    cell.setData(_DATES_ROLE, dates)
                    cell.setData(_ORIGINAL_COUNT_ROLE, count)
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                    if month not in active_months:
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        cell.setBackground(QBrush(QColor(color("readonly"))))
                        cell.setToolTip("Этот месяц не входит в период перечня.")
                    else:
                        self._apply_dates_decoration(cell, dates)
                    self.table.setItem(row, column, cell)

                plan_quantity.setText(str(total))
                comparable_quantity = (
                    item.list_quantity
                    if work_list.start_year == work_list.end_year == year
                    else None
                )
                self._decorate_total(plan_quantity, comparable_quantity, total)
        finally:
            self.table.blockSignals(False)
            self._loading = False
        self._update_plan_status()

    def _cell_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.column() < 3:
            return
        item_id = cell.data(_PLAN_ITEM_ID_ROLE)
        if not item_id:
            return

        text = cell.text().strip()
        try:
            count = int(text) if text else 0
            if count < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self,
                "Проверьте количество",
                "В месяце должно быть целое число не меньше нуля.",
            )
            self._restore_count(cell)
            return

        dates = list(cell.data(_DATES_ROLE) or [])
        if len(dates) > count:
            QMessageBox.warning(
                self,
                "Проверьте количество",
                "Количество не может быть меньше числа выбранных конкретных дат. "
                "Сначала удалите лишние даты двойным щелчком по ячейке.",
            )
            self._restore_count(cell)
            return

        month = cell.column() - 2
        try:
            self.repository.update_work_plan_month(
                str(item_id),
                month=month,
                count=count,
                dates=dates,
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось сохранить", error_text(exc, context=context_text(self), effect="В ячейке будет восстановлено прежнее количество.", recovery="Устраните причину ошибки и введите количество повторно."))
            self._restore_count(cell)
            return

        cell.setData(_ORIGINAL_COUNT_ROLE, count)
        cell.setText(str(count) if count else "")
        self._refresh_row_total(cell.row())
        self._update_plan_status()

    def _restore_count(self, cell: QTableWidgetItem) -> None:
        value = int(cell.data(_ORIGINAL_COUNT_ROLE) or 0)
        self.table.blockSignals(True)
        cell.setText(str(value) if value else "")
        self.table.blockSignals(False)

    def _open_dates(self, row: int, column: int) -> None:
        if column < 3 or not self._current_plan_id:
            return
        cell = self.table.item(row, column)
        if cell is None or not (cell.flags() & Qt.ItemFlag.ItemIsEditable):
            return

        year = int(self.year_combo.currentData())
        month = column - 2
        dates = list(cell.data(_DATES_ROLE) or [])
        dialog = MonthDatesDialog(
            year=year,
            month=month,
            dates=dates,
            work_name=(self.table.item(row, 0).text() if self.table.item(row, 0) else ""),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_dates = dialog.dates()
        current_count = int(cell.data(_ORIGINAL_COUNT_ROLE) or 0)
        new_count = max(current_count, len(new_dates))
        item_id = cell.data(_PLAN_ITEM_ID_ROLE)
        if not item_id:
            return

        try:
            self.repository.update_work_plan_month(
                str(item_id),
                month=month,
                count=new_count,
                dates=new_dates,
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось сохранить даты", error_text(exc, context=context_text(self), effect="Выбранные даты не применены к таблице плана.", recovery="Устраните причину и снова откройте даты этой ячейки (Alt+D)."))
            return

        self.table.blockSignals(True)
        cell.setText(str(new_count) if new_count else "")
        cell.setData(_DATES_ROLE, new_dates)
        cell.setData(_ORIGINAL_COUNT_ROLE, new_count)
        self._apply_dates_decoration(cell, new_dates)
        self.table.blockSignals(False)
        self._refresh_row_total(row)

    def _clear_selected_cells(self) -> None:
        if not self._current_plan_id:
            return

        rows: list[tuple[str, int, int]] = []
        affected_rows: set[int] = set()
        seen: set[tuple[str, int]] = set()

        for index in self.table.selectedIndexes():
            if index.column() < 3:
                continue
            cell = self.table.item(index.row(), index.column())
            if cell is None or not (cell.flags() & Qt.ItemFlag.ItemIsEditable):
                continue
            item_id = cell.data(_PLAN_ITEM_ID_ROLE)
            if not item_id:
                continue
            month = index.column() - 2
            key = (str(item_id), month)
            if key in seen:
                continue
            seen.add(key)
            rows.append((str(item_id), month, 0))
            affected_rows.add(index.row())

        if not rows:
            return

        try:
            self.repository.apply_work_plan_distribution(self._current_plan_id, rows)
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось очистить ячейки", error_text(exc, context=context_text(self)))
            return

        self.table.blockSignals(True)
        try:
            for item_id, month, _count in rows:
                column = month + 2
                for row in range(self.table.rowCount()):
                    cell = self.table.item(row, column)
                    if cell and str(cell.data(_PLAN_ITEM_ID_ROLE) or "") == item_id:
                        cell.setText("")
                        cell.setData(_DATES_ROLE, [])
                        cell.setData(_ORIGINAL_COUNT_ROLE, 0)
                        self._apply_dates_decoration(cell, [])
                        break
        finally:
            self.table.blockSignals(False)

        for row in affected_rows:
            self._refresh_row_total(row)


    def _distribute(self) -> None:
        if not self._current_plan_id or not self._current_work_list_id:
            return

        answer = QMessageBox.question(
            self,
            "Распределить количество?",
            "Количество из перечня будет автоматически распределено по всему "
            "периоду перечня. Значения выбранного года будут заменены, а "
            "конкретные даты в этих месяцах очищены. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return
        year = int(self.year_combo.currentData())
        items = self.repository.list_work_plan_items(self._current_plan_id)

        rows: list[tuple[str, int, int]] = []
        skipped: list[str] = []
        for item in items:
            quantity = integral_quantity(item.list_quantity)
            if quantity is None:
                skipped.append(item.work_name)
                continue
            distribution = balanced_period_distribution(quantity, work_list)
            for month in range(1, 13):
                count = distribution.get((year, month), 0)
                rows.append((item.id, month, count))

        try:
            self.repository.apply_work_plan_distribution(self._current_plan_id, rows)
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось распределить", error_text(exc, context=context_text(self)))
            return

        self._load_table()
        if skipped:
            preview = "\n".join(f"• {name}" for name in skipped[:8])
            if len(skipped) > 8:
                preview += f"\n… еще: {len(skipped) - 8}"
            QMessageBox.information(
                self,
                "Часть строк пропущена",
                "Автоматическое распределение возможно только для целого "
                "неотрицательного количества. Не изменены строки:\n" + preview,
            )

    def _clear_year(self) -> None:
        if not self._current_plan_id:
            return
        answer = QMessageBox.question(
            self,
            "Очистить план?",
            "Все количества и конкретные даты за выбранный год будут удалены.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        items = self.repository.list_work_plan_items(self._current_plan_id)
        rows = [
            (item.id, month, 0)
            for item in items
            for month in range(1, 13)
        ]
        self.repository.apply_work_plan_distribution(self._current_plan_id, rows)
        self._load_table()

    def _refresh_row_total(self, row: int) -> None:
        total = 0
        for column in range(3, 15):
            cell = self.table.item(row, column)
            if cell:
                total += int(cell.data(_ORIGINAL_COUNT_ROLE) or 0)

        total_cell = self.table.item(row, 2)
        quantity_cell = self.table.item(row, 1)
        if total_cell:
            total_cell.setText(str(total))
            work_list = (
                self.repository.get_work_list(self._current_work_list_id)
                if self._current_work_list_id
                else None
            )
            year = int(self.year_combo.currentData()) if self.year_combo.currentData() else 0
            comparable_quantity = (
                quantity_cell.text()
                if (
                    quantity_cell
                    and work_list
                    and work_list.start_year == work_list.end_year == year
                )
                else None
            )
            self._decorate_total(total_cell, comparable_quantity, total)

    @staticmethod
    def _decode_dates(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return sorted(str(item) for item in value if item)

    @staticmethod
    def _apply_dates_decoration(cell: QTableWidgetItem, dates: list[str]) -> None:
        if dates:
            pretty = []
            for value in dates:
                parsed = QDate.fromString(value, Qt.DateFormat.ISODate)
                pretty.append(
                    parsed.toString("dd.MM.yyyy") if parsed.isValid() else value
                )
            cell.setBackground(QBrush(QColor(color("success_surface"))))
            cell.setToolTip("Конкретные даты: " + ", ".join(pretty))
        else:
            cell.setBackground(QBrush())
            cell.setToolTip(
                "Двойной щелчок — выбрать конкретные даты. "
                "Дата необязательна, достаточно количества."
            )

    @staticmethod
    def _decorate_total(
        cell: QTableWidgetItem,
        list_quantity: str | None,
        total: int,
    ) -> None:
        expected = integral_quantity(list_quantity)
        if expected is not None and total == expected:
            cell.setBackground(QBrush(QColor(color("success_surface"))))
            cell.setToolTip("Количество в плане совпадает с количеством в перечне.")
        elif expected is not None:
            cell.setBackground(QBrush(QColor(color("warning_surface"))))
            cell.setToolTip(
                f"В перечне: {expected}. В плане за этот год: {total}."
            )
        else:
            cell.setBackground(QBrush())
            cell.setToolTip(
                "Для переходящего периода количество в плане показано только "
                "за выбранный календарный год."
            )

    def _set_plan_enabled(self, enabled: bool) -> None:
        self.table.setEnabled(enabled)
        self.distribute_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self._refresh_gis_action_button()

    def _update_plan_status(self, plan=None) -> None:
        refresh_planning(self)

    def _refresh_integration_status(self) -> None:
        """Refresh GIS-dependent actions; visible status lives in the sidebar."""

        self._refresh_gis_action_button()

    def _refresh_gis_action_button(self) -> None:
        refresh_planning(self)

    def _set_integration_busy(self, busy: bool) -> None:
        self._integration_busy = busy
        self._refresh_gis_action_button()
        if busy:
            self.plan_status.setText("План: отправка в ГИС ЖКХ…")
        else:
            self._update_plan_status()

    def _run_integration_command(
        self,
        *,
        plan_id: str,
        payload: dict,
        success_callback,
    ) -> None:
        if self._integration_worker is not None and self._integration_worker.isRunning():
            QMessageBox.information(
                self,
                "Операция уже выполняется",
                "Дождитесь завершения текущей операции с ГИС ЖКХ.",
            )
            return

        self._set_integration_busy(True)
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "save_work_plan",
            payload,
            timeout=90.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            success_callback(plan_id, result)

        def failed(message: str) -> None:
            self.repository.update_work_plan_gis_state(
                plan_id,
                gis_last_error=message,
            )
            if self._current_plan_id == plan_id:
                self._update_plan_status()
            QMessageBox.critical(self, "Ошибка обмена с ГИС ЖКХ", error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."))

        def finished() -> None:
            self._integration_worker = None
            self._set_integration_busy(False)
            self._refresh_integration_status()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.start()

    def _load_from_gis(self) -> None:
        if (
            not self._current_plan_id
            or not self._current_work_list_id
            or self.year_combo.currentData() is None
        ):
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None or not work_list.gis_work_list_guid:
            QMessageBox.warning(
                self,
                "Нет GUID размещенного перечня",
                "Сначала загрузите или проверьте перечень в разделе "
                "«Перечни работ и услуг».",
            )
            return

        plan_id = self._current_plan_id
        year = int(self.year_combo.currentData())
        self._integration_busy = True
        self._refresh_gis_action_button()
        self.plan_status.setText("План: загрузка из ГИС ЖКХ…")

        worker = GisIntegrationWorker(
            self.integration_gateway,
            "load_work_plan",
            {
                "workListGuid": work_list.gis_work_list_guid,
                "year": year,
            },
            timeout=90.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            if not result.get("found"):
                self.operation_status.outcome("План в ГИС не найден. Локальные данные сохранены.")
                QMessageBox.information(
                    self,
                    "План в ГИС ЖКХ не найден",
                    "Для выбранного перечня и года планирование в ГИС ЖКХ "
                    "не найдено.",
                )
                return

            remote_items = list(result.get("items") or [])
            remote_months = sum(
                1
                for row in remote_items
                if isinstance(row, dict)
                for month in row.get("monthlyWorks") or []
                if int(month.get("count") or 0) > 0 or bool(month.get("dates"))
            )
            has_local = local_plan_has_data(self.repository, plan_id)
            question = (
                f"В ГИС ЖКХ найден план: работ {len(remote_items)}, "
                f"заполненных месячных значений {remote_months}.\n\n"
            )
            if has_local:
                question += (
                    "Локальный план уже содержит данные. Они будут заменены "
                    "сведениями из ГИС ЖКХ. Продолжить?"
                )
            else:
                question += "Загрузить эти данные в программу?"

            answer = QMessageBox.question(
                self,
                "Загрузить планирование из ГИС ЖКХ?",
                question,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.operation_status.outcome("Загрузка плана отменена. Локальные данные сохранены.")
                return

            try:
                applied = apply_remote_work_plan(
                    self.repository,
                    work_list_id=work_list.id,
                    year=year,
                    remote=result,
                )
            except (sqlite3.DatabaseError, ValueError) as exc:
                self.operation_status.outcome("Не удалось применить план. Изменения импорта отменены; повторите загрузку после устранения ошибки.", tone="danger")
                QMessageBox.critical(
                    self,
                    "Не удалось применить планирование",
                    error_text(exc, context=context_text(self)),
                )
                return

            mark_plan_synced(self, plan_id)
            self._load_table()
            self._update_plan_status()
            self.operation_status.outcome(f"План загружен: работ {applied['items']}, месячных значений {applied['months']}.", tone="success")
            details = (
                f"Загружено работ: {applied['items']}.\n"
                f"Загружено месячных значений: {applied['months']}."
            )
            if applied.get("historical"):
                details += (
                    f"\nИсторических работ, отсутствующих в локальном перечне: "
                    f"{applied['historical']}."
                )
            if applied.get("preserved_local"):
                details += "\nСохранены локальные исторические работы, отсутствующие в ответе ГИС. План требует проверки."
            QMessageBox.information(
                self,
                "Планирование загружено",
                details,
            )

        def progressed(progress: dict) -> None:
            if str(progress.get("phase") or "") == "queued":
                self.plan_status.setText(
                    "План: ожидание предыдущей операции с ГИС ЖКХ…"
                )

        def failed(message: str) -> None:
            QMessageBox.critical(
                self,
                "Не удалось загрузить планирование из ГИС ЖКХ",
                error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."),
            )

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._update_plan_status()
            self._refresh_integration_status()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.progressed.connect(progressed)
        worker.finished.connect(finished)
        worker.start()

    def _publish_to_gis(self) -> None:
        if not self._current_plan_id or not self._current_work_list_id:
            return

        plan = self.repository.get_work_plan(self._current_plan_id)
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not plan or not work_list:
            return
        if not work_list.gis_work_list_guid:
            QMessageBox.warning(
                self,
                "Нет GUID размещенного перечня",
                "Сначала проверьте статус перечня в разделе «Перечни работ и услуг».",
            )
            return

        house = self.repository.get_house(work_list.house_id)
        if not house:
            QMessageBox.critical(
                self,
                "Дом не найден",
                "Карточка дома отсутствует в локальной базе.",
            )
            return

        items = self.repository.list_work_plan_items(plan.id)
        historical_items = [
            item for item in items if item.work_list_item_id is None
        ]
        if historical_items:
            QMessageBox.warning(
                self,
                "Исторический план",
                "В плане есть работы, которые ГИС ЖКХ больше не возвращает "
                "в составе самого перечня. Такой исторический план можно "
                "загрузить и просматривать, но безопасно перезаписать его через "
                "текущий справочник ГИС ЖКХ нельзя.",
            )
            return
        months = self.repository.list_work_plan_months(plan.id)
        try:
            payload = build_gis_work_plan_command_payload(
                work_list_guid=work_list.gis_work_list_guid,
                year=plan.year,
                items=items,
                months=months,
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Нельзя разместить план", error_text(exc, context=context_text(self)))
            return

        sent_fingerprint = plan_fingerprint(self, plan.id)
        total_count = sum(int(row.count) for row in months)
        operation = "обновлен" if plan.gis_work_plan_guid else "создан или обновлен"
        answer = QMessageBox.warning(
            self,
            "Разместить / обновить план?",
            f"Дом:\n{house.address}\n\n"
            f"Перечень: {work_list.period_label}\n"
            f"Год плана: {plan.year}\n"
            f"Суммарное количество по месяцам: {total_count}\n\n"
            f"План в ГИС ЖКХ будет {operation} локальными значениями. "
            "Если план этого года уже существует в ГИС ЖКХ, его месячные значения "
            "будут полностью заменены текущими значениями из приложения.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def on_success(plan_id: str, result: dict) -> None:
            self.repository.update_work_plan_gis_state(
                plan_id,
                gis_work_plan_guid=result.get("workPlanGuid"),
                gis_status=result.get("gisStatus") or "SAVED",
                gis_last_error=None,
            )
            mark_plan_synced(self, plan_id, sent_fingerprint)
            if self._current_plan_id == plan_id:
                self._update_plan_status()

            created = bool(result.get("created"))
            self.operation_status.outcome("План создан в ГИС ЖКХ." if created else "План обновлён в ГИС ЖКХ.", tone="success")
            QMessageBox.information(
                self,
                "План размещен" if created else "План обновлен",
                (
                    "План работ и услуг создан в ГИС ЖКХ."
                    if created
                    else "План работ и услуг обновлен в ГИС ЖКХ."
                ),
            )

        self._run_integration_command(
            plan_id=plan.id,
            payload=payload,
            success_callback=on_success,
        )

    def set_view_mode(self, _mode: str) -> None:
        """Keep the same public resize hook as other main pages."""

    def flush_pending_changes(self) -> None:
        """Monthly cells are saved immediately; kept for the main-window hook."""
