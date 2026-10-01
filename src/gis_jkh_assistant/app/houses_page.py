"""The first application page: management organizations and houses."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import RecordSource, Repository
from gis_jkh_assistant.gis_files.house_data import (
    HouseDataImportError,
    normalize_positive_decimal,
    parse_house_data_template,
    write_house_data_template,
)
from gis_jkh_assistant.gis_files.housing_objects import (
    HousingObjectsImportError,
    parse_housing_objects_export,
)

from .dialogs import HouseDialog, HouseTariffDialog, OrganizationDialog
from .widgets import SafeSpinBox


class HousesPage(QWidget):
    """Editable houses registry with automatic persistence."""

    AUTOSAVE_DELAY_MS = 500

    def __init__(self, repository: Repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self._current_house_id: str | None = None
        self._pending_house_id: str | None = None
        self._pending_fields: dict[str, object] = {}
        self._loading_card = False

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(self.AUTOSAVE_DELAY_MS)
        self._autosave_timer.timeout.connect(self._flush_autosave)

        self._build_ui()
        self.refresh_organizations()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        title_row = QHBoxLayout()
        title = QLabel("Дома")
        title.setObjectName("Title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.save_status = QLabel("Сохранено")
        self.save_status.setObjectName("SaveStatus")
        title_row.addWidget(self.save_status)
        root.addLayout(title_row)

        toolbar = QFrame()
        toolbar.setObjectName("Card")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 10, 12, 10)
        toolbar_layout.setSpacing(8)

        organization_row = QHBoxLayout()
        organization_row.addWidget(QLabel("Управляющая организация"))
        self.organization_combo = QComboBox()
        self.organization_combo.setMinimumWidth(180)
        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        organization_row.addWidget(self.organization_combo, 1)
        toolbar_layout.addLayout(organization_row)

        self.add_org_button = QPushButton("Добавить УО")
        self.edit_org_button = QPushButton("Изменить УО")
        self.delete_org_button = QPushButton("Удалить УО")
        self.import_button = QPushButton("Импорт домов из ГИС ЖКХ…")
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.setToolTip(
            "Нужен XLSX из выгрузки ГИС ЖКХ: "
            "«Реестр объектов жилищного фонда» → «Выгрузить информацию». "
            "ГИС формирует архив «Объекты жилищного фонда.zip» — "
            "распакуйте его и выберите XLSX внутри."
        )
        self.add_house_button = QPushButton("Добавить дом")
        self.export_house_data_button = QPushButton("Шаблон площадей и тарифов…")
        self.import_house_data_button = QPushButton("Импорт площадей и тарифов…")

        self.add_org_button.clicked.connect(self._add_organization)
        self.edit_org_button.clicked.connect(self._edit_organization)
        self.delete_org_button.clicked.connect(self._delete_organization)
        self.import_button.clicked.connect(self._import_file)
        self.add_house_button.clicked.connect(self._add_house)
        self.export_house_data_button.clicked.connect(self._export_house_data_template)
        self.import_house_data_button.clicked.connect(self._import_house_data)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        for button in (
            self.add_org_button,
            self.edit_org_button,
            self.delete_org_button,
            self.import_button,
            self.add_house_button,
        ):
            actions_row.addWidget(button)
        actions_row.addStretch(1)
        toolbar_layout.addLayout(actions_row)

        import_hint = QLabel(
            "Для импорта домов используйте выгрузку ГИС ЖКХ: "
            "«Реестр объектов жилищного фонда» → «Выгрузить информацию». "
            "Из архива «Объекты жилищного фонда.zip» распакуйте и выберите XLSX-файл."
        )
        import_hint.setWordWrap(True)
        import_hint.setObjectName("Muted")
        toolbar_layout.addWidget(import_hint)

        house_data_row = QHBoxLayout()
        house_data_row.setSpacing(8)
        house_data_row.addWidget(self.export_house_data_button)
        house_data_row.addWidget(self.import_house_data_button)
        house_data_row.addStretch(1)
        toolbar_layout.addLayout(house_data_row)

        house_data_hint = QLabel(
            "Для площадей и тарифов приложение формирует свой простой XLSX-шаблон "
            "по домам выбранной УО. На листе «Тарифы» один дом занимает одну строку, "
            "а история идет вправо парами «Действует с / Тариф». Даты можно вводить "
            "как 08.2025 (с 01.08.2025) или как точную дату 10.08.2025."
        )
        house_data_hint.setWordWrap(True)
        house_data_hint.setObjectName("Muted")
        toolbar_layout.addWidget(house_data_hint)

        root.addWidget(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        root.addWidget(splitter, 1)

        left = QFrame()
        left.setObjectName("Card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по адресу")
        self.search_edit.textChanged.connect(self.refresh_houses)
        left_layout.addWidget(self.search_edit)

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("Дома"))
        count_row.addStretch(1)
        self.house_count_label = QLabel("0")
        self.house_count_label.setObjectName("Muted")
        count_row.addWidget(self.house_count_label)
        left_layout.addLayout(count_row)

        self.house_list = QListWidget()
        self.house_list.setWordWrap(True)
        self.house_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.house_list.currentItemChanged.connect(self._house_selection_changed)
        left_layout.addWidget(self.house_list, 1)

        self.delete_house_button = QPushButton("Удалить дом")
        self.delete_house_button.setObjectName("DangerButton")
        self.delete_house_button.clicked.connect(self._delete_house)
        left_layout.addWidget(self.delete_house_button)

        splitter.addWidget(left)

        scroll = QScrollArea()
        scroll.setObjectName("Card")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.card_widget = QWidget()
        self.card_layout = QVBoxLayout(self.card_widget)
        self.card_layout.setContentsMargins(18, 16, 18, 18)
        self.card_layout.setSpacing(14)
        scroll.setWidget(self.card_widget)
        splitter.addWidget(scroll)
        splitter.setSizes([420, 780])

        self._build_house_card()
        self._set_card_enabled(False)

    def _build_house_card(self) -> None:
        section = QLabel("Карточка дома")
        section.setObjectName("SectionTitle")
        self.card_layout.addWidget(section)

        self.address_edit = QLineEdit()
        self.card_org_combo = QComboBox()
        self.source_label = QLabel("—")
        form_main = QFormLayout()
        form_main.addRow("Адрес", self.address_edit)
        form_main.addRow("Управляющая организация", self.card_org_combo)
        form_main.addRow("Источник", self.source_label)
        self.card_layout.addLayout(form_main)

        ids_title = QLabel("Идентификаторы")
        ids_title.setObjectName("SectionTitle")
        self.card_layout.addWidget(ids_title)
        self.object_guid_edit = QLineEdit()
        self.gis_guid_edit = QLineEdit()
        self.fias_guid_edit = QLineEdit()
        self.unique_number_edit = QLineEdit()
        self.cadastral_edit = QLineEdit()
        ids_form = QFormLayout()
        ids_form.addRow("GUID объекта жилищного фонда", self.object_guid_edit)
        ids_form.addRow("HOUSEGUID ГИС ЖКХ", self.gis_guid_edit)
        ids_form.addRow("HOUSEGUID ФИАС", self.fias_guid_edit)
        ids_form.addRow("Уникальный номер дома", self.unique_number_edit)
        ids_form.addRow("Кадастровый номер", self.cadastral_edit)
        self.card_layout.addLayout(ids_form)

        premises_title = QLabel("Помещения")
        premises_title.setObjectName("SectionTitle")
        self.card_layout.addWidget(premises_title)
        self.residential_spin = SafeSpinBox()
        self.residential_spin.setRange(0, 100000)
        self.non_residential_spin = SafeSpinBox()
        self.non_residential_spin.setRange(0, 100000)
        self.total_label = QLabel("0")
        premises_form = QFormLayout()
        premises_form.addRow("Жилых", self.residential_spin)
        premises_form.addRow("Нежилых", self.non_residential_spin)
        premises_form.addRow("Итого", self.total_label)
        self.card_layout.addLayout(premises_form)

        billing_title = QLabel("Начисления")
        billing_title.setObjectName("SectionTitle")
        self.card_layout.addWidget(billing_title)

        self.charge_area_edit = QLineEdit()
        self.charge_area_edit.setPlaceholderText("Например: 395,6")
        billing_form = QFormLayout()
        billing_form.addRow("Площадь к начислению, м²", self.charge_area_edit)
        self.card_layout.addLayout(billing_form)

        tariff_header = QHBoxLayout()
        tariff_header.addWidget(QLabel("Тарифы по периодам"))
        tariff_header.addStretch(1)
        self.add_tariff_button = QPushButton("+ Добавить тариф")
        self.edit_tariff_button = QPushButton("Изменить")
        self.delete_tariff_button = QPushButton("Удалить")
        self.delete_tariff_button.setObjectName("DangerButton")
        tariff_header.addWidget(self.add_tariff_button)
        tariff_header.addWidget(self.edit_tariff_button)
        tariff_header.addWidget(self.delete_tariff_button)
        self.card_layout.addLayout(tariff_header)

        tariff_hint = QLabel(
            "Добавляйте новую строку только когда тариф меняется. "
            "Можно указать месяц (08.2025 = с 01.08.2025) или точную дату "
            "(10.08.2025). Последнее значение действует до следующего изменения."
        )
        tariff_hint.setWordWrap(True)
        tariff_hint.setObjectName("Muted")
        self.card_layout.addWidget(tariff_hint)

        self.tariffs_table = QTableWidget(0, 3)
        self.tariffs_table.setHorizontalHeaderLabels(
            ["Действует с", "Действует до", "Тариф, руб./м² в месяц"]
        )
        self.tariffs_table.verticalHeader().setVisible(False)
        self.tariffs_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tariffs_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tariffs_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tariffs_table.setAlternatingRowColors(True)
        self.tariffs_table.setMinimumHeight(150)
        tariff_header_view = self.tariffs_table.horizontalHeader()
        tariff_header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tariff_header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tariff_header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.card_layout.addWidget(self.tariffs_table)

        self.add_tariff_button.clicked.connect(self._add_tariff)
        self.edit_tariff_button.clicked.connect(self._edit_tariff)
        self.delete_tariff_button.clicked.connect(self._delete_tariff)
        self.tariffs_table.itemDoubleClicked.connect(lambda _item: self._edit_tariff())

        service_title = QLabel("Служебное")
        service_title.setObjectName("SectionTitle")
        self.card_layout.addWidget(service_title)
        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Комментарий")
        self.comment_edit.setMaximumHeight(130)
        self.created_label = QLabel("—")
        self.updated_label = QLabel("—")
        service_form = QFormLayout()
        service_form.addRow("Комментарий", self.comment_edit)
        service_form.addRow("Создано", self.created_label)
        service_form.addRow("Изменено", self.updated_label)
        self.card_layout.addLayout(service_form)
        self.card_layout.addStretch(1)

        field_bindings = {
            self.address_edit: "address",
            self.object_guid_edit: "house_object_guid",
            self.gis_guid_edit: "gis_house_guid",
            self.fias_guid_edit: "fias_house_guid",
            self.unique_number_edit: "gis_unique_house_number",
            self.cadastral_edit: "cadastral_number",
            self.charge_area_edit: "charge_area",
        }
        for widget, field in field_bindings.items():
            widget.textEdited.connect(
                lambda _text, field=field, widget=widget: self._queue_text(field, widget)
            )

        self.comment_edit.textChanged.connect(self._queue_comment)
        self.residential_spin.valueChanged.connect(
            lambda value: self._queue_change("residential_premises_count", value)
        )
        self.non_residential_spin.valueChanged.connect(
            lambda value: self._queue_change("non_residential_premises_count", value)
        )
        self.card_org_combo.currentIndexChanged.connect(self._queue_organization_change)

    def set_compact_mode(self, compact: bool) -> None:
        """Adjust the page density without allowing controls to collapse into unreadable shapes."""
        if compact:
            self.splitter.setSizes([300, 620])
            self.card_layout.setContentsMargins(12, 12, 12, 14)
            self.card_layout.setSpacing(10)
        else:
            self.splitter.setSizes([400, 820])
            self.card_layout.setContentsMargins(18, 16, 18, 18)
            self.card_layout.setSpacing(14)

    def refresh_organizations(self, select_id: str | None = None) -> None:
        current = select_id or self.organization_combo.currentData()
        organizations = self.repository.list_organizations()

        self.organization_combo.blockSignals(True)
        self.organization_combo.clear()
        for organization in organizations:
            self.organization_combo.addItem(organization.name, organization.id)
        if current:
            index = self.organization_combo.findData(current)
            if index >= 0:
                self.organization_combo.setCurrentIndex(index)
        self.organization_combo.blockSignals(False)

        self._reload_card_org_combo()
        self._organization_changed()

    def _reload_card_org_combo(self) -> None:
        current_id = self.card_org_combo.currentData()
        self.card_org_combo.blockSignals(True)
        self.card_org_combo.clear()
        for organization in self.repository.list_organizations():
            self.card_org_combo.addItem(organization.name, organization.id)
        if current_id:
            index = self.card_org_combo.findData(current_id)
            if index >= 0:
                self.card_org_combo.setCurrentIndex(index)
        self.card_org_combo.blockSignals(False)

    def _organization_changed(self) -> None:
        self._flush_autosave()
        self.refresh_houses()

    def refresh_houses(self) -> None:
        organization_id = self.organization_combo.currentData()
        selected_id = self._current_house_id
        self.house_list.blockSignals(True)
        self.house_list.clear()
        if not organization_id:
            self.house_count_label.setText("0")
            self.house_list.blockSignals(False)
            self._load_house(None)
            return

        houses = self.repository.list_houses(organization_id, self.search_edit.text())
        for house in houses:
            item = QListWidgetItem(
                f"{house.address}\nЖилых: {house.residential_premises_count}   "
                f"Нежилых: {house.non_residential_premises_count}"
            )
            item.setData(Qt.UserRole, house.id)
            self.house_list.addItem(item)
        self.house_count_label.setText(str(len(houses)))
        self.house_list.blockSignals(False)

        if selected_id:
            for row in range(self.house_list.count()):
                item = self.house_list.item(row)
                if item.data(Qt.UserRole) == selected_id:
                    self.house_list.setCurrentItem(item)
                    return
        if self.house_list.count():
            self.house_list.setCurrentRow(0)
        else:
            self._load_house(None)

    def _house_selection_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        self._flush_autosave()
        house_id = current.data(Qt.UserRole) if current else None
        self._load_house(house_id)

    def _load_house(self, house_id: str | None) -> None:
        self._current_house_id = house_id
        if not house_id:
            self.tariffs_table.setRowCount(0)
            self._set_card_enabled(False)
            return
        house = self.repository.get_house(house_id)
        if not house:
            self._set_card_enabled(False)
            return

        self._loading_card = True
        try:
            self._set_card_enabled(True)
            self.address_edit.setText(house.address)
            self.object_guid_edit.setText(house.house_object_guid or "")
            self.gis_guid_edit.setText(house.gis_house_guid or "")
            self.fias_guid_edit.setText(house.fias_house_guid or "")
            self.unique_number_edit.setText(house.gis_unique_house_number or "")
            self.cadastral_edit.setText(house.cadastral_number or "")
            self.charge_area_edit.setText(house.charge_area or "")
            self.residential_spin.setValue(house.residential_premises_count)
            self.non_residential_spin.setValue(house.non_residential_premises_count)
            self.total_label.setText(str(house.total_premises_count))
            self.source_label.setText(
                "Импорт" if house.source == RecordSource.IMPORT else "Вручную"
            )
            self.comment_edit.setPlainText(house.comment or "")
            self.created_label.setText(house.created_at or "—")
            self.updated_label.setText(house.updated_at or "—")
            index = self.card_org_combo.findData(house.organization_id)
            if index >= 0:
                self.card_org_combo.setCurrentIndex(index)
        finally:
            self._loading_card = False
        self._refresh_tariffs()
        self._set_save_status("Сохранено")

    def _refresh_tariffs(self, select_id: str | None = None) -> None:
        self.tariffs_table.setRowCount(0)
        if not self._current_house_id:
            return

        tariffs = self.repository.list_house_tariffs(self._current_house_id)
        for tariff in tariffs:
            row = self.tariffs_table.rowCount()
            self.tariffs_table.insertRow(row)

            start_item = QTableWidgetItem(
                f"{tariff.start_month:02d}.{tariff.start_year}"
            )
            start_item.setData(Qt.UserRole, tariff.id)
            self.tariffs_table.setItem(row, 0, start_item)
            self.tariffs_table.setItem(
                row,
                1,
                QTableWidgetItem(tariff.effective_to_label),
            )
            self.tariffs_table.setItem(row, 2, QTableWidgetItem(tariff.amount))

            if select_id == tariff.id:
                self.tariffs_table.selectRow(row)

        if self.tariffs_table.rowCount() and self.tariffs_table.currentRow() < 0:
            self.tariffs_table.selectRow(0)

    def _selected_tariff_id(self) -> str | None:
        row = self.tariffs_table.currentRow()
        if row < 0:
            return None
        item = self.tariffs_table.item(row, 0)
        return str(item.data(Qt.UserRole)) if item else None

    def _add_tariff(self) -> None:
        if not self._current_house_id:
            return

        tariffs = self.repository.list_house_tariffs(self._current_house_id)
        latest = tariffs[-1] if tariffs else None
        dialog = HouseTariffDialog(
            self,
            default_start_year=(latest.start_year + 1) if latest else None,
            default_start_month=latest.start_month if latest else None,
            default_start_day=latest.start_day if latest else 1,
            default_start_precision=latest.start_precision if latest else "month",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            tariff = self.repository.create_house_tariff(
                house_id=self._current_house_id,
                **dialog.values(),
            )
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.critical(self, "Не удалось добавить тариф", str(exc))
            return
        self._refresh_tariffs(select_id=tariff.id)

    def _edit_tariff(self) -> None:
        tariff_id = self._selected_tariff_id()
        if not tariff_id:
            return
        tariff = self.repository.get_house_tariff(tariff_id)
        if not tariff:
            return
        dialog = HouseTariffDialog(self, tariff)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.repository.update_house_tariff(tariff_id, **dialog.values())
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.critical(self, "Не удалось изменить тариф", str(exc))
            return
        self._refresh_tariffs(select_id=tariff_id)

    def _delete_tariff(self) -> None:
        tariff_id = self._selected_tariff_id()
        if not tariff_id:
            return
        tariff = self.repository.get_house_tariff(tariff_id)
        if not tariff:
            return
        answer = QMessageBox.question(
            self,
            "Удалить тариф?",
            f"Удалить тариф {tariff.amount} руб./м² в месяц "
            f"за период {tariff.period_label}?",
        )
        if answer != QMessageBox.Yes:
            return
        self.repository.delete_house_tariff(tariff_id)
        self._refresh_tariffs()

    def _set_card_enabled(self, enabled: bool) -> None:
        for widget in (
            self.address_edit,
            self.card_org_combo,
            self.object_guid_edit,
            self.gis_guid_edit,
            self.fias_guid_edit,
            self.unique_number_edit,
            self.cadastral_edit,
            self.charge_area_edit,
            self.residential_spin,
            self.non_residential_spin,
            self.comment_edit,
            self.tariffs_table,
            self.add_tariff_button,
            self.edit_tariff_button,
            self.delete_tariff_button,
            self.delete_house_button,
        ):
            widget.setEnabled(enabled)

    def _queue_text(self, field: str, widget: QLineEdit) -> None:
        self._queue_change(field, widget.text().strip() or None)

    def _queue_comment(self) -> None:
        if not self._loading_card:
            self._queue_change("comment", self.comment_edit.toPlainText().strip() or None)

    def _queue_organization_change(self) -> None:
        if not self._loading_card:
            organization_id = self.card_org_combo.currentData()
            if organization_id:
                self._queue_change("organization_id", organization_id)

    def _queue_change(self, field: str, value: object) -> None:
        if self._loading_card or not self._current_house_id:
            return
        if self._pending_house_id and self._pending_house_id != self._current_house_id:
            self._flush_autosave()
        self._pending_house_id = self._current_house_id
        self._pending_fields[field] = value
        self._set_save_status("Сохраняется…")
        self._autosave_timer.start()
        if field in {"residential_premises_count", "non_residential_premises_count"}:
            self.total_label.setText(
                str(self.residential_spin.value() + self.non_residential_spin.value())
            )

    def _flush_autosave(self) -> None:
        self._autosave_timer.stop()
        if not self._pending_house_id or not self._pending_fields:
            return
        house_id = self._pending_house_id
        fields = dict(self._pending_fields)
        self._pending_house_id = None
        self._pending_fields.clear()
        try:
            self.repository.update_house(house_id, **fields)
            self._set_save_status("Сохранено")
            if "organization_id" in fields:
                self.refresh_organizations(select_id=str(fields["organization_id"]))
            else:
                self.refresh_houses()
        except (sqlite3.DatabaseError, ValueError) as exc:
            self._set_save_status("Ошибка сохранения")
            QMessageBox.critical(self, "Ошибка сохранения", str(exc))

    def _set_save_status(self, text: str) -> None:
        self.save_status.setText(text)

    def _add_organization(self) -> None:
        dialog = OrganizationDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            organization = self.repository.create_organization(**dialog.values())
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "УО уже существует",
                "Управляющая организация с таким ОГРН уже есть.",
            )
            return
        self.refresh_organizations(select_id=organization.id)

    def _edit_organization(self) -> None:
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            return
        organization = self.repository.get_organization(organization_id)
        if not organization:
            return
        dialog = OrganizationDialog(self, organization)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.repository.update_organization(organization_id, **dialog.values())
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "ОГРН уже используется",
                "УО с таким ОГРН уже существует.",
            )
            return
        self.refresh_organizations(select_id=organization_id)

    def _delete_organization(self) -> None:
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            return
        organization = self.repository.get_organization(organization_id)
        if not organization:
            return
        answer = QMessageBox.question(
            self,
            "Удалить управляющую организацию?",
            f"Будет удалена УО «{organization.name}» и все ее дома из локальной базы. Продолжить?",
        )
        if answer != QMessageBox.Yes:
            return
        self.repository.delete_organization(organization_id)
        self._current_house_id = None
        self.refresh_organizations()

    def _add_house(self) -> None:
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            QMessageBox.information(
                self,
                "Сначала добавьте УО",
                "Дом должен быть привязан к управляющей организации.",
            )
            return
        dialog = HouseDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            house = self.repository.create_house(
                organization_id=organization_id,
                source=RecordSource.MANUAL,
                **dialog.values(),
            )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Дом уже существует",
                "Дом с таким GUID уже есть у выбранной УО.",
            )
            return
        self._current_house_id = house.id
        self.refresh_houses()

    def _delete_house(self) -> None:
        if not self._current_house_id:
            return
        house = self.repository.get_house(self._current_house_id)
        if not house:
            return
        answer = QMessageBox.question(
            self,
            "Удалить дом?",
            f"Удалить «{house.address}» из локальной базы?",
        )
        if answer != QMessageBox.Yes:
            return
        self.repository.delete_house(house.id)
        self._current_house_id = None
        self.refresh_houses()

    def _export_house_data_template(self) -> None:
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            QMessageBox.information(
                self,
                "Сначала выберите УО",
                "Шаблон формируется по домам выбранной управляющей организации.",
            )
            return

        houses = self.repository.list_houses(str(organization_id))
        if not houses:
            QMessageBox.information(
                self,
                "Нет домов",
                "У выбранной УО пока нет домов для формирования шаблона.",
            )
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить шаблон площадей и тарифов",
            "Данные домов.xlsx",
            "Excel (*.xlsx)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".xlsx"):
            filename += ".xlsx"

        tariffs_by_house = {
            house.id: self.repository.list_house_tariffs(house.id)
            for house in houses
        }
        try:
            write_house_data_template(
                Path(filename),
                houses=houses,
                tariffs_by_house=tariffs_by_house,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Не удалось создать шаблон", str(exc))
            return

        QMessageBox.information(
            self,
            "Шаблон создан",
            "Файл содержит листы «Площади» и «Тарифы». Адреса уже заполнены. "
            "На листе «Тарифы» один дом занимает одну строку, а изменения "
            "тарифа заполняются вправо парами «Действует с / Тариф».",
        )

    def _import_house_data(self) -> None:
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            QMessageBox.information(
                self,
                "Сначала выберите УО",
                "Импорт площадей и тарифов выполняется для выбранной УО.",
            )
            return

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Импорт площадей и тарифов",
            "",
            "Excel (*.xlsx)",
        )
        if not filename:
            return

        try:
            result = parse_house_data_template(Path(filename))
        except (HouseDataImportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Не удалось прочитать файл", str(exc))
            return

        houses = self.repository.list_houses(str(organization_id))
        houses_by_id = {house.id: house for house in houses}
        houses_by_address: dict[str, list[object]] = defaultdict(list)
        for house in houses:
            key = " ".join(house.address.split()).casefold()
            houses_by_address[key].append(house)

        def resolve(house_id: str | None, address: str, row_label: str):
            if house_id:
                house = houses_by_id.get(house_id)
                if house is not None:
                    return house
                raise ValueError(
                    f"{row_label}: служебный идентификатор дома не относится "
                    "к выбранной УО. Сформируйте шаблон для нужной УО заново."
                )
            key = " ".join(address.split()).casefold()
            matches = houses_by_address.get(key, [])
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise ValueError(f"{row_label}: дом «{address}» не найден у выбранной УО.")
            raise ValueError(f"{row_label}: адрес «{address}» встречается несколько раз.")

        area_changes: list[tuple[object, str, str | None]] = []
        tariff_changes: list[tuple[object, object, object | None]] = []
        unresolved: list[str] = []

        for row in result.areas:
            try:
                house = resolve(
                    row.house_id,
                    row.address,
                    f"Площади, строка {row.row_number}",
                )
            except ValueError as exc:
                unresolved.append(str(exc))
                continue
            current = house.charge_area
            try:
                current_normalized = (
                    normalize_positive_decimal(current) if current else None
                )
            except ValueError:
                current_normalized = current
            if current_normalized != row.area:
                area_changes.append((house, row.area, current))

        for row in result.tariffs:
            try:
                house = resolve(
                    row.house_id,
                    row.address,
                    f"Тарифы, строка {row.row_number}",
                )
            except ValueError as exc:
                unresolved.append(str(exc))
                continue

            existing = next(
                (
                    item
                    for item in self.repository.list_house_tariffs(house.id)
                    if item.effective_from_date == row.start.value
                ),
                None,
            )
            same = False
            if existing is not None:
                try:
                    same_amount = normalize_positive_decimal(existing.amount) == row.amount
                except ValueError:
                    same_amount = existing.amount == row.amount
                same = (
                    same_amount
                    and existing.start_precision == row.start.precision
                )
            if not same:
                tariff_changes.append((house, row, existing))

        if unresolved:
            message = (
                "Импорт не выполнен: не удалось однозначно сопоставить некоторые строки.\n\n"
                + "\n".join(unresolved[:10])
            )
            if len(unresolved) > 10:
                message += f"\n… еще: {len(unresolved) - 10}"
            QMessageBox.warning(self, "Проверьте файл", message)
            return

        tariff_conflicts = [
            (house, row, existing)
            for house, row, existing in tariff_changes
            if existing is not None
        ]
        new_tariffs = len(tariff_changes) - len(tariff_conflicts)

        preview = (
            f"Площади будут обновлены: {len(area_changes)}\n"
            f"Новых точек тарифа: {new_tariffs}\n"
            f"Существующих точек тарифа будут изменены: {len(tariff_conflicts)}"
        )
        if result.warnings:
            preview += "\n\n" + "\n".join(result.warnings)
        if area_changes:
            preview += "\n\nПримеры изменений площади:\n"
            for house, area, old_area in area_changes[:6]:
                preview += (
                    f"• {house.address}: {old_area or 'не указана'} → {area} м²\n"
                )
        if tariff_conflicts:
            preview += "\n\nПримеры изменений тарифа:\n"
            for house, row, existing in tariff_conflicts[:6]:
                preview += (
                    f"• {house.address}: "
                    f"{existing.effective_from_label} = {existing.amount} → "
                    f"{row.start.input_label} = {row.amount}\n"
                )
        if not area_changes and not tariff_changes:
            QMessageBox.information(
                self,
                "Изменений нет",
                "Все заполненные значения уже совпадают с данными программы.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Применить импорт?",
            preview + "\n\nПрименить эти изменения?",
        )
        if answer != QMessageBox.Yes:
            return

        for house, area, _old in area_changes:
            self.repository.update_house(house.id, charge_area=area)

        for house, row, _existing in tariff_changes:
            self.repository.upsert_house_tariff_change(
                house_id=house.id,
                amount=row.amount,
                start_year=row.start.value.year,
                start_month=row.start.value.month,
                start_day=row.start.value.day,
                start_precision=row.start.precision,
            )

        self.refresh_houses()
        QMessageBox.information(
            self,
            "Импорт завершен",
            f"Обновлено площадей: {len(area_changes)}\n"
            f"Изменено или добавлено тарифов: {len(tariff_changes)}",
        )

    def _import_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите XLSX из выгрузки «Объекты жилищного фонда»",
            "",
            "Выгрузка ГИС ЖКХ (*.xlsx *.xlsm);;Excel (*.xlsx *.xlsm)",
        )
        if not filename:
            return

        try:
            result = parse_housing_objects_export(Path(filename))
        except (HousingObjectsImportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Не удалось импортировать файл", str(exc))
            return

        org_lines = [f"• {org.name} — ОГРН {org.ogrn}" for org in result.organizations]
        version_text = result.version or "не указана"
        type_text = result.file_type or "не указан"
        preview = (
            f"Тип файла: {type_text}\n"
            f"Версия файла: {version_text}\n\n"
            f"Управляющих организаций: {len(result.organizations)}\n"
            f"Домов: {len(result.houses)}\n\n"
            + "\n".join(org_lines[:8])
        )
        if len(org_lines) > 8:
            preview += f"\n… и еще {len(org_lines) - 8}"
        if result.warnings:
            preview += f"\n\nПредупреждений: {len(result.warnings)}"

        answer = QMessageBox.question(
            self,
            "Импортировать данные?",
            preview,
        )
        if answer != QMessageBox.Yes:
            return

        organizations_by_ogrn = {}
        for imported_org in result.organizations:
            organization = self.repository.upsert_imported_organization(
                name=imported_org.name,
                ogrn=imported_org.ogrn,
                kpp=imported_org.kpp,
            )
            organizations_by_ogrn[imported_org.ogrn] = organization

        created = 0
        updated = 0
        per_org = defaultdict(lambda: [0, 0])
        for imported_house in result.houses:
            organization = organizations_by_ogrn.get(imported_house.organization_ogrn)
            if not organization:
                continue
            _house, was_created = self.repository.upsert_imported_house(
                organization.id, imported_house
            )
            if was_created:
                created += 1
                per_org[organization.id][0] += 1
            else:
                updated += 1
                per_org[organization.id][1] += 1

        first_org_id = (
            next(iter(organizations_by_ogrn.values())).id if organizations_by_ogrn else None
        )
        self.refresh_organizations(select_id=first_org_id)

        message = f"Импорт завершен.\nНовых домов: {created}\nОбновлено домов: {updated}"
        if result.warnings:
            message += "\n\n" + "\n".join(result.warnings[:6])
            if len(result.warnings) > 6:
                message += f"\n… еще предупреждений: {len(result.warnings) - 6}"
        QMessageBox.information(self, "Импорт завершен", message)

    def flush_pending_changes(self) -> None:
        """Persist any debounced edits before the application changes context or closes."""
        self._flush_autosave()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.flush_pending_changes()
        super().closeEvent(event)
