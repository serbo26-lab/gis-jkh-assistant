"""Main desktop window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository

from .houses_page import HousesPage


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        self.setWindowTitle("GIS ЖКХ Assistant")
        self.resize(1360, 820)
        self.setMinimumSize(980, 640)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 16)
        app_name = QLabel("GIS ЖКХ Assistant")
        app_name.setObjectName("SectionTitle")
        sidebar_layout.addWidget(app_name)

        self.navigation = QListWidget()
        self.navigation.setFrameShape(QFrame.NoFrame)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for title in ("Дома", "Перечни работ и услуг", "Планирование", "Выполнение", "Настройки"):
            self.navigation.addItem(QListWidgetItem(title))
        sidebar_layout.addWidget(self.navigation, 1)
        layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.houses_page = HousesPage(repository)
        self.pages.addWidget(self.houses_page)
        for title in ("Перечни работ и услуг", "Планирование", "Выполнение", "Настройки"):
            self.pages.addWidget(_placeholder(title))
        layout.addWidget(self.pages, 1)

        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.houses_page.flush_pending_changes()
        self.repository.close()
        super().closeEvent(event)


def _placeholder(title: str) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(24, 20, 24, 24)
    heading = QLabel(title)
    heading.setObjectName("Title")
    layout.addWidget(heading)
    text = QLabel("Раздел будет добавлен на следующих этапах разработки.")
    text.setObjectName("Muted")
    layout.addWidget(text)
    layout.addStretch(1)
    return widget
