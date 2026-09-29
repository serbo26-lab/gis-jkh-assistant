"""Work and service lists page."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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


class WorkListsPage(QWidget):
    """Local work-list registry grouped by organization and house."""

    def __init__(self, repository: Repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self._current_work_list_id: str | None = None
        self._loading = False
        self._period_dirty = False
        self._loaded_period: tuple[int, int, int, int] | None = None

        self._period_timer = QTimer(self)
        self._period_timer.setSingleShot(True)
        self._period_timer.setInterval(650)
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
        row_two.addWidget(QLabel("Год нового перечня"))
        self.year_spin = SafeSpinBox()
        self.year_spin.setRange(2015, 2100)
        self.year_spin.setValue(datetime.now().year)
        self.year_spin.setFixedWidth(138)
        row_two.addWidget(self.year_spin)

        row_two.addWidget(QLabel("Шаблон"))
        self.template_combo = SafeComboBox()
        self.template_combo.setMinimumWidth(240)
        row_two.addWidget(self.template_combo, 1)

        self.templates_button = QPushButton("Шаблоны пользователя")
        self.create_from_current_button = QPushButton("Создать на основе выбранного")
        self.create_button = QPushButton("Создать перечень")
        self.create_button.setObjectName("PrimaryButton")
        row_two.addWidget(self.templates_button)
        row_two.addWidget(self.create_from_current_button)
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
        header.addWidget(QLabel("Все перечни дома"))
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

        self.selected_label = QLabel("Выбранный перечень: —")
        self.selected_label.setObjectName("Muted")
        right_layout.addWidget(self.selected_label)

        period_form = QFormLayout()

        start_widget = QWidget()
        start_layout = QHBoxLayout(start_widget)
        start_layout.setContentsMargins(0, 0, 0, 0)
        self.start_month = SafeComboBox()
        self.start_month.addItems(_MONTHS)
        self.start_year = SafeSpinBox()
        self.start_year.setRange(2015, 2100)
        self.start_year.setFixedWidth(132)
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
        self.end_year.setFixedWidth(132)
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

        input_hint = QLabel(
            "Для каждой строки используется один из двух вариантов: "
            "«Цена + Объем + Кол-во» или «Кол-во + Стоимость». "
            "Недоступные поля блокируются автоматически."
        )
        input_hint.setWordWrap(True)
        input_hint.setObjectName("Muted")
        right_layout.addWidget(input_hint)

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
        splitter.setSizes([340, 840])

        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.templates_button.clicked.connect(self._manage_templates)
        self.create_button.clicked.connect(self._create_work_list)
        self.create_from_current_button.clicked.connect(self._clone_current_work_list)
        self.delete_button.clicked.connect(self._delete_work_list)
        self.work_lists.currentItemChanged.connect(self._work_list_changed)

        for widget in (self.start_month, self.end_month):
            widget.currentIndexChanged.connect(self._queue_period_save)
        for widget in (self.start_year, self.end_year):
            widget.valueChanged.connect(self._queue_period_save)
        self.items_table.itemChanged.connect(self._item_changed)

        self._set_editor_enabled(False)

    def refresh_organizations(self) -> None:
        self._save_period()
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
        self.template_combo.addItem("Без шаблона (пустой)", None)
        if organization_id:
            for template in self.repository.list_work_templates(organization_id):
                self.template_combo.addItem(template.name, template.id)
        if current:
            index = self.template_combo.findData(current)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
        self.template_combo.blockSignals(False)

    def _house_changed(self) -> None:
        self._save_period()
        self._current_work_list_id = None
        self.refresh_work_lists()

    def refresh_work_lists(self, select_id: str | None = None) -> None:
        """Show every work list for the selected house, independent of creation year."""

        house_id = self.house_combo.currentData()
        selected_id = select_id or self._current_work_list_id

        self.work_lists.blockSignals(True)
        self.work_lists.clear()
        selected_row = -1
        lists = self.repository.list_work_lists(house_id) if house_id else []

        for row, work_list in enumerate(lists):
            template = (
                self.repository.get_work_template(work_list.template_id)
                if work_list.template_id
                else None
            )
            template_name = template.name if template else "Без шаблона"
            status = "Локальный" if work_list.status.value == "local" else work_list.status.value
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
        elif self.work_lists.count():
            self.work_lists.setCurrentRow(self.work_lists.count() - 1)
        self.work_lists.blockSignals(False)

        current = self.work_lists.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._load_work_list(current_id)

    def _creation_year(self) -> int:
        self.year_spin.interpretText()
        return self.year_spin.value()

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
        work_list = self.repository.create_work_list(
            house_id=house_id,
            base_year=self._creation_year(),
            template_id=template_id,
        )
        self.refresh_work_lists(select_id=work_list.id)

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

        cloned = self.repository.clone_work_list(source.id, target_year)
        self.year_spin.setValue(target_year)
        self.refresh_work_lists(select_id=cloned.id)

    def _work_list_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        self._save_period()
        work_list_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._load_work_list(work_list_id)

    def _load_work_list(self, work_list_id: str | None) -> None:
        self._period_timer.stop()
        self._period_dirty = False
        self._current_work_list_id = work_list_id

        if not work_list_id:
            self._loaded_period = None
            self._set_editor_enabled(False)
            self.items_table.setRowCount(0)
            self.template_label.setText("—")
            self.status_label.setText("—")
            self.selected_label.setText("Выбранный перечень: —")
            self.save_status.setText("Сохранено")
            return

        work_list = self.repository.get_work_list(work_list_id)
        if not work_list:
            self._loaded_period = None
            self._set_editor_enabled(False)
            return

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
            self.status_label.setText(
                "Локальный" if work_list.status.value == "local" else work_list.status.value
            )
            self.selected_label.setText(f"Выбранный перечень: {work_list.period_label}")
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

        self.items_table.blockSignals(False)

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

    def _save_period(self) -> None:
        self._period_timer.stop()
        if self._loading or not self._current_work_list_id or not self._period_dirty:
            return

        start_year, start_month, end_year, end_month = self._current_period()
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
        self._loaded_period = (start_year, start_month, end_year, end_month)
        self._period_dirty = False
        self.save_status.setText("Сохранено")

        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list:
            self.selected_label.setText(f"Выбранный перечень: {work_list.period_label}")
            self._update_list_item_text(work_list.id)

    def _update_list_item_text(self, work_list_id: str) -> None:
        work_list = self.repository.get_work_list(work_list_id)
        if not work_list:
            return
        template = (
            self.repository.get_work_template(work_list.template_id)
            if work_list.template_id
            else None
        )
        template_name = template.name if template else "Без шаблона"
        status = "Локальный" if work_list.status.value == "local" else work_list.status.value

        for row in range(self.work_lists.count()):
            item = self.work_lists.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == work_list_id:
                item.setText(f"{work_list.period_label}\n{template_name} • {status}")
                break

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

    def _apply_row_input_mode(self, row: int) -> None:
        """Mirror the mutually exclusive input schemes used by GIS ЖКХ."""

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
                item.setBackground(QBrush(QColor("#f0f1f3")))
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

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.start_month,
            self.start_year,
            self.end_month,
            self.end_year,
            self.items_table,
            self.delete_button,
            self.create_from_current_button,
        ):
            widget.setEnabled(enabled)

    def set_compact_mode(self, compact: bool) -> None:
        self.splitter.setSizes([290, 610] if compact else [340, 840])

    def flush_pending_changes(self) -> None:
        self._save_period()
