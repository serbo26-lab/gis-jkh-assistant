"""Novice-facing discovery and readable controls against actual Qt pages."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QLabel, QPushButton, QWidget  # noqa: E402

from gis_jkh_assistant.app.houses_page import HousesPage  # noqa: E402
from gis_jkh_assistant.app.organizations_page import OrganizationsPage  # noqa: E402
from gis_jkh_assistant.app.quality_page import QualityAssessmentPage  # noqa: E402
from gis_jkh_assistant.app.styles import apply_theme  # noqa: E402
from gis_jkh_assistant.app.work_lists_page import WorkListsPage  # noqa: E402
from gis_jkh_assistant.app.workflow_widgets import SectionCard  # noqa: E402
from gis_jkh_assistant.database import Repository  # noqa: E402


class Gateway:
    def __init__(self, version="0.1.30"):
        self.version = version

    def status(self):
        return SimpleNamespace(
            running=True, helper_connected=True, gis_page_ready=True,
            helper_version=self.version,
        )

    def execute(self, *_args, **_kwargs):
        raise AssertionError("A visual test must not send a live GIS request")


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    if os.name == "nt" and "Segoe UI" not in QFontDatabase.families():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    yield application
    apply_theme(application, "light")


def settle(app):
    for _ in range(3):
        app.processEvents()


@pytest.fixture
def pages(app, tmp_path):
    apply_theme(app, "light")
    repository = Repository(tmp_path / "clarity.db")
    org = repository.create_organization(name="УО Пример", ogrn="1022600000560")
    house = repository.create_house(
        organization_id=org.id, address="Улица Примерная, дом 56",
        house_object_guid="house-guid",
    )
    work_list = repository.create_work_list(house_id=house.id, base_year=2026)
    repository.update_work_list_gis_state(
        work_list.id, gis_status="APPROVED", gis_work_list_guid="list-guid",
    )
    gateway = Gateway()
    houses = HousesPage(repository, gateway)
    organizations = OrganizationsPage(repository, houses, gateway)
    lists = WorkListsPage(repository, gateway)
    quality = QualityAssessmentPage(repository, gateway)
    for page in (organizations, houses, lists, quality):
        page.resize(1100, 760)
        page.show()
    lists.refresh_work_lists(select_id=work_list.id)
    settle(app)
    yield SimpleNamespace(
        app=app, repository=repository, organizations=organizations, houses=houses, lists=lists, quality=quality,
    )
    houses._autosave_timer.stop()
    lists._period_timer.stop()
    for page in (organizations, houses, lists, quality):
        page.close()
        page.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle(app)
    repository.close()


def labels(widget):
    return [label.text() for label in widget.findChildren(QLabel)]


def menu_text(button):
    return [action.text() for action in button.menu().actions() if not action.isSeparator()]


def test_all_organization_downloads_are_visible_commands_with_explicit_scope(pages):
    page = pages.houses
    assert page.sync_houses_button.isVisibleTo(page)
    for button in (page.sync_all_management_button, page.sync_all_work_lists_button):
        assert button.isVisibleTo(page)
    assert not page.bulk_actions_button.isVisibleTo(page)
    assert not hasattr(page, "organization_actions_button")
    assert not page.gis_actions_button.isVisibleTo(page)


def test_manual_organization_actions_and_local_files_are_separate_from_gis(pages):
    page = pages.houses
    organizations = pages.organizations
    for button in (organizations.load_organization_button, organizations.add_org_button,
                   organizations.save_button, organizations.delete_org_button):
        assert button.isVisibleTo(organizations)
        assert button.menu() is None
    assert page.add_house_button.isVisibleTo(page)
    assert page.export_house_data_button.isVisibleTo(page)
    assert page.import_house_data_button.isVisibleTo(page)
    assert all("ГИС" not in item for item in menu_text(page.files_actions_button))
    assert not hasattr(page, "delete_org_button")


@pytest.mark.parametrize("version,enabled", [("0.1.30", True), ("0.1.29", False), ("0.1.28", False)])
def test_first_step_offers_import_from_supported_authorized_helper(app, tmp_path, version, enabled):
    repository = Repository(tmp_path / "empty.db")
    houses = HousesPage(repository, Gateway(version))
    page = OrganizationsPage(repository, houses, houses.integration_gateway)
    page.show()
    settle(app)
    assert page.load_organization_button.isVisibleTo(page)
    assert page.add_org_button.isVisibleTo(page)
    assert page.load_organization_button.isEnabled() is enabled
    assert page.add_org_button.isEnabled()
    if not enabled:
        assert "0.1.30" in page.load_organization_button.toolTip()
    page.deleteLater()
    houses.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    repository.close()


def test_work_list_files_and_network_fallback_remain_explicit_and_reachable(pages):
    page = pages.lists
    assert page.files_actions_button.isVisibleTo(page)
    assert menu_text(page.files_actions_button) == ["Выбрать XLSX вручную…", "Сформировать XLSX", "Отправить XLSX в ГИС"]
    assert any(action.isSeparator() for action in page.files_actions_button.menu().actions())
    assert "Шаблоны пользователя…" in menu_text(page.composition_actions)
    assert "Удалить локальный перечень" in menu_text(page.composition_actions)
    page.gis_actions.sync()
    assert "Загрузить перечни дома" in menu_text(page.gis_actions)
    assert "Проверить статус" in menu_text(page.gis_actions)
    for button in (page.cancel_gis_button, page.delete_button):
        assert button.isVisibleTo(page)
        assert button.parentWidget() is page.preparation_bar.groups[1]


def test_disabled_house_download_explains_missing_gis_identity_inline(pages):
    page = pages.lists
    house_id = page.house_combo.currentData()
    pages.repository.update_house(house_id, house_object_guid=None)
    page._refresh_gis_action_buttons()
    assert not page.load_gis_lists_button.isEnabled()
    assert "Дома" in page.load_gis_lists_button.toolTip()
    assert page.network_hint.isVisibleTo(page)
    assert "идентификатор" in page.network_hint.text()


def test_disclosures_say_what_will_open_and_use_svg_chevrons(pages):
    page = pages.lists
    assert page.details_card.isVisibleTo(page)
    assert page.details_more_toggle.text().startswith("Показать ")
    page.details_more_toggle.click()
    assert page.details_more_toggle.text().startswith("Скрыть ")
    page.details_more_toggle.click()
    assert page.details_more_toggle.text().startswith("Показать ")
    assert page.context_status.isVisibleTo(page)
    assert "2026" in page.context_status.text()
    assert pages.houses.house_ids_toggle.text() == "Показать идентификаторы"


def test_quality_local_preparation_and_both_network_scopes_are_visible(pages):
    page = pages.quality
    assert not page.data_actions.isVisibleTo(page)
    for button in (page.load_gis_button, page.form_month_button, page.save_button,
                   page.load_period_button, page.save_period_button, page.auto_act_month_button):
        assert button.isVisibleTo(page)
    assert page.month_acts_button.isVisibleTo(page)
    assert page.month_acts_button.text() == "Акты…"



@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_scope_buttons_keep_the_complete_checked_label_at_large_text(pages, theme, scale):
    apply_theme(pages.app, theme, scale)
    page = pages.quality
    page.resize(820, 760)
    settle(pages.app)
    for scope, button in page.scope_switcher.buttons.items():
        page.scope_switcher.set_scope(scope)
        settle(pages.app)
        font = QFont(button.font())
        font.setBold(True)
        required = QFontMetrics(font).horizontalAdvance(button.text()) + 18
        assert button.width() >= required
        assert button.height() >= QFontMetrics(font).height() + 8
    apply_theme(pages.app, "light")


def test_action_groups_wrap_whole_commands_without_shrinking(app):
    apply_theme(app, "light", 1.5)
    card = SectionCard("Загрузка из ГИС ЖКХ — вся управляющая организация")
    buttons = [QPushButton(text) for text in (
        "Загрузить все дома УО", "Загрузить управление всеми домами", "Загрузить перечни всех домов",
    )]
    for button in buttons:
        card.actions.addWidget(button)
    card.resize(540, 260)
    card.show()
    settle(app)
    assert len({button.y() for button in buttons}) > 1
    for button in buttons:
        assert button.width() >= button.minimumSizeHint().width()
        assert button.geometry().right() < card.width()
    card.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    apply_theme(app, "light")


def test_completed_workflow_and_house_toolbar_have_no_font_dependent_symbols(pages):
    page = pages.houses
    assert all("✓" not in label.text() for label in page.workflow.steps)
    assert any(not symbol.isHidden() and not symbol.pixmap().isNull() for symbol in page.workflow.step_icons)
    assert all("▾" not in button.text() for button in page.findChildren(QPushButton) if button.isVisibleTo(page))
    assert not any(
        frame.isVisibleTo(page) for frame in page.findChildren(QFrame)
        if frame.objectName() == "ToolbarSeparator"
    )


def test_house_content_has_vertical_scroll_for_large_text_and_keeps_detail(pages):
    page = pages.houses
    apply_theme(pages.app, "dark", 1.5)
    page.resize(980, 650)
    settle(pages.app)
    assert page.page_scroll.widget() is page.page_content
    assert page.page_content.width() <= page.page_scroll.viewport().width()
    assert page.splitter.widget(1).isVisibleTo(page)
    assert page.page_scroll.horizontalScrollBar().maximum() == 0
    assert isinstance(page.page_content, QWidget)
    apply_theme(pages.app, "light")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_existing_and_new_native_combo_boxes_have_one_svg_chevron(pages, theme):
    apply_theme(pages.app, theme, 1.5)
    native = QComboBox()
    native.addItem("Выберите вариант")
    native.resize(320, 54)
    native.show()
    settle(pages.app)
    for combo in (pages.houses.organization_combo, pages.lists.registry_combo, native):
        indicators = combo.findChildren(QLabel, "ComboChevron")
        assert len(indicators) == 1
        assert not indicators[0].pixmap().isNull()
        assert indicators[0].geometry().right() < combo.width()
        assert indicators[0].geometry().top() >= 0
    native.deleteLater()
    pages.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    apply_theme(pages.app, "light")
