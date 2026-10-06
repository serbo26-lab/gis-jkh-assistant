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
from .settings_page import SettingsPage
from .styles import COMPACT_WINDOW_STYLESHEET, NARROW_WINDOW_STYLESHEET
from .work_lists_page import WorkListsPage


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        self.integration_gateway = LocalIntegrationGateway()
        self.integration_gateway.start()
        self.setWindowTitle("ГИС ЖКХ Ассистент")
        self.resize(1360, 820)
        self.setMinimumSize(720, 520)
        self._view_mode = "normal"
        self._applying_shared_context = False
        self._shared_context = {
            "organization_id": self.repository.get_setting("ui.context.organization_id"),
            "house_id": self.repository.get_setting("ui.context.house_id"),
            "work_list_id": self.repository.get_setting("ui.context.work_list_id"),
            "year": self.repository.get_setting("ui.context.year"),
        }

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
        app_name = QLabel("ГИС ЖКХ Ассистент")
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
        self.houses_page = HousesPage(repository, self.integration_gateway)
        self.work_lists_page = WorkListsPage(repository, self.integration_gateway)
        self.planning_page = PlanningPage(repository, self.integration_gateway)
        self.fixation_page = FixationPage(repository, self.integration_gateway)
        self.quality_page = QualityAssessmentPage(repository, self.integration_gateway)
        self.settings_page = SettingsPage(repository)
        self.pages.addWidget(self.houses_page)
        self.pages.addWidget(self.work_lists_page)
        self.pages.addWidget(self.planning_page)
        self.pages.addWidget(self.fixation_page)
        self.pages.addWidget(self.quality_page)
        self.pages.addWidget(self.settings_page)
        layout.addWidget(self.pages, 1)

        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.settings_page.sharedContextChanged.connect(
            self._shared_context_mode_changed
        )
        self._connect_shared_context_sources()

        self.integration_status_timer = QTimer(self)
        self.integration_status_timer.setInterval(1500)
        self.integration_status_timer.timeout.connect(self._refresh_integration_status)
        self._refresh_integration_status()
        self.integration_status_timer.start()

        self.navigation.setCurrentRow(0)
        if self.settings_page.shared_context_enabled():
            if not self._shared_context.get("house_id"):
                self._capture_shared_context("houses", "house")
            self._apply_shared_context()

    def _connect_shared_context_sources(self) -> None:
        self.houses_page.organization_combo.currentIndexChanged.connect(
            lambda *_: self._capture_shared_context("houses", "organization")
        )
        self.houses_page.house_list.currentItemChanged.connect(
            lambda *_: self._capture_shared_context("houses", "house")
        )

        self.work_lists_page.organization_combo.currentIndexChanged.connect(
            lambda *_: self._capture_shared_context("work_lists", "organization")
        )
        self.work_lists_page.house_combo.currentIndexChanged.connect(
            lambda *_: self._capture_shared_context("work_lists", "house")
        )
        self.work_lists_page.work_lists.currentItemChanged.connect(
            lambda *_: self._capture_shared_context("work_lists", "work_list")
        )
        self.work_lists_page.year_spin.valueChanged.connect(
            lambda *_: self._capture_shared_context("work_lists", "year")
        )

        for name, page in (
            ("planning", self.planning_page),
            ("fixation", self.fixation_page),
            ("quality", self.quality_page),
        ):
            page.organization_combo.currentIndexChanged.connect(
                lambda *_, name=name: self._capture_shared_context(
                    name, "organization"
                )
            )
            page.house_combo.currentIndexChanged.connect(
                lambda *_, name=name: self._capture_shared_context(name, "house")
            )
            page.work_list_combo.currentIndexChanged.connect(
                lambda *_, name=name: self._capture_shared_context(
                    name, "work_list"
                )
            )
            page.year_combo.currentIndexChanged.connect(
                lambda *_, name=name: self._capture_shared_context(name, "year")
            )

    def _page_by_context_name(self, name: str):
        return {
            "houses": self.houses_page,
            "work_lists": self.work_lists_page,
            "planning": self.planning_page,
            "fixation": self.fixation_page,
            "quality": self.quality_page,
        }[name]

    def _context_values(self, name: str) -> dict[str, object | None]:
        page = self._page_by_context_name(name)
        if name == "houses":
            current_item = page.house_list.currentItem()
            return {
                "organization_id": page.organization_combo.currentData(),
                "house_id": (
                    current_item.data(Qt.ItemDataRole.UserRole)
                    if current_item
                    else None
                ),
                "work_list_id": None,
                "year": None,
            }
        if name == "work_lists":
            current_item = page.work_lists.currentItem()
            return {
                "organization_id": page.organization_combo.currentData(),
                "house_id": page.house_combo.currentData(),
                "work_list_id": (
                    current_item.data(Qt.ItemDataRole.UserRole)
                    if current_item
                    else None
                ),
                "year": page.year_spin.value(),
            }
        return {
            "organization_id": page.organization_combo.currentData(),
            "house_id": page.house_combo.currentData(),
            "work_list_id": page.work_list_combo.currentData(),
            "year": page.year_combo.currentData(),
        }

    def _store_shared_context(self) -> None:
        for field in ("organization_id", "house_id", "work_list_id", "year"):
            value = self._shared_context.get(field)
            self.repository.set_setting(
                f"ui.context.{field}",
                None if value in (None, "") else str(value),
            )

    def _capture_shared_context(self, source: str, kind: str) -> None:
        if (
            self._applying_shared_context
            or not self.settings_page.shared_context_enabled()
        ):
            return

        values = self._context_values(source)
        if kind == "organization":
            self._shared_context["organization_id"] = values["organization_id"]
            self._shared_context["house_id"] = values["house_id"]
            self._shared_context["work_list_id"] = None
        elif kind == "house":
            self._shared_context["organization_id"] = values["organization_id"]
            self._shared_context["house_id"] = values["house_id"]
            self._shared_context["work_list_id"] = None
        elif kind == "work_list":
            self._shared_context["organization_id"] = values["organization_id"]
            self._shared_context["house_id"] = values["house_id"]
            self._shared_context["work_list_id"] = values["work_list_id"]
            if values["year"] is not None:
                self._shared_context["year"] = int(values["year"])
        elif kind == "year" and values["year"] is not None:
            self._shared_context["year"] = int(values["year"])

        self._store_shared_context()
        self._apply_shared_context(exclude=source)

    def _shared_context_mode_changed(self, enabled: bool) -> None:
        if not enabled:
            return
        current_index = self.pages.currentIndex()
        source = {
            0: "houses",
            1: "work_lists",
            2: "planning",
            3: "fixation",
            4: "quality",
        }.get(current_index, "houses")
        values = self._context_values(source)
        self._shared_context["organization_id"] = values["organization_id"]
        self._shared_context["house_id"] = values["house_id"]
        self._shared_context["work_list_id"] = values["work_list_id"]
        if values["year"] is not None:
            self._shared_context["year"] = int(values["year"])
        self._store_shared_context()
        self._apply_shared_context()

    @staticmethod
    def _set_combo_data(combo, value) -> bool:
        index = combo.findData(value)
        combo.blockSignals(True)
        combo.setCurrentIndex(index if index >= 0 else -1)
        combo.blockSignals(False)
        return index >= 0

    def _apply_shared_context(self, exclude: str | None = None) -> None:
        if (
            self._applying_shared_context
            or not self.settings_page.shared_context_enabled()
        ):
            return
        self._applying_shared_context = True
        try:
            for name in ("houses", "work_lists", "planning", "fixation", "quality"):
                if name == exclude:
                    continue
                self._apply_shared_context_to_page(name)
        finally:
            self._applying_shared_context = False

    def _apply_shared_context_to_page(self, name: str) -> None:
        page = self._page_by_context_name(name)
        organization_id = self._shared_context.get("organization_id")
        house_id = self._shared_context.get("house_id")
        work_list_id = self._shared_context.get("work_list_id")
        year = self._shared_context.get("year")
        if year not in (None, ""):
            year = int(year)

        if name == "houses":
            if organization_id is not None:
                self._set_combo_data(page.organization_combo, organization_id)
                page._organization_changed()
            if house_id is None:
                page.house_list.blockSignals(True)
                page.house_list.setCurrentRow(-1)
                page.house_list.blockSignals(False)
                page._load_house(None)
                return
            for row in range(page.house_list.count()):
                item = page.house_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == house_id:
                    page.house_list.setCurrentItem(item)
                    return
            page.house_list.blockSignals(True)
            page.house_list.setCurrentRow(-1)
            page.house_list.blockSignals(False)
            page._load_house(None)
            return

        if organization_id is not None:
            self._set_combo_data(page.organization_combo, organization_id)
            page._organization_changed()

        if house_id is not None:
            self._set_combo_data(page.house_combo, house_id)
            page._house_changed()
        else:
            page.house_combo.blockSignals(True)
            page.house_combo.setCurrentIndex(-1)
            page.house_combo.blockSignals(False)
            page._house_changed()

        if name == "work_lists":
            if year is not None:
                page.year_spin.blockSignals(True)
                page.year_spin.setValue(year)
                page.year_spin.blockSignals(False)

            selected = False
            if work_list_id is not None:
                for row in range(page.work_lists.count()):
                    item = page.work_lists.item(row)
                    if item.data(Qt.ItemDataRole.UserRole) == work_list_id:
                        page.work_lists.setCurrentItem(item)
                        selected = True
                        break
            if not selected:
                page.work_lists.blockSignals(True)
                page.work_lists.setCurrentRow(-1)
                page.work_lists.blockSignals(False)
                page._load_work_list(None)
            return

        if work_list_id is not None:
            found = self._set_combo_data(page.work_list_combo, work_list_id)
        else:
            page.work_list_combo.blockSignals(True)
            page.work_list_combo.setCurrentIndex(-1)
            page.work_list_combo.blockSignals(False)
            found = False
        page._work_list_changed()

        if year is not None and found:
            self._set_combo_data(page.year_combo, year)
            page._year_changed()

    def _refresh_integration_status(self) -> None:
        status = self.integration_gateway.status()
        if not status.running:
            text = "Локальный шлюз не запущен"
            if status.error:
                text += f"\n{status.error}"
            color = "#b42318"
        elif not status.helper_connected:
            text = "ГИС ЖКХ помощник не подключен"
            color = "#8a5b00"
        elif not status.gis_page_ready:
            version = status.helper_version or "?"
            text = f"ГИС ЖКХ помощник {version} подключен\nВкладка ГИС ЖКХ не открыта"
            color = "#8a5b00"
        else:
            version = status.helper_version or "?"
            if not helper_supports_quality_assessment(status.helper_version):
                text = (
                    f"ГИС ЖКХ подключен\nГИС ЖКХ помощник {version} устарел\n"
                    "Требуется 0.1.17+"
                )
                color = "#8a5b00"
            else:
                text = f"ГИС ЖКХ подключен\nГИС ЖКХ помощник {version}"
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
