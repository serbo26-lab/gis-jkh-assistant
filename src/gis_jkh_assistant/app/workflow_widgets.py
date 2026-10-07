"""Small desktop components for context, lifecycle and progressive disclosure."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .icons import icon
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
        layout.addLayout(text, 1)
        self.status = QLabel("Автосохранение")
        self.status.setToolTip("Локальные изменения сохраняются автоматически")
        self.status.setObjectName("SaveStatus")
        self.status.setWordWrap(True)
        self.status.setMaximumWidth(240)
        layout.addWidget(self.status)


class ContextBar(QFrame):
    """A compact responsive context; field order is consistent across pages."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ContextBar")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(12, 9, 12, 9)
        self.grid.setSpacing(8)
        self.fields: list[QWidget] = []
        self._columns = 2

    def add_field(self, label: str, control: QWidget) -> None:
        field = QWidget(self)
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        caption = QLabel(label)
        caption.setObjectName("ContextCaption")
        layout.addWidget(caption)
        control.setMinimumWidth(0)
        if isinstance(control, QComboBox):
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(8)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            control.currentTextChanged.connect(control.setToolTip)
        control.setAccessibleName(label)
        layout.addWidget(control)
        self.fields.append(field)
        self._relayout()

    def _relayout(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        for i, field in enumerate(self.fields):
            self.grid.addWidget(field, i // self._columns, i % self._columns)
        for column in range(4):
            weight = (1 if column == 3 else 3) if self._columns == 4 else 1
            self.grid.setColumnStretch(column, weight if column < self._columns else 0)

    def resizeEvent(self, event):  # noqa: N802
        width = event.size().width()
        columns = 4 if len(self.fields) == 4 and width >= 960 else 1 if width < 620 else 2
        if columns != self._columns:
            self._columns = columns
            self._relayout()
        super().resizeEvent(event)


class SectionCard(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 10, 12, 12)
        self.body.setSpacing(8)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        if title:
            label = QLabel(title)
            label.setObjectName("SectionTitle")
            self.actions.addWidget(label)
        self.actions.addStretch(1)
        self.body.addLayout(self.actions)


class ActionMenuButton(QPushButton):
    """Menu entries observe existing commands, keeping one execution path."""
    def __init__(self, text: str, name: str = "more", parent=None):
        super().__init__(text, parent)
        self.setIcon(icon(name))
        self.menu_model = QMenu(self)
        self.setMenu(self.menu_model)
        self.entries: list[tuple[object, QPushButton, Callable[[], bool] | None]] = []
        self.menu_model.aboutToShow.connect(self.sync)

    def add_command(self, button: QPushButton, *, when=None, text: str | None = None):
        action = self.menu_model.addAction(text or button.text())
        action.setProperty("followCommandText", text is None)
        action.triggered.connect(button.click)
        self.entries.append((action, button, when))
        return action

    def sync(self) -> None:
        for action, button, when in self.entries:
            if action.property("followCommandText"):
                action.setText(button.text())
            action.setEnabled(button.isEnabled())
            action.setVisible(when is None or bool(when()))
            action.setToolTip(button.toolTip())


class DangerZone(ActionMenuButton):
    def __init__(self, text: str = "Опасные действия", parent=None):
        super().__init__(text, "danger", parent)
        self.setObjectName("DangerButton")
        self.setToolTip("Команды удаления и отмены. Перед выполнением потребуется подтверждение.")


class WorkflowPanel(QFrame):
    """Lifecycle plus one recommended action, with an inline blocking reason."""
    navigateRequested = Signal(int)

    def __init__(self, steps: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.setObjectName("WorkflowPanel")
        self._key: str | None = None
        self.commands: dict[str, QPushButton | Callable] = {}
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 10, 12, 10)
        self.body.setSpacing(8)
        self.steps: list[QLabel] = []
        step_row = QHBoxLayout()
        step_row.setSpacing(6)
        for index, title in enumerate(steps):
            label = QLabel(f"{index + 1}  {title}")
            label.setObjectName("WorkflowStep")
            label.setProperty("stepTitle", title)
            label.setWordWrap(True)
            step_row.addWidget(label, 1)
            self.steps.append(label)
        self.body.addLayout(step_row)
        row = QHBoxLayout()
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("SectionTitle")
        self.title.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.detail)
        row.addLayout(text, 1)
        self.primary = QPushButton()
        self.primary.setObjectName("PrimaryButton")
        self.primary.setMinimumWidth(160)
        self.primary.setMaximumWidth(290)
        self.primary.clicked.connect(self._execute)
        row.addWidget(self.primary)
        self.body.addLayout(row)
        self.reason = QLabel()
        self.reason.setObjectName("BlockingReason")
        self.reason.setWordWrap(True)
        self.body.addWidget(self.reason)

    def register(self, key: str, command) -> None:
        self.commands[key] = command

    def show_step(self, active: int, title: str, detail: str, key: str | None,
                  text: str, *, reason: str = "", completed: bool = False,
                  done: set[int] | None = None) -> None:
        self._key = key
        for index, label in enumerate(self.steps):
            is_done = (index < active if done is None else index in done) or completed
            state = "done" if is_done else "current" if index == active else "next"
            label.setProperty("stage", state)
            prefix = "✓" if state == "done" else str(index + 1)
            label.setText(f"{prefix}  {label.property('stepTitle')}")
            repolish(label)
        self.title.setText(title)
        self.detail.setText(detail)
        self.primary.setText(text)
        command = self.commands.get(key)
        enabled = command is not None and (not isinstance(command, QPushButton) or command.isEnabled())
        self.primary.setEnabled(enabled and not reason)
        self.primary.setToolTip(reason or detail)
        glyph = {"check": "quality", "fill": "planning", "form": "quality", "acts": "act",
                 "review_acts": "act", "resume": "sync", "sync": "sync", "new": "add",
                 "create": "add", "organization": "organization", "add": "add",
                 "context": "lists", "fixation": "fixation", "missing": "next", "plan": "planning"}.get(key, "upload")
        self.primary.setIcon(icon("next" if key and key.startswith("next") else glyph, "#ffffff"))
        self.primary.setAccessibleName(text)
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))

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
            self.setProperty("tone", "success")
            repolish(self)
            if queue:
                index, total = queue
                self.show_progress(total, index + 1)
                self.set_text(f"{prefix} • обработано {index + 1} из {total} месяцев")
            else:
                self.hide_progress()
                self.set_text(prefix + " • завершено")

        if queue:
            index, total = queue
            self.set_text(f"{prefix} • {index + 1} из {total} месяцев")
            self.show_progress(total, index)
        worker.progressed.connect(progress)
        worker.failed.connect(failed)
        worker.succeeded.connect(succeeded)
