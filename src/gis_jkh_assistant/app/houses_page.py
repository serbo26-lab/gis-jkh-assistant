"""The first application page: management organizations and houses."""

from __future__ import annotations

from .error_messages import context_text, error_text

import sqlite3
from datetime import datetime
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QMenu,
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
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
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
from gis_jkh_assistant.work_lists.gis_sync import (
    apply_gis_work_list_sync,
    prepare_gis_work_list_sync,
)

from .dialogs import HouseDialog, HouseTariffDialog, OrganizationDialog
from .gis_integration_worker import GisIntegrationWorker, connect_result
from .registry_layouts import adapt_houses
from .registry_workflow import refresh_houses
from .widgets import ActionToolbar, SafeComboBox, SafeSpinBox
from .workflow_widgets import LocalSaveStatus, OperationStatus


class HousesPage(QWidget):
    """Editable houses registry with automatic persistence."""

    AUTOSAVE_DELAY_MS = 500

    def __init__(
        self,
        repository: Repository,
        integration_gateway: LocalIntegrationGateway | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.integration_gateway = integration_gateway
        self._management_worker: GisIntegrationWorker | None = None
        self._houses_sync_worker: GisIntegrationWorker | None = None
        self._work_lists_sync_worker: GisIntegrationWorker | None = None
        self._bulk_operation_name: str | None = None
        self._current_house_id: str | None = None
        self._pending_house_id: str | None = None
        self._pending_fields: dict[str, object] = {}
        self._loading_card = False

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(self.AUTOSAVE_DELAY_MS)
        self._autosave_timer.timeout.connect(self._flush_autosave)

        self._selection_refresh_timer = QTimer(self)
        self._selection_refresh_timer.setSingleShot(True)
        self._selection_refresh_timer.setInterval(0)
        self._selection_refresh_timer.timeout.connect(self._refresh_after_selection)

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
        self.save_status = LocalSaveStatus()
        self.save_status.setObjectName("SaveStatus")
        title_row.addWidget(self.save_status)
        self.retry_save_button = QPushButton("Повторить сохранение")
        self.retry_save_button.clicked.connect(self._flush_autosave)
        self.retry_save_button.hide()
        title_row.addWidget(self.retry_save_button)
        root.addLayout(title_row)

        context = QFrame()
        context.setObjectName("ContextBar")
        context_layout = QHBoxLayout(context)
        context_layout.setContentsMargins(12, 9, 12, 9)
        context_layout.setSpacing(8)
        context_layout.addWidget(QLabel("Управляющая организация"))
        self.organization_combo = SafeComboBox()
        self.organization_combo.setMinimumWidth(260)
        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        context_layout.addWidget(self.organization_combo, 1)
        root.addWidget(context)

        self.add_org_button = QPushButton("Добавить УО")
        self.edit_org_button = QPushButton("Изменить УО")
        self.delete_org_button = QPushButton("Удалить УО")
        self.import_button = QPushButton("Импорт домов из XLSX…")
        self.import_button.setToolTip(
            "Резервный импорт домов из ранее выгруженного XLSX-файла ГИС ЖКХ."
        )
        self.sync_houses_button = QPushButton(
            "Загрузить сведения о МКД из ГИС ЖКХ"
        )
        self.sync_houses_button.setToolTip(
            "Получить все дома текущей управляющей организации напрямую из ГИС ЖКХ."
        )
        self.add_house_button = QPushButton("+ Добавить дом")
        self.export_house_data_button = QPushButton("Шаблон площадей и тарифов…")
        self.import_house_data_button = QPushButton("Импорт площадей и тарифов…")
        self.sync_all_management_button = QPushButton(
            "Загрузить сведения об управлении МКД из ГИС ЖКХ"
        )
        self.sync_all_management_button.setToolTip(
            "Получить период управления и реквизиты договора для всех домов "
            "выбранной управляющей организации."
        )
        self.sync_all_work_lists_button = QPushButton(
            "Загрузить перечни работ МКД из ГИС ЖКХ"
        )
        self.sync_all_work_lists_button.setToolTip(
            "Получить перечни работ и услуг для всех домов выбранной "
            "управляющей организации."
        )

        self.add_org_button.clicked.connect(self._add_organization)
        self.edit_org_button.clicked.connect(self._edit_organization)
        self.delete_org_button.clicked.connect(self._delete_organization)
        self.import_button.clicked.connect(self._import_file)
        self.sync_houses_button.clicked.connect(self._sync_houses_from_gis)
        self.add_house_button.clicked.connect(self._add_house)
        self.export_house_data_button.clicked.connect(self._export_house_data_template)
        self.import_house_data_button.clicked.connect(self._import_house_data)
        self.sync_all_management_button.clicked.connect(
            self._sync_all_management_from_gis
        )
        self.sync_all_work_lists_button.clicked.connect(
            self._sync_all_work_lists_from_gis
        )

        actions = ActionToolbar()

        self.organization_actions_button = QPushButton("Действия с УО ▾")
        organization_menu = QMenu(self.organization_actions_button)
        self.organization_add_action = organization_menu.addAction("Добавить УО")
        self.organization_edit_action = organization_menu.addAction("Изменить УО")
        self.organization_delete_action = organization_menu.addAction("Удалить УО")
        self.organization_add_action.triggered.connect(self.add_org_button.click)
        self.organization_edit_action.triggered.connect(self.edit_org_button.click)
        self.organization_delete_action.triggered.connect(self.delete_org_button.click)
        self.organization_actions_button.setMenu(organization_menu)
        actions.add_widget(self.organization_actions_button)

        actions.add_widget(self.add_house_button)

        self.files_actions_button = QPushButton("Импорт / Экспорт ▾")
        files_menu = QMenu(self.files_actions_button)
        files_menu.addAction(
            "Экспорт Excel: шаблон площадей и тарифов…",
            self.export_house_data_button.click,
        )
        files_menu.addAction(
            "Импорт Excel: площади и тарифы…",
            self.import_house_data_button.click,
        )
        self.import_houses_action = files_menu.addAction(
            "Импорт домов из XLSX…",
            self.import_button.click,
        )
        self.files_actions_button.setMenu(files_menu)
        actions.add_widget(self.files_actions_button)

        actions.add_separator()

        self.gis_actions_button = QPushButton("Синхронизация с ГИС ЖКХ ▾")
        self.gis_actions_button.setObjectName("PrimaryButton")
        gis_menu = QMenu(self.gis_actions_button)
        self.sync_houses_action = gis_menu.addAction(
            "Сведения о МКД",
            self.sync_houses_button.click,
        )
        self.sync_management_action = gis_menu.addAction(
            "Сведения об управлении МКД",
            self.sync_all_management_button.click,
        )
        self.sync_work_lists_action = gis_menu.addAction(
            "Перечни работ МКД",
            self.sync_all_work_lists_button.click,
        )
        self.gis_actions_button.setMenu(gis_menu)
        actions.add_widget(self.gis_actions_button)
        actions.add_stretch()
        root.addWidget(actions)

        house_data_hint = QLabel(
            "XLSX-операции для домов, площадей и тарифов собраны в меню "
            "«Импорт / Экспорт». Массовые операции с ГИС ЖКХ запускаются "
            "последовательно и блокируют повторный запуск до завершения."
        )
        house_data_hint.setWordWrap(True)
        house_data_hint.setObjectName("Muted")
        root.addWidget(house_data_hint)

        self.bulk_status_strip = OperationStatus("Готово к запуску массовой операции.")
        self.bulk_operation_status = self.bulk_status_strip.label
        root.addWidget(self.bulk_status_strip)

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
        adapt_houses(self)

    def _refresh_workflow(self) -> None:
        refresh_houses(self)

    def _build_house_card(self) -> None:
        section = QLabel("Карточка дома")
        section.setObjectName("SectionTitle")
        self.card_layout.addWidget(section)

        self.address_edit = QLineEdit()
        self.card_org_combo = SafeComboBox()
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

        management_title = QLabel("Управление домом")
        management_title.setObjectName("SectionTitle")
        self.card_layout.addWidget(management_title)

        management_hint = QLabel(
            "Окончание — первый день, с которого дом уже не в управлении. "
            "Пустое поле означает действующее управление."
        )
        management_hint.setWordWrap(True)
        management_hint.setObjectName("Muted")
        self.card_layout.addWidget(management_hint)

        self.management_start_edit = QLineEdit()
        self.management_start_edit.setPlaceholderText("ДД.ММ.ГГГГ")
        self.management_end_edit = QLineEdit()
        self.management_end_edit.setPlaceholderText("ДД.ММ.ГГГГ или пусто")
        self.management_end_edit.setToolTip(
            "Дата, с которой дом больше не находится в управлении. "
            "Например, 01.03.2022 означает, что последний месяц управления — февраль 2022."
        )
        self.management_agreement_number_edit = QLineEdit()
        self.management_agreement_date_edit = QLineEdit()
        self.management_agreement_date_edit.setPlaceholderText("ДД.ММ.ГГГГ")
        self.management_source_label = QLabel("—")
        self.sync_management_button = QPushButton(
            "Обновить сведения об управлении из ГИС ЖКХ"
        )
        self.sync_management_button.setToolTip(
            "Получить период управления и номер/дату договора из открытых сведений ГИС ЖКХ."
        )
        self.sync_management_button.clicked.connect(self._sync_management_from_gis)

        management_form = QFormLayout()
        management_form.addRow("Начало управления", self.management_start_edit)
        management_form.addRow("Окончание управления", self.management_end_edit)
        management_form.addRow(
            "Номер договора управления",
            self.management_agreement_number_edit,
        )
        management_form.addRow(
            "Дата договора управления",
            self.management_agreement_date_edit,
        )
        management_form.addRow("Источник сведений", self.management_source_label)
        management_form.addRow("", self.sync_management_button)
        self.card_layout.addLayout(management_form)

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
            self.management_agreement_number_edit: "management_agreement_number",
        }
        for widget, field in field_bindings.items():
            widget.textEdited.connect(
                lambda _text, field=field, widget=widget: self._queue_text(field, widget)
            )

        self.management_start_edit.editingFinished.connect(
            lambda: self._queue_management_date(
                "management_start_date",
                self.management_start_edit,
            )
        )
        self.management_end_edit.editingFinished.connect(
            lambda: self._queue_management_date(
                "management_end_date",
                self.management_end_edit,
            )
        )
        self.management_agreement_date_edit.editingFinished.connect(
            lambda: self._queue_management_date(
                "management_agreement_date",
                self.management_agreement_date_edit,
            )
        )
        self.management_agreement_number_edit.textEdited.connect(
            lambda _text: self._mark_management_manual()
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
        self.card_layout.setContentsMargins(8, 6, 8, 8)
        self.card_layout.setSpacing(6)

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
        if not self._flush_autosave():
            house = self.repository.get_house(self._pending_house_id)
            if house:
                self.organization_combo.blockSignals(True)
                self.organization_combo.setCurrentIndex(
                    self.organization_combo.findData(house.organization_id)
                )
                self.organization_combo.blockSignals(False)
            return
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
        if self._pending_house_id and self._pending_fields:
            pending_house = self.repository.get_house(self._pending_house_id)
            if (pending_house and pending_house.organization_id == organization_id
                    and all(house.id != pending_house.id for house in houses)):
                houses.append(pending_house)
        for house in houses:
            pending_note = " • Не сохранено" if house.id == self._pending_house_id and self._pending_fields else ""
            item = QListWidgetItem(
                f"{house.address}\n"
                f"{'Связан с ГИС' if house.house_object_guid else 'Локальный'} • "
                f"Помещений: {house.total_premises_count}{pending_note}"
            )
            item.setData(Qt.UserRole, house.id)
            self.house_list.addItem(item)
        self.house_count_label.setText(str(len(houses)))
        self.house_list.blockSignals(False)

        if selected_id:
            for row in range(self.house_list.count()):
                item = self.house_list.item(row)
                if item.data(Qt.UserRole) == selected_id:
                    blocked = self.house_list.blockSignals(bool(self._pending_fields))
                    self.house_list.setCurrentItem(item)
                    self.house_list.blockSignals(blocked)
                    return
        if self.house_list.count():
            self.house_list.setCurrentRow(0)
        else:
            self._load_house(None)

    def _house_selection_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        # Qt still uses both items until currentItemChanged has returned. Save
        # without rebuilding the model, then refresh on the next event pass.
        house_id = current.data(Qt.UserRole) if current else None
        had_pending = bool(self._pending_fields)
        if not self._flush_autosave(refresh=False):
            self.house_list.blockSignals(True)
            for row in range(self.house_list.count()):
                item = self.house_list.item(row)
                if item.data(Qt.UserRole) == self._pending_house_id:
                    self.house_list.setCurrentItem(item)
                    break
            self.house_list.blockSignals(False)
            return
        self.house_list.blockSignals(True)
        for row in range(self.house_list.count()):
            item = self.house_list.item(row)
            if item.data(Qt.UserRole) == house_id:
                self.house_list.setCurrentItem(item)
                break
        self.house_list.blockSignals(False)
        self._load_house(house_id)
        if had_pending:
            self._selection_refresh_timer.start()

    def _refresh_after_selection(self) -> None:
        if getattr(self.window(), "_closing", False):
            return
        self.refresh_houses()

    def _load_house(self, house_id: str | None) -> None:
        if self._pending_fields:
            if house_id == self._pending_house_id:
                return  # A refresh must not replace the unsaved editor with DB values.
            if not self._flush_autosave():
                return
        self._current_house_id = house_id
        self.house_detail_title.setText("Выбранный дом: —")
        if not house_id:
            self._clear_house_card()
            self.tariffs_table.setRowCount(0)
            self._set_card_enabled(False)
            self._refresh_workflow()
            return
        house = self.repository.get_house(house_id)
        if not house:
            self._clear_house_card()
            self._set_card_enabled(False)
            self._refresh_workflow()
            return

        self.house_detail_title.setText(house.address)
        self.house_ids_toggle.setChecked(not bool(house.house_object_guid))

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
            self.management_start_edit.setText(
                house.management_start_date or ""
            )
            self.management_end_edit.setText(
                house.management_end_date or ""
            )
            self.management_agreement_number_edit.setText(
                house.management_agreement_number or ""
            )
            self.management_agreement_date_edit.setText(
                house.management_agreement_date or ""
            )
            source_labels = {
                "gis_public": "ГИС ЖКХ",
                "manual": "Вручную",
            }
            self.management_source_label.setText(
                source_labels.get(
                    str(house.management_data_source or ""),
                    "—",
                )
            )
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
        self._refresh_workflow()

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
            QMessageBox.critical(self, "Не удалось добавить тариф", error_text(exc, context=context_text(self)))
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
            QMessageBox.critical(self, "Не удалось изменить тариф", error_text(exc, context=context_text(self)))
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

    def _clear_house_card(self) -> None:
        self._loading_card = True
        try:
            for name in (
                "address_edit", "object_guid_edit", "gis_guid_edit", "fias_guid_edit",
                "unique_number_edit", "cadastral_edit", "charge_area_edit",
                "management_start_edit", "management_end_edit", "management_agreement_number_edit",
                "management_agreement_date_edit", "comment_edit",
            ):
                getattr(self, name).clear()
            self.card_org_combo.setCurrentIndex(-1)
            self.residential_spin.setValue(0)
            self.non_residential_spin.setValue(0)
            for name in ("source_label", "management_source_label", "created_label", "updated_label"):
                getattr(self, name).setText("—")
            self.total_label.setText("0")
        finally:
            self._loading_card = False
        self.save_status.setText("Дом не выбран")

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
            self.management_start_edit,
            self.management_end_edit,
            self.management_agreement_number_edit,
            self.management_agreement_date_edit,
            self.sync_management_button,
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

    @staticmethod
    def _validated_management_date(text: str) -> str | None:
        value = str(text or "").strip()
        if not value:
            return None
        try:
            parsed = datetime.strptime(value, "%d.%m.%Y")
        except ValueError as exc:
            raise ValueError("Используйте дату в формате ДД.ММ.ГГГГ.") from exc
        return parsed.strftime("%d.%m.%Y")

    def _mark_management_manual(self) -> None:
        if self._loading_card:
            return
        self._queue_change("management_data_source", "manual")

    def _queue_management_date(self, field: str, widget: QLineEdit) -> None:
        if self._loading_card:
            return
        try:
            value = self._validated_management_date(widget.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте дату", error_text(exc, context=context_text(self)))
            house = self.repository.get_house(self._current_house_id) if self._current_house_id else None
            widget.setText(str(getattr(house, field, "") or "") if house else "")
            return
        self._queue_change(field, value)
        self._queue_change("management_data_source", "manual")

    @staticmethod
    def _house_sync_identity_values(house) -> set[str]:
        values: set[str] = set()
        for value in (
            house.house_object_guid,
            house.gis_house_guid,
            house.fias_house_guid,
        ):
            text = str(value or "").strip().casefold()
            if text:
                values.add(text)
        return values

    def _local_house_for_gis_item(self, houses, item: dict):
        remote_ids = {
            str(value or "").strip().casefold()
            for value in (
                item.get("houseObjectGuid"),
                item.get("gisHouseGuid"),
                item.get("fiasHouseGuid"),
            )
            if str(value or "").strip()
        }
        for house in houses:
            if remote_ids.intersection(self._house_sync_identity_values(house)):
                return house
        return None

    def _bulk_worker_running(self) -> bool:
        workers = (
            self._houses_sync_worker,
            self._management_worker,
            self._work_lists_sync_worker,
        )
        return any(worker is not None and worker.isRunning() for worker in workers)

    def _set_bulk_status(self, text: str) -> None:
        self.bulk_operation_status.setText(text)
        self.bulk_status_strip.show()
        QCoreApplication.processEvents()

    def _begin_bulk_operation(self, name: str, status_text: str) -> bool:
        if self._bulk_operation_name is not None or self._bulk_worker_running():
            QMessageBox.information(
                self,
                "Массовая операция уже выполняется",
                "Дождитесь завершения текущей массовой операции. "
                "Следующий запрос к ГИС ЖКХ будет выполнен после неё.",
            )
            return False

        self._bulk_operation_name = name
        for button in (
            self.sync_houses_button,
            self.sync_all_management_button,
            self.sync_all_work_lists_button,
            self.import_button,
        ):
            button.setEnabled(False)
        for action in (
            self.sync_houses_action,
            self.sync_management_action,
            self.sync_work_lists_action,
            self.import_houses_action,
        ):
            action.setEnabled(False)
        self.gis_actions_button.setEnabled(False)
        self.files_actions_button.setEnabled(False)
        if self._current_house_id:
            self.sync_management_button.setEnabled(False)
        self._set_bulk_status(status_text)
        self._refresh_workflow()
        return True

    def _finish_bulk_operation(self, status_text: str) -> None:
        self._bulk_operation_name = None
        for button in (
            self.sync_houses_button,
            self.sync_all_management_button,
            self.sync_all_work_lists_button,
            self.import_button,
        ):
            button.setEnabled(True)
        for action in (
            self.sync_houses_action,
            self.sync_management_action,
            self.sync_work_lists_action,
            self.import_houses_action,
        ):
            action.setEnabled(True)
        self.gis_actions_button.setEnabled(True)
        self.files_actions_button.setEnabled(True)
        if self._current_house_id:
            self.sync_management_button.setEnabled(True)
        self._set_bulk_status(status_text)
        self._refresh_workflow()

    @staticmethod
    def _gis_house_changed(house, item: dict) -> bool:
        comparable = {
            "address": str(item.get("address") or "").strip(),
            "house_object_guid": str(item.get("houseObjectGuid") or "").strip() or None,
            "gis_house_guid": str(item.get("gisHouseGuid") or "").strip() or None,
            "fias_house_guid": str(item.get("fiasHouseGuid") or "").strip() or None,
            "cadastral_number": str(item.get("cadastralNumber") or "").strip() or None,
            "residential_premises_count": int(item.get("residentialPremisesCount") or 0),
            "non_residential_premises_count": int(item.get("nonResidentialPremisesCount") or 0),
        }
        return any(getattr(house, field) != value for field, value in comparable.items())

    def _sync_houses_from_gis(self) -> None:
        if not self._flush_autosave():
            return
        if self.integration_gateway is None:
            return
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            QMessageBox.information(
                self,
                "Не выбрана УО",
                "Сначала выберите управляющую организацию.",
            )
            return
        if self._bulk_operation_name is not None or self._bulk_worker_running():
            QMessageBox.information(
                self,
                "Массовая операция уже выполняется",
                "Дождитесь завершения текущей массовой операции.",
            )
            return

        status = self.integration_gateway.status()
        if not (status.running and status.helper_connected and status.gis_page_ready):
            QMessageBox.information(
                self,
                "Нет подключения к ГИС ЖКХ",
                "Откройте личный кабинет ГИС ЖКХ и убедитесь, что "
                "ГИС ЖКХ помощник подключен.",
            )
            return

        organization = self.repository.get_organization(str(organization_id))
        if organization is None:
            return
        local_houses = self.repository.list_houses(str(organization_id))
        seed_house = next(
            (house for house in local_houses if house.house_object_guid),
            None,
        )

        if not self._begin_bulk_operation(
            "houses",
            "Получение сведений о МКД из ГИС ЖКХ…",
        ):
            return

        worker = GisIntegrationWorker(
            self.integration_gateway,
            "list_managed_houses",
            {
                "organizationGuid": organization.gis_organization_guid,
                "organizationRootGuid": organization.gis_organization_root_guid,
                "seedHouseObjectGuid": (
                    seed_house.house_object_guid if seed_house else None
                ),
            },
            timeout=180.0,
            parent=self,
        )
        self._houses_sync_worker = worker

        def succeeded(result: dict) -> None:
            remote_items = list(result.get("items") or [])
            total = int(result.get("total") or len(remote_items))
            if not remote_items:
                QMessageBox.information(
                    self,
                    "Дома не найдены",
                    "ГИС ЖКХ не вернул дома для текущей организации.",
                )
                self._set_bulk_status("Получено домов: 0")
                return

            returned_ogrns = {
                str(item.get("organizationOgrn") or "").strip()
                for item in remote_items
                if str(item.get("organizationOgrn") or "").strip()
            }
            if returned_ogrns and organization.ogrn not in returned_ogrns:
                QMessageBox.critical(
                    self,
                    "Другая управляющая организация",
                    "Открытая сессия ГИС ЖКХ вернула дома другой организации. "
                    "Переключитесь в личном кабинете на нужную УО и повторите загрузку.",
                )
                self._set_bulk_status("Ошибка: открытая сессия вернула другую УО.")
                return

            created_preview = 0
            updated_preview = 0
            unchanged_preview = 0
            for item in remote_items:
                existing = self._local_house_for_gis_item(local_houses, item)
                if existing is None:
                    created_preview += 1
                elif self._gis_house_changed(existing, item):
                    updated_preview += 1
                else:
                    unchanged_preview += 1

            preview = (
                f"Получено из ГИС ЖКХ: {len(remote_items)} из {total}.\n"
                f"Новые дома: {created_preview}.\n"
                f"Будут обновлены: {updated_preview}.\n"
                f"Без изменений: {unchanged_preview}.\n\n"
                "Локальные тарифы, периоды управления, перечни и планы не изменяются.\n"
                "Применить сведения о МКД?"
            )
            answer = QMessageBox.question(
                self,
                "Загрузить сведения о МКД?",
                preview,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_bulk_status("Загрузка сведений о МКД отменена.")
                return

            created = 0
            updated = 0
            unchanged = 0
            current_house_id = self._current_house_id

            for item in remote_items:
                existing = self._local_house_for_gis_item(local_houses, item)
                payload = dict(
                    address=str(item.get("address") or "").strip(),
                    house_object_guid=item.get("houseObjectGuid"),
                    gis_house_guid=item.get("gisHouseGuid"),
                    fias_house_guid=item.get("fiasHouseGuid"),
                    cadastral_number=item.get("cadastralNumber"),
                    residential_premises_count=int(
                        item.get("residentialPremisesCount") or 0
                    ),
                    non_residential_premises_count=int(
                        item.get("nonResidentialPremisesCount") or 0
                    ),
                )
                if existing is None:
                    self.repository.create_house(
                        organization_id=str(organization_id),
                        source=RecordSource.IMPORT,
                        **payload,
                    )
                    created += 1
                    continue

                if self._gis_house_changed(existing, item):
                    self.repository.update_house(existing.id, **payload)
                    updated += 1
                else:
                    unchanged += 1

            org_updates = {}
            if result.get("organizationGuid"):
                org_updates["gis_organization_guid"] = result.get("organizationGuid")
            if result.get("organizationRootGuid"):
                org_updates["gis_organization_root_guid"] = result.get(
                    "organizationRootGuid"
                )
            if org_updates:
                self.repository.update_organization(
                    str(organization_id),
                    **org_updates,
                )

            self.refresh_houses()
            if current_house_id:
                self._load_house(current_house_id)
            self._set_bulk_status(
                f"Сведения о МКД: добавлено {created}, обновлено {updated}, "
                f"без изменений {unchanged}."
            )
            QMessageBox.information(
                self,
                "Загрузка завершена",
                (
                    f"Получено домов: {len(remote_items)}.\n"
                    f"Добавлено: {created}.\n"
                    f"Обновлено: {updated}.\n"
                    f"Без изменений: {unchanged}."
                ),
            )

        def failed(message: str) -> None:
            self._set_bulk_status("Ошибка загрузки сведений о МКД.")
            QMessageBox.critical(
                self,
                "Не удалось загрузить сведения о МКД",
                error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."),
            )

        def finished() -> None:
            self._houses_sync_worker = None
            self._finish_bulk_operation(self.bulk_operation_status.text())

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.start()

    def _sync_all_work_lists_from_gis(self) -> None:
        if not self._flush_autosave():
            return
        if self.integration_gateway is None:
            return

        organization_id = self.organization_combo.currentData()
        if not organization_id:
            QMessageBox.information(
                self,
                "Не выбрана УО",
                "Сначала выберите управляющую организацию.",
            )
            return

        if self._bulk_operation_name is not None or self._bulk_worker_running():
            QMessageBox.information(
                self,
                "Массовая операция уже выполняется",
                "Дождитесь завершения текущей массовой операции.",
            )
            return

        status = self.integration_gateway.status()
        if not (status.running and status.helper_connected and status.gis_page_ready):
            QMessageBox.information(
                self,
                "Нет подключения к ГИС ЖКХ",
                "Откройте личный кабинет ГИС ЖКХ и убедитесь, что "
                "ГИС ЖКХ помощник подключен.",
            )
            return

        houses = self.repository.list_houses(str(organization_id))
        seed_house = next(
            (house for house in houses if house.house_object_guid),
            None,
        )
        if seed_house is None:
            QMessageBox.information(
                self,
                "Нет GUID домов",
                "Сначала загрузите сведения о МКД из ГИС ЖКХ.",
            )
            return

        if not self._begin_bulk_operation(
            "work_lists",
            "Получено перечней: 0",
        ):
            return

        worker = GisIntegrationWorker(
            self.integration_gateway,
            "list_work_lists",
            {"seedHouseObjectGuid": seed_house.house_object_guid},
            timeout=420.0,
            parent=self,
        )
        self._work_lists_sync_worker = worker

        def succeeded(result: dict) -> None:
            remote_items = [
                item for item in list(result.get("items") or [])
                if isinstance(item, dict)
            ]
            def matching_progress(current: int, total: int) -> None:
                self._set_bulk_status(f"Сопоставление {current}/{total}")

            preview = prepare_gis_work_list_sync(
                self.repository,
                str(organization_id),
                remote_items,
                progress_callback=matching_progress,
            )
            self._set_bulk_status(
                f"Сопоставление {preview.received}/{preview.received}"
            )

            summary = (
                f"Получено из ГИС ЖКХ: {preview.received}.\n"
                f"Новых перечней: {len(preview.new_rows)}.\n"
                f"Уже связанных: {len(preview.linked_rows)}.\n"
                f"Конфликтов: {len(preview.conflicts)}.\n"
                f"Не сопоставлено с локальными домами: {len(preview.unmatched)}."
            )

            if not preview.new_rows and not preview.linked_rows:
                details = ""
                if preview.conflicts:
                    details = "\n\n" + "\n".join(preview.conflicts[:5])
                QMessageBox.information(
                    self,
                    "Перечни МКД из ГИС ЖКХ",
                    summary + "\n\nИзменять локальную базу не требуется." + details,
                )
                self._set_bulk_status("Перечни: изменений нет.")
                return

            answer = QMessageBox.question(
                self,
                "Загрузить перечни работ по всем МКД?",
                summary
                + "\n\nНовые перечни будут добавлены в локальную базу. "
                "Существующие локальные перечни автоматически не перезаписываются.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_bulk_status("Массовая загрузка перечней отменена.")
                return

            def save_progress(current: int, total: int) -> None:
                self._set_bulk_status(f"Сохранение {current}/{total}")

            def status_progress(current: int, total: int) -> None:
                self._set_bulk_status(f"Обновление статусов {current}/{total}")

            applied = apply_gis_work_list_sync(
                self.repository,
                preview,
                save_progress_callback=save_progress,
                status_progress_callback=status_progress,
            )
            self._set_bulk_status(
                f"Перечни: добавлено {applied.imported}, "
                f"обновлено статусов {applied.updated}."
            )

            result_text = (
                f"Добавлено перечней: {applied.imported}.\n"
                f"Обновлено статусов: {applied.updated}.\n"
                f"Конфликтов пропущено: {len(preview.conflicts)}.\n"
                f"Не сопоставлено домов: {len(preview.unmatched)}."
            )
            if applied.errors:
                result_text += (
                    f"\nОшибок при сохранении: {len(applied.errors)}.\n\n"
                    + "\n".join(applied.errors[:5])
                )
                QMessageBox.warning(
                    self,
                    "Массовая загрузка завершена с замечаниями",
                    result_text,
                )
            else:
                QMessageBox.information(
                    self,
                    "Массовая загрузка перечней завершена",
                    result_text,
                )

        def failed(message: str) -> None:
            self._set_bulk_status("Ошибка массовой загрузки перечней.")
            QMessageBox.critical(
                self,
                "Не удалось загрузить перечни МКД",
                error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."),
            )

        def progressed(progress: dict) -> None:
            phase = str(progress.get("phase") or "")
            if phase == "queued":
                self._set_bulk_status("Ожидание завершения предыдущей операции…")
                return
            if phase == "receiving_work_lists":
                received = int(progress.get("received") or 0)
                self._set_bulk_status(f"Получено перечней: {received}")

        def finished() -> None:
            self._work_lists_sync_worker = None
            self._finish_bulk_operation(self.bulk_operation_status.text())

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.progressed.connect(progressed)
        worker.finished.connect(finished)
        worker.start()

    @staticmethod
    def _house_management_identity_values(house) -> set[str]:
        result: set[str] = set()
        for value in (
            house.house_object_guid,
            house.gis_house_guid,
            house.fias_house_guid,
        ):
            text = str(value or "").strip().casefold()
            if text:
                result.add(text)
        return result

    @staticmethod
    def _has_manual_management_data(house) -> bool:
        return bool(
            house.management_data_source == "manual"
            and (
                house.management_start_date
                or house.management_end_date
                or house.management_agreement_number
                or house.management_agreement_date
            )
        )

    def _sync_all_management_from_gis(self) -> None:
        if not self._flush_autosave():
            return
        if self.integration_gateway is None:
            return
        organization_id = self.organization_combo.currentData()
        if not organization_id:
            QMessageBox.information(
                self,
                "Не выбрана УО",
                "Сначала выберите управляющую организацию.",
            )
            return
        if self._bulk_operation_name is not None or self._bulk_worker_running():
            QMessageBox.information(
                self,
                "Массовая операция уже выполняется",
                "Дождитесь завершения текущей массовой операции.",
            )
            return

        houses = self.repository.list_houses(str(organization_id))
        if not houses:
            QMessageBox.information(
                self,
                "Нет домов",
                "У выбранной управляющей организации нет домов для обновления.",
            )
            return

        manual_houses = [
            house for house in houses if self._has_manual_management_data(house)
        ]
        overwrite_manual = False
        if manual_houses:
            message = QMessageBox(self)
            message.setWindowTitle("Обновить сведения по всем домам?")
            message.setIcon(QMessageBox.Icon.Question)
            message.setText(
                f"Будут запрошены сведения для {len(houses)} домов. "
                f"У {len(manual_houses)} домов есть данные, введённые вручную."
            )
            message.setInformativeText(
                "Можно сохранить ручные значения или заменить их сведениями из ГИС ЖКХ."
            )
            keep_button = message.addButton(
                "Не заменять ручные",
                QMessageBox.ButtonRole.AcceptRole,
            )
            replace_button = message.addButton(
                "Обновить все",
                QMessageBox.ButtonRole.ActionRole,
            )
            cancel_button = message.addButton(
                "Отмена",
                QMessageBox.ButtonRole.RejectRole,
            )
            message.exec()
            clicked = message.clickedButton()
            if clicked is cancel_button or clicked is None:
                return
            overwrite_manual = clicked is replace_button
            if clicked is keep_button:
                overwrite_manual = False
        else:
            answer = QMessageBox.question(
                self,
                "Обновить сведения по всем домам?",
                f"Получить из ГИС ЖКХ период управления и договор для {len(houses)} домов?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        organization = self.repository.get_organization(str(organization_id))
        seed_house = next(
            (house for house in houses if house.house_object_guid),
            None,
        )
        organization_root_guid = (
            organization.gis_organization_root_guid
            if organization is not None
            else None
        )
        if not organization_root_guid and seed_house is None:
            QMessageBox.warning(
                self,
                "Недостаточно идентификаторов",
                "Не удалось определить GUID организации: ни у одного дома нет "
                "GUID объекта жилищного фонда.",
            )
            return

        if not self._begin_bulk_operation(
            "management",
            f"Сопоставление сведений об управлении 0/{len(houses)}",
        ):
            return

        worker = GisIntegrationWorker(
            self.integration_gateway,
            "list_house_management_info",
            {
                "organizationRootGuid": organization_root_guid,
                "seedHouseObjectGuid": (
                    seed_house.house_object_guid if seed_house else None
                ),
            },
            timeout=180.0,
            parent=self,
        )
        self._management_worker = worker

        def succeeded(result: dict) -> None:
            remote_items = list(result.get("items") or [])
            indexed_items: list[tuple[set[str], dict]] = []
            for item in remote_items:
                identities = {
                    str(value).strip().casefold()
                    for value in (item.get("identities") or [])
                    if str(value or "").strip()
                }
                if identities:
                    indexed_items.append((identities, item))

            updated = 0
            not_found = 0
            skipped_manual = 0
            organization_guid = None
            resolved_root_guid = result.get("organizationRootGuid")

            for index, house in enumerate(houses, start=1):
                if self._has_manual_management_data(house) and not overwrite_manual:
                    skipped_manual += 1
                    self._set_bulk_status(f"Сопоставление сведений об управлении {index}/{len(houses)}")
                    QCoreApplication.processEvents()
                    continue

                local_ids = self._house_management_identity_values(house)
                match = None
                if local_ids:
                    for remote_ids, remote_item in indexed_items:
                        if local_ids.intersection(remote_ids):
                            match = remote_item
                            break

                if match is None:
                    not_found += 1
                    self._set_bulk_status(f"Сопоставление сведений об управлении {index}/{len(houses)}")
                    QCoreApplication.processEvents()
                    continue

                self.repository.update_house(
                    house.id,
                    management_start_date=match.get("managementStartDate"),
                    management_end_date=match.get("managementEndDate"),
                    management_agreement_number=match.get("agreementNumber"),
                    management_agreement_date=match.get("agreementDate"),
                    management_data_source="gis_public",
                )
                organization_guid = (
                    organization_guid or match.get("organizationGuid")
                )
                resolved_root_guid = (
                    resolved_root_guid or match.get("organizationRootGuid")
                )
                updated += 1
                self._set_bulk_status(f"Сопоставление сведений об управлении {index}/{len(houses)}")
                QCoreApplication.processEvents()

            organization_updates = {}
            if organization_guid:
                organization_updates["gis_organization_guid"] = organization_guid
            if resolved_root_guid:
                organization_updates["gis_organization_root_guid"] = resolved_root_guid
            if organization_updates:
                self.repository.update_organization(
                    str(organization_id),
                    **organization_updates,
                )
            current_house_id = self._current_house_id
            self.refresh_houses()
            if current_house_id:
                self._load_house(current_house_id)

            self._set_bulk_status(
                f"Сведения об управлении: обработано {len(houses)}/{len(houses)}, "
                f"обновлено {updated}."
            )
            details = [f"Обновлено: {updated}."]
            if not_found:
                details.append(f"Не найдено в открытых сведениях: {not_found}.")
            if skipped_manual:
                details.append(f"Сохранены ручные сведения: {skipped_manual}.")
            QMessageBox.information(
                self,
                "Массовое обновление завершено",
                "\n".join(details),
            )

        def failed(message: str) -> None:
            self._set_bulk_status("Ошибка загрузки сведений об управлении.")
            QMessageBox.critical(
                self,
                "Не удалось обновить дома",
                error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."),
            )

        def finished() -> None:
            self._management_worker = None
            self._finish_bulk_operation(self.bulk_operation_status.text())

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.start()

    def _sync_management_from_gis(self) -> None:
        if not self._flush_autosave():
            return
        if self._bulk_operation_name is not None:
            QMessageBox.information(
                self,
                "Выполняется массовая операция",
                "Дождитесь завершения массовой загрузки перед точечным запросом.",
            )
            return
        if not self._current_house_id or self.integration_gateway is None:
            return
        house = self.repository.get_house(self._current_house_id)
        if house is None:
            return
        if not house.house_object_guid:
            QMessageBox.warning(
                self,
                "Нет GUID дома",
                "Для получения сведений нужен GUID объекта жилищного фонда.",
            )
            return
        if (
            house.management_data_source == "manual"
            and (
                house.management_start_date
                or house.management_end_date
                or house.management_agreement_number
                or house.management_agreement_date
            )
        ):
            answer = QMessageBox.question(
                self,
                "Заменить ручные сведения?",
                "В карточке есть сведения об управлении, введённые вручную. "
                "Заменить их данными из открытой части ГИС ЖКХ?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if self._management_worker is not None and self._management_worker.isRunning():
            return

        self.sync_management_button.setEnabled(False)
        self.sync_management_button.setText("Получение сведений…")
        worker = GisIntegrationWorker(
            self.integration_gateway,
            "get_house_management_info",
            {
                "houseObjectGuid": house.house_object_guid,
                "gisHouseGuid": house.gis_house_guid,
                "fiasHouseGuid": house.fias_house_guid,
            },
            timeout=60.0,
            parent=self,
        )
        self._management_worker = worker
        house_id = house.id
        organization_id = house.organization_id

        def succeeded(result: dict) -> None:
            self.repository.update_house(
                house_id,
                management_start_date=result.get("managementStartDate"),
                management_end_date=result.get("managementEndDate"),
                management_agreement_number=result.get("agreementNumber"),
                management_agreement_date=result.get("agreementDate"),
                management_data_source="gis_public",
            )
            self.repository.update_organization(
                organization_id,
                gis_organization_guid=result.get("organizationGuid"),
                gis_organization_root_guid=result.get("organizationRootGuid"),
            )
            if self._current_house_id == house_id:
                self._load_house(house_id)
            QMessageBox.information(
                self,
                "Сведения обновлены",
                "Период управления и реквизиты договора получены из ГИС ЖКХ.",
            )

        def failed(message: str) -> None:
            QMessageBox.critical(
                self,
                "Не удалось получить сведения",
                error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."),
            )

        def finished() -> None:
            self._management_worker = None
            if self._current_house_id:
                self.sync_management_button.setEnabled(True)
            self.sync_management_button.setText(
                "Обновить сведения об управлении из ГИС ЖКХ"
            )

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.start()

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
            if not self._flush_autosave():
                return
        self._pending_house_id = self._current_house_id
        self._pending_fields[field] = value
        self._set_save_status("Сохраняется…")
        self._autosave_timer.start()
        if field in {"residential_premises_count", "non_residential_premises_count"}:
            self.total_label.setText(
                str(self.residential_spin.value() + self.non_residential_spin.value())
            )

    def _flush_autosave(self, *, refresh: bool = True) -> bool:
        self._autosave_timer.stop()
        if not self._pending_house_id or not self._pending_fields:
            return True
        house_id = self._pending_house_id
        fields = dict(self._pending_fields)
        try:
            self.repository.update_house(house_id, **fields)
            self._pending_house_id = None
            self._pending_fields.clear()
            self.retry_save_button.hide()
            self._set_save_status("Сохранено")
            if not refresh:
                return True
            if "organization_id" in fields:
                self.refresh_organizations(select_id=str(fields["organization_id"]))
            else:
                self.refresh_houses()
        except (sqlite3.DatabaseError, ValueError) as exc:
            self._set_save_status("Ошибка сохранения")
            self.retry_save_button.show()
            QMessageBox.critical(self, "Ошибка сохранения", error_text(exc, context=context_text(self), effect="Ввод сохранён в карточке дома. Переход и закрытие заблокированы до сохранения.", recovery="Устраните причину и нажмите «Повторить сохранение»."))
            return False
        return True

    def _set_save_status(self, text: str) -> None:
        self.save_status.setText("Сохранено локально" if text == "Сохранено" else text)

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
        guard = getattr(self.window(), "can_delete_local_data", None)
        if guard is not None and not guard():
            return
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
        guard = getattr(self.window(), "can_delete_local_data", None)
        if guard is not None and not guard():
            return
        if not self._current_house_id:
            return
        house = self.repository.get_house(self._current_house_id)
        if not house:
            return
        answer = QMessageBox.question(
            self,
            "Удалить дом?",
            f"Удалить «{house.address}» из локальной базы?\n\n"
            "Связанные перечни, планы, фиксация и оценки этого дома также будут удалены.",
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
            QMessageBox.critical(self, "Не удалось создать шаблон", error_text(exc, context=context_text(self)))
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
            QMessageBox.critical(self, "Не удалось прочитать файл", error_text(exc, context=context_text(self)))
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
        if self._bulk_operation_name is not None:
            QMessageBox.information(
                self,
                "Выполняется массовая операция",
                "Дождитесь завершения массовой загрузки перед импортом домов из XLSX.",
            )
            return
        message = QMessageBox(self)
        message.setWindowTitle("Импорт домов из XLSX")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(
            "Для импорта домов используйте выгрузку ГИС ЖКХ:\n"
            "«Реестр объектов жилищного фонда» → «Выгрузить информацию».\n\n"
            "Из архива «Объекты жилищного фонда.zip» распакуйте XLSX-файл."
        )
        choose_button = message.addButton(
            "Выбрать файл…",
            QMessageBox.ButtonRole.AcceptRole,
        )
        message.addButton(
            "Отмена",
            QMessageBox.ButtonRole.RejectRole,
        )
        message.exec()
        if message.clickedButton() is not choose_button:
            return

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
            QMessageBox.critical(self, "Не удалось импортировать файл", error_text(exc, context=context_text(self)))
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

    def flush_pending_changes(self) -> bool:
        """Persist any debounced edits before the application changes context or closes."""
        self._selection_refresh_timer.stop()
        return self._flush_autosave()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.flush_pending_changes():
            event.ignore()
            return
        super().closeEvent(event)
