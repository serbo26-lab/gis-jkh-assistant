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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
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
from .widgets import SafeComboBox, SafeSpinBox
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.page_scroll = QScrollArea()
        self.page_scroll.setObjectName("PageScroll")
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.page_content = QWidget()
        self.page_content.setObjectName("PageContent")
        self.page_scroll.setWidget(self.page_content)
        outer.addWidget(self.page_scroll)

        root = QVBoxLayout(self.page_content)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        self.page_layout = root

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
        self.context_card = context
        context_layout = QGridLayout(context)
        self.context_layout = context_layout
        context_layout.setContentsMargins(14, 12, 14, 12)
        context_layout.setHorizontalSpacing(10)
        context_layout.setVerticalSpacing(8)

        context_title = QLabel("Контекст")
        context_title.setObjectName("SectionTitle")
        context_layout.addWidget(context_title, 0, 0, 1, 4)

        context_layout.addWidget(QLabel("Управляющая организация"), 1, 0)
        self.organization_combo = SafeComboBox()
        self.organization_combo.setMinimumWidth(280)
        context_layout.addWidget(self.organization_combo, 1, 1, 1, 3)

        context_layout.addWidget(QLabel("Дом"), 2, 0)
        self.house_combo = SafeComboBox()
        self.house_combo.setMinimumWidth(320)
        context_layout.addWidget(self.house_combo, 2, 1, 1, 3)

        creation_title = QLabel("Создание перечня")
        creation_title.setObjectName("SectionTitle")
        context_layout.addWidget(creation_title, 3, 0, 1, 4)

        context_layout.addWidget(QLabel("Год"), 4, 0)
        self.year_spin = SafeSpinBox()
        self.year_spin.setRange(2015, 2100)
        self.year_spin.setValue(datetime.now().year)
        self.year_spin.setFixedWidth(138)
        context_layout.addWidget(self.year_spin, 4, 1)

        context_layout.addWidget(QLabel("Шаблон перечня"), 4, 2)
        self.template_combo = SafeComboBox()
        self.template_combo.setMinimumWidth(260)
        context_layout.addWidget(self.template_combo, 4, 3)

        self.templates_button = QPushButton("Шаблоны пользователя")
        self.create_from_current_button = QPushButton("Создать на основе выбранного перечня")
        self.create_button = QPushButton("Создать новый перечень")
        self.create_button.setObjectName("PrimaryButton")

        creation_actions = QGridLayout()
        self.creation_actions = creation_actions
        creation_actions.setHorizontalSpacing(8)
        creation_actions.setVerticalSpacing(6)
        creation_actions.addWidget(self.templates_button, 0, 0)
        creation_actions.addWidget(self.create_button, 0, 3)
        creation_actions.setColumnStretch(1, 1)
        creation_actions.setColumnStretch(2, 1)
        context_layout.addLayout(creation_actions, 5, 0, 1, 4)

        gis_title = QLabel("Обмен с ГИС ЖКХ")
        gis_title.setObjectName("SectionTitle")
        context_layout.addWidget(gis_title, 6, 0, 1, 4)

        self.export_template_status = QLabel("Шаблон экспорта: не загружен")
        self.export_template_status.setObjectName("Muted")
        self.export_template_status.setWordWrap(True)
        context_layout.addWidget(self.export_template_status, 7, 0, 1, 4)

        self.load_gis_lists_button = QPushButton(
            "Загрузить перечни работ и услуг по МКД"
        )
        self.load_gis_lists_button.setToolTip(
            "Получить из ГИС ЖКХ перечни только для выбранного дома."
        )
        self.load_export_template_button = QPushButton(
            "Получить / обновить шаблон из ГИС ЖКХ"
        )
        self.load_export_template_button.setToolTip(
            "Автоматически запросить официальный XLSX-шаблон перечня в ГИС ЖКХ "
            "и сохранить его как техническую основу."
        )
        self.load_export_template_file_button = QPushButton("Выбрать XLSX вручную…")
        self.load_export_template_file_button.setToolTip(
            "Резервный способ: выбрать ранее выгруженный официальный XLSX-файл."
        )
        self.export_button = QPushButton("Сформировать XLSX из выбранного перечня")
        self.upload_to_gis_button = QPushButton("Загрузить в ГИС ЖКХ")
        self.sync_gis_button = QPushButton("Проверить статус в ГИС")
        self.cancel_gis_button = QPushButton("Отменить перечень в ГИС ЖКХ")
        self.cancel_gis_button.setObjectName("DangerButton")
        self.cancel_gis_button.setToolTip(
            "Отменить размещенный перечень. ГИС ЖКХ удалит связанные планирование, "
            "фиксацию и оценку качества; после этого состав перечня можно изменить."
        )
        self.save_editable_gis_button = QPushButton("Сохранить изменения в ГИС ЖКХ")
        self.save_editable_gis_button.setToolTip(
            "Сохранить новый состав отмененного перечня или проекта напрямую в ГИС ЖКХ."
        )
        self.approve_gis_button = QPushButton("Разместить информацию")
        self.approve_gis_button.setObjectName("PrimaryButton")

        gis_actions = QGridLayout()
        self.gis_actions = gis_actions
        gis_actions.setHorizontalSpacing(8)
        gis_actions.setVerticalSpacing(6)
        gis_actions.addWidget(self.load_gis_lists_button, 0, 0)
        gis_actions.addWidget(self.load_export_template_button, 0, 1)
        gis_actions.addWidget(self.load_export_template_file_button, 0, 2)
        gis_actions.addWidget(self.export_button, 1, 0)
        gis_actions.addWidget(self.upload_to_gis_button, 1, 1)
        gis_actions.addWidget(self.sync_gis_button, 1, 2)
        gis_actions.addWidget(self.cancel_gis_button, 2, 0)
        gis_actions.addWidget(self.save_editable_gis_button, 2, 1)
        gis_actions.addWidget(self.approve_gis_button, 2, 2)
        gis_actions.setColumnStretch(1, 1)
        context_layout.addLayout(gis_actions, 8, 0, 1, 4)

        context_layout.setColumnStretch(1, 1)
        context_layout.setColumnStretch(3, 3)
        root.addWidget(context)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        splitter.setMinimumHeight(300)
        root.addWidget(splitter, 0)

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

        self.create_from_current_button.setToolTip(
            "Создать новый перечень на основе выбранного в списке выше."
        )
        left_layout.addWidget(self.create_from_current_button)

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
        self.start_month.setMinimumWidth(160)
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
        self.end_month.setMinimumWidth(160)
        self.end_month.addItems(_MONTHS)
        self.end_year = SafeSpinBox()
        self.end_year.setRange(2015, 2100)
        self.end_year.setFixedWidth(132)
        end_layout.addWidget(self.end_month, 1)
        end_layout.addWidget(self.end_year)
        period_form.addRow("Период по", end_widget)

        self.template_label = QLabel("—")
        self.status_label = QLabel("—")
        self.planned_total_label = QLabel("—")
        self.gis_state_label = QLabel("—")
        self.gis_state_label.setWordWrap(True)
        period_form.addRow("Шаблон", self.template_label)
        period_form.addRow("Статус", self.status_label)
        period_form.addRow("Планируемая сумма", self.planned_total_label)
        period_form.addRow("ГИС ЖКХ", self.gis_state_label)
        right_layout.addLayout(period_form)

        splitter.addWidget(right)
        splitter.setSizes([340, 900])

        self.items_card = QFrame()
        self.items_card.setObjectName("Card")
        items_layout = QVBoxLayout(self.items_card)
        items_layout.setContentsMargins(16, 14, 16, 16)
        items_layout.setSpacing(10)

        items_header = QHBoxLayout()
        items_title = QLabel("Работы и услуги")
        items_title.setObjectName("SectionTitle")
        items_header.addWidget(items_title)
        items_header.addStretch(1)
        items_layout.addLayout(items_header)

        self.add_works_button = QPushButton("Добавить…")
        self.add_works_button.setToolTip(
            "Добавить работы и услуги из актуального справочника организации в ГИС ЖКХ."
        )
        self.remove_works_button = QPushButton("Удалить")
        self.remove_works_button.setToolTip(
            "Удалить выбранные строки из локального перечня."
        )
        self.clear_works_button = QPushButton("Очистить состав")
        self.clear_works_button.setToolTip(
            "Удалить все работы и услуги из локального состава перечня."
        )
        self.replace_from_template_button = QPushButton("Заменить по шаблону")
        self.replace_from_template_button.setToolTip(
            "Полностью заменить состав перечня работами выбранного сверху шаблона, "
            "сопоставленными с актуальным справочником ГИС ЖКХ."
        )
        self.save_as_template_button = QPushButton("В шаблон…")
        self.save_as_template_button.setToolTip(
            "Сохранить текущий состав перечня как пользовательский шаблон."
        )
        self.lazy_fill_button = QPushButton("Распределить сумму…")
        self.lazy_fill_button.setToolTip(
            "Распределить планируемую итоговую сумму между работами и услугами."
        )

        self.items_actions = QGridLayout()
        self.items_actions.setHorizontalSpacing(8)
        self.items_actions.setVerticalSpacing(6)
        items_layout.addLayout(self.items_actions)

        input_hint = QLabel(
            "Для каждой строки используется один из двух вариантов: "
            "«Цена + Объем + Кол-во» или «Кол-во + Стоимость». "
            "Недоступные поля блокируются автоматически."
        )
        input_hint.setWordWrap(True)
        input_hint.setObjectName("Muted")
        items_layout.addWidget(input_hint)

        self.items_table = QTableWidget(0, 5)
        self.items_table.setHorizontalHeaderLabels(
            ["Работа / услуга", "Цена", "Объем", "Кол-во", "Стоимость"]
        )
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setMinimumHeight(440)
        self.items_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.items_table.verticalHeader().setVisible(False)
        header_view = self.items_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        items_layout.addWidget(self.items_table, 1)

        root.addWidget(self.items_card, 1)

        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.templates_button.clicked.connect(self._manage_templates)
        self.load_gis_lists_button.clicked.connect(self._load_house_work_lists_from_gis)
        self.load_export_template_button.clicked.connect(
            self._download_export_template_from_gis
        )
        self.load_export_template_file_button.clicked.connect(
            self._load_export_template_file
        )
        self.export_button.clicked.connect(self._export_current_work_list)
        self.upload_to_gis_button.clicked.connect(self._upload_current_work_list_to_gis)
        self.sync_gis_button.clicked.connect(self._sync_current_work_list_from_gis)
        self.cancel_gis_button.clicked.connect(self._cancel_current_work_list_in_gis)
        self.save_editable_gis_button.clicked.connect(
            self._save_editable_work_list_to_gis
        )
        self.approve_gis_button.clicked.connect(self._approve_current_work_list_in_gis)
        self.create_button.clicked.connect(self._create_work_list)
        self.create_from_current_button.clicked.connect(self._clone_current_work_list)
        self.delete_button.clicked.connect(self._delete_work_list)
        self.add_works_button.clicked.connect(self._add_works_from_directory)
        self.remove_works_button.clicked.connect(self._remove_selected_works)
        self.clear_works_button.clicked.connect(self._clear_all_works)
        self.replace_from_template_button.clicked.connect(
            self._replace_current_from_template
        )
        self.save_as_template_button.clicked.connect(self._save_current_as_template)
        self.lazy_fill_button.clicked.connect(self._open_lazy_distribution)
        self.work_lists.currentItemChanged.connect(self._work_list_changed)

        for widget in (self.start_month, self.end_month):
            widget.currentIndexChanged.connect(self._queue_period_save)
        for widget in (self.start_year, self.end_year):
            widget.valueChanged.connect(self._queue_period_save)
        self.items_table.itemChanged.connect(self._item_changed)

        self._view_mode = "normal"
        self._apply_view_mode("normal")
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
            if template:
                template_name = template.name
            elif work_list.gis_work_list_guid:
                template_name = "Из ГИС ЖКХ"
            else:
                template_name = "Пустой перечень"
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
        elif self.work_lists.count():
            self.work_lists.setCurrentRow(self.work_lists.count() - 1)
        self.work_lists.blockSignals(False)

        current = self.work_lists.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._load_work_list(current_id)

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
            self.template_label.setText(template.name if template else "Пустой перечень")
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

    @staticmethod
    def _work_list_status_text(work_list) -> str:
        value = work_list.status.value
        if value == "local":
            return "Локальный"
        if value == "gis_draft":
            return "Проект в ГИС ЖКХ"
        if value == "placed":
            return "Размещен в ГИС ЖКХ"
        return value

    @staticmethod
    def _gis_state_text(work_list) -> str:
        if work_list.gis_status == "DRAFT":
            text = "Проект"
        elif work_list.gis_status == "APPROVED":
            text = "Размещен"
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

        enabled = ready and selected and not self._integration_busy
        self.sync_gis_button.setEnabled(enabled)
        self.upload_to_gis_button.setEnabled(
            enabled and gis_status in {None, "", "NOT_FOUND"}
        )
        self.approve_gis_button.setEnabled(enabled and gis_status == "DRAFT")

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
                "Сначала загрузите сведения о МКД из ГИС ЖКХ.",
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
                + "\n\nМожно воспользоваться резервной кнопкой "
                "«Выбрать XLSX вручную…».",
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
                "Сначала нажмите «Получить / обновить шаблон из ГИС ЖКХ». "
                "Если автоматическая загрузка недоступна, используйте "
                "«Выбрать XLSX вручную…».",
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
        worker = GisIntegrationWorker(
            self.integration_gateway,
            command_type,
            payload,
            timeout=timeout,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
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
            "Загрузить перечень в ГИС ЖКХ?",
            f"Будет загружен перечень {work_list.period_label}\n"
            f"для дома:\n{house.address}\n\n"
            "После импорта в ГИС ЖКХ он должен появиться со статусом «Проект». "
            "Размещение выполняется отдельной кнопкой.",
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
                    "Перечень загружен",
                    "ГИС ЖКХ принял файл. Перечень найден со статусом «Проект».",
                )
            else:
                QMessageBox.information(
                    self,
                    "Файл отправлен",
                    "ГИС ЖКХ принял файл в обработку. Проект пока не найден. "
                    "Через некоторое время нажмите «Проверить статус в ГИС».",
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
            select_id = (
                applied.imported_ids[-1]
                if applied.imported_ids
                else self._current_work_list_id
            )
            self.refresh_work_lists(select_id=select_id)

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

    def _add_works_from_directory(self) -> None:
        self._save_period()
        if not self._current_work_list_id:
            return
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if not work_list:
            return
        if str(work_list.gis_status or "").upper() not in {"", "NOT_FOUND"}:
            QMessageBox.information(
                self,
                "Состав уже передан в ГИС ЖКХ",
                "Добавление и удаление работ доступно только у локального перечня, "
                "который ещё не загружен в ГИС ЖКХ.",
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

    def _set_editor_enabled(self, enabled: bool) -> None:
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if enabled and self._current_work_list_id
            else None
        )
        composition_editable = bool(
            work_list
            and str(work_list.gis_status or "").upper() in {"", "NOT_FOUND"}
        )
        for widget in (
            self.start_month,
            self.start_year,
            self.end_month,
            self.end_year,
            self.items_table,
            self.delete_button,
            self.create_from_current_button,
            self.export_button,
            self.lazy_fill_button,
            self.save_as_template_button,
        ):
            widget.setEnabled(enabled)
        self.add_works_button.setEnabled(enabled and composition_editable)
        self.remove_works_button.setEnabled(enabled and composition_editable)
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

        if mode == "normal":
            self.page_content.setMinimumSize(1040, 700)
            self.page_layout.setContentsMargins(20, 18, 20, 20)
            self.page_layout.setSpacing(14)
            self.context_layout.setContentsMargins(14, 12, 14, 12)
            self.context_layout.setHorizontalSpacing(10)
            self.context_layout.setVerticalSpacing(8)
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            self.splitter.setSizes([340, 900])

            self.creation_actions.addWidget(self.templates_button, 0, 0)
            self.creation_actions.addWidget(self.create_button, 0, 3)
            self.creation_actions.setColumnStretch(1, 1)
            self.creation_actions.setColumnStretch(2, 1)

            self.gis_actions.addWidget(self.load_gis_lists_button, 0, 0)
            self.gis_actions.addWidget(self.load_export_template_button, 0, 1)
            self.gis_actions.addWidget(self.load_export_template_file_button, 0, 2)
            self.gis_actions.addWidget(self.export_button, 1, 0)
            self.gis_actions.addWidget(self.upload_to_gis_button, 1, 1)
            self.gis_actions.addWidget(self.sync_gis_button, 1, 2)
            self.gis_actions.addWidget(self.approve_gis_button, 2, 2)
            self.gis_actions.setColumnStretch(1, 1)

            self.items_actions.addWidget(self.add_works_button, 0, 0)
            self.items_actions.addWidget(self.remove_works_button, 0, 1)
            self.items_actions.addWidget(self.save_as_template_button, 0, 2)
            self.items_actions.addWidget(self.lazy_fill_button, 0, 3)
            for column in range(4):
                self.items_actions.setColumnStretch(column, 1)
            self.items_table.setMinimumHeight(440)

        elif mode == "compact":
            self.page_content.setMinimumSize(760, 660)
            self.page_layout.setContentsMargins(14, 12, 14, 14)
            self.page_layout.setSpacing(10)
            self.context_layout.setContentsMargins(10, 9, 10, 9)
            self.context_layout.setHorizontalSpacing(8)
            self.context_layout.setVerticalSpacing(6)
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            self.splitter.setSizes([280, 620])

            self.creation_actions.addWidget(self.templates_button, 0, 0)
            self.creation_actions.addWidget(self.create_button, 0, 3)
            self.creation_actions.setColumnStretch(1, 1)
            self.creation_actions.setColumnStretch(2, 1)

            self.gis_actions.addWidget(self.load_gis_lists_button, 0, 0)
            self.gis_actions.addWidget(self.load_export_template_button, 0, 1)
            self.gis_actions.addWidget(self.load_export_template_file_button, 0, 2)
            self.gis_actions.addWidget(self.export_button, 1, 0)
            self.gis_actions.addWidget(self.upload_to_gis_button, 1, 1)
            self.gis_actions.addWidget(self.sync_gis_button, 1, 2)
            self.gis_actions.addWidget(self.approve_gis_button, 2, 2)
            self.gis_actions.setColumnStretch(1, 1)

            self.items_actions.addWidget(self.add_works_button, 0, 0)
            self.items_actions.addWidget(self.remove_works_button, 0, 1)
            self.items_actions.addWidget(self.save_as_template_button, 1, 0)
            self.items_actions.addWidget(self.lazy_fill_button, 1, 1)
            self.items_actions.setColumnStretch(0, 1)
            self.items_actions.setColumnStretch(1, 1)
            self.items_table.setMinimumHeight(400)

        else:
            # On a narrow window the two work areas are stacked vertically instead
            # of being squeezed into unusable columns. The whole page remains
            # scrollable, so no controls disappear below the viewport.
            self.page_content.setMinimumSize(620, 980)
            self.page_layout.setContentsMargins(10, 8, 10, 10)
            self.page_layout.setSpacing(8)
            self.context_layout.setContentsMargins(9, 8, 9, 8)
            self.context_layout.setHorizontalSpacing(6)
            self.context_layout.setVerticalSpacing(5)
            self.splitter.setOrientation(Qt.Orientation.Vertical)
            self.splitter.setSizes([260, 360])

            self.creation_actions.addWidget(self.templates_button, 0, 0)
            self.creation_actions.addWidget(self.create_button, 0, 1)
            self.creation_actions.setColumnStretch(0, 1)
            self.creation_actions.setColumnStretch(1, 1)

            self.gis_actions.addWidget(self.load_gis_lists_button, 0, 0, 1, 2)
            self.gis_actions.addWidget(self.load_export_template_button, 1, 0, 1, 2)
            self.gis_actions.addWidget(self.load_export_template_file_button, 2, 0, 1, 2)
            self.gis_actions.addWidget(self.export_button, 3, 0, 1, 2)
            self.gis_actions.addWidget(self.upload_to_gis_button, 4, 0, 1, 2)
            self.gis_actions.addWidget(self.sync_gis_button, 5, 0)
            self.gis_actions.addWidget(self.approve_gis_button, 5, 1)
            self.gis_actions.setColumnStretch(0, 1)
            self.gis_actions.setColumnStretch(1, 1)

            self.items_actions.addWidget(self.add_works_button, 0, 0)
            self.items_actions.addWidget(self.remove_works_button, 0, 1)
            self.items_actions.addWidget(self.save_as_template_button, 1, 0)
            self.items_actions.addWidget(self.lazy_fill_button, 1, 1)
            self.items_actions.setColumnStretch(0, 1)
            self.items_actions.setColumnStretch(1, 1)
            self.items_table.setMinimumHeight(360)

    def set_compact_mode(self, compact: bool) -> None:
        """Compatibility shim for older callers."""

        self.set_view_mode("compact" if compact else "normal")

    def flush_pending_changes(self) -> None:
        self._save_period()
