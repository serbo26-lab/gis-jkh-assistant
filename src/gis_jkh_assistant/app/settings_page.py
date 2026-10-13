"""Settings for the desktop workspace; changes are saved immediately."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QWidget

from gis_jkh_assistant.database import Repository

from .page_layouts import page_root
from .error_messages import error_text
from .workflow_widgets import SectionCard

_SHARED_CONTEXT_KEY = "ui.shared_context_enabled"
_THEME_KEY = "ui.theme"
_NEXT_STEP_PANEL_KEY = "ui.next_step_panel_enabled"


class SettingsPage(QWidget):
    sharedContextChanged = Signal(bool)
    themeChanged = Signal(str)
    scaleChanged = Signal(int)
    nextStepPanelChanged = Signal(bool)
    helperSetupRequested = Signal()

    def __init__(self, repository: Repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository

        root = page_root(self, "Настройки", "Рабочий контекст, оформление и связь с ГИС ЖКХ")
        self.save_status.setText("Настройки сохраняются сразу")
        context = SectionCard("Рабочий контекст")
        hint = QLabel(
            "Общий выбор сохраняет УО, дом, перечень и год при переходах между разделами."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        context.setToolTip(hint.text())
        self.shared_radio = QRadioButton("Общий выбор")
        self.independent_radio = QRadioButton("Независимый выбор в каждом разделе")
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.shared_radio)
        group.addButton(self.independent_radio)
        context.body.addWidget(self.shared_radio)
        context.body.addWidget(self.independent_radio)
        self.context_explanation = QLabel(
            "Общий выбор сохраняет объект при переходах. Независимый — запоминает выбор каждого раздела отдельно."
        )
        self.context_explanation.setWordWrap(True)
        self.context_explanation.setObjectName("Muted")
        context.body.addWidget(self.context_explanation)
        enabled = self.repository.get_setting(_SHARED_CONTEXT_KEY) == "1"
        self.shared_radio.setChecked(enabled)
        self.independent_radio.setChecked(not enabled)
        self.shared_radio.toggled.connect(self._shared_toggled)
        root.addWidget(context)
        context.setMaximumWidth(900)

        appearance = SectionCard("Оформление")
        self.light_radio = QRadioButton("Светлая тема")
        self.dark_radio = QRadioButton("Тёмная тема")
        themes = QButtonGroup(self)
        themes.setExclusive(True)
        themes.addButton(self.light_radio)
        themes.addButton(self.dark_radio)
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.light_radio)
        theme_row.addWidget(self.dark_radio)
        theme_row.addStretch(1)
        appearance.body.addLayout(theme_row)
        self.dark_radio.setChecked(self.theme_name() == "dark")
        self.light_radio.setChecked(self.theme_name() == "light")
        self.light_radio.toggled.connect(lambda checked: self._theme_toggled("light", checked))
        self.dark_radio.toggled.connect(lambda checked: self._theme_toggled("dark", checked))
        # Keep the handle for older integrations, but expose no enlargement UI.
        # Native Windows DPI remains Qt's responsibility.
        self.scale_combo = QComboBox(self)
        self.scale_combo.setObjectName("InterfaceScale")
        self.scale_combo.addItem("100% — Обычный", 100)
        self.scale_combo.setEnabled(False)
        self.scale_combo.hide()
        root.addWidget(appearance)
        appearance.setMaximumWidth(900)

        guidance = SectionCard("Панель следующего шага")
        self.next_step_panel_checkbox = QCheckBox()
        self.next_step_panel_checkbox.setAccessibleName("Панель следующего шага")
        self.next_step_panel_checkbox.setChecked(self.next_step_panel_enabled())
        self._update_next_step_panel_label()
        self.next_step_panel_checkbox.toggled.connect(self._next_step_panel_toggled)
        guidance.body.addWidget(self.next_step_panel_checkbox)
        guidance_hint = QLabel(
            "Подсказывает следующий шаг во всех рабочих разделах. "
            "При выключении панель и её подсказки скрываются; рабочие команды остаются доступны."
        )
        guidance_hint.setObjectName("Muted")
        guidance_hint.setWordWrap(True)
        guidance.body.addWidget(guidance_hint)
        root.addWidget(guidance)
        guidance.setMaximumWidth(900)

        connection = SectionCard("Связь с ГИС ЖКХ")
        self.connection_status = QLabel("Проверка подключения…")
        self.connection_status.setWordWrap(True)
        connection.body.addWidget(self.connection_status)
        explanation = QLabel(
            "Откройте ГИС ЖКХ в браузере, войдите и включите помощник. "
            "Локальная работа доступна без подключения."
        )
        explanation.setObjectName("Muted")
        explanation.setWordWrap(True)
        connection.body.addWidget(explanation)
        self.recover_connection_button = QPushButton("Как восстановить подключение…")
        self.recover_connection_button.setCheckable(True)
        self.recover_connection_button.setAccessibleDescription(
            "Показывает действия для восстановления связи без повторной установки"
        )
        self.recovery_steps = QLabel(
            "1. Откройте ГИС ЖКХ в браузере и войдите в личный кабинет. "
            "2. Проверьте, что помощник включён именно в этом профиле браузера. "
            "3. Если соединение не появилось, перезапустите приложение и "
            "обновите состояние связи. Устанавливать помощник повторно "
            "не нужно, если он уже добавлен и включён."
        )
        self.recovery_steps.setObjectName("Muted")
        self.recovery_steps.setWordWrap(True)
        self.recovery_steps.hide()
        self.recover_connection_button.toggled.connect(self.recovery_steps.setVisible)
        connection.body.addWidget(self.recover_connection_button)
        connection.body.addWidget(self.recovery_steps)

        self.helper_setup_button = QPushButton("Установить / настроить помощник…")
        self.helper_setup_button.setAccessibleName("Настроить браузерный помощник")
        self.helper_setup_button.clicked.connect(self.helperSetupRequested.emit)
        connection.body.addWidget(self.helper_setup_button, 0)
        root.addWidget(connection)
        connection.setMaximumWidth(900)
        root.addStretch(1)

    def shared_context_enabled(self) -> bool:
        return self.shared_radio.isChecked()

    def theme_name(self) -> str:
        return "dark" if self.repository.get_setting(_THEME_KEY) == "dark" else "light"

    def scale_percent(self) -> int:
        return 100

    def next_step_panel_enabled(self) -> bool:
        return self.repository.get_setting(_NEXT_STEP_PANEL_KEY) != "0"

    def _update_next_step_panel_label(self) -> None:
        self.next_step_panel_checkbox.setText(
            "Включена" if self.next_step_panel_checkbox.isChecked() else "Выключена"
        )

    def _next_step_panel_toggled(self, checked: bool) -> None:
        previous = self.next_step_panel_enabled()
        if not self._save_setting(
            _NEXT_STEP_PANEL_KEY, "1" if checked else "0", "Панель следующего шага",
        ):
            self.next_step_panel_checkbox.blockSignals(True)
            self.next_step_panel_checkbox.setChecked(previous)
            self.next_step_panel_checkbox.blockSignals(False)
            self._update_next_step_panel_label()
            return
        self._update_next_step_panel_label()
        self.nextStepPanelChanged.emit(checked)

    def _scale_changed(self, _index: int) -> None:
        self.scale_combo.setCurrentIndex(0)

    def _shared_toggled(self, checked: bool) -> None:
        previous = self.repository.get_setting(_SHARED_CONTEXT_KEY) == "1"
        if not self._save_setting(_SHARED_CONTEXT_KEY, "1" if checked else "0", "Рабочий контекст"):
            for control in (self.shared_radio, self.independent_radio):
                control.blockSignals(True)
            self.shared_radio.setChecked(previous)
            self.independent_radio.setChecked(not previous)
            for control in (self.shared_radio, self.independent_radio):
                control.blockSignals(False)
            return
        self.sharedContextChanged.emit(bool(checked))

    def _theme_toggled(self, theme: str, checked: bool) -> None:
        if checked:
            previous = self.theme_name()
            if not self._save_setting(_THEME_KEY, theme, "Тема интерфейса"):
                for control in (self.light_radio, self.dark_radio):
                    control.blockSignals(True)
                self.light_radio.setChecked(previous == "light")
                self.dark_radio.setChecked(previous == "dark")
                for control in (self.light_radio, self.dark_radio):
                    control.blockSignals(False)
                return
            self.themeChanged.emit(theme)

    def _save_setting(self, key: str, value: str, name: str) -> bool:
        try:
            self.repository.set_setting(key, value)
        except (sqlite3.DatabaseError, OSError, ValueError) as exc:
            self.save_status.setText("Локально: ошибка сохранения")
            QMessageBox.warning(self, "Настройка не сохранена", error_text(
                exc, context=name, effect="Сохранена прежняя настройка; изменение не применено.",
            ))
            return False
        self.save_status.setText("Локально: сохранено")
        return True

    def set_connection_status(
        self, text: str, *, helper_connected: bool | None = None,
        gis_page_ready: bool | None = None,
    ) -> None:
        self.connection_status.setText(text)
        if helper_connected is None:
            return
        self.helper_setup_button.setText(
            "Настроить / обновить помощник…" if helper_connected
            else "Установить / настроить помощник…"
        )
        self.recover_connection_button.setText(
            "Как открыть вкладку ГИС ЖКХ…" if helper_connected and not gis_page_ready
            else "Как восстановить подключение…"
        )
        # The troubleshooting entry is distinct from the setup wizard, and
        # does not assert that installing the extension fixes a lost session.
        self.recover_connection_button.setVisible(
            not (helper_connected and gis_page_ready)
        )
        if helper_connected and gis_page_ready:
            self.recover_connection_button.setChecked(False)
