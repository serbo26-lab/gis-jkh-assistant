"""Grouped section navigation with a bottom-aligned settings entry."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


class _SectionList(QListWidget):
    def __init__(self, navigation):
        super().__init__(navigation)
        self.navigation = navigation
        self.setObjectName("Navigation")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        row = self.navigation.currentRow()
        targets = {
            Qt.Key.Key_Up: max(0, row - 1),
            Qt.Key.Key_Down: min(self.navigation.count() - 1, row + 1),
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: self.navigation.count() - 1,
        }
        if event.key() in targets and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.navigation.setCurrentRow(targets[event.key()])
            self.navigation.setFocus(Qt.FocusReason.OtherFocusReason)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        super().mousePressEvent(event)
        # Follow the accepted selection, including a rollback after a failed save.
        self.navigation.setFocus(Qt.FocusReason.MouseFocusReason)


class SidebarNavigation(QWidget):
    """One logical selection across groups; separators and the gap are not items."""

    currentRowChanged = Signal(int)

    def __init__(self, groups, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._current_row = -1
        self._entries = []
        self.views = []
        self.dividers = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for group_index, entries in enumerate(groups):
            if group_index == len(groups) - 1:
                layout.addStretch(1)
            elif group_index:
                divider = QFrame(self)
                divider.setObjectName("NavigationDivider")
                divider.setFixedHeight(1)
                layout.addSpacing(7)
                layout.addWidget(divider)
                layout.addSpacing(7)
                self.dividers.append(divider)
            view = _SectionList(self)
            self.views.append(view)
            offset = len(self._entries)
            for title, section_icon, name, description in entries:
                item = QListWidgetItem(section_icon, title)
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setToolTip(description)
                view.addItem(item)
                self._entries.append((view, item))
            view.currentRowChanged.connect(
                lambda row, start=offset: self.setCurrentRow(start + row) if row >= 0 else None)
            layout.addWidget(view)
        self.setFocusProxy(self.views[0])
        self.refresh_metrics(20)

    def count(self):
        return len(self._entries)

    def item(self, row):
        return self._entries[row][1]

    def item_view(self, row):
        return self._entries[row][0]

    def currentRow(self):  # noqa: N802 - QListWidget-compatible selection API
        return self._current_row

    def selectedItems(self):  # noqa: N802 - QListWidget-compatible selection API
        return [item for view in self.views for item in view.selectedItems()]

    def setCurrentRow(self, row):  # noqa: N802 - selection API
        if not 0 <= row < self.count():
            return
        changed = row != self._current_row
        self._current_row = row
        selected_view, selected_item = self._entries[row]
        had_focus = self.hasFocus() or any(view.hasFocus() for view in self.views)
        for view in self.views:
            view.setFocusPolicy(Qt.FocusPolicy.StrongFocus if view is selected_view
                                else Qt.FocusPolicy.NoFocus)
            blocked = view.blockSignals(True)
            try:
                view.setCurrentRow(-1)
                view.clearSelection()
                if view is selected_view:
                    view.setCurrentItem(selected_item)
            finally:
                view.blockSignals(blocked)
        self.setFocusProxy(selected_view)
        if had_focus:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        if changed:
            self.currentRowChanged.emit(row)

    def refresh_metrics(self, icon_size):
        """Give each native list just its rows; only the gap before Settings grows."""
        for view in self.views:
            view.ensurePolished()
            view.setIconSize(QSize(icon_size, icon_size))
            font = QFont(view.font())
            font.setBold(True)
            metrics = QFontMetrics(font)
            width = max(1, view.viewport().width() - icon_size - 26)
            height = 0
            for row in range(view.count()):
                item = view.item(row)
                text = metrics.boundingRect(0, 0, width, 1000, Qt.TextFlag.TextWordWrap, item.text())
                row_height = max(icon_size, text.height()) + 20
                item.setSizeHint(QSize(0, row_height))
                height += row_height
            view.setFixedHeight(height)
