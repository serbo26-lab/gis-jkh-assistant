"""Readable command rows that wrap at button boundaries instead of clipping."""

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class ActionFlowLayout(QLayout):
    """Small height-for-width layout for commands within one semantic group."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):  # noqa: N802 - Qt API
        widget = item.widget()
        if widget is not None:
            widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._items.append(item)

    def addStretch(self, _stretch=0):  # noqa: N802 - compatible action-row API
        # Commands align together; empty flexible spacers cannot divide a group.
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width):  # noqa: N802 - Qt API
        return self._arrange(QRect(0, 0, width, 0), measure=True)

    def setGeometry(self, rect):  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._arrange(rect)

    def sizeHint(self):  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802 - Qt API
        result = QSize()
        for item in self._items:
            if not item.isEmpty():
                result = result.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return result + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, measure=False):
        margins = self.contentsMargins()
        available = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = available.x(), available.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint()
            widget = item.widget()
            gap = self.spacing()
            if gap < 0 and isinstance(widget, QWidget):
                gap = widget.style().layoutSpacing(
                    QSizePolicy.ControlType.PushButton, QSizePolicy.ControlType.PushButton,
                    Qt.Orientation.Horizontal, None, widget,
                )
            gap = max(6, gap)
            if x > available.x() and x + size.width() > available.right() + 1:
                x, y, row_height = available.x(), y + row_height + gap, 0
            if not measure:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + gap
            row_height = max(row_height, size.height())
        return y + row_height - rect.y() + margins.bottom()
