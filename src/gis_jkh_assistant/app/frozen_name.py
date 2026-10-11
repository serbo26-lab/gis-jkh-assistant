"""Keep the read-only work name visible while scrolling the yearly plan."""
from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView


class FrozenName(QObject):
    def __init__(self, table):
        super().__init__(table)
        self.table = table
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
        table.horizontalHeader().sectionResized.connect(lambda *_: self.sync())
        table.verticalHeader().sectionResized.connect(lambda row, _old, height: self.view.setRowHeight(row, height))
        table.horizontalScrollBar().valueChanged.connect(lambda *_: self.sync())
        table.model().rowsInserted.connect(lambda *_: QTimer.singleShot(0, self.sync))
        table.installEventFilter(self)
        table.currentCellChanged.connect(lambda *_: QTimer.singleShot(0, self.ensure_current_visible))
        self.sync()

    def sync(self):
        table = self.table
        self.view.setFont(table.font())
        self.view.horizontalHeader().setFont(table.horizontalHeader().font())
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
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange):
            QTimer.singleShot(0, self.sync)
        return False
