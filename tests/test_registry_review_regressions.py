"""Registry regressions from the user's UI review, using isolated local data."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QStyle, QStyleOptionComboBox  # noqa: E402

from gis_jkh_assistant.app.houses_page import HousesPage  # noqa: E402
from gis_jkh_assistant.app.organizations_page import OrganizationsPage  # noqa: E402
from gis_jkh_assistant.app.styles import apply_theme  # noqa: E402
from gis_jkh_assistant.app.ui_scale import apply_layout_scale  # noqa: E402
from gis_jkh_assistant.app.work_lists_page import WorkListsPage  # noqa: E402
from gis_jkh_assistant.app.workflow_widgets import ActionMenuButton  # noqa: E402
from gis_jkh_assistant.database import Repository  # noqa: E402


class Gateway:
    def __init__(self):
        self.ready = True

    def status(self):
        return SimpleNamespace(
            running=True, helper_connected=self.ready, gis_page_ready=self.ready,
            helper_version="0.1.30",
        )

    def execute(self, *_args, **_kwargs):
        raise AssertionError("A UI regression must not make a live GIS request")


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    return app


@pytest.fixture
def registry(application, tmp_path):
    apply_theme(application, "light")
    repository = Repository(tmp_path / "ui-review.db")
    organization = repository.create_organization(name="УО Пример", ogrn="1022600000000")
    house = repository.create_house(
        organization_id=organization.id, address="Улица Примерная, дом 1",
        house_object_guid="house-guid",
    )
    work_list = repository.create_work_list(
        house_id=house.id, base_year=2026,
        items=[SimpleNamespace(
            position=1, work_code="1", work_guid="work-guid", work_name="Уборка",
            price=None, volume=None, quantity="1", cost="100", allocation_percent=None,
        )],
    )
    gateway = Gateway()
    houses = HousesPage(repository, gateway)
    organizations = OrganizationsPage(repository, houses, gateway)
    lists = WorkListsPage(repository, gateway)
    for page in (organizations, houses, lists):
        page.resize(1100, 760)
        page.show()
    lists.refresh_work_lists(select_id=work_list.id)
    application.processEvents()
    yield SimpleNamespace(
        repository=repository, organization=organization, house=house,
        work_list=work_list, gateway=gateway, organizations=organizations, houses=houses, lists=lists,
    )
    houses._autosave_timer.stop()
    lists._period_timer.stop()
    for page in (organizations, houses, lists):
        page.close()
        page.deleteLater()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    repository.close()
    apply_theme(application, "light")


def command_surface(page, source):
    """A direct control or a named, one-level menu proxy is reachable."""
    if source.isVisibleTo(page):
        return source
    for menu in page.findChildren(ActionMenuButton):
        if not menu.isVisibleTo(page):
            continue
        menu.sync()
        for action, button, _when in menu.entries:
            if button is source and action.isVisible():
                assert action.isEnabled() == source.isEnabled()
                return action
    raise AssertionError(f"No visible command surface for {source.text()}")


def test_dangerous_registry_commands_remain_named_and_reachable(registry):
    for page, buttons in (
        (registry.organizations, (registry.organizations.delete_org_button,)),
        (registry.houses, (
            registry.houses.delete_house_button,
            registry.houses.delete_tariff_button,
        )),
        (registry.lists, (
            registry.lists.delete_button, registry.lists.remove_works_button,
            registry.lists.clear_works_button, registry.lists.replace_from_template_button,
        )),
    ):
        for button in buttons:
            surface = command_surface(page, button)
            assert button.objectName() == "DangerButton"
            assert button.menu() is None
            assert not surface.icon().isNull()
            assert surface.text() == button.text()
            assert len(surface.text().split()) > 1


def test_list_composition_commands_are_direct_and_separate_from_xlsx(registry):
    page = registry.lists
    assert not page.composition_actions.isVisibleTo(page)
    assert page.files_actions_button.isVisibleTo(page)
    composition = [button for _action, button, _when in page.composition_actions.entries]
    files = [button for _action, button, _when in page.files_actions_button.entries]
    for source in (page.delete_button, page.templates_button,
                   page.replace_from_template_button, page.save_as_template_button):
        assert source in composition
        assert source not in files
        assert source.isVisibleTo(page)
        assert source.menu() is None
        command_surface(page, source)
    assert page.load_export_template_file_button in files
    assert page.export_button in files


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_selected_period_remains_readable_and_new_list_fits_window(registry, application, scale):
    page = registry.lists
    apply_theme(application, "light", scale)
    apply_layout_scale(page, scale)
    for _ in range(4):
        application.processEvents()
    required = page.registry_combo.fontMetrics().horizontalAdvance(registry.work_list.period_label)
    option = QStyleOptionComboBox()
    page.registry_combo.initStyleOption(option)
    edit = page.registry_combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
        QStyle.SubControl.SC_ComboBoxEditField, page.registry_combo)
    assert edit.width() >= required
    assert page.creation_toggle.width() >= page.creation_toggle.minimumSizeHint().width()
    page.creation_toggle.setChecked(True)
    for _ in range(4):
        application.processEvents()
    assert page.page_content.width() <= page.page_scroll.viewport().width() + 2
    assert page.page_scroll.horizontalScrollBar().maximum() == 0


def test_period_is_visible_and_technical_expansion_survives_refresh(registry):
    page = registry.lists
    assert not page.details_toggle.isChecked()
    assert page.details_toggle.isHidden()
    assert page.details_card.isVisibleTo(page)
    assert registry.work_list.period_label in page.registry_combo.currentText()
    page.details_more_toggle.click()
    page.refresh_work_lists(select_id=registry.work_list.id)
    page._refresh_integration_status()
    assert page.details_more_toggle.isChecked()
    assert page.details_card.isVisibleTo(page)
    assert page.start_year.isVisibleTo(page)
    assert page.end_month.isVisibleTo(page)
    page.details_more_toggle.click()
    page._refresh_gis_action_buttons()
    assert not page.details_more_toggle.isChecked()
    assert page.details_card.isVisibleTo(page)


def test_placed_list_cancellation_remains_discoverable_when_offline(registry):
    page = registry.lists
    registry.repository.update_work_list_gis_state(
        registry.work_list.id, gis_status="APPROVED", gis_work_list_guid="list-guid",
    )
    registry.gateway.ready = False
    page.refresh_work_lists(select_id=registry.work_list.id)
    action = command_surface(page, page.cancel_gis_button)
    assert action.isVisible()
    assert not action.isEnabled()
    assert "помощник" in action.toolTip()
    assert "Опасные действия" not in page.items_hint.text()


@pytest.mark.parametrize("command", ["delete_button", "remove_works_button", "clear_works_button"])
def test_direct_list_danger_click_keeps_existing_confirmation(registry, monkeypatch, command):
    prompts = []

    def decline(*args, **_kwargs):
        prompts.append(args[1])
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", decline)
    monkeypatch.setattr(QMessageBox, "warning", decline)
    page = registry.lists
    page.items_table.selectRow(0)
    surface = command_surface(page, getattr(page, command))
    (surface.click if hasattr(surface, "click") else surface.trigger)()
    assert len(prompts) == 1
    assert registry.repository.get_work_list(registry.work_list.id) is not None
    assert len(registry.repository.list_work_list_items(registry.work_list.id)) == 1


@pytest.mark.parametrize("command", ["delete_org_button", "delete_house_button", "delete_tariff_button"])
def test_direct_house_danger_click_keeps_existing_confirmation(registry, monkeypatch, command):
    tariff = registry.repository.create_house_tariff(
        house_id=registry.house.id, amount="25", start_year=2026, start_month=1,
    )
    registry.houses._refresh_tariffs(select_id=tariff.id)
    page = registry.organizations if command == "delete_org_button" else registry.houses
    prompts = []

    def decline(*args, **_kwargs):
        prompts.append(args[1])
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", decline)
    surface = command_surface(page, getattr(page, command))
    (surface.click if hasattr(surface, "click") else surface.trigger)()
    assert len(prompts) == 1
    assert registry.repository.get_organization(registry.organization.id) is not None
    assert registry.repository.get_house(registry.house.id) is not None
    assert registry.repository.get_house_tariff(tariff.id) is not None
