"""Local planning page for placed work and service lists."""

from __future__ import annotations

import calendar
import json

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
from gis_jkh_assistant.work_planning import (
    active_months_for_work_list,
    balanced_period_distribution,
    build_gis_work_plan_command_payload,
    helper_supports_work_plans,
    integral_quantity,
)

from .gis_integration_worker import GisIntegrationWorker
from .widgets import SafeComboBox


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

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.year = int(year)
        self.month = int(month)
        self.setWindowTitle(f"Плановые даты — {self.month:02d}.{self.year}")
        self.setMinimumWidth(520)

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.page_scroll = QScrollArea()
        self.page_scroll.setObjectName("PageScroll")
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("PageContent")
        self.page_scroll.setWidget(content)
        outer.addWidget(self.page_scroll)

        root = QVBoxLayout(content)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        title = QLabel("Планирование работ и услуг")
        title.setObjectName("Title")
        root.addWidget(title)

        intro = QLabel(
            "Раздел использует только перечни, уже размещенные в ГИС ЖКХ. "
            "Для каждого месяца можно указать количество вручную. Конкретные даты "
            "необязательны: двойной щелчок по месячной ячейке открывает календарь."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        context = QFrame()
        context.setObjectName("Card")
        context_layout = QGridLayout(context)
        context_layout.setContentsMargins(14, 12, 14, 12)
        context_layout.setHorizontalSpacing(10)
        context_layout.setVerticalSpacing(8)

        context_layout.addWidget(QLabel("Управляющая организация"), 0, 0)
        self.organization_combo = SafeComboBox()
        self.organization_combo.setMinimumWidth(300)
        context_layout.addWidget(self.organization_combo, 0, 1, 1, 3)

        context_layout.addWidget(QLabel("Дом"), 1, 0)
        self.house_combo = SafeComboBox()
        self.house_combo.setMinimumWidth(340)
        context_layout.addWidget(self.house_combo, 1, 1, 1, 3)

        context_layout.addWidget(QLabel("Размещенный перечень"), 2, 0)
        self.work_list_combo = SafeComboBox()
        self.work_list_combo.setMinimumWidth(340)
        context_layout.addWidget(self.work_list_combo, 2, 1, 1, 3)

        context_layout.addWidget(QLabel("Год плана"), 3, 0)
        self.year_combo = SafeComboBox()
        self.year_combo.setFixedWidth(150)
        context_layout.addWidget(self.year_combo, 3, 1)

        self.plan_status = QLabel("План: —")
        self.plan_status.setObjectName("Muted")
        self.plan_status.setWordWrap(True)
        context_layout.addWidget(self.plan_status, 3, 2, 1, 2)

        self.distribute_button = QPushButton("Распределить количество по месяцам")
        self.distribute_button.setToolTip(
            "Распределить количество из размещенного перечня по всему его периоду. "
            "Для полного года и количества 12 получится по 1 в каждый месяц."
        )
        self.clear_button = QPushButton("Очистить план за выбранный год")
        self.publish_button = QPushButton("Разместить / обновить план в ГИС ЖКХ")
        self.publish_button.setObjectName("PrimaryButton")
        self.publish_button.setToolTip(
            "Создать новый план в ГИС ЖКХ или полностью обновить уже существующий "
            "план этого перечня и года локальными значениями."
        )

        actions = QHBoxLayout()
        actions.addWidget(self.distribute_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        actions.addWidget(self.publish_button)
        context_layout.addLayout(actions, 4, 0, 1, 4)

        context_layout.setColumnStretch(1, 2)
        context_layout.setColumnStretch(3, 1)
        root.addWidget(context)

        self.empty_hint = QLabel(
            "Для выбранного дома нет размещенных перечней. "
            "Сначала разместите перечень в разделе «Перечни работ и услуг» "
            "и при необходимости нажмите «Проверить статус в ГИС»."
        )
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.empty_hint.setStyleSheet("color: #1f1f1f; font-weight: 600;")
        root.addWidget(self.empty_hint)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(12, 12, 12, 12)

        table_hint = QLabel(
            "«Количество в плане» считается автоматически как сумма месяцев. "
            "Ячейки вне периода выбранного перечня недоступны. "
            "Автораспределение заменяет месячные количества и очищает ранее "
            "выбранные конкретные даты. Выделенные месячные ячейки можно очистить "
            "клавишей Del — удалятся и количество, и выбранные даты."
        )
        table_hint.setObjectName("Muted")
        table_hint.setWordWrap(True)
        table_layout.addWidget(table_hint)

        self.table = PlanningTable(0, 15)
        self.table.setHorizontalHeaderLabels(
            ["Работа / услуга", "В перечне", "В плане", *_MONTH_HEADERS]
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(3, 15):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table_layout.addWidget(self.table, 1)
        root.addWidget(table_card, 1)

        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.work_list_combo.currentIndexChanged.connect(self._work_list_changed)
        self.year_combo.currentIndexChanged.connect(self._year_changed)
        self.distribute_button.clicked.connect(self._distribute)
        self.clear_button.clicked.connect(self._clear_year)
        self.publish_button.clicked.connect(self._publish_to_gis)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.cellDoubleClicked.connect(self._open_dates)
        self.table.delete_requested.connect(self._clear_selected_cells)

        self._set_plan_enabled(False)

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
        work_list_id = self.work_list_combo.currentData()
        self._current_work_list_id = str(work_list_id) if work_list_id else None
        self._current_plan_id = None

        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if self._current_work_list_id
            else None
        )
        if work_list:
            for year in range(work_list.start_year, work_list.end_year + 1):
                self.year_combo.addItem(str(year), year)
            preferred = work_list.base_year
            index = self.year_combo.findData(preferred)
            self.year_combo.setCurrentIndex(index if index >= 0 else 0)
        self.year_combo.blockSignals(False)
        self._year_changed()

    def _year_changed(self) -> None:
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
        except ValueError as exc:
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
                        cell.setBackground(QBrush(QColor("#f0f1f3")))
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
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось сохранить", str(exc))
            self._restore_count(cell)
            return

        cell.setData(_ORIGINAL_COUNT_ROLE, count)
        cell.setText(str(count) if count else "")
        self._refresh_row_total(cell.row())

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
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось сохранить даты", str(exc))
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
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось очистить ячейки", str(exc))
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
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось распределить", str(exc))
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
            cell.setBackground(QBrush(QColor("#e8f4e8")))
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
            cell.setBackground(QBrush(QColor("#e8f4e8")))
            cell.setToolTip("Количество в плане совпадает с количеством в перечне.")
        elif expected is not None:
            cell.setBackground(QBrush(QColor("#fff4d6")))
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
        if plan is None and self._current_plan_id:
            plan = self.repository.get_work_plan(self._current_plan_id)
        if plan is None:
            self.plan_status.setText("План: —")
            return

        if plan.gis_work_plan_guid:
            text = f"План ГИС ЖКХ: {plan.gis_work_plan_guid}"
            if plan.gis_status == "SAVED":
                text += " • размещен / обновлен"
            elif plan.gis_status:
                text += f" • {plan.gis_status}"
            if plan.gis_last_sync_at:
                text += f" • синхронизировано {plan.gis_last_sync_at}"
            if plan.gis_last_error:
                text += f" • ошибка: {plan.gis_last_error}"
            self.plan_status.setText(text)
        else:
            text = "План: локально, еще не размещен через приложение."
            if plan.gis_last_error:
                text += f" Последняя ошибка: {plan.gis_last_error}"
            self.plan_status.setText(text)

    def _refresh_integration_status(self) -> None:
        """Refresh GIS-dependent actions; visible status lives in the sidebar."""

        self._refresh_gis_action_button()

    def _refresh_gis_action_button(self) -> None:
        if not hasattr(self, "publish_button"):
            return
        status = self.integration_gateway.status()
        ready = (
            status.running
            and status.helper_connected
            and status.gis_page_ready
            and helper_supports_work_plans(status.helper_version)
        )
        self.publish_button.setEnabled(
            bool(self._current_plan_id)
            and bool(self._current_work_list_id)
            and ready
            and not self._integration_busy
        )

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
            QMessageBox.critical(self, "Ошибка обмена с ГИС ЖКХ", message)

        def finished() -> None:
            self._integration_worker = None
            self._set_integration_busy(False)
            self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
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
        months = self.repository.list_work_plan_months(plan.id)
        try:
            payload = build_gis_work_plan_command_payload(
                work_list_guid=work_list.gis_work_list_guid,
                year=plan.year,
                items=items,
                months=months,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Нельзя разместить план", str(exc))
            return

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
            if self._current_plan_id == plan_id:
                self._update_plan_status()

            created = bool(result.get("created"))
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
