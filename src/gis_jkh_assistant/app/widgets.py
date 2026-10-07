"""Reusable application widgets."""

from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QMouseEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolButton,
    QWidget,
)


class SafeSpinBox(QSpinBox):
    """Spin box with explicit +/- buttons and safe wheel behavior."""

    BUTTON_WIDTH = 24

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setMinimumWidth(112)

        self._plus_button = QToolButton(self)
        self._plus_button.setText("+")
        self._plus_button.setObjectName("SpinPlusButton")
        self._plus_button.setAutoRepeat(True)
        self._plus_button.clicked.connect(self.stepUp)

        self._minus_button = QToolButton(self)
        self._minus_button.setText("−")
        self._minus_button.setObjectName("SpinMinusButton")
        self._minus_button.setAutoRepeat(True)
        self._minus_button.clicked.connect(self.stepDown)

        margins = self.lineEdit().textMargins()
        self.lineEdit().setTextMargins(
            margins.left(),
            margins.top(),
            self.BUTTON_WIDTH * 2 + 4,
            margins.bottom(),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        height = max(20, self.height() - 4)
        y = 2
        right = self.width() - 2
        self._plus_button.setGeometry(
            right - self.BUTTON_WIDTH * 2,
            y,
            self.BUTTON_WIDTH,
            height,
        )
        self._minus_button.setGeometry(
            right - self.BUTTON_WIDTH,
            y,
            self.BUTTON_WIDTH,
            height,
        )


class SafeComboBox(QComboBox):
    """Combo box that does not change its value from wheel scrolling on hover."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()



class ActionToolbar(QFrame):
    """Single-row semantic action bar shared by application pages."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ActionToolbar")
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 8, 10, 8)
        self.layout.setSpacing(8)

    def add_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ToolbarGroupLabel")
        self.layout.addWidget(label)
        return label

    def add_widget(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.layout.addWidget(widget, stretch)
        return widget

    def add_separator(self) -> QFrame:
        separator = QFrame()
        separator.setObjectName("ToolbarSeparator")
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        self.layout.addWidget(separator)
        return separator

    def add_stretch(self, stretch: int = 1) -> None:
        self.layout.addStretch(stretch)


class ScopeSwitcher(QFrame):
    """Compact two-state selector used for month-vs-period actions."""

    scopeChanged = Signal(str)

    def __init__(
        self,
        first_key: str,
        first_text: str,
        second_key: str,
        second_text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ScopeSwitcher")
        self._keys = (first_key, second_key)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in (
            (first_key, first_text),
            (second_key, second_text),
        ):
            button = QPushButton(text)
            button.setObjectName("ScopeSwitcherButton")
            button.setCheckable(True)
            button.setProperty("scopeKey", key)
            self.group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button
            button.clicked.connect(
                lambda _checked=False, value=key: self.scopeChanged.emit(value)
            )
        self.buttons[first_key].setChecked(True)

    def scope(self) -> str:
        for key, button in self.buttons.items():
            if button.isChecked():
                return key
        return self._keys[0]

    def set_scope(self, key: str) -> None:
        button = self.buttons.get(key)
        if button is None:
            return
        changed = not button.isChecked()
        button.setChecked(True)
        if changed:
            self.scopeChanged.emit(key)


class StatusStrip(QFrame):
    """Compact page-level operation/status strip with optional progress."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusStrip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)

        self.label = QLabel(text)
        self.label.setObjectName("StatusStripText")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        self.progress = QProgressBar()
        self.progress.setObjectName("StatusStripProgress")
        self.progress.setTextVisible(True)
        self.progress.setMinimumWidth(150)
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(6)
        layout.addLayout(self.actions)

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def show_progress(self, maximum: int, value: int = 0) -> None:
        self.progress.setRange(0, max(0, int(maximum)))
        self.progress.setValue(max(0, int(value)))
        self.progress.setVisible(True)

    def hide_progress(self) -> None:
        self.progress.setVisible(False)
