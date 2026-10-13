"""Selection commands belong to the assessment column they control."""

from PySide6.QtCore import QEvent, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QStyle, QStyleOptionHeader

from .icons import set_button_icon
from .page_layouts import ReadableHeader


class AssessmentHeader(ReadableHeader):
    """Two real, keyboard-accessible buttons inside the first header section."""

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.commands = ()
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._position_commands)
        self.sectionResized.connect(self._queue_fit)
        self.sectionMoved.connect(self._queue_fit)
        self.geometriesChanged.connect(self._queue_fit)

    def bind_commands(self, include, exclude):
        self.commands = (include, exclude)
        for button, label, semantic in (
            (include, "Отметить все работы", "success"),
            (exclude, "Снять все отметки", "danger"),
        ):
            button.setParent(self.viewport())
            button.setText("")
            button.setObjectName("AssessmentSelectionButton")
            button.setProperty("compactCommand", True)
            button.setAccessibleName(label)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.installEventFilter(self)
            button.setToolTip(label + ". Акты и значения перерасчёта сохраняются.")
            set_button_icon(button, "check", semantic, 16)
            button.show()
        self.parent().horizontalHeaderItem(0).setToolTip(
            "Выбор работ для оценки. Зелёная галочка — отметить все; красная — снять все отметки."
        )
        self.updateGeometry()
        self._queue_fit()

    def sectionSizeFromContents(self, index):  # noqa: N802
        size = super().sectionSizeFromContents(index)
        if index != 0 or not self.commands:
            return size
        button_height = max(button.minimumSizeHint().height() for button in self.commands)
        button_width = sum(max(24, button.minimumSizeHint().width()) for button in self.commands) + 12
        return QSize(max(size.width(), button_width), max(size.height(), self.fontMetrics().height() + button_height + 12))

    def paintSection(self, painter, rect, logical_index):  # noqa: N802
        if logical_index != 0 or not self.commands:
            return super().paintSection(painter, rect, logical_index)
        option = QStyleOptionHeader()
        self.initStyleOptionForIndex(option, logical_index)
        option.rect = rect
        option.text = ""
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)
        painter.save()
        font = QFont(self.font())
        font.setPointSizeF(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(self.palette().windowText().color())
        painter.drawText(rect.adjusted(4, 3, -4, -max(button.height() for button in self.commands) - 6),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "В оценку")
        painter.restore()

    def _queue_fit(self, *_args):
        if not self._fit_timer.isActive():
            self._fit_timer.start(0)

    def _position_commands(self):
        if not self.commands:
            return
        widths = [max(24, button.minimumSizeHint().width()) for button in self.commands]
        height = max(button.minimumSizeHint().height() for button in self.commands)
        section_left = self.sectionViewportPosition(0)
        section_width = self.sectionSize(0)
        x = section_left + (section_width - sum(widths) - 4) // 2
        for button, width in zip(self.commands, widths):
            button.setGeometry(QRect(x, self.height() - height - 4, width, height))
            button.setVisible(section_left >= 0 and section_left + section_width <= self.viewport().width())
            x += width + 4

    def scrollContentsBy(self, dx, dy):  # noqa: N802
        super().scrollContentsBy(dx, dy)
        self._queue_fit()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if watched in self.commands and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                # QHeaderView inherits QAbstractItemView's grid navigation,
                # which consumes Tab from embedded buttons. Traverse the real
                # window focus chain instead of navigating header cells.
                backwards = (event.key() == Qt.Key.Key_Backtab
                             or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return self.window().focusNextPrevChild(not backwards)
        return super().eventFilter(watched, event)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.StyleChange,
                            QEvent.Type.FontChange, QEvent.Type.LayoutRequest):
            if hasattr(self, "_fit_timer"):
                self._queue_fit()
        return result
