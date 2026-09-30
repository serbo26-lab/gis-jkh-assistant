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
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway

from .houses_page import HousesPage
from .styles import COMPACT_WINDOW_STYLESHEET, NARROW_WINDOW_STYLESHEET
from .work_lists_page import WorkListsPage


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        self.integration_gateway = LocalIntegrationGateway()
        self.integration_gateway.start()
        self.setWindowTitle("GIS ЖКХ Assistant")
        self.resize(1360, 820)
        self.setMinimumSize(720, 520)
        self._view_mode = "normal"

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        self.sidebar = sidebar
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
        self.work_lists_page = WorkListsPage(repository, self.integration_gateway)
        self.pages.addWidget(self.houses_page)
        self.pages.addWidget(self.work_lists_page)
        for title in ("Планирование", "Выполнение", "Настройки"):
            self.pages.addWidget(_placeholder(title))
        layout.addWidget(self.pages, 1)

        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.navigation.setCurrentRow(0)

    def _navigation_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 1:
            self.work_lists_page.refresh_organizations()

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        height = event.size().height()

        if width < 980 or height < 600:
            mode = "narrow"
        elif width < 1180 or height < 720:
            mode = "compact"
        else:
            mode = "normal"

        if mode != self._view_mode:
            self._view_mode = mode

            if mode == "normal":
                self.sidebar.setFixedWidth(220)
                self.setStyleSheet("")
            elif mode == "compact":
                self.sidebar.setFixedWidth(180)
                self.setStyleSheet(COMPACT_WINDOW_STYLESHEET)
            else:
                self.sidebar.setFixedWidth(155)
                self.setStyleSheet(NARROW_WINDOW_STYLESHEET)

            self.houses_page.set_compact_mode(mode != "normal")
            self.work_lists_page.set_view_mode(mode)

        super().resizeEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.houses_page.flush_pending_changes()
        self.work_lists_page.flush_pending_changes()
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
