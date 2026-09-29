"""Reusable application widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QSpinBox, QToolButton


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
