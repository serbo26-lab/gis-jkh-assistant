"""Reusable application widgets."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Signal, Qt
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import icon, set_label_icon
from .styles import color


class _ComboChevron(QObject):
    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo
        self.label = QLabel(combo)
        self.label.setObjectName("ComboChevron")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.setAccessibleName("Открыть список")
        combo.installEventFilter(self)
        self.refresh()

    def refresh(self):
        app = QApplication.instance()
        scale = float(app.property("uiScale") or 1.0) if app else 1.0
        size = round(16 * scale)
        self.label.setPixmap(icon("chevron_down", color("muted_text"), size).pixmap(size, size))
        self.label.setGeometry(self.combo.width() - round(21 * scale),
                               (self.combo.height() - size) // 2, size, size)
        self.label.raise_()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange, QEvent.Type.StyleChange,
                            QEvent.Type.PaletteChange):
            self.refresh()
        return super().eventFilter(watched, event)


def ensure_combo_chevron(combo):
    if not hasattr(combo, "_svg_chevron"):
        combo._svg_chevron = _ComboChevron(combo)
    else:
        combo._svg_chevron.refresh()


class _ComboChevronInstaller(QObject):
    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Polish and isinstance(watched, QComboBox):
            ensure_combo_chevron(watched)
        return super().eventFilter(watched, event)


def refresh_combo_chevrons(app):
    if not hasattr(app, "_combo_chevron_installer"):
        app._combo_chevron_installer = _ComboChevronInstaller(app)
        app.installEventFilter(app._combo_chevron_installer)
    for widget in app.allWidgets():
        if isinstance(widget, QComboBox):
            ensure_combo_chevron(widget)


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

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._refresh_spin_geometry)
        self._refresh_spin_geometry()

    def _refresh_spin_geometry(self):
        app = QApplication.instance()
        scale = float(app.property("uiScale") or 1.0) if app else 1.0
        width = round(self.BUTTON_WIDTH * scale)
        self.lineEdit().setTextMargins(0, 0, 0, 0)
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(str(max(2100, self.maximum()))) + width * 2 + 24)
        # QSS + NoButtons may give Qt's internal edit field a width of one pixel.
        # Lay it out explicitly alongside our own buttons, with no second inset.
        self.lineEdit().setGeometry(6, 2, max(1, self.width() - width * 2 - 10),
                                    max(20, self.height() - 4))
        for index, button in enumerate((self._plus_button, self._minus_button)):
            button.setGeometry(self.width() - 2 - width * (2 - index), 2,
                               width, max(20, self.height() - 4))

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
        self._refresh_spin_geometry()

    def changeEvent(self, event):  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            if hasattr(self, "_minus_button"):
                self._geometry_timer.start(0)


class SafeComboBox(QComboBox):
    """Combo box that does not change its value from wheel scrolling on hover."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMaxVisibleItems(20)
        self.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        ensure_combo_chevron(self)

    def showPopup(self) -> None:  # noqa: N802 - Qt API
        """Keep a scrollable native popup below its field and inside the window."""
        view = self.view()
        view.setMaximumHeight(16777215)
        super().showPopup()
        if not self.count():
            return
        popup = view.window()
        bounds = self.window().rect().translated(
            self.window().mapToGlobal(self.window().rect().topLeft())
        )
        screen = self.screen()
        if screen:
            bounds = bounds.intersected(screen.availableGeometry())
        bounds.adjust(4, 4, -4, -4)
        top = self.mapToGlobal(self.rect().topLeft()).y()
        anchor = self.mapToGlobal(self.rect().bottomLeft())
        below = max(1, bounds.bottom() - anchor.y())
        above = max(1, top - bounds.top())
        row_height = max(view.sizeHintForRow(max(0, self.currentIndex())),
                         self.fontMetrics().height() + 8)
        desired = min(self.maxVisibleItems(), self.count()) * row_height + 4
        opens_below = below >= min(desired, 3 * row_height) or below >= above
        height = min(desired, below if opens_below else above)
        width = min(max(self.width(), popup.width()), bounds.width())
        x = max(bounds.left(), min(anchor.x(), bounds.right() - width + 1))
        y = anchor.y() + 1 if opens_below else top - height
        popup.setMinimumSize(0, 0)
        popup.setMaximumHeight(height)
        popup.setGeometry(x, y, width, height)
        view.setMaximumHeight(max(1, height - 2))
        view.scrollTo(self.model().index(
            max(0, self.currentIndex()), self.modelColumn(), self.rootModelIndex()
        ))

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
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_text)
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
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            self.group.addButton(button)
            layout.addWidget(button)
            self.buttons[key] = button
            button.clicked.connect(
                lambda _checked=False, value=key: self.scopeChanged.emit(value)
            )
            button.toggled.connect(self._queue_fit)
        self.buttons[first_key].setChecked(True)
        self._fit_text()
        self._queue_fit()

    def _queue_fit(self, *_args) -> None:
        if not self._fit_timer.isActive():
            self._fit_timer.start(0)

    def _fit_text(self) -> None:
        for button in self.buttons.values():
            font = QFont(button.font())
            font.setBold(True)
            metrics = QFontMetrics(font)
            button.setMinimumWidth(metrics.horizontalAdvance(button.text()) + 32)
            button.setMinimumHeight(0)
            button.setMinimumHeight(max(button.minimumSizeHint().height(), metrics.height() + 8))

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "buttons"):
            self._queue_fit()

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(5)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        layout.addLayout(row)

        self.label = QLabel(text)
        self.label.setObjectName("StatusStripText")
        self.label.setWordWrap(True)
        self.state_icon = QLabel()
        set_label_icon(self.state_icon, "check")
        self.state_icon.hide()
        row.addWidget(self.state_icon)
        row.addWidget(self.label, 1)

        self.progress = QProgressBar()
        self.progress.setObjectName("StatusStripProgress")
        self.progress.setTextVisible(False)
        self.progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(6)
        row.addLayout(self.actions)

    def set_text(self, text: str) -> None:
        self.label.setText(text)
        self.state_icon.setVisible(bool(text) and self.property("tone") == "success")

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def show_progress(self, maximum: int, value: int = 0) -> None:
        self.progress.setRange(0, max(0, int(maximum)))
        self.progress.setValue(max(0, int(value)))
        self.progress.setVisible(True)

    def hide_progress(self) -> None:
        self.progress.setVisible(False)
