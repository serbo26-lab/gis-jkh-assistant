"""Monthly work/service fixation page."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.models import WorkFixationPeriod
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
from gis_jkh_assistant.work_fixation import (
    build_fixation_command_payload,
    helper_supports_fixations,
    item_has_fact,
)
from gis_jkh_assistant.work_planning import active_months_for_work_list

from .gis_integration_worker import GisIntegrationWorker
from .widgets import SafeComboBox


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
_MONTH_SHORT = ("Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек")
_ITEM_ID_ROLE = Qt.ItemDataRole.UserRole


class FixationPage(QWidget):
    """Create factual monthly indicators and publish them sequentially to GIS ЖКХ."""

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
        self._queue_period_ids: list[str] = []
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_failed_period_id: str | None = None

        self._integration_status_timer = QTimer(self)
        self._integration_status_timer.setInterval(1500)
        self._integration_status_timer.timeout.connect(self._refresh_integration_status)

        self._build_ui()
        self.refresh_organizations()
        self._refresh_integration_status()
        self._integration_status_timer.start()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("PageContent")
        scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        title = QLabel("Фиксация работ и услуг")
        title.setObjectName("Title")
        root.addWidget(title)

        intro = QLabel(
            "Фиксация ведется помесячно. Можно заполнить один месяц вручную, "
            "скопировать плановые показатели в факт для месяца или сразу для всего года. "
            "Заполненные месяцы можно разместить в ГИС ЖКХ по одному либо одной очередью."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        context = QFrame()
        context.setObjectName("Card")
        grid = QGridLayout(context)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Управляющая организация"), 0, 0)
        self.organization_combo = SafeComboBox()
        grid.addWidget(self.organization_combo, 0, 1, 1, 3)

        grid.addWidget(QLabel("Дом"), 1, 0)
        self.house_combo = SafeComboBox()
        grid.addWidget(self.house_combo, 1, 1, 1, 3)

        grid.addWidget(QLabel("Размещенный перечень"), 2, 0)
        self.work_list_combo = SafeComboBox()
        grid.addWidget(self.work_list_combo, 2, 1, 1, 3)

        grid.addWidget(QLabel("Год"), 3, 0)
        self.year_combo = SafeComboBox()
        self.year_combo.setFixedWidth(120)
        grid.addWidget(self.year_combo, 3, 1)

        grid.addWidget(QLabel("Месяц"), 3, 2)
        self.month_combo = SafeComboBox()
        self.month_combo.setMinimumWidth(170)
        grid.addWidget(self.month_combo, 3, 3)

        self.browser_status = QLabel("Браузер: проверка подключения…")
        self.browser_status.setObjectName("Muted")
        self.browser_status.setWordWrap(True)
        grid.addWidget(self.browser_status, 4, 0, 1, 4)

        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 1)
        root.addWidget(context)

        self.empty_hint = QLabel(
            "Для выбранного дома нет размещенных перечней. Сначала разместите перечень "
            "в разделе «Перечни работ и услуг» и проверьте его статус в ГИС ЖКХ."
        )
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.empty_hint.setStyleSheet("color: #1f1f1f; font-weight: 600;")
        root.addWidget(self.empty_hint)

        month_card = QFrame()
        month_card.setObjectName("Card")
        month_layout = QVBoxLayout(month_card)
        month_layout.setContentsMargins(12, 12, 12, 12)
        month_layout.setSpacing(8)

        self.month_status = QLabel("Период: —")
        self.month_status.setWordWrap(True)
        month_layout.addWidget(self.month_status)

        self.year_status = QLabel("")
        self.year_status.setObjectName("Muted")
        self.year_status.setWordWrap(True)
        month_layout.addWidget(self.year_status)

        fill_actions = QHBoxLayout()
        self.fill_month_button = QPushButton("Заполнить по плану этот месяц")
        self.fill_year_button = QPushButton("Заполнить по плану весь год")
        self.clear_month_button = QPushButton("Очистить факт за месяц")
        fill_actions.addWidget(self.fill_month_button)
        fill_actions.addWidget(self.fill_year_button)
        fill_actions.addWidget(self.clear_month_button)
        fill_actions.addStretch(1)
        month_layout.addLayout(fill_actions)

        publish_actions = QHBoxLayout()
        self.publish_month_button = QPushButton("Разместить выбранный месяц")
        self.publish_year_button = QPushButton("Разместить заполненные месяцы года")
        self.publish_year_button.setObjectName("PrimaryButton")
        self.continue_queue_button = QPushButton("Продолжить очередь")
        self.continue_queue_button.setVisible(False)
        publish_actions.addWidget(self.publish_month_button)
        publish_actions.addWidget(self.publish_year_button)
        publish_actions.addWidget(self.continue_queue_button)
        publish_actions.addStretch(1)
        month_layout.addLayout(publish_actions)

        self.queue_progress = QProgressBar()
        self.queue_progress.setVisible(False)
        self.queue_progress.setTextVisible(True)
        month_layout.addWidget(self.queue_progress)

        self.queue_label = QLabel("")
        self.queue_label.setObjectName("Muted")
        self.queue_label.setWordWrap(True)
        month_layout.addWidget(self.queue_label)

        root.addWidget(month_card)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(12, 12, 12, 12)

        hint = QLabel(
            "Плановые показатели берутся из локального размещенного перечня и плана. "
            "Редактируются только фактические цена, объем, количество и стоимость. "
            "Внеплановые работы, фотографии и акты пока не редактируются приложением; "
            "при обновлении существующей фиксации они сохраняются в ГИС ЖКХ."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        table_layout.addWidget(hint)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "Работа / услуга",
                "Цена план",
                "Цена факт",
                "Объем план",
                "Объем факт",
                "Кол-во план",
                "Кол-во факт",
                "Стоимость план",
                "Стоимость факт",
                "Статус",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 10):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table_layout.addWidget(self.table, 1)
        root.addWidget(table_card, 1)

        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.work_list_combo.currentIndexChanged.connect(self._work_list_changed)
        self.year_combo.currentIndexChanged.connect(self._year_changed)
        self.month_combo.currentIndexChanged.connect(self._month_changed)
        self.fill_month_button.clicked.connect(self._fill_month_from_plan)
        self.fill_year_button.clicked.connect(self._fill_year_from_plan)
        self.clear_month_button.clicked.connect(self._clear_month)
        self.publish_month_button.clicked.connect(self._publish_selected_month)
        self.publish_year_button.clicked.connect(self._publish_year)
        self.continue_queue_button.clicked.connect(self._continue_queue)
        self.table.itemChanged.connect(self._table_item_changed)

        self._set_enabled(False)

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
        value = self.work_list_combo.currentData()
        self._current_work_list_id = str(value) if value else None
        self._current_period_id = None

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
            index = self.year_combo.findData(work_list.base_year)
            self.year_combo.setCurrentIndex(index if index >= 0 else 0)
        self.year_combo.blockSignals(False)
        self._year_changed()

    def _year_changed(self) -> None:
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if self._current_work_list_id
            else None
        )
        year = self.year_combo.currentData()
        if work_list and year is not None:
            for month in active_months_for_work_list(work_list, int(year)):
                self.month_combo.addItem(_MONTHS[month - 1], month)
        self.month_combo.blockSignals(False)
        if self.month_combo.count():
            self.month_combo.setCurrentIndex(0)
        self._month_changed()
        self._refresh_year_status()

    def _month_changed(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            self._current_period_id = None
            self.table.setRowCount(0)
            self.month_status.setText("Период: —")
            self._set_enabled(False)
            return

        month = self.month_combo.currentData()
        if month is None:
            self._current_period_id = None
            self.table.setRowCount(0)
            self._set_enabled(False)
            return

        try:
            period = self.repository.get_or_create_work_fixation_period(
                work_list_id=self._current_work_list_id,
                year=int(self.year_combo.currentData()),
                month=int(month),
            )
            self.repository.refresh_work_fixation_plan_snapshot(period.id)
        except ValueError as exc:
            self._current_period_id = None
            self.table.setRowCount(0)
            self.month_status.setText(str(exc))
            self._set_enabled(False)
            return

        self._current_period_id = period.id
        self._set_enabled(True)
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _load_table(self) -> None:
        if not self._current_period_id:
            self.table.setRowCount(0)
            return

        items = self.repository.list_work_fixation_items(self._current_period_id)
        self._loading = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        try:
            for row, item in enumerate(items):
                values = (
                    item.work_name,
                    item.planned_price,
                    item.fact_price,
                    item.planned_amount,
                    item.fact_amount,
                    str(item.planned_count) if item.planned_count else "",
                    str(item.fact_count) if item.fact_count is not None else "",
                    item.planned_total_cost,
                    item.fact_total_cost,
                    "заполнено" if item_has_fact(item) else "—",
                )
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value or "")
                    if column == 0:
                        cell.setData(_ITEM_ID_ROLE, item.id)
                    if column not in (2, 4, 6, 8):
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if column in (1, 3, 5, 7):
                        cell.setBackground(QBrush(QColor("#f5f6f7")))
                    if column == 9 and item_has_fact(item):
                        cell.setBackground(QBrush(QColor("#e8f4e8")))
                    self.table.setItem(row, column, cell)
        finally:
            self.table.blockSignals(False)
            self._loading = False

    def _table_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.column() not in (2, 4, 6, 8):
            return
        name_cell = self.table.item(cell.row(), 0)
        item_id = name_cell.data(_ITEM_ID_ROLE) if name_cell else None
        if not item_id:
            return

        count_text = self.table.item(cell.row(), 6).text().strip()
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

        try:
            self.repository.update_work_fixation_item(
                str(item_id),
                fact_price=text_at(2),
                fact_amount=text_at(4),
                fact_count=fact_count,
                fact_total_cost=text_at(8),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось сохранить", str(exc))
            self._load_table()
            return

        status = self.table.item(cell.row(), 9)
        if status:
            status.setText("заполнено")
            status.setBackground(QBrush(QColor("#e8f4e8")))
        self._refresh_period_status()
        self._refresh_year_status()

    def _fill_month_from_plan(self) -> None:
        if not self._current_period_id:
            return
        self.repository.fill_work_fixation_from_plan(self._current_period_id)
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _fill_year_from_plan(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        year = int(self.year_combo.currentData())
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return

        answer = QMessageBox.question(
            self,
            "Заполнить весь год по плану?",
            "Фактические показатели всех месяцев этого года будут заменены "
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
            self.repository.fill_work_fixation_from_plan(period.id)

        self._month_changed()
        self._refresh_year_status()

    def _clear_month(self) -> None:
        if not self._current_period_id:
            return
        answer = QMessageBox.question(
            self,
            "Очистить фактические показатели?",
            "Фактические цена, объем, количество и стоимость за выбранный месяц "
            "будут очищены. Плановые показатели останутся.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.clear_work_fixation_period(self._current_period_id)
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()

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
        text = (
            f"{_MONTHS[period.month - 1]} {period.year}: заполнено строк "
            f"{filled} из {len(items)}"
        )
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
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            self.year_status.setText("")
            return
        year = int(self.year_combo.currentData())
        periods = {
            period.month: period
            for period in self.repository.list_work_fixation_periods(
                self._current_work_list_id,
                year=year,
            )
        }
        labels = []
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return
        for month in active_months_for_work_list(work_list, year):
            period = periods.get(month)
            if period is None:
                suffix = "—"
            else:
                items = self.repository.list_work_fixation_items(period.id)
                has_data = any(item_has_fact(item) for item in items)
                status = str(period.gis_status or "").upper()
                if status == "SAVED":
                    suffix = "✓"
                elif status == "ERROR":
                    suffix = "!"
                elif status == "MODIFIED":
                    suffix = "*"
                elif has_data:
                    suffix = "готов"
                else:
                    suffix = "—"
            labels.append(f"{_MONTH_SHORT[month - 1]}: {suffix}")
        self.year_status.setText(" • ".join(labels))

    def _periods_with_data_for_year(self) -> list[WorkFixationPeriod]:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return []
        periods = self.repository.list_work_fixation_periods(
            self._current_work_list_id,
            year=int(self.year_combo.currentData()),
        )
        return [
            period
            for period in periods
            if any(
                item_has_fact(item)
                for item in self.repository.list_work_fixation_items(period.id)
            )
        ]

    def _publish_selected_month(self) -> None:
        if not self._current_period_id:
            return
        period = self.repository.get_work_fixation_period(self._current_period_id)
        if period is None:
            return
        if not any(
            item_has_fact(item)
            for item in self.repository.list_work_fixation_items(period.id)
        ):
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
            "остановится, уже успешно размещенные месяцы останутся размещенными.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._queue_period_ids = [period.id for period in periods]
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_failed_period_id = None
        self.continue_queue_button.setVisible(False)
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
        self.continue_queue_button.setVisible(False)
        self.queue_label.setText("Продолжаем очередь…")
        self._start_next_queue_item()

    def _start_next_queue_item(self) -> None:
        if self._queue_index >= len(self._queue_period_ids):
            self.queue_progress.setValue(len(self._queue_period_ids))
            self.queue_label.setText(
                f"Очередь завершена. Успешно размещено месяцев: {self._queue_successes}."
            )
            self._refresh_year_status()
            return
        period_id = self._queue_period_ids[self._queue_index]
        period = self.repository.get_work_fixation_period(period_id)
        if period:
            self.queue_label.setText(
                f"Размещается {_MONTHS[period.month - 1]} {period.year} "
                f"({self._queue_index + 1} из {len(self._queue_period_ids)})…"
            )
        self._send_period(period_id, queue_mode=True)

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
        try:
            payload = build_fixation_command_payload(
                fias_house_code=fias or "",
                work_list_guid=work_list.gis_work_list_guid or "",
                year=period.year,
                month=period.month,
                items=self.repository.list_work_fixation_items(period.id),
            )
        except ValueError as exc:
            message = str(exc)
            self.repository.update_work_fixation_gis_state(
                period_id,
                gis_status="ERROR",
                gis_last_error=message,
            )
            if queue_mode:
                self._queue_failed_period_id = period_id
                self.queue_label.setText(
                    f"Очередь остановлена на периоде {_MONTHS[period.month - 1]} "
                    f"{period.year}: {message}"
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
            self.repository.update_work_fixation_gis_state(
                period_id,
                reporting_period_guid=result.get("reportingPeriodGuid"),
                gis_status="SAVED",
                gis_last_error=None,
                fixed_item_guids=fixed_guids,
            )
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
                period_now = self.repository.get_work_fixation_period(period_id)
                label = (
                    f"{_MONTHS[period_now.month - 1]} {period_now.year}"
                    if period_now
                    else "текущий месяц"
                )
                self.queue_label.setText(
                    f"Очередь остановлена на периоде {label}: {message}"
                )
                self.continue_queue_button.setText(f"Продолжить с {label}")
                self.continue_queue_button.setVisible(True)
            else:
                QMessageBox.critical(self, "Ошибка обмена с ГИС ЖКХ", message)

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_integration_status()
            if queue_mode and self._queue_failed_period_id is None:
                QTimer.singleShot(250, self._start_next_queue_item)
            elif not queue_mode and command_succeeded["value"]:
                QMessageBox.information(
                    self,
                    "Фиксация размещена",
                    f"Фиксация за {_MONTHS[period.month - 1]} {period.year} "
                    "успешно сохранена в ГИС ЖКХ.",
                )

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _refresh_integration_status(self) -> None:
        status = self.integration_gateway.status()
        if not status.running:
            self.browser_status.setText("Браузер: локальная интеграция не запущена")
            self.browser_status.setStyleSheet("color: #b42318;")
        elif not status.helper_connected:
            self.browser_status.setText(
                "Браузер: помощник не подключен. Откройте ГИС ЖКХ и проверьте расширение."
            )
            self.browser_status.setStyleSheet("color: #8a5b00;")
        elif not status.gis_page_ready:
            self.browser_status.setText(
                "Браузер: помощник подключен, но вкладка ГИС ЖКХ не открыта."
            )
            self.browser_status.setStyleSheet("color: #8a5b00;")
        elif not helper_supports_fixations(status.helper_version):
            version = status.helper_version or "не определена"
            self.browser_status.setText(
                f"Браузер: помощник {version} нужно обновить до 0.1.6 для фиксаций."
            )
            self.browser_status.setStyleSheet("color: #8a5b00;")
        else:
            self.browser_status.setText(
                f"Браузер: ГИС ЖКХ подключен • помощник {status.helper_version}"
            )
            self.browser_status.setStyleSheet("color: #107c10;")
        self._refresh_gis_buttons()

    def _refresh_gis_buttons(self) -> None:
        status = self.integration_gateway.status()
        ready = (
            status.running
            and status.helper_connected
            and status.gis_page_ready
            and helper_supports_fixations(status.helper_version)
            and not self._integration_busy
        )
        self.publish_month_button.setEnabled(bool(self._current_period_id) and ready)
        self.publish_year_button.setEnabled(
            bool(self._current_work_list_id) and ready
        )
        self.continue_queue_button.setEnabled(ready)

    def _set_enabled(self, enabled: bool) -> None:
        self.table.setEnabled(enabled)
        self.fill_month_button.setEnabled(enabled)
        self.fill_year_button.setEnabled(enabled)
        self.clear_month_button.setEnabled(enabled)
        self._refresh_gis_buttons()

    def set_view_mode(self, _mode: str) -> None:
        """Public resize hook used by the main window."""

    def flush_pending_changes(self) -> None:
        """Cells are saved immediately; kept for the main-window hook."""
