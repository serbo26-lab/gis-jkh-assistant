"""Main desktop window."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
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
from gis_jkh_assistant.quality_assessment import helper_supports_quality_assessment

from .fixation_page import FixationPage
from .houses_page import HousesPage
from .planning_page import PlanningPage
from .quality_page import QualityAssessmentPage
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
        for title in (
            "Дома",
            "Перечни работ и услуг",
            "Планирование",
            "Фиксация работ и услуг",
            "Оценка качества",
            "Настройки",
        ):
            self.navigation.addItem(QListWidgetItem(title))
        sidebar_layout.addWidget(self.navigation, 1)

        self.integration_card = QFrame()
        self.integration_card.setObjectName("SidebarStatusCard")
        integration_layout = QVBoxLayout(self.integration_card)
        integration_layout.setContentsMargins(10, 9, 10, 9)
        integration_layout.setSpacing(4)

        integration_title = QLabel("Связь с ГИС ЖКХ")
        integration_title.setObjectName("SidebarStatusTitle")
        integration_layout.addWidget(integration_title)

        self.integration_status = QLabel("Проверка подключения…")
        self.integration_status.setObjectName("SidebarStatusText")
        self.integration_status.setWordWrap(True)
        integration_layout.addWidget(self.integration_status)

        sidebar_layout.addWidget(self.integration_card)
        layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.houses_page = HousesPage(repository)
        self.work_lists_page = WorkListsPage(repository, self.integration_gateway)
        self.planning_page = PlanningPage(repository, self.integration_gateway)
        self.fixation_page = FixationPage(repository, self.integration_gateway)
        self.quality_page = QualityAssessmentPage(repository, self.integration_gateway)
        self.pages.addWidget(self.houses_page)
        self.pages.addWidget(self.work_lists_page)
        self.pages.addWidget(self.planning_page)
        self.pages.addWidget(self.fixation_page)
        self.pages.addWidget(self.quality_page)
        self.pages.addWidget(_placeholder("Настройки"))
        layout.addWidget(self.pages, 1)

        self.navigation.currentRowChanged.connect(self._navigation_changed)

        self.integration_status_timer = QTimer(self)
        self.integration_status_timer.setInterval(1500)
        self.integration_status_timer.timeout.connect(self._refresh_integration_status)
        self._refresh_integration_status()
        self.integration_status_timer.start()

        self.navigation.setCurrentRow(0)

    def _refresh_integration_status(self) -> None:
        status = self.integration_gateway.status()
        if not status.running:
            text = "Локальный шлюз не запущен"
            if status.error:
                text += f"\n{status.error}"
            color = "#b42318"
        elif not status.helper_connected:
            text = "Помощник не подключен"
            color = "#8a5b00"
        elif not status.gis_page_ready:
            version = status.helper_version or "?"
            text = f"Помощник {version} подключен\nВкладка ГИС ЖКХ не открыта"
            color = "#8a5b00"
        else:
            version = status.helper_version or "?"
            if not helper_supports_quality_assessment(status.helper_version):
                text = (
                    f"ГИС ЖКХ подключен\nПомощник {version} устарел\n"
                    "Требуется 0.1.14+"
                )
                color = "#8a5b00"
            else:
                text = f"ГИС ЖКХ подключен\nПомощник {version}"
                if status.session_keep_alive_error:
                    text += "\nСессия: ошибка продления"
                    color = "#8a5b00"
                elif status.session_keep_alive_enabled:
                    text += "\nСессия: автопродление вкл."
                    color = "#107c10"
                else:
                    text += "\nСессия: активна"
                    color = "#107c10"

        self.integration_status.setText(text)
        self.integration_status.setStyleSheet(f"color: {color};")

        self.work_lists_page._refresh_integration_status()
        self.planning_page._refresh_integration_status()
        self.fixation_page._refresh_integration_status()
        self.quality_page._refresh_integration_status()

    def _navigation_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 1:
            self.work_lists_page.refresh_organizations()
        elif index == 2:
            self.planning_page.refresh_organizations()
        elif index == 3:
            self.fixation_page.refresh_organizations()
        elif index == 4:
            self.quality_page.refresh_organizations()

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
            self.planning_page.set_view_mode(mode)
            self.fixation_page.set_view_mode(mode)
            self.quality_page.set_view_mode(mode)

        super().resizeEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.houses_page.flush_pending_changes()
        self.work_lists_page.flush_pending_changes()
        self.planning_page.flush_pending_changes()
        self.fixation_page.flush_pending_changes()
        self.quality_page.flush_pending_changes()
        self.integration_gateway.stop()
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
