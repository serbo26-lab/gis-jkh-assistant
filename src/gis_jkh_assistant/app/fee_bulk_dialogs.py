"""Review house scope, tariff periods and protocol before generating fee projects."""
from datetime import date
import sqlite3

from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gis_jkh_assistant.fee_bulk import (
    apply_amendments, available_protocols, create_projects, prepare_amendments, prepare_candidates, prepare_jobs,
)
from .fee_bulk_queue import LABELS
from .icons import set_button_icon
from .widgets import SafeComboBox


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
                if not enabled:
                    cell.setToolTip(str(values[-1]))
            table.setItem(row, column, cell)
    table.setColumnWidth(0, 55)
    table.blockSignals(False)


def selected_rows(table):
    return [row for row in range(table.rowCount())
            if table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
            and table.item(row, 0).checkState() == Qt.CheckState.Checked]


def _select_all(table, checked):
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)


def _selection_controls(table, layout):
    controls = QHBoxLayout()
    buttons = []
    for text, checked in (("Выбрать доступные", True), ("Снять выбор", False)):
        button = QPushButton(text)
        set_button_icon(button, "check-all")
        button.clicked.connect(lambda _=False, value=checked: _select_all(table, value))
        controls.addWidget(button)
        buttons.append(button)
    counter = QLabel()
    counter.setWordWrap(True)
    controls.addWidget(counter, 1)
    layout.addLayout(controls)
    return *buttons, counter


