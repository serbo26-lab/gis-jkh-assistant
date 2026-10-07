"""Settings for the desktop workspace; changes are saved immediately."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QLabel, QRadioButton, QWidget

from gis_jkh_assistant.database import Repository

from .page_layouts import page_root
from .workflow_widgets import SectionCard

_SHARED_CONTEXT_KEY = "ui.shared_context_enabled"
_THEME_KEY = "ui.theme"


class SettingsPage(QWidget):
    sharedContextChanged = Signal(bool)
    themeChanged = Signal(str)

    def __init__(self, repository: Repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository

        root = page_root(self, "Настройки", "Рабочий контекст, оформление и связь с ГИС ЖКХ")
        self.save_status.setText("Настройки сохраняются автоматически")
        context = SectionCard("Выбор между разделами")
        hint = QLabel(
            "Управляющая организация → дом → перечень → год составляют рабочий контекст. "
            "Общий выбор позволяет продолжать работу с тем же объектом в следующем разделе."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        context.body.addWidget(hint)
        self.shared_radio = QRadioButton("Общий выбор")
        self.independent_radio = QRadioButton("Независимый выбор в каждом разделе")
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.shared_radio)
        group.addButton(self.independent_radio)
        context.body.addWidget(self.shared_radio)
        context.body.addWidget(self.independent_radio)
        self.context_explanation = QLabel(
            "При общем выборе переход между разделами сохраняет выбранные дом, перечень и год. "
            "Если перечень ещё не размещён, планирование и месячные разделы объяснят, "
            "какой этап нужно завершить. Другой перечень автоматически выбран не будет."
        )
        self.context_explanation.setWordWrap(True)
        self.context_explanation.setObjectName("Muted")
        context.body.addWidget(self.context_explanation)
        enabled = self.repository.get_setting(_SHARED_CONTEXT_KEY) == "1"
        self.shared_radio.setChecked(enabled)
        self.independent_radio.setChecked(not enabled)
        self.shared_radio.toggled.connect(self._shared_toggled)
        root.addWidget(context)

        appearance = SectionCard("Оформление")
        self.light_radio = QRadioButton("Светлая тема")
        self.dark_radio = QRadioButton("Тёмная тема")
        themes = QButtonGroup(self)
        themes.setExclusive(True)
        themes.addButton(self.light_radio)
        themes.addButton(self.dark_radio)
        appearance.body.addWidget(self.light_radio)
        appearance.body.addWidget(self.dark_radio)
        self.dark_radio.setChecked(self.theme_name() == "dark")
        self.light_radio.setChecked(self.theme_name() == "light")
        self.light_radio.toggled.connect(lambda checked: self._theme_toggled("light", checked))
        self.dark_radio.toggled.connect(lambda checked: self._theme_toggled("dark", checked))
        root.addWidget(appearance)

        connection = SectionCard("Связь с ГИС ЖКХ")
        self.connection_status = QLabel("Проверка подключения…")
        self.connection_status.setWordWrap(True)
        connection.body.addWidget(self.connection_status)
        explanation = QLabel(
            "Для получения и размещения данных откройте ГИС ЖКХ в браузере, "
            "войдите в свою учётную запись и включите браузерный помощник. "
            "Состояние подключения видно в левой панели. "
            "Локальные данные можно редактировать и без подключения; "
            "изменения сохраняются автоматически."
        )
        explanation.setObjectName("Muted")
        explanation.setWordWrap(True)
        connection.body.addWidget(explanation)
        root.addWidget(connection)
        root.addStretch(1)

    def shared_context_enabled(self) -> bool:
        return self.shared_radio.isChecked()

    def theme_name(self) -> str:
        return "dark" if self.repository.get_setting(_THEME_KEY) == "dark" else "light"

    def _shared_toggled(self, checked: bool) -> None:
        self.repository.set_setting(_SHARED_CONTEXT_KEY, "1" if checked else "0")
        self.sharedContextChanged.emit(bool(checked))

    def _theme_toggled(self, theme: str, checked: bool) -> None:
        if checked:
            self.repository.set_setting(_THEME_KEY, theme)
            self.themeChanged.emit(theme)

    def set_connection_status(self, text: str) -> None:
        self.connection_status.setText(text)
