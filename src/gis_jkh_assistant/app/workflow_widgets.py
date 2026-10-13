"""Small desktop components for context, lifecycle and progressive disclosure."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .icons import icon, set_button_icon
from .action_layout import ActionFlowLayout
from .styles import color
from .widgets import StatusStrip


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        text.setSpacing(3)
        heading = QLabel(title)
        heading.setObjectName("Title")
        text.addWidget(heading)
        if subtitle:
            label = QLabel(subtitle)
            label.setObjectName("Muted")
            label.setWordWrap(True)
            text.addWidget(label)
            label.hide()
            heading.setToolTip(subtitle)
        layout.addLayout(text, 1)
        self.status = LocalSaveStatus()
        self.status.setToolTip("Локальные изменения сохраняются автоматически")
        self.status.setObjectName("SaveStatus")
        self.status.setWordWrap(False)
        layout.addWidget(self.status)


class LocalSaveStatus(QLabel):
    """Keep persistence vocabulary separate from exchange and business results."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("Сохранено локально")

    def setText(self, text):  # noqa: N802 - Qt API
        aliases = {
            "Сохранено локально": "Локально: сохранено",
            "Сохраняется…": "Локально: сохранение…",
            "Ошибка сохранения": "Локально: ошибка сохранения",
        }
        super().setText(aliases.get(text, text))
        self.setProperty("saveError", "ошибка" in self.text().lower() or "несохранённые" in self.text())
        repolish(self)