def _available_count(table):
    return sum(bool(table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
               for row in range(table.rowCount()))


def _blocked_reasons(table):
    reasons = dict.fromkeys(table.item(row, table.columnCount() - 1).text()
                           for row in range(table.rowCount())
                           if not table.item(row, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
    return "\n".join(reason for reason in reasons if reason)


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
            "save_approve": "Последовательно сохранить выбранные проекты и правки в ГИС, затем разместить каждый успешно сохранённый проект.",
        }
        if preview and action in {"save", "save_approve"} and any(
                (record := page.store.get(job.record_id)) and record["remote"] and
                (record["remote"].get("hasScanBasis") or len(record["remote"].get("ownerVotingList") or []) != 1)
                for job in jobs if job.record_id and job.state == "ready"):
            descriptions[action] += " В записях со сканом или несколькими основаниями прежнее основание будет заменено выбранным протоколом из ГИС."
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
        self.select_button = self.clear_button = None
        if preview:
            self.select_button, self.clear_button, self.counter = _selection_controls(self.table, layout)
        else:
            self.counter = QLabel()
            layout.addWidget(self.counter)
        self.selection_hint = QLabel()
        self.selection_hint.setWordWrap(True)
        if preview and not _available_count(self.table):
            empty_messages = {
                "load": "Нет домов, доступных для загрузки.",
                "save": "Нет проектов, доступных для сохранения.",
                "approve": "Нет проектов, готовых к размещению. Локальные проекты и правки сначала сохраните в ГИС или выберите «Сохранить и разместить».",
                "save_approve": "Нет проектов, доступных для сохранения и размещения.",
            }
            reasons = _blocked_reasons(self.table)
            self.selection_hint.setText(empty_messages[action] + ("\n" + reasons if reasons else ""))
        self.selection_hint.setVisible(bool(self.selection_hint.text()))
        layout.addWidget(self.selection_hint)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel if preview
            else QDialogButtonBox.StandardButton.Close)
        if preview:
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText({
                "load": "Загрузить сведения", "save": "Сохранить проекты в ГИС",
                "approve": "Разместить проекты в ГИС",
                "save_approve": "Сохранить и разместить проекты в ГИС"}[action])
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
        available = _available_count(self.table)
        self.counter.setText(f"Выбрано: {count}; доступно: {available}; всего записей: {len(self.jobs)}")
        if self.select_button is not None:
            self.select_button.setEnabled(available > 0)
            self.clear_button.setEnabled(count > 0)
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
        self.house_id = page._house_id
        house = self.repository.get_house(self.house_id)
        if not house or house.organization_id != self.organization_id:
            raise ValueError("Выберите дом текущей УО.")
        self.candidates, self.created = [], []
        self._loading_context = self._close_after_load = False
        self.setWindowTitle("Сформировать проекты платы по тарифам дома")
        self.resize(1120, 700)
        layout = QVBoxLayout(self)
        hint = QLabel(house.address + "\nВыберите период. Будет создан отдельный локальный проект на каждый период тарифа. "
                      "Существующие сведения не заменяются; в ГИС ничего не отправляется.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        starts = [tariff.effective_from_date for tariff in self.repository.list_effective_house_tariffs(house.id)]
        load_row = QHBoxLayout()
        self.load_button = QPushButton("Обновить сведения и протоколы дома из ГИС")
        set_button_icon(self.load_button, "download-cloud")
        self.stop_button = QPushButton("Остановить после текущего запроса")
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
        self.protocol_combo = SafeComboBox()
        self.protocol_combo.setMinimumContentsLength(30)
        self.protocol_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.protocol_combo.setPlaceholderText("Выберите общее основание или отложите выбор")
        protocol_row.addWidget(self.protocol_combo, 1)
        layout.addLayout(protocol_row)
        basis_hint = QLabel("Выбранный протокол подставится во все новые проекты этого дома. Его можно изменить в отдельном проекте. "
                            "Без подходящего протокола отправка в ГИС запрещена.")
        basis_hint.setWordWrap(True)
        layout.addWidget(basis_hint)
        self.preview = _table(("Выбор", "Период", "Руб./м² в мес.", "Договор", "Основание", "Проверка"))
        for column, width in enumerate((55, 200, 120, 140, 180)):
            self.preview.setColumnWidth(column, width)
        layout.addWidget(self.preview, 1)
        self.counter = QLabel()
        layout.addWidget(self.counter)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Создать локальные проекты")
        self.buttons.accepted.connect(self._create)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
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
        return [self.house_id]

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
                self.protocol_combo.addItem(_protocol_label(protocol), protocol)
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
                rows.append(((f"{_display_date(doc['start'])} — {_display_date(doc['end'])}",
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
            self.progress.setText("У дома нет кода ФИАС. Загрузите сведения о доме.")
            return
        self._loading_context = True
        for widget in (self.start_edit, self.end_edit, self.protocol_combo, self.load_button):
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
        for widget in (self.start_edit, self.end_edit, self.protocol_combo, self.load_button):
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
            if self.page.organization_combo.currentData() != self.organization_id or self.page._house_id != self.house_id:
                raise ValueError("УО или дом изменились. Закройте окно и начните формирование заново.")
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


class FeeAmendmentDialog(QDialog):
    """Review replacements on existing records; no GIS writes or new identities."""

    def __init__(self, page, record_ids):
        super().__init__(page)
        self.page, self.repository, self.store = page, page.repository, page.store
        self.organization_id, self.house_id = page.organization_combo.currentData(), page._house_id
        self.record_ids = list(record_ids)
        self.choices, self.updated, self.candidates = {}, [], []
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh)
        house = self.repository.get_house(self.house_id)
        self.tariffs = self.repository.list_house_tariffs(self.house_id)
        self.setWindowTitle("Обновить выбранные периоды по локальным тарифам")
        self.resize(1120, 620)
        layout = QVBoxLayout(self)
        hint = QLabel(house.address + "\nДаты и суммы берутся из локальных тарифов раздела «Дома». "
                      "Проверьте соответствие периодов. Для неоднозначных записей выберите тариф вручную. "
                      "Изменения сохранятся локально. Затем можно сохранить проекты в ГИС или сохранить и разместить их.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel("Основание для изменений"))
        self.protocol_combo = SafeComboBox()
        self.protocol_combo.addItem("Сохранить основания отдельных записей", None)
        for protocol in available_protocols(self.repository, self.store, self.organization_id, [self.house_id]):
            self.protocol_combo.addItem(_protocol_label(protocol), protocol)
        row.addWidget(self.protocol_combo, 1)
        layout.addLayout(row)
        note = QLabel("Если в записи вместо протокола был скан или другое основание, выберите протокол из ГИС здесь "
                      "или после сохранения в карточке записи. При отправке он заменит прежнее основание. "
                      "Одна запись ГИС изменяется по одному локальному периоду; длинные периоды автоматически не разбиваются.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.preview = _table(("Выбор", "Период в ГИС", "Плата в ГИС", "Локальный тариф", "Новый период", "Новая плата", "Проверка"))
        for column, width in enumerate((55, 175, 90, 300, 175, 90)):
            self.preview.setColumnWidth(column, width)
        layout.addWidget(self.preview, 1)
        self.select_button, self.clear_button, self.counter = _selection_controls(self.preview, layout)
        self.selection_hint = QLabel()
        self.selection_hint.setWordWrap(True)
        layout.addWidget(self.selection_hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Сохранить локальные изменения")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.protocol_combo.currentIndexChanged.connect(self._refresh)
        self.preview.itemChanged.connect(self._counter)
        self._refresh()

    def _refresh(self, *_):
        previous = {self.candidates[row].record_id for row in selected_rows(self.preview)} if self.candidates else None
        self.candidates = prepare_amendments(self.repository, self.store, self.organization_id, self.house_id,
            self.record_ids, self.protocol_combo.currentData(), self.choices)
        rows = []
        for candidate in self.candidates:
            record = self.store.get(candidate.record_id)
            old = record["document"]
            new = candidate.document
            rows.append(((f"{_display_date(old.get('start'))} — {_display_date(old.get('end'))}", old.get("amount", ""), "",
                          f"{_display_date(new.get('start'))} — {_display_date(new.get('end'))}", new.get("amount", ""),
                          candidate.detail), not candidate.blocked))
        for row in range(self.preview.rowCount()):
            old = self.preview.cellWidget(row, 3)
            if old is not None:
                old.hide()
                self.preview.removeCellWidget(row, 3)
        _fill(self.preview, rows, True)
        for row, candidate in enumerate(self.candidates):
            combo = SafeComboBox()
            combo.addItem("Выберите локальный тариф", None)
            for tariff in self.tariffs:
                combo.addItem(f"{tariff.period_full_label} · {tariff.amount} руб./м²", tariff.id)
            combo.setCurrentIndex(max(0, combo.findData(candidate.tariff_id)))
            combo.setToolTip(combo.currentText())
            combo.currentIndexChanged.connect(lambda _index, record_id=candidate.record_id, field=combo: self._choose_tariff(record_id, field.currentData()))
            self.preview.setCellWidget(row, 3, combo)
            if previous is not None and candidate.record_id not in previous:
                self.preview.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        self._counter()

    def _choose_tariff(self, record_id, tariff_id):
        self.choices[record_id] = tariff_id
        self._refresh_timer.start(0)

    def _counter(self, *_):
        count = len(selected_rows(self.preview))
        available = _available_count(self.preview)
        self.counter.setText(f"Выбрано изменений: {count}; доступно: {available}. Изменения сохраняются локально.")
        self.select_button.setEnabled(available > 0)
        self.clear_button.setEnabled(count > 0)
        self.selection_hint.setText("Нет доступных изменений.\n" + _blocked_reasons(self.preview)
                                    if self.preview.rowCount() and not available else "")
        self.selection_hint.setVisible(bool(self.selection_hint.text()))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(count > 0)

    def _apply(self):
        try:
            if self._refresh_timer.isActive():
                self._refresh_timer.stop()
                self._refresh()
            if self.page._house_id != self.house_id or self.page.organization_combo.currentData() != self.organization_id:
                raise ValueError("УО или дом изменились. Откройте предварительный просмотр заново.")
            reviewed = [self.candidates[row] for row in selected_rows(self.preview)]
            self.updated = apply_amendments(self.repository, self.store, self.organization_id,
                                            self.house_id, reviewed, self.protocol_combo.currentData())
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Изменения не сохранены", str(exc))
            self._refresh()
            return
        if self.updated:
            self.accept()
