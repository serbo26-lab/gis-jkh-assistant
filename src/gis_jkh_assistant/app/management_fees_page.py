"""Management fee projects based on house tariffs and existing GIS protocols."""
from __future__ import annotations

import sqlite3
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gis_jkh_assistant.management_fees import (
    ManagementFeeStore, STATUSES, helper_supports_fees, validate_document,
)
from gis_jkh_assistant.gis_integration import IntegrationError
from .action_layout import ActionFlowLayout
from .gis_integration_worker import GisIntegrationWorker, connect_result
from .icons import set_button_icon
from .workflow_widgets import OperationStatus, SectionCard, style_danger_button


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
        self.organization_combo, self.house_combo = QComboBox(), QComboBox()
        for label, combo in (("УО", self.organization_combo), ("Дом", self.house_combo)):
            context.addWidget(QLabel(label))
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            context.addWidget(combo, 1)
        body.addLayout(context)
        actions = ActionFlowLayout()
        self.load_button = QPushButton("Загрузить сведения из ГИС")
        self.new_button = QPushButton("Новый проект из тарифа…")
        self.delete_button = QPushButton("Удалить локальный проект…")
        for button, name in ((self.load_button, "download"), (self.new_button, "add"), (self.delete_button, "danger")):
            set_button_icon(button, name)
            actions.addWidget(button)
        body.addLayout(actions)
        self.operation_status = OperationStatus()
        self.operation_status.hide()
        body.addWidget(self.operation_status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Период платы", "Руб./м² в месяц", "Основание", "Договор", "Состояние"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(145)
        body.addWidget(self.table, 1)
        self.empty = QLabel("Загрузите сведения из ГИС или создайте проект из тарифа, заполненного в разделе «Дома».")
        self.empty.setWordWrap(True)
        body.addWidget(self.empty)
        self.editor = SectionCard("Выбранные сведения о плате")
        form = QFormLayout()
        self.contract_combo = QComboBox()
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
        for item in (self.protocol_text, self.protocol_button, self.refresh_protocols_button):
            basis.addWidget(item)
        form.addRow("Основание", basis)
        self.editor.body.addLayout(form)
        self.state_text = QLabel()
        self.state_text.setWordWrap(True)
        self.editor.body.addWidget(self.state_text)
        commands = ActionFlowLayout()
        self.local_button = QPushButton("Сохранить локально")
        self.save_button = QPushButton("Сохранить проект в ГИС")
        self.approve_button = QPushButton("Разместить…")
        self.annul_button = QPushButton("Аннулировать сведения…")
        self.approve_button.setObjectName("PrimaryButton")
        style_danger_button(self.annul_button)
        for button, name in ((self.local_button, "check"), (self.save_button, "upload"),
                             (self.approve_button, "upload"), (self.annul_button, "danger")):
            set_button_icon(button, name)
            commands.addWidget(button)
        self.editor.body.addLayout(commands)
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
        self.contract_combo.currentIndexChanged.connect(self._contract_changed)
        self.start_edit.dateChanged.connect(self._edited)
        self.end_edit.dateChanged.connect(self._edited)
        self.amount_edit.textChanged.connect(self._edited)
        self.load_button.clicked.connect(self._load)
        self.new_button.clicked.connect(self._new)
        self.delete_button.clicked.connect(self._delete)
        self.local_button.clicked.connect(self.flush_pending_changes)
        self.protocol_button.clicked.connect(self._choose_protocol)
        self.refresh_protocols_button.clicked.connect(self._load_protocols)
        self.save_button.clicked.connect(lambda: self._send("save_management_fee_project"))
        self.approve_button.clicked.connect(lambda: self._send("approve_management_fee"))
        self.annul_button.clicked.connect(lambda: self._send("annul_management_fee"))
        self.refresh_organizations()

    def refresh_organizations(self):
        if self._integration_busy or not self.flush_pending_changes():
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

    def _refresh_table(self, selected=None):
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
            values = (f"{display_date(document.get('start'))} — {display_date(document.get('end'))}",
                      document.get("amount", ""), protocol_label(document.get("protocol")),
                      (document.get("contract") or {}).get("number", "Не выбран"), state)
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
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
            self.protocol_text.setText(protocol_label(self._protocol))
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
        tariffs = self.repository.list_house_tariffs(self._house_id)
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
        if record and QMessageBox.question(self, "Удалить локальный проект?", "Проект будет удалён только из программы.") == QMessageBox.StandardButton.Yes:
            try:
                self.store.delete_local(record["id"])
            except (sqlite3.DatabaseError, ValueError) as exc:
                QMessageBox.warning(self, "Проект не удалён", str(exc))
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
        self.store.import_remote(self._house_id, result)
        self._refresh_table(selected)
        pending = any(row["pending"] for row in self.store.list(self._house_id))
        message = f"Загружено договоров: {len(result['contracts'])}, сведений о плате: {len(result['records'])}, протоколов: {len(result['protocols'])}."
        if pending:
            message += " Результат прежней отправки ещё не установлен. Проверьте сведения в личном кабинете; повторная отправка заблокирована."
        self.operation_status.outcome(message, tone="warning" if pending else "success")

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
        if not self.flush_pending_changes():
            return
        record = self._record()
        if not record or record["pending"]:
            return
        payload = self._payload()
        remote = record["remote"] or {}
        payload.update(remoteGuid=record["remote_guid"], lastUpdate=remote.get("lastUpdate"))
        try:
            document = validate_document(record["document"])
        except ValueError as exc:
            QMessageBox.information(self, "Проверьте проект", str(exc))
            return
        payload["document"] = document
        titles = {"save_management_fee_project": "Сохранить проект в ГИС?",
                  "approve_management_fee": "Разместить сведения о плате в ГИС?",
                  "annul_management_fee": "Аннулировать сведения о плате в ГИС?"}
        if command == "annul_management_fee":
            reason, ok = QInputDialog.getMultiLineText(self, titles[command], "Причина аннулирования сведений о плате (договор управления не аннулируется)")
            if not ok or not reason.strip():
                return
            payload["reason"] = reason.strip()
        text = (f"Дом: {self.house_combo.currentText()}\nДоговор: {self.contract_combo.currentText()}\n"
                f"Период: {display_date(document['start'])} — {display_date(document['end'])}\n"
                f"Размер платы: {document['amount']} руб./м² в месяц\nОснование: {protocol_label(document['protocol'])}")
        if payload.get("reason"):
            text += "\nПричина: " + payload["reason"]
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
            self.operation_status.outcome(f"ГИС подтвердила: {state.lower()}.", tone="success")
        self._run(command, payload, applied)

    def _run(self, command, payload, callback):
        if self._integration_busy:
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
        busy = self._integration_busy or self._workflow_busy
        record = self._record()
        remote = record["remote"] if record else None
        pending = bool(record and record["pending"])
        supported = not remote or (not remote.get("hasOtherDetails") and remote.get("type") == "BY_PROTOCOL" and remote.get("protocolFromVoting")
                                   and len(remote.get("ownerVotingList") or []) == 1)
        editable = bool(record and not pending and (not remote or remote["status"] == "DRAFT") and supported)
        status = self.integration_gateway.status() if self.integration_gateway else None
        house = self.repository.get_house(self._house_id) if self._house_id else None
        connected = bool(status and status.gis_page_ready and helper_supports_fees(status.helper_version) and house and house.fias_house_guid)
        has_contract = bool(self.contract_combo.currentData())
        for field in (self.organization_combo, self.house_combo, self.table):
            field.setEnabled(not busy)
        for field in (self.contract_combo, self.start_edit, self.end_edit, self.amount_edit):
            field.setEnabled(editable and not busy)
        # A server project belongs to its original contract; editing cannot move it.
        self.contract_combo.setEnabled(editable and not remote and not busy)
        self.new_button.setEnabled(bool(house) and not busy)
        self.load_button.setEnabled(connected and not busy)
        self.delete_button.setEnabled(bool(record and not record["remote_guid"] and not pending) and not busy)
        self.local_button.setEnabled(editable and not busy)
        self.protocol_button.setEnabled(editable and has_contract and not busy)
        self.refresh_protocols_button.setEnabled(editable and has_contract and connected and not busy)
        self.save_button.setEnabled(editable and has_contract and connected and not busy)
        self.approve_button.setEnabled(bool(remote and remote["status"] == "DRAFT" and supported and
                                           not record["dirty"] and not self._dirty and not pending) and connected and not busy)
        self.annul_button.setEnabled(bool(remote and remote["status"] == "APPROVED" and supported and not pending) and connected and not busy)
        if record and not self._dirty:
            text = STATUSES.get((remote or {}).get("status", "LOCAL"), "Неизвестное состояние")
            if pending:
                text += ". Результат отправки не установлен. Загрузите сведения из ГИС."
            elif remote and record["dirty"]:
                text += ". Есть локальные правки; перед размещением сохраните проект в ГИС."
            self.state_text.setText(text)
        if not house:
            reason = "Выберите УО и дом."
        elif not house.fias_house_guid:
            reason = "Для обмена нужен код ФИАС дома. Загрузите сведения о доме из ГИС."
        elif not status or not status.gis_page_ready:
            reason = "Для обмена откройте личный кабинет нужной УО с включённым помощником. Локальный проект можно подготовить сейчас."
        elif not helper_supports_fees(status.helper_version):
            reason = "Для этого раздела обновите браузерный помощник до версии 0.1.33 или новее."
        elif not supported:
            reason = "Сведения с другим основанием, несколькими протоколами или дополнительной детализацией доступны для просмотра. Изменяйте их в ГИС."
        else:
            reason = "Размер платы копируется из «Домов». Основание выбирается из протоколов ГИС. Проект и размещение — отдельные операции."
        self.reason.setText(reason)