class ContextBar(QFrame):
    """A compact responsive context; field order is consistent across pages."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ContextBar")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(8, 6, 8, 6)
        self.grid.setSpacing(6)
        self.fields: list[QWidget] = []
        self._columns = 2
        self._compact = False
        self._expanded = False
        self.summary = QLabel()
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.summary.setAccessibleName("Текущий дом, перечень и год")
        self.expand_button = QPushButton("Изменить выбор…")
        self.expand_button.setCheckable(True)
        self.expand_button.toggled.connect(self._expand)
        self.controls = []

    def _expand(self, expanded):
        self._expanded = expanded
        self._relayout()

    def set_compact(self, compact):
        # Critical context remains directly selectable at every window size.
        self._compact = False
        self._relayout()

    def _summary_changed(self):
        if not all(isinstance(control, QComboBox) for control in self.controls):
            return
        values = [control.currentText() for control in self.controls]
        if len(values) == 4:
            full = f"{values[1]} • {values[3]} • {values[2]}"
            self.summary.setToolTip(" • ".join(values))
            self.summary.setText(self.summary.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, max(80, self.summary.width())))

    def add_field(self, label: str, control: QWidget) -> None:
        field = QWidget(self)
        layout = QHBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        short = {"Управляющая организация": "УО", "Размещённый перечень": "Перечень",
                 "Существующий перечень": "Перечень", "Месяц просмотра": "Месяц"}
        caption = QLabel(short.get(label, label))
        caption.setToolTip(label)
        caption.setObjectName("ContextCaption")
        caption.setBuddy(control)
        layout.addWidget(caption)
        control.setMinimumWidth(0)
        if isinstance(control, QComboBox):
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(8)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            control.currentTextChanged.connect(control.setToolTip)
        control.setAccessibleName(label)
        control.setProperty("contextFieldLabel", label)
        layout.addWidget(control, 1)
        self.fields.append(field)
        self.controls.append(control)
        if isinstance(control, QComboBox):
            control.currentTextChanged.connect(self._summary_changed)
        self._relayout()

    def _relayout(self) -> None:
        self._columns = self._readable_columns(self.width())
        while self.grid.count():
            self.grid.takeAt(0)
        self.summary.setVisible(self._compact)
        self.expand_button.setVisible(self._compact)
        if self._compact:
            self.grid.addWidget(self.summary, 0, 0, 1, max(1, self._columns - 1))
            self.grid.addWidget(self.expand_button, 0, max(1, self._columns - 1))
        count = len(self.fields)
        scale = float(QApplication.instance().property("uiScale") or 1.0)
        for control in self.controls:
            if control.property("contextFieldLabel") in {"Месяц", "Месяц просмотра"}:
                control.setMinimumWidth(control.fontMetrics().horizontalAdvance("Сентябрь") + round(72 * scale))
            elif control.property("contextFieldLabel") == "Год":
                control.setMinimumWidth(control.fontMetrics().horizontalAdvance("2026") + round(64 * scale))
        for i, field in enumerate(self.fields):
            field.setVisible(not self._compact or self._expanded)
            if self._columns == 2 and count >= 4:
                if i < 2:
                    self.grid.addWidget(field, 0, i * 3, 1, 3)
                elif i == 2:
                    self.grid.addWidget(field, 1, 0, 1, 4)
                else:
                    self.grid.addWidget(field, 1, 4 + i - 3, 1, 2 if count == 4 else 1)
            else:
                self.grid.addWidget(field, i // self._columns, i % self._columns)
        for column in range(6):
            weight = (1 if column >= 3 else 3) if self._columns >= 4 else 1
            self.grid.setColumnStretch(column, weight if column < (6 if self._columns == 2 and count >= 4 else self._columns) else 0)
        self._summary_changed()

    def _readable_columns(self, width: int) -> int:
        scale = float(QApplication.instance().property("uiScale") or 1.0)
        lengths = (18, 24, 26, 6, 8)
        # The fields have stylesheet fonts different from the container font.
        # Include text padding and the styled arrow when choosing a single row.
        required = sum(control.fontMetrics().horizontalAdvance("о" * lengths[min(i, 4)]) + 72 * scale
                       for i, control in enumerate(self.controls))
        return len(self.fields) if width >= required else 1 if width < 420 * scale else 2

    def resizeEvent(self, event):  # noqa: N802
        width = event.size().width()
        columns = self._readable_columns(width)
        if columns != self._columns:
            self._columns = columns
            self._relayout()
        self._summary_changed()
        super().resizeEvent(event)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "controls"):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._relayout)


class SectionCard(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(8, 6, 8, 8)
        self.body.setSpacing(6)
        self.actions = ActionFlowLayout()
        self.actions.setSpacing(6)
        if title:
            label = QLabel(title)
            label.setObjectName("SectionTitle")
            label.setWordWrap(True)
            self.body.addWidget(label)
        self.body.addLayout(self.actions)


class WorkspaceCard(SectionCard):
    """Respect wrapped command/error heights when a splitter sizes the workspace."""
    def __init__(self):
        super().__init__()
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_minimum)

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.FontChange):
            if hasattr(self, "_fit_timer") and not self._fit_timer.isActive():
                self._fit_timer.start(0)
        return result

    def _fit_minimum(self):
        if not self.isVisible():
            return
        margins = self.body.contentsMargins()
        width = max(1, self.width() - margins.left() - margins.right())
        heights = []
        for index in range(self.body.count()):
            item = self.body.itemAt(index)
            widget = item.widget()
            if widget:
                if item.isEmpty():
                    continue
                height = max(widget.minimumHeight(), widget.minimumSizeHint().height())
                if widget.hasHeightForWidth():
                    height = max(height, widget.heightForWidth(width))
            else:
                height = max(0, item.heightForWidth(width) if item.hasHeightForWidth() else item.minimumSize().height())
            if height > 0:
                heights.append(height)
        required = margins.top() + margins.bottom() + sum(heights) + self.body.spacing() * max(0, len(heights) - 1)
        if self.minimumHeight() != required:
            self.setMinimumHeight(required)


class ActionMenuButton(QPushButton):
    """Menu entries observe existing commands, keeping one execution path."""
    def __init__(self, text: str, name: str = "actions", parent=None):
        super().__init__(text, parent)
        set_button_icon(self, name)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.menu_model = QMenu(self)
        self.setMenu(self.menu_model)
        self.entries: list[tuple[object, QPushButton, Callable[[], bool] | None]] = []
        self.submenus: list[ActionMenuButton] = []
        self.menu_model.aboutToShow.connect(self.sync)

    def add_command(self, button: QPushButton, *, when=None, text: str | None = None):
        action = self.menu_model.addAction(text or button.text())
        action.setIcon(button.icon())
        action.setProperty("followCommandText", text is None)
        action.triggered.connect(button.click)
        self.entries.append((action, button, when))
        return action

    def add_submenu(self, button):
        button.menu_model.setTitle(button.text())
        self.menu_model.addMenu(button.menu_model)
        self.submenus.append(button)

    def sync(self) -> None:
        for submenu in self.submenus:
            submenu.sync()
        for action, button, when in self.entries:
            if action.property("followCommandText"):
                action.setText(button.text())
            action.setEnabled(button.isEnabled())
            action.setVisible(when is None or bool(when()))
            action.setToolTip(button.toolTip())
            action.setIcon(button.icon())


class DangerZone(ActionMenuButton):
    def __init__(self, text: str = "Опасные действия", parent=None):
        super().__init__(text, "danger", parent)
        self.setObjectName("DangerButton")
        self.setToolTip("Команды удаления и отмены. Перед выполнением потребуется подтверждение.")


def style_danger_button(button: QPushButton) -> QPushButton:
    """Show the named command directly, retaining its existing confirmation."""
    button.setObjectName("DangerButton")
    set_button_icon(button, "danger", "danger")
    button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    button.show()
    return button


class WorkflowPanel(QFrame):
    """Lifecycle plus one recommended action, with an inline blocking reason."""
    navigateRequested = Signal(int)

    def __init__(self, steps: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.setObjectName("WorkflowPanel")
        self._key: str | None = None
        self._compact = True
        self.commands: dict[str, QPushButton | Callable] = {}
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(8, 6, 8, 6)
        self.body.setSpacing(4)
        self.steps: list[QLabel] = []
        self.step_icons: list[QLabel] = []
        step_row = QHBoxLayout()
        self.step_widgets = []
        step_row.setSpacing(6)
        for index, title in enumerate(steps):
            step = QWidget()
            step_layout = QHBoxLayout(step)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(2)
            status_icon = QLabel()
            status_icon.hide()
            step_layout.addWidget(status_icon)
            label = QLabel(f"{index + 1}. {title}")
            label.setObjectName("WorkflowStep")
            label.setProperty("stepTitle", title)
            label.setWordWrap(True)
            step_layout.addWidget(label, 1)
            step_row.addWidget(step, 1)
            self.steps.append(label)
            self.step_widgets.append(step)
            self.step_icons.append(status_icon)
        self.body.addLayout(step_row)
        row = QHBoxLayout()
        self.action_row = row
        row.setSpacing(12)
        self._summary_widget = QWidget(self)
        text = QVBoxLayout(self._summary_widget)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("SectionTitle")
        self.title.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.detail)
        row.addWidget(self._summary_widget, 1)
        self.primary = QPushButton()
        self.primary.setObjectName("PrimaryButton")
        self.primary.setMinimumWidth(160)
        self.primary.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.primary.clicked.connect(self._execute)
        row.addWidget(self.primary)
        self.help_button = QPushButton("Как продолжить")
        self.help_button.setCheckable(True)
        self.help_button.setObjectName("DisclosureButton")
        self.help_button.toggled.connect(self._show_help)
        row.addWidget(self.help_button)
        self.body.addLayout(row)
        self.reason = QLabel()
        self.reason.setObjectName("BlockingReason")
        self.reason.setWordWrap(True)
        self.body.addWidget(self.reason)
        self._text_metrics_timer = QTimer(self)
        self._text_metrics_timer.setSingleShot(True)
        self._text_metrics_timer.timeout.connect(self._fit_text_metrics)
        self.set_compact(True)

    def _show_help(self, expanded):
        for step in self.step_widgets:
            step.setVisible(expanded)
        self.detail.setVisible(expanded)
        self._queue_text_metrics()

    def _queue_text_metrics(self):
        if hasattr(self, "_text_metrics_timer") and not self._text_metrics_timer.isActive():
            self._text_metrics_timer.start(0)

    def _fit_text_metrics(self):
        for label in (self.title, self.detail, self.reason):
            if label.isVisible() and label.width() > 0:
                required = max(label.fontMetrics().height(), label.heightForWidth(label.width()))
                if label.minimumHeight() != required:
                    label.setMinimumHeight(required)

    def place_help_below_actions(self) -> None:
        """Put expanded guidance on its own full-width row for dense monthly pages.

        The command row must not share a narrow text column with the expanded
        explanation. The existing labels, signals and next-step behavior remain
        unchanged; other pages retain their original compact summary.
        """
        if hasattr(self, "help_panel"):
            return
        self.help_panel = QWidget(self)
        content = QVBoxLayout(self.help_panel)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(4)
        for label in (self.title, self.detail):
            label.setParent(self.help_panel)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            content.addWidget(label)
        self._summary_widget.hide()
        self.body.insertWidget(self.body.indexOf(self.reason), self.help_panel)
        self.help_panel.setVisible(self.help_button.isChecked())
        self.help_button.toggled.connect(self.help_panel.setVisible)

    def register(self, key: str, command) -> None:
        self.commands[key] = command

    def set_compact(self, compact):
        self._compact = True
        self._show_help(self.help_button.isChecked())
        self.action_row.setDirection(QBoxLayout.Direction.LeftToRight)

    def show_step(self, active: int, title: str, detail: str, key: str | None,
                  text: str, *, reason: str = "", completed: bool = False,
                  done: set[int] | None = None) -> None:
        self._key = key
        for index, label in enumerate(self.steps):
            is_done = (index < active if done is None else index in done) or completed
            state = "done" if is_done else "current" if index == active else "next"
            label.setProperty("stage", state)
            prefix = "" if state == "done" else f"{index + 1}. "
            label.setText(f"{prefix}{label.property('stepTitle')}")
            status_icon = self.step_icons[index]
            status_icon.setPixmap(icon("check", color("success"), 16).pixmap(16, 16))
            status_icon.setVisible(state == "done")
            repolish(label)
        self.title.setText(title)
        self.detail.setText(detail)
        self.primary.setText(text)
        self.primary.setVisible(key not in getattr(self, "direct_keys", set()))
        command = self.commands.get(key)
        for alternate in self.commands.values():
            if isinstance(alternate, QPushButton) and alternate.property("workflowAlternate") and not hasattr(self, "direct_keys"):
                alternate.setVisible(alternate is not command)
        enabled = command is not None and (not isinstance(command, QPushButton) or command.isEnabled())
        self.primary.setEnabled(enabled and not reason)
        self.primary.setToolTip(reason or detail)
        glyph = {"check": "quality", "fill": "planning", "form": "quality", "acts": "act",
                 "review_acts": "act", "resume": "sync", "sync": "sync", "new": "add",
                 "create": "add", "organization": "organization", "add": "add",
                 "context": "lists", "fixation": "fixation", "missing": "next", "plan": "planning"}.get(key, "upload")
        set_button_icon(self.primary, "next" if key and key.startswith("next") else glyph, "on_accent")
        self.primary.setAccessibleName(text)
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self._queue_text_metrics()

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        primary_width = 0 if self.primary.isHidden() else self.primary.minimumSizeHint().width()
        needed = self.title.minimumSizeHint().width() + primary_width + self.help_button.minimumSizeHint().width() + 2 * self.action_row.spacing() + 16
        if hasattr(self, "compact_scope") and not self.compact_scope.isHidden():
            needed = (self.compact_scope.minimumSizeHint().width() + self.primary.minimumSizeHint().width()
                      + self.help_button.minimumSizeHint().width() + 3 * self.action_row.spacing() + 16)
        direction = (QBoxLayout.Direction.TopToBottom if event.size().width() < needed
                     else QBoxLayout.Direction.LeftToRight)
        if self.action_row.direction() != direction:
            self.action_row.setDirection(direction)
        super().resizeEvent(event)
        self._queue_text_metrics()

    def changeEvent(self, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.FontChange and hasattr(self, "primary"):
            self.primary.setMinimumWidth(max(160, self.primary.sizeHint().width()))
        super().changeEvent(event)

    def _execute(self) -> None:
        command = self.commands.get(self._key)
        if isinstance(command, QPushButton):
            command.click()
        elif callable(command):
            command()


class OperationStatus(StatusStrip):
    """Shared status/progress presentation; does not change worker execution."""
    def __init__(self, text: str = "Готово к работе", parent=None):
        super().__init__(text, parent)
        self._generation = 0

    def outcome(self, text: str, *, tone: str = "muted"):
        self._outcome_generation = self._generation
        self.setProperty("tone", tone)
        repolish(self)
        self.hide_progress()
        self.set_text(text)
        self.show()

    def bind_worker(self, worker, title: str, context: str = "", queue=None):
        self._generation += 1
        generation = self._generation
        prefix = " • ".join(value for value in (title, context) if value)
        self.setProperty("tone", "accent")
        repolish(self)
        self.set_text(prefix + "…")
        self.show_progress(0)
        self.show()

        def progress(data):
            if generation != self._generation:
                return
            if data.get("phase") == "queued":
                position = f" • месяц {queue[0] + 1} из {queue[1]}" if queue else ""
                self.set_text(prefix + position + " • ожидание предыдущей операции")
                return
            total = data.get("total") or data.get("totalCount")
            value = data.get("processed") or data.get("current") or data.get("received") or 0
            if queue:
                index, months = queue
                self.show_progress(months, index)
                within = f" • данные: {value} из {total}" if total else f" • получено {value}" if value else ""
                self.set_text(f"{prefix} • месяц {index + 1} из {months}" + within)
            elif total:
                self.show_progress(int(total), int(value))
                self.set_text(f"{prefix} • обработано {value} из {total}")
            elif value:
                self.set_text(f"{prefix} • получено {value}")

        def failed(message):
            if generation != self._generation:
                return
            self.setProperty("tone", "danger")
            repolish(self)
            self.hide_progress()
            self.set_text(f"Остановлено • {prefix}\n{message}")

        def succeeded(_result):
            if generation != self._generation:
                return
            self.setProperty("tone", "accent")
            repolish(self)
            if queue:
                index, total = queue
                self.show_progress(total, index)
                self.set_text(f"{prefix} • ответ получен, проверка результата • завершено {index} из {total} месяцев")
            else:
                self.hide_progress()
                self.set_text(prefix + " • ответ получен, проверка результата")

        if queue:
            index, total = queue
            self.set_text(f"{prefix} • {index + 1} из {total} месяцев")
            self.show_progress(total, index)
        worker.progressed.connect(progress)
        worker.failed.connect(failed)
        worker.succeeded.connect(succeeded)
