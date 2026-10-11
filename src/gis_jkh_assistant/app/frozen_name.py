"""Keep the read-only work name visible while scrolling the yearly plan."""
from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView


class FrozenName(QObject):
    def __init__(self, table):
        super().__init__(table)
        self.table = table
        # Header signals also arrive while Qt is repolishing the application.
        # Defer mutations of the second view, and cancel them with its owner.
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self.sync)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self.ensure_current_visible)
        self.view = QTableView(table)
        self.view.setObjectName("FrozenName")
        self.view.setModel(table.model())
        self.view.setSelectionModel(table.selectionModel())
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().hide()
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for column in range(1, table.columnCount()):
            self.view.hideColumn(column)
        for view in (table, self.view):
            view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.verticalScrollBar().valueChanged.connect(self.view.verticalScrollBar().setValue)
        self.view.verticalScrollBar().valueChanged.connect(table.verticalScrollBar().setValue)
        table.horizontalHeader().sectionResized.connect(self.schedule_sync)
        table.verticalHeader().sectionResized.connect(self.schedule_sync)
        table.horizontalScrollBar().valueChanged.connect(self.schedule_sync)
        table.model().rowsInserted.connect(self.schedule_sync)
        table.model().modelReset.connect(self.schedule_sync)
        table.installEventFilter(self)
        table.currentCellChanged.connect(lambda *_: self._scroll_timer.start(0))
        self.sync()

    def schedule_sync(self, *_args):
        if not self._sync_timer.isActive():
            self._sync_timer.start(0)

    def sync(self):
        table = self.table
        # Fonts come from the parent and QSS, without creating a local override
        # during application-wide style propagation.
        self.view.horizontalHeader().setFixedHeight(table.horizontalHeader().height())
        self.view.setColumnWidth(0, table.columnWidth(0))
        for row in range(table.rowCount()):
            self.view.setRowHeight(row, table.rowHeight(row))
        frame = table.frameWidth()
        self.view.setGeometry(frame, frame, table.columnWidth(0) + frame,
                              table.viewport().height() + table.horizontalHeader().height())
        self.view.setVisible(table.horizontalScrollBar().value() > 0)
        self.view.raise_()

    def ensure_current_visible(self):
        cell = self.table.currentItem()
        if cell is not None and cell.column() > 0:
            left = self.table.visualItemRect(cell).left()
            width = self.table.columnWidth(0)
            if left < width:
                bar = self.table.horizontalScrollBar()
                bar.setValue(bar.value() - (width - left))

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange,
                            QEvent.Type.StyleChange):
            self.schedule_sync()
        return False
