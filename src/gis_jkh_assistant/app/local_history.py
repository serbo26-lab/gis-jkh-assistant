"""Undo completed local fact edits, scoped to a period and guarded against stale data."""
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QMessageBox, QPushButton

FIELDS = ('fact_price', 'fact_amount', 'fact_count', 'fact_total_cost')


def values(item):
    return tuple(getattr(item, field) for field in FIELDS)


class FactHistory:
    def __init__(self, page):
        self.page = page
        self.undo_entries = []
        self.redo_entries = []
        self.shortcuts = []
        self.buttons = []
        for title, sequence, redo in (("Отменить локальную правку", QKeySequence.StandardKey.Undo, False),
                                       ("Повторить локальную правку", QKeySequence.StandardKey.Redo, True)):
            button = QPushButton(title, page.command_host)
            button.setToolTip("Только локальные числовые показатели текущего месяца; обмен с ГИС не отменяется.")
            button.clicked.connect(lambda _checked=False, value=redo: self.apply(value))
            page.data_actions.add_command(button)
            self.buttons.append(button)
            shortcut = QShortcut(QKeySequence(sequence), page.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda value=redo: self.apply(value))
            self.shortcuts.append(shortcut)
        page.data_actions.menu_model.aboutToShow.connect(self.refresh)
        self.refresh()

    def refresh(self):
        page = self.page
        available = bool(page._current_period_id) and not (page._integration_busy or page._queue_running)
        for button, shortcut, entries in zip(self.buttons, self.shortcuts, (self.undo_entries, self.redo_entries)):
            enabled = available and any(entry[0] == page._current_period_id for entry in entries)
            button.setEnabled(enabled)
            shortcut.setEnabled(enabled)
        page.data_actions.sync()

    def record(self, item, after):
        before = values(item)
        if before != after:
            self.undo_entries.append((item.fixation_period_id, item.id, before, after))
            self.undo_entries = self.undo_entries[-100:]
            self.redo_entries.clear()
        self.refresh()

    def apply(self, redo=False):
        page = self.page
        if page._integration_busy or page._queue_running:
            return
        source, target = (self.redo_entries, self.undo_entries) if redo else (self.undo_entries, self.redo_entries)
        index = next((i for i in range(len(source)-1, -1, -1)
                      if source[i][0] == page._current_period_id), None)
        if index is None:
            return
        period, item_id, before, after = source[index]
        expected, restored = (before, after) if redo else (after, before)
        try:
            current = next((i for i in page.repository.list_work_fixation_items(period) if i.id == item_id), None)
            if current is None or values(current) != expected:
                QMessageBox.warning(page, "Правка уже изменилась", "Строка была изменена другой операцией. Отмена не выполнена, текущие данные сохранены.")
                source.pop(index)
                self.refresh()
                return
            page.repository.update_work_fixation_item(item_id, **dict(zip(FIELDS, restored)))
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(page, "Не удалось отменить правку", str(exc))
            return
        target.append(source.pop(index))
        self.refresh()
        page._load_table()
        page._refresh_period_status()
        page._refresh_year_status()
