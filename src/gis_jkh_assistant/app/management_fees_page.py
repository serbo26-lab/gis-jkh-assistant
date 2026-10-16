"""Management fee projects based on house tariffs and existing GIS protocols."""
from __future__ import annotations

import sqlite3
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gis_jkh_assistant.management_fees import (
    ManagementFeeStore, STATUSES, can_edit_remote, helper_supports_fees, period_warning, validate_document,
)
from gis_jkh_assistant.fee_bulk import transfer_reason
from gis_jkh_assistant.gis_integration import IntegrationError
from .action_layout import ActionFlowLayout
from .gis_integration_worker import GisIntegrationWorker, connect_result
from .icons import set_button_icon
from .workflow_widgets import CommandGroup, OperationStatus, SectionCard, style_danger_button
from .fee_bulk_dialogs import FeeAmendmentDialog, FeeGenerationDialog, FeeQueueDialog
from .fee_bulk_queue import FeeQueue
from .widgets import SafeComboBox


def display_date(value):
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return value or "—"


def protocol_label(protocol):
    return (f"№ {protocol.get('protocolCode', 'Без номера')} от {display_date(protocol.get('protocolDate'))}"
            if protocol else "Протокол не выбран")


class ManagementFeesPage(QWidget):
    def __init__(self, repository, integration_gateway=None):
        super().__init__()
        self.repository, self.integration_gateway = repository, integration_gateway
        self.store = ManagementFeeStore(repository)
        self._record_id = None
        self._house_id = None
        self._dirty = False
        self._loading = False
        self._integration_busy = False
        self._workflow_busy = False
        self._worker = None
        self._protocol = None
        self._save_error = False
        self._queue_running = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page_content = QWidget()
        self.page_layout = QVBoxLayout(self.page_content)
        body = self.page_layout
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(6)
        title = QLabel("Информация о размере платы")
        title.setObjectName("Title")
        body.addWidget(title)
        context = QHBoxLayout()
        self.organization_combo, self.house_combo = QComboBox(self), SafeComboBox()
        self.organization_combo.hide()
        for label, combo in (("Дом", self.house_combo),):
            context.addWidget(QLabel(label))
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            context.addWidget(combo, 1)
        body.addLayout(context)
        self.import_group = CommandGroup("Импорт из ГИС ЖКХ — выбранный дом")
        self.load_button = QPushButton("Загрузить размер платы дома из ГИС")
        self.new_button = QPushButton("Новый проект из тарифа…")
        self.delete_button = QPushButton("Удалить локальный проект…")
        self.select_all_button = QPushButton("Выбрать все периоды")
        self.clear_selection_button = QPushButton("Снять выбор")
        self.selection_count = QLabel("Отмечено периодов: 0")
        self.select_all_button.clicked.connect(lambda: self._check_all(True))
        self.clear_selection_button.clicked.connect(lambda: self._check_all(False))
        style_danger_button(self.delete_button)
        for button, name in ((self.load_button, "download-cloud"), (self.new_button, "file-template"),
                             (self.delete_button, "trash"), (self.select_all_button, "check-all"),
                             (self.clear_selection_button, "check-all")):
            set_button_icon(button, name)
            button.setProperty("compactCommand", True)
        self.import_group.actions.addWidget(self.load_button)
        body.addWidget(self.import_group)
        self.preparation_group = CommandGroup("Подготовка локальных проектов по тарифам дома")
        self.bulk_generate_button = QPushButton("Сформировать проекты по тарифам…")
        self.bulk_amend_button = QPushButton("Обновить отмеченные по тарифам…")
        self.bulk_group = CommandGroup("Экспорт в ГИС ЖКХ — отмеченные периоды")
        self.bulk_save_button = QPushButton("Сохранить проекты в ГИС…")
        self.bulk_approve_button = QPushButton("Разместить сохранённые проекты…")
        self.bulk_save_approve_button = QPushButton("Сохранить и разместить проекты…")
        self.bulk_save_button.setToolTip("Сохранить отмеченные локальные проекты и правки в ГИС без размещения.")
        self.bulk_approve_button.setToolTip("Разместить отмеченные проекты, уже сохранённые в ГИС, без локальных правок.")
        self.bulk_save_approve_button.setToolTip("Сохранить отмеченные проекты и правки в ГИС, затем разместить их.")
        for button, glyph in ((self.bulk_generate_button, "wand"), (self.bulk_amend_button, "edit"),
                              (self.bulk_save_button, "upload-cloud"),
                              (self.bulk_approve_button, "upload-cloud"),
                              (self.bulk_save_approve_button, "upload-cloud")):
            set_button_icon(button, glyph)
            button.setProperty("compactCommand", True)
        for button in (self.new_button, self.bulk_generate_button, self.bulk_amend_button):
            self.preparation_group.actions.addWidget(button)
        body.addWidget(self.preparation_group)
        for button in (self.bulk_save_button, self.bulk_approve_button, self.bulk_save_approve_button):
            self.bulk_group.actions.addWidget(button)
        self.periods_card = SectionCard("Периоды размера платы")
        for control in (self.select_all_button, self.clear_selection_button, self.selection_count):
            self.periods_card.actions.addWidget(control)
        self.operation_status = OperationStatus()
        self.operation_status.hide()
        self.queue_summary = QLabel()
        self.queue_summary.setWordWrap(True)
        self.queue_summary.hide()
        self.queue_group = CommandGroup("Очередь экспорта в ГИС ЖКХ")
        self.queue_group.hide()
        self.queue_group.body.insertWidget(1, self.queue_summary)
        self.queue_stop_button = QPushButton("Остановить после текущего запроса")
        self.queue_continue_button = QPushButton("Продолжить остальные")
        self.queue_results_button = QPushButton("Результаты очереди…")
        for button in (self.queue_stop_button, self.queue_continue_button, self.queue_results_button):
            self.queue_group.actions.addWidget(button)
            button.setProperty("compactCommand", True)
            button.hide()
        set_button_icon(self.queue_results_button, "services")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Выбор", "Период платы", "Руб./м² в месяц", "Основание", "Договор", "Состояние"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setToolTip("Отметьте периоды галочками для массовой операции. Нажатие строки открывает её сведения. Ctrl+A — отметить все периоды.")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 56)
        self._select_all_shortcut = QShortcut(QKeySequence.StandardKey.SelectAll, self.table)
        self._select_all_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._select_all_shortcut.activated.connect(lambda: self._check_all(True))
        self.table.setMinimumHeight(195)
        self.periods_card.body.addWidget(self.table, 1)
        body.addWidget(self.periods_card, 1)
        self.empty = QLabel("Загрузите сведения из ГИС или создайте проект из тарифа, заполненного в разделе «Дома».")
        self.empty.setWordWrap(True)
        self.periods_card.body.addWidget(self.empty)
        body.addWidget(self.bulk_group)
        body.addWidget(self.operation_status)
        body.addWidget(self.queue_group)
        self.editor = SectionCard("Открытый период — сведения и действия")
        form = QFormLayout()
        self.contract_combo = SafeComboBox()
        self.contract_combo.setMinimumContentsLength(20)
        self.contract_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form.addRow("Договор управления", self.contract_combo)
        period = QHBoxLayout()
        self.start_edit, self.end_edit = QDateEdit(), QDateEdit()
        for field in (self.start_edit, self.end_edit):
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd.MM.yyyy")
            field.setDateRange(QDate(1900, 1, 1), QDate(9999, 12, 31))
        period.addWidget(self.start_edit)
        period.addWidget(QLabel("—"))
        period.addWidget(self.end_edit)
        period.addStretch()
        form.addRow("Период платы", period)
        self.amount_edit = QLineEdit()
        self.amount_edit.setMaximumWidth(180)
        self.amount_edit.setPlaceholderText("Руб. за 1 м² в месяц")
        form.addRow("Плата, руб./м² в месяц", self.amount_edit)
        basis = ActionFlowLayout()
        self.protocol_text = QLabel()
        self.protocol_text.setWordWrap(True)
        self.protocol_text.setProperty("flowFullText", True)
        self.protocol_button = QPushButton("Выбрать протокол из ГИС…")
        self.refresh_protocols_button = QPushButton("Обновить протоколы")
        set_button_icon(self.refresh_protocols_button, "sync")
        for item in (self.protocol_text, self.protocol_button, self.refresh_protocols_button):
            basis.addWidget(item)
        form.addRow("Основание", basis)
        self.editor.body.addLayout(form)
        self.state_text = QLabel()
        self.state_text.setWordWrap(True)
        self.editor.body.addWidget(self.state_text)
        self.local_record_group = CommandGroup("Локальные изменения открытого периода")
        self.record_export_group = CommandGroup("Экспорт в ГИС ЖКХ — открытый период")
        self.record_removal_group = CommandGroup("Удаление и аннулирование открытого периода")
        self.edit_button = QPushButton("Изменить…")
        self.cancel_edit_button = QPushButton("Отменить локальные правки…")
        self.local_button = QPushButton("Сохранить локально")
        self.save_button = QPushButton("Сохранить проект в ГИС")
        self.approve_button = QPushButton("Разместить…")
        self.annul_button = QPushButton("Аннулировать сведения в ГИС…")
        self.approve_button.setObjectName("PrimaryButton")
        style_danger_button(self.annul_button)
        for button, name in ((self.edit_button, "edit"), (self.cancel_edit_button, "edit"),
                             (self.local_button, "save"), (self.save_button, "upload-cloud"),
                             (self.approve_button, "upload-cloud"), (self.annul_button, "ban")):
            set_button_icon(button, name)
            button.setProperty("compactCommand", True)
        for button in (self.edit_button, self.local_button, self.cancel_edit_button):
            self.local_record_group.actions.addWidget(button)
        for button in (self.save_button, self.approve_button):
            self.record_export_group.actions.addWidget(button)
        for button in (self.delete_button, self.annul_button):
            self.record_removal_group.actions.addWidget(button)
        for group in (self.local_record_group, self.record_export_group, self.record_removal_group):
            self.editor.body.addWidget(group)
        body.addWidget(self.editor)
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        self.reason.setObjectName("Muted")
        body.addWidget(self.reason)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.page_content)
        self.page_scroll = scroll
        root.addWidget(scroll)
        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.table.currentCellChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._check_changed)
        self.contract_combo.currentIndexChanged.connect(self._contract_changed)
        self.start_edit.dateChanged.connect(self._edited)
        self.end_edit.dateChanged.connect(self._edited)
        self.amount_edit.textChanged.connect(self._edited)
        self.load_button.clicked.connect(self._load)
        self.new_button.clicked.connect(self._new)
        self.delete_button.clicked.connect(self._delete)
        self.local_button.clicked.connect(self.flush_pending_changes)
        self.edit_button.clicked.connect(self._begin_edit)
        self.cancel_edit_button.clicked.connect(self._cancel_edit)
        self.protocol_button.clicked.connect(self._choose_protocol)
        self.refresh_protocols_button.clicked.connect(self._load_protocols)
        self.save_button.clicked.connect(lambda: self._send("save_management_fee_project"))
        self.approve_button.clicked.connect(lambda: self._send("approve_management_fee"))
        self.annul_button.clicked.connect(lambda: self._send("annul_management_fee"))
        self.fee_queue = FeeQueue(self)
        self.fee_queue.changed.connect(self._update_queue_ui)
        self.bulk_generate_button.clicked.connect(self._generate_bulk)
        self.bulk_amend_button.clicked.connect(self._amend_bulk)
        self.bulk_save_button.clicked.connect(lambda: self._start_bulk("save"))
        self.bulk_approve_button.clicked.connect(lambda: self._start_bulk("approve"))
        self.bulk_save_approve_button.clicked.connect(lambda: self._start_bulk("save_approve"))
        self.queue_stop_button.clicked.connect(self.fee_queue.request_stop)
        self.queue_continue_button.clicked.connect(self._resume_bulk)
        self.queue_results_button.clicked.connect(self._show_queue_results)
        focus_order = (self.house_combo, self.load_button, self.new_button, self.bulk_generate_button,
                       self.bulk_amend_button, self.select_all_button, self.clear_selection_button,
                       self.table, self.bulk_save_button, self.bulk_approve_button, self.bulk_save_approve_button,
                       self.queue_stop_button, self.queue_continue_button, self.queue_results_button,
                       self.contract_combo, self.start_edit, self.end_edit, self.amount_edit,
                       self.protocol_button, self.refresh_protocols_button, self.edit_button,
                       self.local_button, self.cancel_edit_button, self.save_button, self.approve_button,
                       self.delete_button, self.annul_button)
        for before, after in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(before, after)
        self.refresh_organizations()

    def refresh_organizations(self):
        if self._integration_busy or self._queue_running or not self.flush_pending_changes():
            return
        old = self.organization_combo.currentData()
        self.organization_combo.blockSignals(True)
        self.organization_combo.clear()
        for org in self.repository.list_organizations():
            self.organization_combo.addItem(org.name, org.id)
        index = self.organization_combo.findData(old)
        self.organization_combo.setCurrentIndex(index if index >= 0 else (0 if self.organization_combo.count() else -1))
        self.organization_combo.blockSignals(False)
        self._organization_changed()

    def _organization_changed(self):
        if not self.flush_pending_changes():
            self._restore_context()
            return
        old = self.house_combo.currentData()
        self.house_combo.blockSignals(True)
        self.house_combo.clear()
        org_id = self.organization_combo.currentData()
        for house in self.repository.list_houses(org_id) if org_id else []:
            self.house_combo.addItem(house.address, house.id)
        index = self.house_combo.findData(old)
        self.house_combo.setCurrentIndex(index if index >= 0 else (0 if self.house_combo.count() else -1))
        self.house_combo.blockSignals(False)
        self._house_changed()

    def _restore_context(self):
        house = self.repository.get_house(self._house_id) if self._house_id else None
        for combo, value in ((self.organization_combo, house.organization_id if house else None),
                             (self.house_combo, self._house_id)):
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(value))
            combo.blockSignals(False)

    def _house_changed(self):
        if not self.flush_pending_changes():
            self._restore_context()
            return
        house_id = self.house_combo.currentData()
        selected = self._record_id if house_id == self._house_id else None
        self._house_id = house_id
        self._refresh_table(selected)

    def _record(self):
        return self.store.get(self._record_id) if self._record_id else None

    def _selected_record_ids(self):
        return [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(self.table.rowCount())
                if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _check_all(self, checked):
        if not self.table.isEnabled():
            return
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._refresh_integration_status()

    def _check_changed(self, item):
        if item.column() == 0:
            self._refresh_integration_status()

    def _refresh_table(self, selected=None):
        checked_ids = set(self._selected_record_ids())
        self.table.blockSignals(True)
        rows = self.store.list(self._house_id) if self._house_id else []
        self.table.setRowCount(len(rows))
        selected_row = -1
        for row, record in enumerate(rows):
            document, remote = record["document"], record["remote"] or {}
            state = STATUSES.get(remote.get("status", "LOCAL"), "Неизвестно")
            if record["pending"]:
                state += " • результат не проверен"
            elif remote and record["dirty"]:
                state += " • локальные правки"
            if period_warning(document):
                state += " • проверьте даты"
            values = (f"{display_date(document.get('start'))} — {display_date(document.get('end'))}",
                      document.get("amount", ""), (protocol_label(document.get("protocol")) if document.get("protocol") else
                      "Скан / другое основание" if remote.get("hasScanBasis") else "Протокол не выбран"),
                      (document.get("contract") or {}).get("number", "Не выбран"), state)
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, record["id"])
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if record["id"] in checked_ids else Qt.CheckState.Unchecked)
            check.setToolTip("Отметить период для массовой операции")
            self.table.setItem(row, 0, check)
            for column, text in enumerate(values, 1):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text + ("\n" + period_warning(document) if period_warning(document) else ""))
                cell.setData(Qt.ItemDataRole.UserRole, record["id"])
                self.table.setItem(row, column, cell)
            if record["id"] == selected:
                selected_row = row
        self.table.setCurrentCell(selected_row if selected_row >= 0 else (0 if rows else -1), 0)
        self.table.blockSignals(False)
        self.empty.setVisible(not rows)
        item = self.table.currentItem()
        self._record_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._show_record()

    def _selection_changed(self, row, _column, old_row, _old_column):
        if not self.flush_pending_changes():
            self.table.blockSignals(True)
            self.table.setCurrentCell(old_row, 0)
            self.table.blockSignals(False)
            return
        item = self.table.item(row, 0)
        self._record_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._show_record()

    def _show_record(self):
        self._loading = True
        record = self._record()
        self.editor.setVisible(bool(record))
        if record:
            document = record["document"]
            context = self.store.context(self._house_id)
            self.contract_combo.clear()
            self.contract_combo.addItem("Выберите договор", None)
            contracts = list(context["contracts"])
            saved = document.get("contract")
            if saved and not any((item["versionGuid"], item["objectRootGuid"]) ==
                                 (saved["versionGuid"], saved["objectRootGuid"]) for item in contracts):
                contracts.append(saved)
            for item in contracts:
                self.contract_combo.addItem(f"№ {item['number']} от {display_date(item.get('date'))} • "
                                            f"{display_date(item.get('from'))} — {display_date(item.get('to'))}", item)
            index = next((i for i in range(1, self.contract_combo.count())
                          if saved and all(self.contract_combo.itemData(i)[key] == saved[key]
                                           for key in ("versionGuid", "objectRootGuid"))), 0)
            self.contract_combo.setCurrentIndex(index)
            for field, key in ((self.start_edit, "start"), (self.end_edit, "end")):
                field.setDate(QDate.fromString(document.get(key, ""), "yyyy-MM-dd"))
            self.amount_edit.setText(document.get("amount", ""))
            self._protocol = document.get("protocol")
            basis_label = protocol_label(self._protocol)
            if not self._protocol and (record["remote"] or {}).get("hasScanBasis"):
                basis_label = "Скан протокола в ГИС; для изменения выберите протокол из реестра"
            self.protocol_text.setText(basis_label)
        self._dirty = False
        self._loading = False
        self._refresh_integration_status()

    def _contract_changed(self):
        if self._loading:
            return
        self._protocol = None
        self.protocol_text.setText(protocol_label(None))
        self._edited()

    def _edited(self, *_):
        if not self._loading and self._record_id:
            self._dirty = True
            self.state_text.setText("Есть несохранённые локальные изменения.")
            self._refresh_integration_status()

    def _document(self):
        return {"start": self.start_edit.date().toString("yyyy-MM-dd"),
                "end": self.end_edit.date().toString("yyyy-MM-dd"),
                "amount": self.amount_edit.text().strip(),
                "contract": self.contract_combo.currentData(), "protocol": self._protocol}

    def flush_pending_changes(self):
        if not self._dirty or not self._record_id:
            return True
        try:
            self.store.save_local(self._house_id, self._document(), self._record_id)
        except (sqlite3.DatabaseError, ValueError) as exc:
            self._save_error = True
            QMessageBox.warning(self, "Проект не сохранён", f"{exc}\nИзменения остаются в форме. Повторите сохранение.")
            return False
        self._save_error = self._dirty = False
        self.state_text.setText("Локально: сохранено. Данные в ГИС не изменены.")
        return True

    def has_pending_save_error(self):
        return self._save_error

    def _new(self):
        if not self._house_id or not self.flush_pending_changes():
            return
        tariffs = self.repository.list_effective_house_tariffs(self._house_id)
        if not tariffs:
            QMessageBox.information(self, "Нет тарифа", "Сначала заполните тариф дома в разделе «Дома».")
            return
        labels = [f"{item.period_label} • {item.amount} руб./м²" for item in tariffs]
        choice, ok = QInputDialog.getItem(self, "Тариф для нового проекта", "Выберите тариф из раздела «Дома»", labels, 0, False)
        if not ok:
            return
        tariff = tariffs[labels.index(choice)]
        start = tariff.effective_from_date
        end = tariff.effective_to_date or date(start.year, 12, 31)
        context = self.store.context(self._house_id)
        contract = context["contracts"][0] if len(context["contracts"]) == 1 else None
        document = {"amount": tariff.amount, "start": start.isoformat(), "end": end.isoformat(),
                    "contract": contract, "protocol": None}
        try:
            record_id = self.store.save_local(self._house_id, document)
        except sqlite3.DatabaseError as exc:
            QMessageBox.warning(self, "Проект не создан", str(exc))
            return
        self._refresh_table(record_id)
        self.state_text.setText("Тариф скопирован из «Домов». Проверьте окончание периода и выберите основание. Изменения здесь не меняют тариф дома.")

    def _delete(self):
        record = self._record()
        if not record or not self.delete_button.isEnabled():
            return
        annulled = (record["remote"] or {}).get("status") == "TERMINATED"
        title = "Убрать аннулированную запись из списка?" if annulled else "Удалить локальный проект?"
        message = ("Запись будет удалена только из программы. В ГИС ничего не изменится. "
                   "При повторной загрузке из ГИС она появится снова." if annulled else
                   "Проект будет удалён только из программы.")
        if QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.store.delete_local(record["id"])
            except (sqlite3.DatabaseError, ValueError) as exc:
                QMessageBox.warning(self, "Запись не удалена", str(exc))
                return
            self._dirty = False
            self._refresh_table()

    def _choose_protocol(self):
        protocols = self.store.context(self._house_id)["protocols"]
        if not protocols:
            QMessageBox.information(self, "Нет доступных протоколов", "Нажмите «Обновить протоколы». Если список пуст, разместите подходящий протокол ОСС в ГИС.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Основание из ГИС ЖКХ")
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        caption = QLabel("Выберите протокол по номеру и дате. Одного совпадения номера недостаточно.")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        table = QTableWidget(len(protocols), 2)
        table.setHorizontalHeaderLabels(("Номер протокола", "Дата протокола"))
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i, item in enumerate(protocols):
            table.setItem(i, 0, QTableWidgetItem(item["protocolCode"]))
            table.setItem(i, 1, QTableWidgetItem(display_date(item["protocolDate"])))
        table.setCurrentCell(-1, -1)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Выбрать протокол")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        table.itemSelectionChanged.connect(lambda: buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(table.currentRow() >= 0))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and table.currentRow() >= 0:
            self._protocol = protocols[table.currentRow()]
            self.protocol_text.setText(protocol_label(self._protocol))
            self._edited()

    def _payload(self):
        house = self.repository.get_house(self._house_id)
        return {"fiasHouseGuid": house.fias_house_guid, "contract": self.contract_combo.currentData()}

    def _load(self):
        if not self.flush_pending_changes():
            return
        self._run("load_management_fees", {"fiasHouseGuid": self.repository.get_house(self._house_id).fias_house_guid}, self._loaded)

    def _loaded(self, result):
        selected = self._record_id
        preserved = self.store.import_remote(self._house_id, result)
        self._refresh_table(selected)
        pending = any(row["pending"] for row in self.store.list(self._house_id))
        message = f"Загружено договоров: {len(result['contracts'])}, сведений о плате: {len(result['records'])}, протоколов: {len(result['protocols'])}."
        if preserved:
            message += f" Локальные правки аннулированных сведений сохранены отдельными локальными проектами: {preserved}."
        if pending:
            message += " Результат прежней отправки ещё не установлен. Проверьте сведения в личном кабинете; повторная отправка заблокирована."
        errors = result.get("contractErrors") or []
        if errors:
            message += " Частичная загрузка: " + "; ".join(
                f"договор № {item.get('number', 'без номера')}: {item.get('message', 'не прочитан')}" for item in errors)
        self.operation_status.outcome(message, tone="warning" if pending or errors else "success")

    def _load_protocols(self):
        if not self.flush_pending_changes():
            return
        def loaded(result):
            context = self.store.context(self._house_id)
            context["protocols"] = result["protocols"]
            self.store.save_context(self._house_id, context)
            self.operation_status.outcome(f"Доступных протоколов: {len(result['protocols'])}. Выберите основание по номеру и дате.", tone="success")
        self._run("load_fee_protocols", self._payload(), loaded)

    def _send(self, command):
        if self._integration_busy or self._queue_running or not self.flush_pending_changes():
            return
        record = self._record()
        if not record or record["pending"]:
            return
        payload = self._payload()
        remote = record["remote"] or {}
        if command == "approve_management_fee" and (not remote or record["dirty"]):
            command = "save_and_approve_management_fee"
        payload.update(remoteGuid=record["remote_guid"], lastUpdate=remote.get("lastUpdate"))
        try:
            document = (record["document"] if command == "annul_management_fee"
                        else validate_document(record["document"]))
        except ValueError as exc:
            QMessageBox.information(self, "Проверьте проект", str(exc))
            return
        payload["document"] = document
        titles = {"save_management_fee_project": "Сохранить проект в ГИС?",
                  "save_and_approve_management_fee": "Сохранить и разместить сведения о плате в ГИС?",
                  "approve_management_fee": "Разместить сведения о плате в ГИС?",
                  "annul_management_fee": "Аннулировать сведения о плате в ГИС?"}
        if command == "annul_management_fee":
            reason, ok = QInputDialog.getMultiLineText(self, titles[command], "Причина аннулирования сведений о плате (договор управления не аннулируется)")
            if not ok or not reason.strip():
                return
            payload["reason"] = reason.strip()
        text = (f"Дом: {self.house_combo.currentText()}\nДоговор: {self.contract_combo.currentText()}\n"
                f"Период: {display_date(document['start'])} — {display_date(document['end'])}\n"
                f"Размер платы: {document['amount']} руб./м² в месяц\nОснование: {protocol_label(document.get('protocol'))}")
        if payload.get("reason"):
            text += "\nПричина: " + payload["reason"]
        if command == "save_and_approve_management_fee":
            text += "\n\nПрограмма сохранит изменения в проекте, затем разместит его. Если второй этап не завершится, сохранённый проект останется в ГИС."
        elif command == "save_management_fee_project" and remote.get("status") == "APPROVED":
            text += "\n\nГИС сохранит изменения как проект новой версии. Для размещения потребуется отдельное действие."
        if remote and (remote.get("hasScanBasis") or len(remote.get("ownerVotingList") or []) != 1) and command != "annul_management_fee":
            text += "\n\nПрежнее основание будет заменено выбранным протоколом из ГИС."
        if QMessageBox.question(self, titles[command], text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        record_id = record["id"]
        # Persist uncertainty before dispatch; an HTTP timeout is not proof of rollback.
        try:
            self.store.mark_pending(record_id)
        except sqlite3.DatabaseError as exc:
            QMessageBox.warning(self, "Обмен не начат", f"Не удалось сохранить состояние проекта: {exc}")
            return
        def applied(result):
            self.store.apply_remote(record_id, result["record"], result["contract"])
            self._refresh_table(record_id)
            state = STATUSES[result["record"]["status"]]
            message = f"ГИС подтвердила: {state.lower()}."
            if command == "save_management_fee_project" and result["record"]["status"] == "APPROVED":
                message = "Изменений нет: данные совпадают с размещёнными сведениями."
            self.operation_status.outcome(message, tone="success")
        self._run(command, payload, applied)

    def _begin_edit(self):
        if not self.flush_pending_changes():
            return
        try:
            self.store.begin_published_edit(self._record_id)
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось начать изменение", str(exc))
            return
        self._show_record()
        self.amount_edit.setFocus()

    def _cancel_edit(self):
        if QMessageBox.question(self, "Отменить локальные правки?",
                                "В форме будут восстановлены данные последней загрузки. Сведения в ГИС не изменятся.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.discard_local_edits(self._record_id)
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Правки не отменены", str(exc))
            return
        self._dirty = False
        self._refresh_table(self._record_id)

    def _run(self, command, payload, callback):
        if self._integration_busy or self._queue_running:
            return
        try:
            worker = GisIntegrationWorker(self.integration_gateway, command, payload, timeout=180, parent=self)
        except (ValueError, IntegrationError) as exc:
            QMessageBox.warning(self, "Обмен не начат", str(exc))
            return
        self._worker = worker
        self._integration_busy = True
        connect_result(worker, worker.succeeded, callback)
        connect_result(worker, worker.failed, self._failed)
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.deleteLater)
        self._refresh_integration_status()
        worker.start()

    def _failed(self, message):
        pending = bool(self._record() and self._record()["pending"])
        if pending:
            message += "\nИзменения в форме сохранены. Загрузите сведения из ГИС и проверьте результат перед повторной отправкой."
        self.operation_status.outcome(message, tone="danger")
        QMessageBox.warning(self, "Обмен с ГИС не завершён", message)

    def _finished(self):
        self._integration_busy = False
        self._worker = None
        self._refresh_integration_status()

    def _refresh_workflow(self):
        self._refresh_integration_status()

    def _refresh_integration_status(self):
        busy = self._integration_busy or self._workflow_busy or self._queue_running
        record = self._record()
        remote = record["remote"] if record else None
        pending = bool(record and record["pending"])
        supported = not remote or can_edit_remote(remote)
        changed = bool(record and (record["dirty"] or self._dirty))
        editable = bool(record and not pending and supported and
                        (not remote or remote["status"] == "DRAFT" or
                         (remote["status"] == "APPROVED" and record["dirty"])))
        status = self.integration_gateway.status() if self.integration_gateway else None
        house = self.repository.get_house(self._house_id) if self._house_id else None
        connected = bool(status and status.gis_page_ready and helper_supports_fees(status.helper_version) and house and house.fias_house_guid)
        has_contract = bool(self.contract_combo.currentData())
        self.edit_button.setVisible(bool(remote and remote["status"] == "APPROVED" and not changed))
        self.edit_button.setEnabled(supported and not pending and not busy)
        self.cancel_edit_button.setVisible(bool(remote and changed and remote["status"] != "TERMINATED"))
        self.cancel_edit_button.setEnabled(not pending and not busy)
        for field in (self.organization_combo, self.house_combo, self.table):
            field.setEnabled(not busy)
        for field in (self.contract_combo, self.start_edit, self.end_edit, self.amount_edit):
            field.setEnabled(editable and not busy)
        # A server project belongs to its original contract; editing cannot move it.
        self.contract_combo.setEnabled(editable and not remote and not busy)
        self.new_button.setEnabled(bool(house) and not busy)
        self.load_button.setEnabled(connected and not busy)
        annulled = bool(remote and remote.get("status") == "TERMINATED")
        self.delete_button.setText("Убрать из списка…" if annulled else "Удалить локальный проект…")
        self.delete_button.setEnabled(bool(record and (not record["remote_guid"] or annulled)
                                           and not pending and not (annulled and changed)) and not busy)
        self.local_button.setEnabled(editable and not busy)
        self.protocol_button.setEnabled(editable and has_contract and not busy)
        self.refresh_protocols_button.setEnabled(editable and has_contract and connected and not busy)
        self.save_button.setEnabled(editable and has_contract and connected and not busy)
        save_first = not remote or changed
        self.approve_button.setText("Сохранить и разместить…" if save_first else "Разместить…")
        may_place = (editable or bool(remote and remote["status"] == "DRAFT")) and supported and not pending
        self.approve_button.setEnabled(bool(may_place and self._protocol and has_contract) and connected and not busy)
        self.annul_button.setEnabled(bool(remote and remote["status"] == "APPROVED" and
                                          not changed and not pending) and connected and not busy)
        if record and not self._dirty:
            text = STATUSES.get((remote or {}).get("status", "LOCAL"), "Неизвестное состояние")
            if pending:
                text += ". Результат отправки не установлен. Загрузите сведения из ГИС."
            elif remote and record["dirty"]:
                text += ". Есть локальные правки. Сохраните проект в ГИС или сохраните и разместите изменения."
            self.state_text.setText(text)
        if not house:
            reason = "Выберите УО и дом."
        elif not house.fias_house_guid:
            reason = "Для обмена нужен код ФИАС дома. Загрузите сведения о доме из ГИС."
        elif not status or not status.gis_page_ready:
            reason = "Для обмена откройте личный кабинет нужной УО с включённым помощником. Локальный проект можно подготовить сейчас."
        elif not helper_supports_fees(status.helper_version):
            reason = "Для этого раздела обновите браузерный помощник до версии 0.1.34 или новее."
        elif remote and remote["status"] == "TERMINATED":
            reason = "Аннулированные сведения доступны для просмотра. Их локальную копию можно убрать из списка; повторная загрузка из ГИС вернёт её."
        elif not supported:
            reason = "Сведения с дополнительной детализацией или из внешней системы изменяются в ГИС. Размещённую запись можно аннулировать здесь."
        elif remote and remote["status"] == "APPROVED":
            reason = "«Изменить…» открывает локальные правки. Сохраните их как проект новой версии или сохраните и разместите. Тариф в «Домах» не изменится."
        else:
            reason = "Размер платы копируется из «Домов». Выберите протокол из ГИС, затем сохраните проект в ГИС или сохраните и разместите его."
        warning = period_warning(record["document"]) if record else ""
        self.reason.setText(reason + ("\n" + warning if warning else ""))
        bulk_connected = bool(status and status.gis_page_ready and helper_supports_fees(status.helper_version))
        self.bulk_generate_button.setEnabled(bool(house) and not busy)
        checked = [self.store.get(record_id) for record_id in self._selected_record_ids()]
        checked = [row for row in checked if row]
        self.bulk_amend_button.setEnabled(any(not row["pending"] and (not row["remote"] or can_edit_remote(row["remote"]))
                                              for row in checked) and not busy)
        self.select_all_button.setEnabled(bool(record) and not busy)
        self.clear_selection_button.setEnabled(bool(checked) and not busy)
        self.selection_count.setText(f"Отмечено периодов: {len(checked)}")
        for button, action in ((self.bulk_save_button, "save"), (self.bulk_approve_button, "approve"),
                               (self.bulk_save_approve_button, "save_approve")):
            candidates = [dict(row, document=self._document(), dirty=1) if row["id"] == self._record_id and self._dirty
                          else row for row in checked]
            button.setEnabled(bool(house) and bulk_connected and not busy and any(
                not transfer_reason(self.repository, row, self.organization_combo.currentData(), action)
                for row in candidates))
        self._update_queue_ui()

    def _can_start_bulk(self, *, require_connection=False):
        window = self.window()
        pages = getattr(window, "_data_pages", lambda: (self,))()
        if (getattr(window, "_closing", False) or getattr(window, "_close_requested", False)
                or self._queue_running or self._integration_busy or self._workflow_busy
                or any(getattr(window, "_page_is_busy", lambda _page: False)(page) for page in pages)
                or getattr(window, "_active_workers", None)):
            QMessageBox.information(self, "Выполняется операция", "Дождитесь завершения текущей операции.")
            return False
        if not self.organization_combo.currentData() or not self._house_id:
            return False
        if require_connection:
            status = self.integration_gateway.status() if self.integration_gateway else None
            if not status or not status.gis_page_ready or not helper_supports_fees(status.helper_version):
                QMessageBox.information(self, "Подключите ГИС ЖКХ", "Откройте кабинет нужной УО и подключите помощник версии 0.1.34 или новее.")
                return False
        return all(page.flush_pending_changes() is not False for page in pages)

    def _generate_bulk(self):
        if not self._can_start_bulk():
            return
        if not self.repository.list_effective_house_tariffs(self._house_id):
            QMessageBox.information(self, "Нет тарифов", "Сначала заполните тарифы домов в разделе «Дома».")
            return
        dialog = FeeGenerationDialog(self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        created = dialog.created
        dialog.deleteLater()
        if accepted:
            selected = next((record_id for house_id, record_id in created if house_id == self._house_id), self._record_id)
            self._refresh_table(selected)
            created_ids = {record_id for _house_id, record_id in created}
            self._check_records(created_ids)
            self.operation_status.outcome(
                f"Создано локальных проектов: {len(created)}. Проверьте договоры и основания, затем сохраните проекты в ГИС отдельной очередью.", tone="success")

    def _start_bulk(self, action):
        from gis_jkh_assistant.fee_bulk import prepare_jobs
        if not self._can_start_bulk(require_connection=True):
            return
        organization_id = self.organization_combo.currentData()
        house_id = self._house_id
        selected_ids = set(self._selected_record_ids())
        if not selected_ids:
            QMessageBox.information(self, "Выберите периоды", "Отметьте нужные периоды галочками в таблице.")
            return
        jobs = [job for job in prepare_jobs(self.repository, self.store, organization_id, [house_id], action)
                if job.record_id in selected_ids]
        if not jobs:
            QMessageBox.information(self, "Нет записей", "Сначала сформируйте проекты по тарифам или загрузите сведения из ГИС.")
            return
        dialog = FeeQueueDialog(self, jobs)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        selected = dialog.selected_jobs()
        dialog.deleteLater()
        if (accepted and self._can_start_bulk(require_connection=True)
                and self.organization_combo.currentData() == organization_id and self._house_id == house_id):
            self.fee_queue.start(selected)

    def _resume_bulk(self):
        if (self._can_start_bulk(require_connection=True) and self.fee_queue.jobs
                and self.fee_queue.jobs[0].organization_id == self.organization_combo.currentData()
                and all(job.house_id == self._house_id for job in self.fee_queue.jobs)):
            self.fee_queue.resume()

    def _show_queue_results(self):
        dialog = FeeQueueDialog(self, self.fee_queue.jobs, preview=False)
        dialog.exec()
        dialog.deleteLater()

    def _update_queue_ui(self):
        queue = self.fee_queue
        self.queue_group.setVisible(bool(queue.jobs))
        queue_title = ("Очередь импорта из ГИС ЖКХ" if queue.jobs and queue.jobs[0].action == "load"
                       else "Очередь экспорта в ГИС ЖКХ")
        self.queue_group.caption.setText(queue_title)
        self.queue_group.setAccessibleName(queue_title)
        self.queue_summary.setVisible(bool(queue.jobs))
        current = next((job for job in queue.jobs if job.state == "running"), None)
        organization = self.repository.get_organization(queue.jobs[0].organization_id) if queue.jobs else None
        house = self.repository.get_house(queue.jobs[0].house_id) if queue.jobs else None
        scope = f"Дом: {house.address}. " if house else (f"УО: {organization.name}. " if organization else "")
        self.queue_summary.setText(scope + queue.summary() + (f"\nСейчас: {current.address}" if current else ""))
        self.queue_stop_button.setVisible(queue.running)
        self.queue_stop_button.setEnabled(not queue.stop_requested)
        self.queue_continue_button.setVisible(not queue.running and any(job.state == "stopped" for job in queue.jobs))
        self.queue_continue_button.setEnabled(bool(queue.jobs and queue.jobs[0].organization_id == self.organization_combo.currentData()
                                                   and all(job.house_id == self._house_id for job in queue.jobs)))
        self.queue_results_button.setVisible(bool(queue.jobs) and not queue.running)

    def _amend_bulk(self):
        if not self._can_start_bulk():
            return
        selected = self._selected_record_ids()
        if not selected:
            return
        dialog = FeeAmendmentDialog(self, selected)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        updated = dialog.updated
        dialog.deleteLater()
        if accepted:
            self._refresh_table(updated[0] if updated else self._record_id)
            self._check_records(updated)
            self.operation_status.outcome(
                f"Подготовлено изменений: {len(updated)}. Проверьте основания, затем сохраните выбранные проекты в ГИС.", tone="success")

    def _check_records(self, record_ids):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole) in record_ids:
                item.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self._refresh_integration_status()
