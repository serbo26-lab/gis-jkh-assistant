"""User-facing application settings."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository


_SHARED_CONTEXT_KEY = "ui.shared_context_enabled"


class SettingsPage(QWidget):
    sharedContextChanged = Signal(bool)

    def __init__(self, repository: Repository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        title = QLabel("Настройки")
        title.setObjectName("Title")
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 16)
        card_layout.setSpacing(10)

        section = QLabel("Контекст между разделами")
        section.setObjectName("SectionTitle")
        card_layout.addWidget(section)

        hint = QLabel(
            "Выберите, должны ли дом, размещённый перечень и год сохраняться "
            "общими при переходе между разделами программы."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        card_layout.addWidget(hint)

        self.independent_radio = QRadioButton(
            "Независимый выбор — каждый раздел хранит свой выбор"
        )
        self.shared_radio = QRadioButton(
            "Общий выбор — дом, размещённый перечень и год синхронизируются"
        )

        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.independent_radio)
        group.addButton(self.shared_radio)

        card_layout.addWidget(self.independent_radio)
        card_layout.addWidget(self.shared_radio)

        enabled = self.repository.get_setting(_SHARED_CONTEXT_KEY) == "1"
        self.shared_radio.setChecked(enabled)
        self.independent_radio.setChecked(not enabled)

        self.shared_radio.toggled.connect(self._shared_toggled)

        root.addWidget(card)
        root.addStretch(1)

    def shared_context_enabled(self) -> bool:
        return self.shared_radio.isChecked()

    def _shared_toggled(self, checked: bool) -> None:
        self.repository.set_setting(_SHARED_CONTEXT_KEY, "1" if checked else "0")
        self.sharedContextChanged.emit(bool(checked))
