"""Main desktop window."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
from gis_jkh_assistant.gis_integration.organization_context import (
    MIN_ORGANIZATION_ROUTING_HELPER_VERSION, helper_supports_organization_routing,
)
from gis_jkh_assistant.quality_assessment import (
    MIN_QUALITY_HELPER_VERSION, helper_supports_quality_assessment,
)

from .fixation_page import FixationPage
from .houses_page import HousesPage
from .icons import icon, set_button_icon, set_label_icon
from .management_fees_page import ManagementFeesPage
from .organizations_page import OrganizationsPage
from .planning_page import PlanningPage
from .quality_page import QualityAssessmentPage
from .settings_page import SESSION_CONNECTION_GUIDANCE, SettingsPage
from .sidebar_navigation import SidebarNavigation
from .styles import apply_theme, color
from .ui_scale import apply_layout_scale, scaled
from .window_chrome import apply_window_theme
from .work_lists_page import WorkListsPage
from .workflow_widgets import WorkflowPanel


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        self._ui_scale = 1.0
        app = QApplication.instance()
        if app is not None:
            from .russian_buttons import install
            install(app)
            app.setStyle("Fusion")
            apply_theme(app, repository.get_setting("ui.theme") or "light", self._ui_scale)
        self.integration_gateway = LocalIntegrationGateway()
        self.integration_gateway.start()
        self.setWindowTitle("ГИС ЖКХ Ассистент")
        self.resize(1366, 768)
        self.setMinimumSize(720, 520)
        self._view_mode = "normal"
        self._last_context_page_index = 1
        self._applying_shared_context = False
        self._closing = False
        self._close_requested = False
        self._active_workers = set()
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close)
        self._pending_theme_pages = set()
        self._shared_context = {
            "organization_id": self.repository.get_setting("ui.context.organization_id"),
            "house_id": self.repository.get_setting("ui.context.house_id"),
            "work_list_id": self.repository.get_setting("ui.context.work_list_id"),
            "year": self.repository.get_setting("ui.context.year"),
        }
        self._shared_context_revision = 0
        self._last_context_snapshot = self._normalized_context(self._shared_context)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(228)
        sidebar_layout = QVBoxLayout(sidebar)
        self.sidebar_layout = sidebar_layout
        sidebar_layout.setContentsMargins(12, 16, 12, 16)
        app_name = QLabel("ГИС ЖКХ\nАссистент")
        app_name.setObjectName("SectionTitle")
        brand_row = QHBoxLayout()
        brand_row.addWidget(app_name, 1)
        self.app_logo = QLabel()
        self.app_logo.setAccessibleName("Логотип ГИС ЖКХ Ассистента")
        set_label_icon(self.app_logo, "app-logo", 32)
        brand_row.addWidget(self.app_logo)
        sidebar_layout.addLayout(brand_row)
        self._update_application_icon()

        self.organization_caption = QLabel()
        self.organization_caption.setObjectName("ContextCaption")
        self.organization_caption.setWordWrap(True)
        self.organization_caption.setTextFormat(Qt.TextFormat.PlainText)
        self.organization_caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.organization_caption.setAccessibleName("Выбранная управляющая организация")
        sidebar_layout.addWidget(self.organization_caption)

        groups = (
            (("УО", "organizations", "Организация, руководители и реквизиты актов"),
             ("Дома", "houses", "Данные домов выбранной УО")),
            (("Перечни работ и услуг", "lists", "Перечни работ и услуг: состав и размещение"),
             ("Планирование", "planning", "План работ по размещённому перечню"),
             ("Фиксация работ и услуг", "fixation", "Фиксация выполненных работ и услуг по месяцам"),
             ("Оценка качества", "quality", "Оценка качества, акты и размещение")),
            (("Информация о размере платы", "fees", "Плата по договору управления и основания из ГИС"),),
            (("Настройки", "settings", "Рабочий контекст и оформление"),),
        )
        self.navigation = SidebarNavigation([
            [(title, icon(name, color("muted_text")), name, description)
             for title, name, description in group] for group in groups
        ])
        sidebar_layout.addWidget(self.navigation, 1)

        self.context_card = QFrame()
        self.context_card.setObjectName("SidebarStatusCard")
        self.context_layout = QVBoxLayout(self.context_card)
        self.context_layout.setContentsMargins(10, 9, 10, 9)
        self.context_layout.setSpacing(4)
        context_title = QLabel("Рабочий контекст")
        context_title.setObjectName("SidebarStatusTitle")
        self.context_layout.addWidget(context_title)
        context_title.hide()
        self.context_mode_label = QLabel()
        self.context_mode_label.setObjectName("SidebarStatusText")
        self.context_mode_label.setWordWrap(True)
        self.context_layout.addWidget(self.context_mode_label)
        sidebar_layout.addWidget(self.context_card)

        self.integration_card = QFrame()
        self.integration_card.setObjectName("SidebarStatusCard")
        integration_layout = QVBoxLayout(self.integration_card)
        self.integration_layout = integration_layout
        integration_layout.setContentsMargins(10, 9, 10, 9)
        integration_layout.setSpacing(4)

        integration_title = QLabel("Связь с ГИС ЖКХ")
        integration_title.setObjectName("SidebarStatusTitle")
        integration_layout.addWidget(integration_title)
        integration_title.hide()

        self.integration_status = QLabel("Проверка подключения…")
        self.integration_status.setObjectName("SidebarStatusText")
        self.integration_status.setWordWrap(True)
        connection_row = QHBoxLayout()
        self.integration_icon = QLabel()
        set_label_icon(self.integration_icon, "pulse")
        connection_row.addWidget(self.integration_icon)
        connection_row.addWidget(self.integration_status, 1)
        integration_layout.addLayout(connection_row)
        self.integration_browser = QLabel()
        self.integration_browser.setObjectName("SidebarStatusText")
        self.integration_browser.setWordWrap(True)
        self.integration_browser.hide()
        integration_layout.addWidget(self.integration_browser)

        sidebar_layout.addWidget(self.integration_card)
        layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.houses_page = HousesPage(repository, self.integration_gateway)
        self.organizations_page = OrganizationsPage(repository, self.houses_page, self.integration_gateway)
        self.management_fees_page = ManagementFeesPage(repository, self.integration_gateway)
        self.houses_page.organization_context.hide()
        self.work_lists_page = WorkListsPage(repository, self.integration_gateway)
        self.planning_page = PlanningPage(repository, self.integration_gateway)
        self.fixation_page = FixationPage(repository, self.integration_gateway)
        self.quality_page = QualityAssessmentPage(repository, self.integration_gateway)
        self.settings_page = SettingsPage(repository)
        self.pages.addWidget(self.organizations_page)
        self.pages.addWidget(self.houses_page)
        self.pages.addWidget(self.work_lists_page)
        self.pages.addWidget(self.planning_page)
        self.pages.addWidget(self.fixation_page)
        self.pages.addWidget(self.quality_page)
        self.pages.addWidget(self.management_fees_page)
        self.pages.addWidget(self.settings_page)
        layout.addWidget(self.pages, 1)

        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.settings_page.sharedContextChanged.connect(
            self._shared_context_mode_changed
        )
        self.settings_page.themeChanged.connect(self._theme_changed)
        self.settings_page.scaleChanged.connect(self._scale_changed)
        self.settings_page.nextStepPanelChanged.connect(self._next_step_panel_changed)
        self.settings_page.helperSetupRequested.connect(self._open_helper_setup)
        for name in ("houses", "fees", "work_lists", "planning", "fixation", "quality"):
            page = self._page_by_context_name(name)
            page.context_notice = QLabel()
            page.context_notice.setObjectName("BlockingReason")
            page.context_notice.setWordWrap(True)
            page.context_notice.hide()
            page_layout = getattr(page, "page_layout", page.layout())
            page_layout.insertWidget(1, page.context_notice)
            workflow = getattr(page, "workflow", None)
            if workflow is not None:
                workflow.navigateRequested.connect(
                    lambda index, source=name: self._workflow_navigation_requested(source, index)
                )
        self.organizations_page.workflow.navigateRequested.connect(
            self._navigate_workflow_target)
        self._connect_shared_context_sources()
        self._next_step_panel_changed(self.settings_page.next_step_panel_enabled())

        # Organization selection is global in both tab-context modes. Restore it
        # before the per-house/list/year shared context is applied below.
        selected_org = self._shared_context.get("organization_id")
        if self.organizations_page.organization_combo.findData(selected_org) < 0:
            selected_org = self.organizations_page.organization_combo.currentData()
        saved_house_id = self._shared_context.get("house_id")
        saved_house = repository.get_house(saved_house_id) if saved_house_id else None
        if saved_house is None or saved_house.organization_id != selected_org:
            self._shared_context.update(house_id=None, work_list_id=None)
        self._applying_shared_context = True
        try:
            self._shared_context["organization_id"] = selected_org
            for name in ("houses", "fees", "work_lists", "planning", "fixation", "quality"):
                self._apply_global_organization_to_page(name)
        finally:
            self._applying_shared_context = False
        self._store_shared_context()

        self.integration_status_timer = QTimer(self)
        self.integration_status_timer.setInterval(1500)
        self.integration_status_timer.timeout.connect(self._refresh_integration_status)
        self.integration_status_timer.start()

        if self.settings_page.shared_context_enabled():
            if not self._shared_context.get("house_id"):
                self._capture_shared_context("houses", "house")
            self._apply_shared_context()
        self.navigation.setCurrentRow(0)
        if not repository.get_setting("ui.helper.intro_seen"):
            self.helper_intro = QWidget()
            intro = QHBoxLayout(self.helper_intro)
            intro.setContentsMargins(4, 2, 4, 2)
            label = QLabel("Для обмена с ГИС настройте браузерный помощник. Локальная работа доступна сразу.")
            label.setWordWrap(True)
            label.setObjectName("Muted")
            intro.addWidget(label, 1)
            setup = QPushButton("Настроить помощник…")
            set_button_icon(setup, "browser-helper")
            setup.clicked.connect(self._open_helper_setup)
            intro.addWidget(setup)
            dismiss = QPushButton("Продолжить локально")
            def dismiss_intro():
                repository.set_setting("ui.helper.intro_seen", "1")
                self.helper_intro.hide()
            dismiss.clicked.connect(dismiss_intro)
            intro.addWidget(dismiss)
            self.organizations_page.page_content.layout().insertWidget(1, self.helper_intro)
        self._refresh_integration_status()
        self._update_context_summary()
        self._update_display_layout(force=True)

    def _open_helper_setup(self) -> None:
        from .helper_setup_dialog import HelperSetupDialog

        if (
            self._active_workers or getattr(self, "_result_callbacks", 0)
            or any(self._page_is_busy(page) for page in self._data_pages())
        ):
            QMessageBox.information(
                self, "Выполняется операция",
                "Дождитесь завершения обмена с ГИС перед установкой или обновлением помощника.",
            )
            return
        dialog = getattr(self, "_helper_setup_dialog", None)
        if dialog is None:
            dialog = HelperSetupDialog(self.integration_gateway, self)
            self._helper_setup_dialog = dialog
        elif not dialog.isVisible():
            dialog.reopen()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _connect_shared_context_sources(self) -> None:
        self.management_fees_page.house_combo.currentIndexChanged.connect(
            lambda *_: self._capture_shared_context("fees", "house"))
        self.houses_page.organization_combo.currentIndexChanged.connect(
            lambda *_: self._capture_shared_context("houses", "organization")
        )
        self.houses_page.organization_combo.currentTextChanged.connect(
            self._update_organization_caption
        )
        self.houses_page.house_list.currentItemChanged.connect(
            lambda *_: self._capture_shared_context("houses", "house")
        )

        self.work_lists_page.house_combo.currentIndexChanged.connect(
            lambda *_: self._capture_shared_context("work_lists", "house")
        )
        self.work_lists_page.work_lists.currentItemChanged.connect(
            lambda *_: self._capture_shared_context("work_lists", "work_list")
        )
        self.work_lists_page.contextSelectionChanged.connect(
            self._explicit_registry_selection_changed
        )
        # The registry's year input belongs to creating a new list. Changing it
        # must not replace the current year used by planning/monthly workflows.

        for name, page in (
            ("planning", self.planning_page),
            ("fixation", self.fixation_page),
            ("quality", self.quality_page),
        ):
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
            "fees": self.management_fees_page,
            "work_lists": self.work_lists_page,
            "planning": self.planning_page,
            "fixation": self.fixation_page,
            "quality": self.quality_page,
        }[name]

    def _context_values(self, name: str) -> dict[str, object | None]:
        page = self._page_by_context_name(name)
        if name == "fees":
            return {"organization_id": page.organization_combo.currentData(),
                    "house_id": page.house_combo.currentData(), "work_list_id": None, "year": None}
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
            work_list_id = (
                current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
            )
            work_list = self.repository.get_work_list(str(work_list_id)) if work_list_id else None
            year = work_list.base_year if work_list else None
            shared_year = self._shared_context.get("year")
            if work_list and shared_year not in (None, ""):
                candidate = int(shared_year)
                if work_list.start_year <= candidate <= work_list.end_year:
                    year = candidate
            return {
                "organization_id": page.organization_combo.currentData(),
                "house_id": page.house_combo.currentData(),
                "work_list_id": work_list_id,
                "year": year,
            }
        return {
            "organization_id": page.organization_combo.currentData(),
            "house_id": page.house_combo.currentData(),
            "work_list_id": page.work_list_combo.currentData(),
            "year": page.year_combo.currentData(),
        }

    def _store_shared_context(self) -> None:
        snapshot = self._normalized_context(self._shared_context)
        if snapshot != self._last_context_snapshot:
            self._shared_context_revision += 1
            self._last_context_snapshot = snapshot
        for field in ("organization_id", "house_id", "work_list_id", "year"):
            value = self._shared_context.get(field)
            self.repository.set_setting(
                f"ui.context.{field}",
                None if value in (None, "") else str(value),
            )
        self._update_context_summary()

    @staticmethod
    def _normalized_context(values: dict) -> dict[str, str]:
        return {
            field: str(values.get(field) or "")
            for field in ("organization_id", "house_id", "work_list_id", "year")
        }

    def _observe_context_operation(self, page, worker) -> None:
        """Record the context before asynchronous commands can emit UI changes."""
        self._active_workers.add(worker)
        self.organizations_page.refresh_availability()
        def settled():
            self._active_workers.discard(worker)
            if not self._closing:
                self.organizations_page.refresh_availability()
            if self._close_requested:
                self._close_timer.start(0)
        worker.finished.connect(lambda: QTimer.singleShot(0, settled))
        worker.finished.connect(
            lambda: QTimer.singleShot(0, self._reload_pending_page_themes)
        )
        if page is not self.work_lists_page:
            return
        page._operation_shared_context = dict(self._shared_context)
        page._operation_shared_revision = self._shared_context_revision
        page._operation_source_context = self._context_values("work_lists")
        page._context_operation_worker = worker
        page._pending_context_selection = None
        worker.finished.connect(
            lambda: QTimer.singleShot(0, lambda: self._finish_registry_selection(page, worker))
        )

    def _explicit_registry_selection_changed(self) -> None:
        """A created/explicitly selected list may arrive before its worker settles."""
        if self._applying_shared_context or not self.settings_page.shared_context_enabled():
            return
        page = self.work_lists_page
        if not self._page_is_busy(page):
            self._capture_shared_context("work_lists", "work_list")
            return
        worker = getattr(page, "_context_operation_worker", None)
        if worker is None:
            return
        page._pending_context_selection = {
            "worker": worker,
            "selected": self._context_values("work_lists"),
            "baseline": dict(page._operation_shared_context),
            "revision": page._operation_shared_revision,
            "operation": dict(page._operation_source_context),
        }

    def _finish_registry_selection(self, page, worker) -> None:
        if self._closing:
            return
        pending = getattr(page, "_pending_context_selection", None)
        if pending is None or pending["worker"] is not worker:
            return
        if self._page_is_busy(page):
            # A queued command owns this page now; it must keep its own context.
            page._pending_context_selection = None
            return
        page._pending_context_selection = None
        if not self.settings_page.shared_context_enabled():
            return
        current = self._normalized_context(self._shared_context)
        baseline = self._normalized_context(pending["baseline"])
        unchanged = (
            current == baseline and self._shared_context_revision == pending["revision"]
        )
        still_same_operation = (
            self.pages.currentWidget() is page
            and current == self._normalized_context(pending["operation"])
        )
        if not unchanged and not still_same_operation:
            return
        if self._normalized_context(self._context_values("work_lists")) != self._normalized_context(
            pending["selected"]
        ):
            return
        self._capture_shared_context("work_lists", "work_list")

    def _capture_shared_context(self, source: str, kind: str) -> None:
        if self._applying_shared_context:
            return
        if source == "houses" and kind == "organization":
            self._global_organization_changed()
            return
        if (
            not self.settings_page.shared_context_enabled()
            or self._page_is_busy(self._page_by_context_name(source))
        ):
            return

        values = self._context_values(source)
        if values["organization_id"] != self._shared_context.get("organization_id"):
            # Ignore transient house/list signals while the UO selector is being
            # rebuilt. Only its accepted selection can change the global UO.
            return
        if kind in ("organization", "house"):
            object_changed = any(
                self._shared_context.get(field) != values[field]
                for field in ("organization_id", "house_id")
            )
            self._shared_context["organization_id"] = values["organization_id"]
            self._shared_context["house_id"] = values["house_id"]
            if object_changed:
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

    def organization_switch_busy(self) -> bool:
        return bool(self._active_workers or getattr(self, "_result_callbacks", 0)
                    or any(self._page_is_busy(page) for page in self._data_pages()))

    def prepare_organization_change(self) -> bool:
        """Flush every affected page before the UO selector changes any data."""
        if self.organization_switch_busy():
            QMessageBox.information(self, "Выполняется операция",
                "Дождитесь завершения текущей операции перед сменой управляющей организации.")
            return False
        previous_guard = self._applying_shared_context
        self._applying_shared_context = True
        try:
            return all(page.flush_pending_changes() is not False for page in self._data_pages())
        finally:
            self._applying_shared_context = previous_guard

    def _global_organization_changed(self) -> None:
        organization_id = self.organizations_page.organization_combo.currentData()
        if organization_id == self._shared_context.get("organization_id"):
            self._update_organization_caption()
            return
        self._shared_context.update(organization_id=organization_id,
                                    house_id=self._context_values("houses")["house_id"],
                                    work_list_id=None)
        self._applying_shared_context = True
        try:
            for name in ("fees", "work_lists", "planning", "fixation", "quality"):
                self._apply_global_organization_to_page(name)
        finally:
            self._applying_shared_context = False
        self._store_shared_context()
        self._apply_shared_context(exclude="houses")

    def _apply_global_organization_to_page(self, name: str) -> None:
        page = self._page_by_context_name(name)
        organization_id = self._shared_context.get("organization_id")
        if self._page_is_busy(page) or getattr(page, "has_pending_save_error", lambda: False)():
            return
        if organization_id and page.organization_combo.findData(organization_id) < 0:
            page.refresh_organizations()
        if page.organization_combo.currentData() != organization_id:
            self._set_combo_data(page.organization_combo, organization_id)
            page._organization_changed()
            if name == "houses":
                self.organizations_page.refresh_current()

    def _shared_context_mode_changed(self, enabled: bool) -> None:
        if not enabled:
            self._update_context_summary()
            self._update_context_notices()
            return
        current_index = self.pages.currentIndex()
        if self._context_name_for_index(current_index) is None:
            current_index = self._last_context_page_index
        source = self._context_name_for_index(current_index) or "houses"
        values = self._context_values(source)
        preserve_list = source == "fees" and all(values[field] == self._shared_context.get(field)
                                                 for field in ("organization_id", "house_id"))
        self._shared_context["organization_id"] = values["organization_id"]
        self._shared_context["house_id"] = values["house_id"]
        if not preserve_list:
            self._shared_context["work_list_id"] = values["work_list_id"]
        if values["year"] is not None:
            self._shared_context["year"] = int(values["year"])
        self._store_shared_context()
        self._apply_shared_context()

    @staticmethod
    def _set_combo_data(combo, value) -> bool:
        index = combo.findData(value)
        blocked = combo.blockSignals(True)
        combo.setCurrentIndex(index if index >= 0 else -1)
        combo.blockSignals(blocked)
        return index >= 0

    def _apply_shared_context(self, exclude: str | None = None) -> None:
        if (
            self._applying_shared_context
            or not self.settings_page.shared_context_enabled()
        ):
            return
        self._applying_shared_context = True
        try:
            for name in ("houses", "fees", "work_lists", "planning", "fixation", "quality"):
                if name == exclude:
                    continue
                self._apply_shared_context_to_page(name)
        finally:
            self._applying_shared_context = False
        self._update_context_notices()

    def _apply_shared_context_to_page(self, name: str) -> None:
        page = self._page_by_context_name(name)
        if self._page_is_busy(page):
            return
        if getattr(page, "has_pending_save_error", lambda: False)():
            return
        organization_id = self._shared_context.get("organization_id")
        house_id = self._shared_context.get("house_id")
        work_list_id = self._shared_context.get("work_list_id")
        year = self._shared_context.get("year")
        if year not in (None, ""):
            year = int(year)

        if name == "fees" and not page.flush_pending_changes():
            return

        if name == "houses":
            changed = page.organization_combo.currentData() != organization_id
            if changed and not self.organizations_page.flush_pending_changes():
                return
            self._set_combo_data(page.organization_combo, organization_id)
            if changed:
                page._organization_changed()
                self.organizations_page.refresh_current()
            if house_id is None:
                if page.house_list.currentRow() >= 0:
                    page.house_list.blockSignals(True)
                    page.house_list.setCurrentRow(-1)
                    page.house_list.blockSignals(False)
                    page._load_house(None)
                return
            current = page.house_list.currentItem()
            if (
                current is not None
                and current.data(Qt.ItemDataRole.UserRole) == house_id
            ):
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

        organization_changed = page.organization_combo.currentData() != organization_id
        self._set_combo_data(page.organization_combo, organization_id)
        if organization_changed:
            page._organization_changed()

        if house_id is not None:
            house_changed = page.house_combo.currentData() != house_id
            self._set_combo_data(page.house_combo, house_id)
            if house_changed:
                page._house_changed()
        else:
            if page.house_combo.currentIndex() >= 0:
                page.house_combo.blockSignals(True)
                page.house_combo.setCurrentIndex(-1)
                page.house_combo.blockSignals(False)
                page._house_changed()

        if name == "fees":
            return
        if name == "work_lists":
            current = page.work_lists.currentItem()
            current_id = (
                current.data(Qt.ItemDataRole.UserRole)
                if current is not None
                else None
            )
            if work_list_id is not None and current_id == work_list_id:
                return

            if work_list_id is not None:
                for row in range(page.work_lists.count()):
                    item = page.work_lists.item(row)
                    if item.data(Qt.ItemDataRole.UserRole) == work_list_id:
                        page.work_lists.setCurrentItem(item)
                        return

            if current_id is not None:
                page.work_lists.blockSignals(True)
                page.work_lists.setCurrentRow(-1)
                page.work_lists.blockSignals(False)
                page._load_work_list(None)
            return

        current_work_list_id = page.work_list_combo.currentData()
        if work_list_id is not None:
            found = self._set_combo_data(page.work_list_combo, work_list_id)
        else:
            found = False
            if page.work_list_combo.currentIndex() >= 0:
                page.work_list_combo.blockSignals(True)
                page.work_list_combo.setCurrentIndex(-1)
                page.work_list_combo.blockSignals(False)

        if current_work_list_id != page.work_list_combo.currentData():
            page._work_list_changed()

        if year is not None and found and page.year_combo.currentData() != year:
            self._set_combo_data(page.year_combo, year)
            page._year_changed()

    def _update_context_summary(self) -> None:
        self._update_organization_caption()
        if not self.settings_page.shared_context_enabled():
            self.context_mode_label.setText("Режим вкладок:\nНезависимый выбор")
            self.context_mode_label.setToolTip(
                "УО общая для всей программы. Каждый раздел запоминает свой дом, перечень и период. Изменить режим можно в настройках."
            )
            return
        house_id = self._shared_context.get("house_id")
        house = self.repository.get_house(str(house_id)) if house_id else None
        address = house.address if house else "Выберите дом"
        self.context_mode_label.setText("Режим вкладок:\nОбщий выбор")
        self.context_mode_label.setToolTip(
            "Выбор переносится между разделами. Изменить режим можно в настройках.\n" + address
        )

    def _update_organization_caption(self, *_args) -> None:
        organization_id = self._shared_context.get("organization_id")
        organization = self.repository.get_organization(organization_id) if organization_id else None
        name = organization.name if organization else "УО не выбрана"
        # Preserve the navigation's height in short windows. The full name is
        # always available in the tooltip; ordinary windows show up to 3 lines.
        metrics = self.organization_caption.fontMetrics()
        width = max(1, self.sidebar.width() - self.sidebar_layout.contentsMargins().left()
                    - self.sidebar_layout.contentsMargins().right())
        words = name.split()
        lines = []
        max_lines = 1 if self.height() < 700 else 3
        while words and len(lines) < max_lines - 1:
            line = words.pop(0)
            while words and metrics.horizontalAdvance(line + " " + words[0]) <= width:
                line += " " + words.pop(0)
            lines.append(metrics.elidedText(line, Qt.TextElideMode.ElideRight, width))
        if words:
            lines.append(metrics.elidedText(" ".join(words), Qt.TextElideMode.ElideRight, width))
        self.organization_caption.setText("\n".join(lines))
        self.organization_caption.setToolTip(name + "\nВыбрать организацию можно в разделе «УО».")

    def _update_application_icon(self) -> None:
        app_icon = icon("app-logo", size=64)
        self.setWindowIcon(app_icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(app_icon)

    def _update_context_notices(self) -> None:
        enabled = self.settings_page.shared_context_enabled()
        selected_list_id = self._shared_context.get("work_list_id")
        selected_list = (
            self.repository.get_work_list(str(selected_list_id)) if selected_list_id else None
        )
        selected_year = self._shared_context.get("year")
        for name in ("houses", "fees", "work_lists", "planning", "fixation", "quality"):
            page = self._page_by_context_name(name)
            values = self._context_values(name)
            message = ""
            if enabled:
                fields = ("organization_id", "house_id") if name in ("houses", "fees") else tuple(values)
                context_differs = any(
                    str(self._shared_context.get(field) or "") != str(values[field] or "")
                    for field in fields
                )
                if self._page_is_busy(page) and context_differs:
                    message = (
                        "В этом разделе выполняется операция для прежнего выбора. "
                        "Дождитесь завершения, затем откройте раздел повторно: "
                        "будет применён общий выбор. Текущая операция продолжает работать с прежним выбором."
                    )
                for field, label in (
                    ("organization_id", "управляющая организация"), ("house_id", "дом"),
                ):
                    if message:
                        break
                    expected = self._shared_context.get(field)
                    if expected and values[field] != expected:
                        message = (
                            f"Общий выбор сохранён, но выбранный {label} недоступен в этом разделе. "
                            + ("Проверьте выбор в разделе «УО»." if field == "organization_id"
                               else "Проверьте выбор в разделе «Дома».")
                        )
                        break
                if not message and name not in ("houses", "fees") and selected_list_id:
                    if values["work_list_id"] != selected_list_id:
                        if selected_list and name != "work_lists":
                            message = (
                                f"Выбранный перечень {selected_list.period_label} недоступен "
                                "для этого этапа. Получите размещённый перечень или завершите "
                                "его размещение в разделе «Перечни». Общий выбор сохранён."
                            )
                        else:
                            message = (
                                "Выбранный перечень недоступен. Общий выбор сохранён; "
                                "выберите доступный перечень в разделе «Перечни»."
                            )
                    elif selected_year and values["year"] is None and name != "work_lists":
                        message = (
                            f"Год {selected_year} вне периода выбранного перечня. "
                            "Общий выбор сохранён. Выберите доступный год в поле «Год» этого раздела."
                        )
            page.context_notice.setText(message)
            page.context_notice.setVisible(bool(message))

    def _next_step_panel_changed(self, enabled: bool) -> None:
        for panel in self.pages.findChildren(WorkflowPanel):
            panel.set_guidance_enabled(enabled)

    def _theme_changed(self, theme: str) -> None:
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme, self._ui_scale)
        self._update_application_icon()
        self._apply_window_chrome()
        self._update_display_layout(force=True)
        for row in range(self.navigation.count()):
            item = self.navigation.item(row)
            item.setIcon(icon(str(item.data(Qt.ItemDataRole.UserRole)), color("muted_text")))
        for page in (
            self.work_lists_page, self.planning_page, self.fixation_page, self.quality_page,
        ):
            if self._page_is_busy(page):
                self._pending_theme_pages.add(page)
            else:
                self._pending_theme_pages.discard(page)
                self._reload_page_theme(page)
        self._refresh_integration_status()

    def _reload_pending_page_themes(self) -> None:
        if self._closing:
            return
        for page in tuple(self._pending_theme_pages):
            if not self._page_is_busy(page):
                self._pending_theme_pages.discard(page)
                self._reload_page_theme(page)

    def _scale_changed(self, percent: int) -> None:
        """Enlargement is suspended; old preferences must not reactivate it."""
        if self._ui_scale == 1.0:
            return
        self._ui_scale = 1.0
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.settings_page.theme_name(), self._ui_scale)
        self._update_display_layout(force=True)

    def _apply_window_chrome(self) -> None:
        if getattr(self, "_closing", False):
            return
        apply_window_theme(self, self.repository.get_setting("ui.theme") or "light")

    def _reload_page_theme(self, page) -> None:
        if self._closing:
            return
        if self._page_is_busy(page):
            return
        previous_guard = self._applying_shared_context
        self._applying_shared_context = True
        try:
            if page.flush_pending_changes() is False:
                return
            if page is self.work_lists_page:
                if page._current_work_list_id:
                    page._load_items(page._current_work_list_id)
            else:
                page._load_table()
                if page is self.fixation_page:
                    page._load_unplanned_table()
        finally:
            self._applying_shared_context = previous_guard

    @staticmethod
    def _page_is_busy(page) -> bool:
        if any(
            bool(getattr(page, name, False))
            for name in ("_integration_busy", "_workflow_busy", "_queue_running")
        ):
            return True
        if getattr(page, "_bulk_operation_name", None) is not None:
            return True
        bulk_running = getattr(page, "_bulk_worker_running", None)
        if bulk_running is not None and bulk_running():
            return True
        worker = getattr(page, "_integration_worker", None)
        return worker is not None and worker.isRunning()

    def _refresh_integration_status(self) -> None:
        self._reload_pending_page_themes()
        status = self.integration_gateway.status()
        session_renewal_failed = bool(
            status.running and status.helper_connected and status.gis_page_ready
            and status.session_keep_alive_error
        )
        intro = getattr(self, "helper_intro", None)
        if (
            intro is not None and not intro.isHidden()
            and status.running and status.helper_connected and status.gis_page_ready
            and helper_supports_organization_routing(status.helper_version)
        ):
            self.repository.set_setting("ui.helper.intro_seen", "1")
            intro.hide()
        if not status.running:
            text = "Служба связи не запущена"
            if status.error:
                text += f"\n{status.error}"
            tone = "danger"
        elif not status.helper_connected:
            text = "ГИС ЖКХ помощник не подключен"
            tone = "warning"
        elif not status.gis_page_ready:
            version = status.helper_version or "?"
            text = f"ГИС ЖКХ помощник {version} подключен\nВкладка ГИС ЖКХ не открыта"
            tone = "warning"
        else:
            version = status.helper_version or "?"
            heading = (
                "Состояние сессии ГИС ЖКХ не подтверждено" if session_renewal_failed
                else "ГИС ЖКХ подключен"
            )
            text = f"{heading}\nГИС ЖКХ помощник {version} подключён"
            tone = "success"
            if not helper_supports_quality_assessment(status.helper_version):
                text += (
                    "\nПомощник устарел\n"
                    f"Требуется {'.'.join(map(str, MIN_QUALITY_HELPER_VERSION))}+"
                )
                tone = "warning"
            elif not helper_supports_organization_routing(status.helper_version):
                minimum = ".".join(map(str, MIN_ORGANIZATION_ROUTING_HELPER_VERSION))
                text += f"\nДля выбора вкладки УО требуется {minimum}+"
                tone = "warning"
            if session_renewal_failed:
                text += "\nСессия: ошибка продления\n" + SESSION_CONNECTION_GUIDANCE
                tone = "warning"
            elif helper_supports_organization_routing(status.helper_version):
                if status.session_keep_alive_enabled:
                    text += "\nСессия: автопродление вкл."
                else:
                    text += "\nСессия: активна"

        brief = ("Служба связи не запущена" if not status.running else
                 "Помощник не подключён" if not status.helper_connected else
                 "Откройте вкладку ГИС ЖКХ" if not status.gis_page_ready else
                 "Помощник устарел" if "устарел" in text else
                 "Обновите браузерный помощник" if "требуется" in text.lower() else
                 "Ошибка продления сессии" if status.session_keep_alive_error else
                 text.split("\n")[0])
        browser_names = status.browser_names
        browser_text = (
            ("Браузер: " if len(browser_names) == 1 else "Браузеры: ") + ", ".join(browser_names)
            if browser_names and browser_names != ("Браузер не определён",)
            else "Браузер не определён"
        )
        browser_connected = status.running and status.helper_connected
        self.integration_browser.setText(browser_text)
        self.integration_browser.setVisible(browser_connected)
        self.integration_browser.setToolTip(
            "Помощники с открытой вкладкой ГИС ЖКХ."
            if status.gis_page_ready else "Подключённые браузерные помощники."
        )
        if browser_connected:
            text += "\n" + browser_text
        self.integration_status.setText(brief)
        self.integration_status.setToolTip(text)
        self.integration_status.setStyleSheet(f"color: {color(tone)};")
        self.settings_page.set_connection_status(
            text, helper_connected=status.helper_connected,
            gis_page_ready=status.gis_page_ready,
            session_renewal_failed=session_renewal_failed,
        )

        self.houses_page._refresh_workflow()
        self.organizations_page.refresh_availability()
        self.management_fees_page._refresh_integration_status()
        self.work_lists_page._refresh_integration_status()
        self.planning_page._refresh_integration_status()
        self.fixation_page._refresh_integration_status()
        self.quality_page._refresh_integration_status()

    def _restore_navigation(self, index: int) -> None:
        if self.pages.currentIndex() != index:
            return
        blocked = self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(index)
        self.navigation.blockSignals(blocked)

    def _navigation_changed(self, index: int) -> None:
        if index < 0 or index >= self.pages.count():
            return
        previous_page = self.pages.currentWidget()
        if previous_page is not self.pages.widget(index):
            flush = getattr(previous_page, "flush_pending_changes", None)
            if flush is not None:
                if flush() is False:
                    previous = self.pages.currentIndex()
                    self._restore_navigation(previous)
                    # Mouse selection may complete after currentRowChanged has returned.
                    QTimer.singleShot(0, lambda: self._restore_navigation(previous))
                    return
        context_name = self._context_name_for_index(index)
        if context_name is not None:
            self._last_context_page_index = index

        # Rebuilding dependent selectors emits transient selection signals. Guard
        # the entire refresh, so those signals cannot replace the user's context
        # with the first (or newest) available list and its default year.
        previous_guard = self._applying_shared_context
        self._applying_shared_context = True
        try:
            page_busy = context_name is not None and self._page_is_busy(
                self._page_by_context_name(context_name)
            )
            if context_name is not None and context_name != "houses" and not page_busy:
                self._page_by_context_name(context_name).refresh_organizations()
            if context_name is not None:
                self._apply_global_organization_to_page(context_name)
            if context_name is not None and self.settings_page.shared_context_enabled():
                self._apply_shared_context_to_page(context_name)
        finally:
            self._applying_shared_context = previous_guard
        if index == 0:
            self.organizations_page.refresh_current()
        self.pages.setCurrentIndex(index)
        target_page = self.pages.currentWidget()
        if (self.settings_page.shared_context_enabled()
                and previous_page in (self.fixation_page, self.quality_page)
                and target_page in (self.fixation_page, self.quality_page)
                and previous_page is not target_page and not self._page_is_busy(target_page)
                and previous_page._current_work_list_id == target_page._current_work_list_id
                and previous_page.year_combo.currentData() == target_page.year_combo.currentData()):
            month = previous_page._current_month
            if month is not None:
                target_page._select_month(month)
            target_page.scope_switcher.set_scope(previous_page.scope_switcher.scope())
        self._update_context_summary()
        self._update_context_notices()

    def _context_name_for_index(self, index):
        page = self.pages.widget(index)
        if page is self.organizations_page:
            return "houses"
        return next((name for name in ("houses", "fees", "work_lists", "planning", "fixation", "quality")
                     if page is self._page_by_context_name(name)), None)

    def _navigate_workflow_target(self, index):
        # Workflow targets are stable business stages, independent of sidebar insertions.
        pages = (self.houses_page, self.work_lists_page, self.planning_page,
                 self.fixation_page, self.quality_page, self.settings_page)
        target = self.organizations_page if index == -1 else pages[index]
        self.navigation.setCurrentRow(self.pages.indexOf(target))

    def _workflow_navigation_requested(self, source: str, index: int) -> None:
        """Continue a monthly workflow with the same month and action scope."""
        monthly_names = {3: "fixation", 4: "quality"}
        source_page = self._page_by_context_name(source)
        source_values = self._context_values(source)
        month = getattr(source_page, "_current_month", None)
        scope = getattr(source_page, "scope_switcher", None)
        selected_scope = scope.scope() if scope is not None else None
        self._navigate_workflow_target(index)
        if source not in monthly_names.values() or index not in monthly_names:
            return
        target_name = monthly_names[index]
        target_page = self._page_by_context_name(target_name)
        target_values = self._context_values(target_name)
        if not source_values["work_list_id"] or source_values["year"] is None:
            return
        if any(source_values[field] != target_values[field] for field in source_values):
            return
        if self._page_is_busy(target_page):
            return
        month_button = target_page.month_buttons.get(int(month)) if month is not None else None
        if month_button is not None and month_button.isEnabled():
            target_page._select_month(int(month))
        if selected_scope is not None:
            target_page.scope_switcher.set_scope(selected_scope)

    def _update_display_layout(self, *, force: bool = False) -> None:
        if not hasattr(self, "settings_page"):
            return
        width = self.width() / self._ui_scale
        height = self.height() / self._ui_scale

        if width < 980 or height < 600:
            mode = "narrow"
        elif width < 1180 or height < 720:
            mode = "compact"
        else:
            mode = "normal"

        if force or mode != self._view_mode:
            self._view_mode = mode
            # Density changes layout, never the user's chosen text size.
            self.navigation.ensurePolished()
            self.sidebar.setFixedWidth(min(272, round(228 + 88 * (self._ui_scale - 1))))
            # Below this width the two-pane house form and context selectors
            # cannot provide all controls. Never advertise an unusable size.
            self.setMinimumWidth(round(1000 + 400 * (self._ui_scale - 1)))
            self.sidebar_layout.setContentsMargins(*(
                scaled(value, self._ui_scale) for value in (8, 10, 8, 10)
            ))
            self.integration_layout.setContentsMargins(*(
                scaled(value, self._ui_scale) for value in (10, 9, 10, 9)
            ))
            self.integration_layout.setSpacing(scaled(4, self._ui_scale))
            self.context_layout.setContentsMargins(*(
                scaled(value, self._ui_scale) for value in (10, 9, 10, 9)
            ))
            self.context_layout.setSpacing(scaled(4, self._ui_scale))
            self.houses_page.set_compact_mode(mode != "normal")
            self.work_lists_page.set_view_mode(mode)
            self.planning_page.set_view_mode(mode)
            self.fixation_page.set_view_mode(mode)
            self.quality_page.set_view_mode(mode)
            # The houses density hook supplies new base margins at each mode.
            self.houses_page.card_layout.setProperty("uiBaseMargins", None)
            self.houses_page.card_layout.setProperty("uiBaseSpacing", None)
            apply_layout_scale(self.pages, self._ui_scale)
            QTimer.singleShot(0, self._refresh_navigation_metrics)
        self._update_organization_caption()

    def _refresh_navigation_metrics(self) -> None:
        """Measure after stylesheet/layout settles, including the selected font."""
        self.navigation.refresh_metrics(scaled(20, self._ui_scale))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._update_display_layout()
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._apply_window_chrome()

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.WinIdChange and hasattr(self, "repository"):
            QTimer.singleShot(0, self._apply_window_chrome)
        return result

    def can_delete_local_data(self) -> bool:
        if self._active_workers or getattr(self, "_result_callbacks", 0) or any(self._page_is_busy(page) for page in self._data_pages()):
            QMessageBox.information(self, "Выполняется операция", "Дождитесь завершения обмена с ГИС перед удалением локальных данных.")
            return False
        return (self.organizations_page.flush_pending_changes() is not False
                and self.houses_page.flush_pending_changes() is not False
                and self.management_fees_page.flush_pending_changes() is not False)

    def _data_pages(self):
        return (self.organizations_page, self.houses_page, self.work_lists_page, self.planning_page,
                self.fixation_page, self.quality_page, self.management_fees_page)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._closing:
            event.accept()
            return
        # Keep the Qt event loop and repository alive for queued success/failure
        # callbacks. A running command completes; following queue items do not start.
        if self._active_workers or getattr(self, "_result_callbacks", 0) or any(self._page_is_busy(page) for page in self._data_pages()):
            self._close_requested = True
            self.centralWidget().setEnabled(False)
            self.statusBar().showMessage("Завершение текущих операций… Приложение закроется после сохранения результатов.")
            self._close_timer.start(100)
            event.ignore()
            return
        for page in self._data_pages():
            if page.flush_pending_changes() is False:
                self._close_requested = False
                self.centralWidget().setEnabled(True)
                self.statusBar().clearMessage()
                event.ignore()
                return
        self._closing = True
        self.integration_status_timer.stop()
        self.integration_gateway.stop()
        self.repository.close()
        super().closeEvent(event)
