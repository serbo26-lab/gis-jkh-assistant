"""Work and service lists page."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database.repository import Repository

from .widgets import SafeComboBox, SafeSpinBox
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


class WorkListsPage(QWidget):
    """Local work-list registry grouped by organization, house and year."""

    def __init__(self, repository: Repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self._current_work_list_id: str | None = None
        self._loading = False

        self._period_timer = QTimer(self)
        self._period_timer.setSingleShot(True)
        self._period_timer.setInterval(450)
        self._period_timer.timeout.connect(self._save_period)

        self._build_ui()
        self.refresh_organizations()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        title_row = QHBoxLayout()
        title = QLabel("Перечни работ и услуг")
        title.setObjectName("Title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.save_status = QLabel("Сохранено")
        self.save_status.setObjectName("SaveStatus")
        title_row.addWidget(self.save_status)
        root.addLayout(title_row)

        context = QFrame()
        context.setObjectName("Card")
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(12, 10, 12, 10)
        context_layout.setSpacing(8)

        row_one = QHBoxLayout()
        row_one.addWidget(QLabel("Управляющая организация"))
        self.organization_combo = SafeComboBox()
        self.organization_combo.setMinimumWidth(220)
        row_one.addWidget(self.organization_combo, 1)
        row_one.addWidget(QLabel("Дом"))
        self.house_combo = SafeComboBox()
        self.house_combo.setMinimumWidth(280)
        row_one.addWidget(self.house_combo, 2)
        context_layout.addLayout(row_one)

        row_two = QHBoxLayout()
        row_two.addWidget(QLabel("Год"))
        self.year_spin = SafeSpinBox()
        self.year_spin.setRange(2015, 2100)
        self.year_spin.setValue(datetime.now().year)
        self.year_spin.setFixedWidth(110)
        row_two.addWidget(self.year_spin)

        row_two.addWidget(QLabel("Шаблон"))
        self.template_combo = SafeComboBox()
        self.template_combo.setMinimumWidth(240)
        row_two.addWidget(self.template_combo, 1)

        self.templates_button = QPushButton("Шаблоны пользователя")
        self.create_button = QPushButton("Создать перечень")
        self.create_button.setObjectName("PrimaryButton")
        row_two.addWidget(self.templates_button)
        row_two.addWidget(self.create_button)
        context_layout.addLayout(row_two)
        root.addWidget(context)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        root.addWidget(splitter, 1)

        left = QFrame()
        left.setObjectName("Card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        header = QHBoxLayout()
        header.addWidget(QLabel("Перечни дома"))
        header.addStretch(1)
        self.list_count = QLabel("0")
        self.list_count.setObjectName("Muted")
        header.addWidget(self.list_count)
        left_layout.addLayout(header)

        self.work_lists = QListWidget()
        self.work_lists.setWordWrap(True)
        self.work_lists.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_layout.addWidget(self.work_lists, 1)

        self.delete_button = QPushButton("Удалить перечень")
        self.delete_button.setObjectName("DangerButton")
        left_layout.addWidget(self.delete_button)
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("Card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 16)
        right_layout.setSpacing(12)

        section = QLabel("Параметры перечня")
        section.setObjectName("SectionTitle")
        right_layout.addWidget(section)

        period_form = QFormLayout()

        start_widget = QWidget()
        start_layout = QHBoxLayout(start_widget)
        start_layout.setContentsMargins(0, 0, 0, 0)
        self.start_month = SafeComboBox()
        self.start_month.addItems(_MONTHS)
        self.start_year = SafeSpinBox()
        self.start_year.setRange(2015, 2100)
        start_layout.addWidget(self.start_month, 1)
        start_layout.addWidget(self.start_year)
        period_form.addRow("Период с", start_widget)

        end_widget = QWidget()
        end_layout = QHBoxLayout(end_widget)
        end_layout.setContentsMargins(0, 0, 0, 0)
        self.end_month = SafeComboBox()
        self.end_month.addItems(_MONTHS)
        self.end_year = SafeSpinBox()
        self.end_year.setRange(2015, 2100)
        end_layout.addWidget(self.end_month, 1)
        end_layout.addWidget(self.end_year)
        period_form.addRow("Период по", end_widget)

        self.template_label = QLabel("—")
        self.status_label = QLabel("—")
        period_form.addRow("Шаблон", self.template_label)
        period_form.addRow("Статус", self.status_label)
        right_layout.addLayout(period_form)

        items_title = QLabel("Работы и услуги")
        items_title.setObjectName("SectionTitle")
        right_layout.addWidget(items_title)

        self.items_table = QTableWidget(0, 5)
        self.items_table.setHorizontalHeaderLabels(
            ["Работа / услуга", "Цена", "Объем", "Кол-во", "Стоимость"]
        )
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.verticalHeader().setVisible(False)
        header_view = self.items_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        right_layout.addWidget(self.items_table, 1)

        splitter.addWidget(right)
        splitter.setSizes([330, 850])

        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.year_spin.valueChanged.connect(self.refresh_work_lists)
        self.templates_button.clicked.connect(self._manage_templates)
        self.create_button.clicked.connect(self._create_work_list)
        self.delete_button.clicked.connect(self._delete_work_list)
        self.work_lists.currentItemChanged.connect(self._work_list_changed)

        for widget in (self.start_month, self.end_month):
            widget.currentIndexChanged.connect(self._queue_period_save)
        for widget in (self.start_year, self.end_year):
            widget.valueChanged.connect(self._queue_period_save)
        self.items_table.itemChanged.connect(self._item_changed)

        self._set_editor_enabled(False)

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
        self._period_timer.stop()
        organization_id = self.organization_combo.currentData()

        self.house_combo.blockSignals(True)
        self.house_combo.clear()
        if organization_id:
            for house in self.repository.list_houses(organization_id):
                self.house_combo.addItem(house.address, house.id)
        self.house_combo.blockSignals(False)

        self._refresh_templates()
        self._house_changed()

    def _refresh_templates(self) -> None:
        organization_id = self.organization_combo.currentData()
        current = self.template_combo.currentData()
        self.template_combo.clear()
        self.template_combo.addItem("Без шаблона (пустой)", None)
        if organization_id:
            for template in self.repository.list_work_templates(organization_id):
                self.template_combo.addItem(template.name, template.id)
        if current:
            index = self.template_combo.findData(current)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)

    def _house_changed(self) -> None:
        self._period_timer.stop()
        self._current_work_list_id = None
        self.refresh_work_lists()

    def refresh_work_lists(self) -> None:
        house_id = self.house_combo.currentData()
        year = self.year_spin.value()
        selected_id = self._current_work_list_id

        self.work_lists.blockSignals(True)
        self.work_lists.clear()
        lists = self.repository.list_work_lists(house_id, year) if house_id else []
        for work_list in lists:
            item = QListWidgetItem(
                f"{work_list.period_label}\n"
                f"{'Локальный' if work_list.status.value == 'local' else work_list.status.value}"
            )
            item.setData(Qt.ItemDataRole.UserRole, work_list.id)
            self.work_lists.addItem(item)
            if work_list.id == selected_id:
                self.work_lists.setCurrentItem(item)
        self.list_count.setText(str(len(lists)))
        self.work_lists.blockSignals(False)

        if self.work_lists.currentItem() is None and self.work_lists.count():
            self.work_lists.setCurrentRow(0)
        elif not self.work_lists.count():
            self._load_work_list(None)

    def _create_work_list(self) -> None:
        house_id = self.house_combo.currentData()
        if not house_id:
            QMessageBox.information(
                self,
                "Не выбран дом",
                "Выберите управляющую организацию и дом.",
            )
            return

        template_id = self.template_combo.currentData()
        work_list = self.repository.create_work_list(
            house_id=house_id,
            base_year=self.year_spin.value(),
            template_id=template_id,
        )
        self._current_work_list_id = work_list.id
        self.refresh_work_lists()

    def _work_list_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        self._save_period()
        work_list_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._load_work_list(work_list_id)

    def _load_work_list(self, work_list_id: str | None) -> None:
        self._current_work_list_id = work_list_id
        if not work_list_id:
            self._set_editor_enabled(False)
            self.items_table.setRowCount(0)
            self.template_label.setText("—")
            self.status_label.setText("—")
            return

        work_list = self.repository.get_work_list(work_list_id)
        if not work_list:
            self._set_editor_enabled(False)
            return

        self._loading = True
        try:
            self._set_editor_enabled(True)
            self.start_month.setCurrentIndex(work_list.start_month - 1)
            self.start_year.setValue(work_list.start_year)
            self.end_month.setCurrentIndex(work_list.end_month - 1)
            self.end_year.setValue(work_list.end_year)
            template = (
                self.repository.get_work_template(work_list.template_id)
                if work_list.template_id
                else None
            )
            self.template_label.setText(template.name if template else "Без шаблона")
            self.status_label.setText(
                "Локальный" if work_list.status.value == "local" else work_list.status.value
            )
            self._load_items(work_list_id)
        finally:
            self._loading = False
        self.save_status.setText("Сохранено")

    def _load_items(self, work_list_id: str) -> None:
        items = self.repository.list_work_list_items(work_list_id)
        self.items_table.blockSignals(True)
        self.items_table.setRowCount(len(items))
        for row, item in enumerate(items):
            name_item = QTableWidgetItem(item.work_name)
            name_item.setData(Qt.ItemDataRole.UserRole, item.id)
            if item.work_code:
                name_item.setToolTip(f"Код работы: {item.work_code}")
            self.items_table.setItem(row, 0, name_item)
            values = (item.price, item.volume, item.quantity, item.cost)
            for column, value in enumerate(values, start=1):
                self.items_table.setItem(row, column, QTableWidgetItem(value or ""))
        self.items_table.blockSignals(False)

    def _queue_period_save(self) -> None:
        if self._loading or not self._current_work_list_id:
            return
        self.save_status.setText("Сохраняется…")
        self._period_timer.start()

    def _save_period(self) -> None:
        self._period_timer.stop()
        if self._loading or not self._current_work_list_id:
            return

        start_year = self.start_year.value()
        start_month = self.start_month.currentIndex() + 1
        end_year = self.end_year.value()
        end_month = self.end_month.currentIndex() + 1

        if (start_year, start_month) > (end_year, end_month):
            self.save_status.setText("Некорректный период")
            return

        self.repository.update_work_list_period(
            self._current_work_list_id,
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
            end_month=end_month,
        )
        self.save_status.setText("Сохранено")
        self.refresh_work_lists()

    def _item_changed(self, table_item: QTableWidgetItem) -> None:
        if self._loading or not self._current_work_list_id:
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
        self.repository.update_work_list_item(
            item_id,
            **{field: table_item.text().strip() or None},
        )
        self.save_status.setText("Сохранено")

    def _manage_templates(self) -> None:
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

    def _delete_work_list(self) -> None:
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

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.start_month,
            self.start_year,
            self.end_month,
            self.end_year,
            self.items_table,
            self.delete_button,
        ):
            widget.setEnabled(enabled)

    def set_compact_mode(self, compact: bool) -> None:
        self.splitter.setSizes([280, 620] if compact else [330, 850])

    def flush_pending_changes(self) -> None:
        self._save_period()
