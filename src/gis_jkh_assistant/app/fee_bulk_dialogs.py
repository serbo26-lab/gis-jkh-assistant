"""Review house scope, tariff periods and protocol before generating fee projects."""
from datetime import date
import sqlite3

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gis_jkh_assistant.fee_bulk import available_protocols, create_projects, prepare_candidates, prepare_jobs
from .fee_bulk_queue import LABELS
from .icons import set_button_icon


def _display_date(value):
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "—"


def _protocol_label(protocol):
    return f"№ {protocol.get('protocolCode', '')} от {_display_date(protocol.get('protocolDate'))}" if protocol else "Не выбран"


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(32)
    table.setWordWrap(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table, rows, checked):
    table.blockSignals(True)
    table.setRowCount(len(rows))
    for row, (values, enabled) in enumerate(rows):
        for column, value in enumerate(("", *values)):
            cell = QTableWidgetItem(str(value))
            cell.setToolTip(str(value))
            if column == 0:
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                              (Qt.ItemFlag.ItemIsUserCheckable if enabled else Qt.ItemFlag.NoItemFlags))
                cell.setCheckState(Qt.CheckState.Checked if enabled and checked else Qt.CheckState.Unchecked)
            table.setItem(row, column, cell)
    table.setColumnWidth(0, 55)
    table.blockSignals(False)


def selected_rows(table):
    return [row for row in range(table.rowCount())
            if table.item(row, 0).checkState() == Qt.CheckState.Checked]


def _select_all(table, checked):
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)


class FeeQueueDialog(QDialog):
    def __init__(self, page, jobs, *, preview=True):
        super().__init__(page)
        self.jobs = jobs
        self.setWindowTitle("Проверка очереди сведений о плате" if preview else "Результаты очереди сведений о плате")
        self.resize(1120, 600)
        layout = QVBoxLayout(self)
        organization = page.repository.get_organization(jobs[0].organization_id) if jobs else None
        action = jobs[0].action if jobs else "load"
        descriptions = {
            "load": "Последовательно загрузить договоры, протоколы и сведения. Данные в ГИС не изменяются.",
            "save": "Сохранить выбранные локальные проекты и правки в проекты ГИС. Размещение не выполняется.",
            "approve": "Разместить выбранные проекты, ранее сохранённые в ГИС. Локальные правки сначала нужно сохранить в ГИС.",
        }
        caption = QLabel(f"УО: {organization.name if organization else 'Не выбрана'}\n{descriptions[action]}")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.table = _table(("Выбор", "Дом", "Период", "Руб./м² в мес.", "Основание", "Результат / проверка"))
        rows = []
        for job in jobs:
            document = job.document
            period = f"{_display_date(document.get('start'))} — {_display_date(document.get('end'))}" if document else "Все сведения дома"
            rows.append(((job.address, period, document.get("amount", ""),
                          _protocol_label(document.get("protocol")) if document else "",
                          job.detail or LABELS[job.state]), preview and job.state == "ready"))
        _fill(self.table, rows, preview)
        for column, width in enumerate((55, 255, 180, 110, 165)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        for text, checked in (("Выбрать доступные", True), ("Снять выбор", False)) if preview else ():
            button = QPushButton(text)
            set_button_icon(button, "check-all")
            button.clicked.connect(lambda _=False, value=checked: _select_all(self.table, value))
            controls.addWidget(button)
        self.counter = QLabel()
        controls.addWidget(self.counter, 1)
        layout.addLayout(controls)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel if preview
            else QDialogButtonBox.StandardButton.Close)
        if preview:
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText({
                "load": "Загрузить сведения", "save": "Сохранить проекты в ГИС",
                "approve": "Разместить проекты в ГИС"}[action])
            if action != "load":
                self.buttons.button(QDialogButtonBox.StandardButton.Ok).setDefault(False)
                self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
            self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.table.itemChanged.connect(self._counter)
        self._counter()

    def _counter(self, *_):
        count = len(selected_rows(self.table))
        self.counter.setText(f"Выбрано: {count}; всего записей: {len(self.jobs)}")
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button:
            button.setEnabled(count > 0)

    def selected_jobs(self):
        return [self.jobs[row] for row in selected_rows(self.table)]


class FeeGenerationDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.repository, self.store = page, page.repository, page.store
        self.organization_id = page.organization_combo.currentData()
        self.houses = self.repository.list_houses(self.organization_id)
        self.candidates, self.created = [], []
        self._loading_context = self._close_after_load = False
        self.setWindowTitle("Сформировать проекты платы по тарифам домов")
        self.resize(1120, 700)
        layout = QVBoxLayout(self)
        hint = QLabel("Выберите дома и период. Будет создан отдельный локальный проект на каждый период тарифа. "
                      "Существующие сведения не заменяются; в ГИС ничего не отправляется.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.houses_table = _table(("Выбор", "Дом", "Тарифов", "Договоры и протоколы"))
        self.houses_table.setMaximumHeight(155)
        self.houses_table.setMinimumHeight(110)
        rows = []
        starts = []
        for house in self.houses:
            tariffs = self.repository.list_effective_house_tariffs(house.id)
            starts.extend(tariff.effective_from_date for tariff in tariffs)
            context = self.store.context(house.id)
            rows.append(((house.address, len(tariffs), f"Договоров: {len(context['contracts'])}; протоколов: {len(context['protocols'])}"), bool(tariffs)))
        _fill(self.houses_table, rows, True)
        self.houses_table.setColumnWidth(1, 480)
        self.houses_table.setColumnWidth(2, 100)
        layout.addWidget(self.houses_table)
        load_row = QHBoxLayout()
        self.load_button = QPushButton("Загрузить договоры и протоколы выбранных домов из ГИС")
        set_button_icon(self.load_button, "download-cloud")
        self.stop_button = QPushButton("Остановить после текущего дома")
        self.stop_button.hide()
        load_row.addWidget(self.load_button)
        load_row.addWidget(self.stop_button)
        load_row.addStretch()
        layout.addLayout(load_row)
        self.progress = QLabel()
        self.progress.setWordWrap(True)
        layout.addWidget(self.progress)
        dates = QHBoxLayout()
        dates.addWidget(QLabel("Период формирования с"))
        self.start_edit, self.end_edit = QDateEdit(), QDateEdit()
        today = date.today()
        first = min(starts) if starts else date(today.year, 1, 1)
        last = date(max(today.year, first.year), 12, 31)
        for field, value in ((self.start_edit, first), (self.end_edit, last)):
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd.MM.yyyy")
            field.setDateRange(QDate(1900, 1, 1), QDate(9999, 12, 31))
            field.setDate(QDate(value.year, value.month, value.day))
        dates.addWidget(self.start_edit)
        dates.addWidget(QLabel("по"))
        dates.addWidget(self.end_edit)
        dates.addStretch()
        layout.addLayout(dates)
        protocol_row = QHBoxLayout()
        protocol_row.addWidget(QLabel("Протокол по умолчанию"))
        self.protocol_combo = QComboBox()
        self.protocol_combo.setMinimumContentsLength(30)
        self.protocol_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.protocol_combo.setPlaceholderText("Выберите общее основание или отложите выбор")
        protocol_row.addWidget(self.protocol_combo, 1)
        layout.addLayout(protocol_row)
        basis_hint = QLabel("Протокол подставляется только для относящихся к нему домов. Для остальных основание "
                            "можно выбрать отдельно в проекте. Без подходящего протокола отправка в ГИС запрещена.")
        basis_hint.setWordWrap(True)
        layout.addWidget(basis_hint)
        self.preview = _table(("Выбор", "Дом", "Период", "Руб./м² в мес.", "Договор", "Основание", "Проверка"))
        for column, width in enumerate((55, 235, 180, 110, 110, 145)):
            self.preview.setColumnWidth(column, width)
        layout.addWidget(self.preview, 1)
        self.counter = QLabel()
        layout.addWidget(self.counter)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Создать локальные проекты")
        self.buttons.accepted.connect(self._create)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.houses_table.itemChanged.connect(self._houses_changed)
        self.start_edit.dateChanged.connect(self._refresh_candidates)
        self.end_edit.dateChanged.connect(self._refresh_candidates)
        self.protocol_combo.currentIndexChanged.connect(self._refresh_candidates)
        self.preview.itemChanged.connect(self._counter)
        self.load_button.clicked.connect(self._load_context)
        self.stop_button.clicked.connect(page.fee_queue.request_stop)
        page.fee_queue.changed.connect(self._queue_progress)
        page.fee_queue.settled.connect(self._loaded_context)
        self._houses_changed()

    def house_ids(self):
        return [self.houses[row].id for row in selected_rows(self.houses_table)]

    def _houses_changed(self, *_):
        old = self.protocol_combo.currentData()
        chosen = self.protocol_combo.currentIndex() >= 0
        self.protocol_combo.blockSignals(True)
        self.protocol_combo.clear()
        self.protocol_combo.addItem("Выбрать основание для каждого проекта позже", None)
        try:
            selected_houses = set(self.house_ids())
            protocols = available_protocols(self.repository, self.store, self.organization_id, selected_houses)
            for protocol in protocols:
                addresses = [house.address for house in self.houses if house.id in selected_houses
                             and house.fias_house_guid in protocol["fiasHouseCodes"]]
                self.protocol_combo.addItem(f"{_protocol_label(protocol)} • {'; '.join(addresses)}", protocol)
        except ValueError as exc:
            self.progress.setText(str(exc))
        index = next((i for i in range(self.protocol_combo.count()) if self.protocol_combo.itemData(i) == old), -1)
        self.protocol_combo.setCurrentIndex(index if chosen else -1)
        self.protocol_combo.blockSignals(False)
        self._refresh_candidates()

    def _dates(self):
        return (date.fromisoformat(field.date().toString("yyyy-MM-dd")) for field in (self.start_edit, self.end_edit))

    def _refresh_candidates(self, *_):
        try:
            start, end = self._dates()
            self.candidates = prepare_candidates(self.repository, self.store, self.organization_id,
                                                  self.house_ids(), start, end, self.protocol_combo.currentData())
            rows = []
            for candidate in self.candidates:
                doc = candidate.document
                rows.append(((candidate.address, f"{_display_date(doc['start'])} — {_display_date(doc['end'])}",
                              doc["amount"], (doc["contract"] or {}).get("number", "Не выбран"),
                              _protocol_label(doc["protocol"]), candidate.detail), not candidate.blocked))
            _fill(self.preview, rows, True)
        except (ValueError, sqlite3.Error) as exc:
            self.candidates = []
            self.preview.setRowCount(0)
            self.progress.setText(str(exc))
        self._counter()

    def _counter(self, *_):
        count = len(selected_rows(self.preview))
        self.counter.setText(f"Выбрано проектов: {count}; недоступно для формирования: {sum(c.blocked for c in self.candidates)}.")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            count > 0 and self.protocol_combo.currentIndex() >= 0 and not self._loading_context)

    def _load_context(self):
        if not self.page._can_start_bulk(require_connection=True):
            return
        jobs = prepare_jobs(self.repository, self.store, self.organization_id, self.house_ids(), "load")
        if not any(job.state == "ready" for job in jobs):
            self.progress.setText("У выбранных домов нет кода ФИАС. Загрузите сведения о домах.")
            return
        self._loading_context = True
        for widget in (self.houses_table, self.start_edit, self.end_edit, self.protocol_combo, self.load_button):
            widget.setEnabled(False)
        self.stop_button.show()
        self._counter()
        self.page.fee_queue.start(jobs)

    def _queue_progress(self):
        if self._loading_context:
            self.progress.setText(self.page.fee_queue.summary())

    def _loaded_context(self):
        if not self._loading_context:
            return
        self._loading_context = False
        for widget in (self.houses_table, self.start_edit, self.end_edit, self.protocol_combo, self.load_button):
            widget.setEnabled(True)
        self.stop_button.hide()
        self._houses_changed()
        if self._close_after_load or getattr(self.page.window(), "_close_requested", False):
            super().reject()

    def _create(self):
        selected = [self.candidates[row] for row in selected_rows(self.preview)]
        if not selected or self.protocol_combo.currentIndex() < 0 or self._loading_context:
            return
        try:
            if self.page.organization_combo.currentData() != self.organization_id:
                raise ValueError("Выбранная УО изменилась. Закройте окно и начните формирование заново.")
            start, end = self._dates()
            self.created = create_projects(self.repository, self.store, self.organization_id, self.house_ids(),
                                           start, end, self.protocol_combo.currentData(), selected)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Проекты не созданы", str(exc))
            self._refresh_candidates()
            return
        self.accept()

    def reject(self):
        if self._loading_context:
            self._close_after_load = True
            self.page.fee_queue.request_stop()
            return
        super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self._loading_context:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
