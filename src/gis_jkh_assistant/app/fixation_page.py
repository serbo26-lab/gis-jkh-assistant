"""Monthly work/service fixation page."""

from __future__ import annotations

import mimetypes

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

from .fixation_dialogs import AddUnplannedWorkDialog, FixationPhotoDialog
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
        self._current_month: int | None = None
        self._month_button_columns = 12
        self._queue_period_ids: list[str] = []
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_failed_period_id: str | None = None

        self._build_ui()
        self.refresh_organizations()
        self._refresh_integration_status()

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

        self.month_buttons_layout = QGridLayout()
        self.month_buttons_layout.setHorizontalSpacing(6)
        self.month_buttons_layout.setVerticalSpacing(6)
        self.month_buttons: dict[int, QPushButton] = {}
        for month, name in enumerate(_MONTHS, start=1):
            button = QPushButton(f"{name}\n—")
            button.setObjectName("MonthButton")
            button.setCheckable(True)
            button.setMinimumWidth(72)
            button.clicked.connect(
                lambda _checked=False, value=month: self._select_month(value)
            )
            self.month_buttons[month] = button
        month_layout.addLayout(self.month_buttons_layout)
        self._relayout_month_buttons()

        self.month_status = QLabel("Период: —")
        self.month_status.setObjectName("Muted")
        self.month_status.setWordWrap(True)
        month_layout.addWidget(self.month_status)

        fill_actions = QHBoxLayout()
        self.fill_month_button = QPushButton("Заполнить по плану этот месяц")
        self.fill_year_button = QPushButton("Заполнить по плану весь год")
        self.clear_month_button = QPushButton("Очистить факт плановых работ")
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
            "Редактируются фактические цена, объем, количество и стоимость. "
            "К плановой работе можно прикрепить фото. Акты относятся к следующему "
            "разделу «Оценка качества» и здесь не редактируются."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        table_layout.addWidget(hint)

        self.table = QTableWidget(0, 11)
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
                "Фото",
                "Статус",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 11):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table_layout.addWidget(self.table)
        root.addWidget(table_card)

        unplanned_card = QFrame()
        unplanned_card.setObjectName("Card")
        unplanned_layout = QVBoxLayout(unplanned_card)
        unplanned_layout.setContentsMargins(12, 12, 12, 12)

        unplanned_header = QHBoxLayout()
        unplanned_title = QLabel("Внеплановые работы")
        unplanned_title.setObjectName("SectionTitle")
        self.add_unplanned_button = QPushButton("Добавить внеплановую работу")
        unplanned_header.addWidget(unplanned_title)
        unplanned_header.addStretch(1)
        unplanned_header.addWidget(self.add_unplanned_button)
        unplanned_layout.addLayout(unplanned_header)

        unplanned_hint = QLabel(
            "Внеплановой может быть только работа, которой нет в плане выбранного периода. "
            "Справочник работ загружается из ГИС ЖКХ. Для строки можно указать фото и комментарий."
        )
        unplanned_hint.setObjectName("Muted")
        unplanned_hint.setWordWrap(True)
        unplanned_layout.addWidget(unplanned_hint)

        self.unplanned_table = QTableWidget(0, 9)
        self.unplanned_table.setHorizontalHeaderLabels(
            [
                "Работа / услуга",
                "Вид / ед.",
                "Цена",
                "Объем",
                "Количество",
                "Стоимость",
                "Комментарий",
                "Фото",
                "",
            ]
        )
        self.unplanned_table.setAlternatingRowColors(True)
        self.unplanned_table.verticalHeader().setVisible(False)
        unplanned_header_view = self.unplanned_table.horizontalHeader()
        unplanned_header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        unplanned_header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4, 5, 7, 8):
            unplanned_header_view.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        unplanned_layout.addWidget(self.unplanned_table)
        root.addWidget(unplanned_card)
        root.addStretch(1)

        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.work_list_combo.currentIndexChanged.connect(self._work_list_changed)
        self.year_combo.currentIndexChanged.connect(self._year_changed)
        self.fill_month_button.clicked.connect(self._fill_month_from_plan)
        self.fill_year_button.clicked.connect(self._fill_year_from_plan)
        self.clear_month_button.clicked.connect(self._clear_month)
        self.publish_month_button.clicked.connect(self._publish_selected_month)
        self.publish_year_button.clicked.connect(self._publish_year)
        self.continue_queue_button.clicked.connect(self._continue_queue)
        self.table.itemChanged.connect(self._table_item_changed)
        self.unplanned_table.itemChanged.connect(self._unplanned_item_changed)
        self.add_unplanned_button.clicked.connect(self._request_add_unplanned)

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
        self._current_month = None

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
        except ValueError as exc:
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
            item = self.month_buttons_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(self)
        for index, month in enumerate(range(1, 13)):
            row, column = divmod(index, self._month_button_columns)
            button = self.month_buttons[month]
            button.setParent(self)
            self.month_buttons_layout.addWidget(button, row, column)
            self.month_buttons_layout.setColumnStretch(column, 1)

    def _resize_table_to_rows(
        self,
        table: QTableWidget,
        *,
        max_rows: int,
        minimum_rows: int = 1,
    ) -> None:
        row_count = table.rowCount()
        visible_rows = min(max(row_count, minimum_rows), max_rows)
        row_height = max(24, table.verticalHeader().defaultSectionSize())
        header_height = max(26, table.horizontalHeader().height())
        frame = table.frameWidth() * 2
        height = header_height + visible_rows * row_height + frame + 4
        table.setFixedHeight(height)
        table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if row_count > max_rows
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def _resize_planned_table(self) -> None:
        self._resize_table_to_rows(self.table, max_rows=15)

    def _resize_unplanned_table(self) -> None:
        self._resize_table_to_rows(self.unplanned_table, max_rows=8)


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
                for column, value in enumerate(values[:9]):
                    cell = QTableWidgetItem(value or "")
                    if column == 0:
                        cell.setData(_ITEM_ID_ROLE, item.id)
                    if column not in (2, 4, 6, 8):
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if column in (1, 3, 5, 7):
                        cell.setBackground(QBrush(QColor("#f5f6f7")))
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

                status = QTableWidgetItem(values[9] or "")
                status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if item_has_fact(item):
                    status.setBackground(QBrush(QColor("#e8f4e8")))
                self.table.setItem(row, 10, status)
        finally:
            self.table.blockSignals(False)
            self._loading = False
        self._resize_planned_table()

    def _load_unplanned_table(self) -> None:
        if not self._current_period_id:
            self.unplanned_table.setRowCount(0)
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
                    " / ".join(
                        value
                        for value in (item.work_type_code, item.unit_name)
                        if value
                    ),
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
                    if column in (0, 1):
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
                self.unplanned_table.setCellWidget(row, 7, photo_button)

                remove_button = QPushButton("Удалить")
                remove_button.clicked.connect(
                    lambda _checked=False, item_id=item.id: self._delete_unplanned(item_id)
                )
                self.unplanned_table.setCellWidget(row, 8, remove_button)
        finally:
            self.unplanned_table.blockSignals(False)
            self._loading = False
        self._resize_unplanned_table()

    def _open_planned_photos(self, item_id: str) -> None:
        dialog = FixationPhotoDialog(
            self.repository,
            fixation_item_id=item_id,
            parent=self,
        )
        dialog.exec()
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()

    def _open_unplanned_photos(self, item_id: str) -> None:
        dialog = FixationPhotoDialog(
            self.repository,
            unplanned_item_id=item_id,
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
        if self._loading or cell.column() not in (2, 3, 4, 5, 6):
            return
        name_cell = self.unplanned_table.item(cell.row(), 0)
        item_id = name_cell.data(_ITEM_ID_ROLE) if name_cell else None
        if not item_id:
            return

        count_text = self.unplanned_table.item(cell.row(), 4).text().strip()
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

        def text_at(column: int) -> str | None:
            value = self.unplanned_table.item(cell.row(), column).text().strip()
            return value or None

        try:
            self.repository.update_work_fixation_unplanned_item(
                str(item_id),
                fact_price=text_at(2),
                fact_amount=text_at(3),
                fact_count=fact_count,
                fact_total_cost=text_at(5),
                comment=text_at(6),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось сохранить", str(exc))
            self._load_unplanned_table()
            return
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
                "Для выбора внеплановой работы нужен подключенный браузерный помощник 0.1.7+.",
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
                QMessageBox.warning(self, "Не удалось добавить работу", str(exc))
                return
            self._load_unplanned_table()
            self._refresh_period_status()
            self._refresh_year_status()

        def failed(message: str) -> None:
            QMessageBox.critical(self, "Не удалось получить справочник работ", message)

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

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

        status = self.table.item(cell.row(), 10)
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
        year_value = self.year_combo.currentData()
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if self._current_work_list_id
            else None
        )
        if not work_list or year_value is None:
            for month, button in self.month_buttons.items():
                button.setText(f"{_MONTHS[month - 1]}\n—")
                button.setEnabled(False)
                button.setChecked(False)
            return

        year = int(year_value)
        active_months = set(active_months_for_work_list(work_list, year))
        periods = {
            period.month: period
            for period in self.repository.list_work_fixation_periods(
                self._current_work_list_id,
                year=year,
            )
        }

        for month, button in self.month_buttons.items():
            if month not in active_months:
                status_text = "вне периода"
                button.setEnabled(False)
            else:
                button.setEnabled(True)
                period = periods.get(month)
                if period is None:
                    status_text = "—"
                else:
                    has_data = self._period_has_data(period.id)
                    status = str(period.gis_status or "").upper()
                    if status == "SAVED":
                        status_text = "размещено"
                    elif status == "ERROR":
                        status_text = "ошибка"
                    elif status == "MODIFIED":
                        status_text = "изменено"
                    elif has_data:
                        status_text = "готов"
                    else:
                        status_text = "—"

            button.setText(f"{_MONTHS[month - 1]}\n{status_text}")
            button.setChecked(month == self._current_month and month in active_months)


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
            if self._period_has_data(period.id)
        ]

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
            unplanned_guids = {
                str(item.get("localItemId")): str(item.get("guid"))
                for item in result.get("unplannedItems", [])
                if item.get("localItemId") and item.get("guid")
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
        """Refresh GIS-dependent actions; visible status lives in the sidebar."""

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
        self.add_unplanned_button.setEnabled(bool(self._current_period_id) and ready)
        self.continue_queue_button.setEnabled(ready)

    def _set_enabled(self, enabled: bool) -> None:
        self.table.setEnabled(enabled)
        self.unplanned_table.setEnabled(enabled)
        self.fill_month_button.setEnabled(enabled)
        self.fill_year_button.setEnabled(enabled)
        self.clear_month_button.setEnabled(enabled)
        self._refresh_gis_buttons()

    def set_view_mode(self, mode: str) -> None:
        """Adapt month navigation density to the available window size."""

        columns = 12 if mode == "normal" else 6
        if columns != self._month_button_columns:
            self._relayout_month_buttons(columns)
        self._resize_planned_table()
        self._resize_unplanned_table()

    def flush_pending_changes(self) -> None:
        """Cells are saved immediately; kept for the main-window hook."""
