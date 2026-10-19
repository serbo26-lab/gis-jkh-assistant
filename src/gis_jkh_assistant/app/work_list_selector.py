"""Visible registry using the existing selectors as the single selection source."""
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QSizePolicy, QTableWidget,
    QTableWidgetItem, QWidget,
)


class WorkListSelector(QTableWidget):
    def __init__(self, page, source):
        super().__init__(0, 3, page)
        self.page, self.source = page, source
        self.setProperty("compactTable", True)
        self._syncing = False
        self._owner = None
        self._scroll_positions = {}
        self._initial_height_applied = False
        self.setAccessibleName("Перечни выбранного дома")
        self.setHorizontalHeaderLabels(["Период перечня", "Состояние в ГИС", "Локальные данные"])
        self.verticalHeader().hide()
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setTabKeyNavigation(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setAlternatingRowColors(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.sync)
        model = source.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.dataChanged):
            signal.connect(self.queue_sync)
        if hasattr(source, "currentIndexChanged"):
            source.currentIndexChanged.connect(self.queue_sync)
        else:
            source.currentRowChanged.connect(self.queue_sync)
        source.installEventFilter(self)
        self.currentCellChanged.connect(self._selected)
        self.verticalScrollBar().valueChanged.connect(self._remember_scroll)
        self.queue_sync()

    def queue_sync(self, *_args):
        if not self._timer.isActive():
            self._timer.start(0)

    def _remember_scroll(self, value):
        if not self._syncing and self._owner:
            self._scroll_positions[self._owner] = value

    def _entries(self):
        if hasattr(self.source, "itemData"):
            return [self.source.itemData(i) for i in range(self.source.count())], self.source.currentIndex()
        return [self.source.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.source.count())], self.source.currentRow()

    def sync(self):
        if getattr(self.page.window(), "_closing", False):
            return
        ids, current = self._entries()
        owner = self.page.house_combo.currentData()
        scroll = self._scroll_positions.get(owner, 0)
        self._syncing = True
        self.blockSignals(True)
        try:
            self._owner = owner
            self.setRowCount(len(ids))
            for row, key in enumerate(ids):
                work_list = self.page.repository.get_work_list(key) if key else None
                if not work_list:
                    continue
                state = str(work_list.gis_status or "LOCAL").upper()
                status = {"APPROVED": "Размещён", "DRAFT": "Проект", "CANCELLED": "Отменён",
                          "LOCAL": "Не отправлен", "ERROR": "Ошибка",
                          "IMPORT_SENT": "Обрабатывается", "NOT_FOUND": "Не найден"}.get(state, "Не проверено")
                baseline = self.page.repository.get_setting(f"ui_work_list_saved:{key}")
                local = ("Состав загружен" if self.page.repository.list_work_list_items(key)
                         else "Состав пуст" if not work_list.gis_work_list_guid or baseline
                         else "Состав не загружен")
                if baseline:
                    from .registry_workflow import work_list_fingerprint
                    if baseline != work_list_fingerprint(self.page, key):
                        local = "Есть локальные изменения"
                if key == getattr(self.page, "_current_work_list_id", None) and (
                    getattr(self.page, "_period_dirty", False) or getattr(self.page, "_pending_items", {})
                ):
                    local = "Есть несохранённые изменения"
                for column, text in enumerate((work_list.period_label, status, local)):
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    self.setItem(row, column, item)
            if current >= 0:
                self.setCurrentCell(current, 0)
            else:
                self.clearSelection()
                self.setCurrentItem(None)
            self.setEnabled(self.source.isEnabled())
            row_height = max(20, self.fontMetrics().height() + 6)
            self.verticalHeader().setDefaultSectionSize(row_height)
            header = self.horizontalHeader().sizeHint().height() + self.frameWidth() * 2
            self.setMinimumHeight(header + row_height)
            self.setMaximumHeight(header + row_height * max(1, min(5, len(ids))))
            if ids and not self._initial_height_applied and hasattr(self.page, "workspace_splitter"):
                key = "ui.registry.height." + type(self.page).__name__
                saved = self.page.repository.get_setting(key)
                preferred = int(saved) if saved and saved.isdigit() else self.maximumHeight()
                self.page.workspace_splitter.setSizes([preferred, max(100, self.page.height() - preferred)])
                self._initial_height_applied = True
            if owner in self._scroll_positions:
                self.verticalScrollBar().setValue(scroll)
            elif current >= 0:
                self.scrollToItem(self.item(current, 0))
            # Preserve deliberate browsing, but don't leave a formerly visible
            # selection cut in half when an expanded panel reduces this viewport.
            selected = self.item(current, 0) if current >= 0 else None
            if selected is not None:
                rect = self.visualItemRect(selected)
                viewport = self.viewport().rect()
                if rect.intersects(viewport) and not viewport.contains(rect):
                    self.scrollToItem(selected)
        finally:
            self.blockSignals(False)
            self._syncing = False

    def _selected(self, row, *_args):
        if self._syncing or row < 0 or not self.source.isEnabled():
            return
        if hasattr(self.source, "currentIndexChanged"):
            self.source.setCurrentIndex(row)
        else:
            self.source.setCurrentRow(row)
        self.queue_sync()  # A rejected selection is restored by its controller.

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "_timer"):
            self.queue_sync()

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.queue_sync()
        return False


def install_selector(page, root, source, *, start=2):
    """Keep the header/context above a vertically resizable registry/workspace."""
    source.hide()
    page.list_selector = WorkListSelector(page, source)
    workspace = QWidget()
    from .workflow_widgets import ContentHeightGuard, ReservedVBoxLayout, WorkspaceSplitter
    body = ReservedVBoxLayout(workspace)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(4)
    while root.count() > start:
        item = root.takeAt(start)
        if item.widget():
            body.addWidget(item.widget(), 1 if item.widget().property("mainWorkspace") else 0)
        else:
            body.addItem(item)
    # QSplitter otherwise keeps the old workspace minimum after an inline
    # disclosure grows, letting child widgets overlap instead of scrolling.
    workspace.height_guard = ContentHeightGuard(workspace)
    page.workspace_splitter = WorkspaceSplitter()
    page.workspace_splitter.setChildrenCollapsible(False)
    page.workspace_splitter.addWidget(page.list_selector)
    page.workspace_splitter.addWidget(workspace)
    page.workspace_splitter.setStretchFactor(0, 0)
    page.workspace_splitter.setStretchFactor(1, 1)
    page.workspace_splitter.setSizes([150, 700])
    root.addWidget(page.workspace_splitter, 1)
    # No data lives in this size preference; save it per section.
    key = "ui.registry.height." + type(page).__name__
    saved = page.repository.get_setting(key)
    if saved and saved.isdigit():
        page.workspace_splitter.setSizes([int(saved), 700])
    page.workspace_splitter.splitterMoved.connect(
        lambda *_: None if getattr(page.window(), "_closing", False) else
        page.repository.set_setting(key, str(page.workspace_splitter.sizes()[0]))
    )
    QWidget.setTabOrder(page.house_combo, page.list_selector)
    following = getattr(page, "year_combo", None) or getattr(page, "start_month", None)
    if following:
        QWidget.setTabOrder(page.list_selector, following)
