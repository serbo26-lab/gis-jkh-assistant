"""A single UO selection across the desktop without losing per-page edits."""
import struct

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QMessageBox

import test_ui_workflow as workflows
from gis_jkh_assistant.app import icons, main_window

qt_app = workflows.qt_app
desktop = workflows.desktop


def _pages(window):
    return [window.houses_page, window.work_lists_page, window.planning_page,
            window.fixation_page, window.quality_page, window.management_fees_page]


@pytest.mark.parametrize("shared", [True, False])
def test_uo_selection_is_global_in_both_modes_and_survives_navigation(desktop, shared):
    window, repo = desktop.window, desktop.repository
    (window.settings_page.shared_radio if shared else window.settings_page.independent_radio).setChecked(True)
    other = repo.create_organization(name="Вторая вымышленная УО", ogrn="1022600000541")
    other_house = repo.create_house(organization_id=other.id, address="г. Тестовый, ул. Новая, д. 20")
    selector = window.organizations_page.organization_combo
    window.organizations_page.refresh_organizations()
    address = "г. Тестовый, локальная правка, д. 1"
    window.houses_page.address_edit.setText(address)
    window.houses_page.address_edit.textEdited.emit(address)
    selector.setCurrentIndex(selector.findData(other.id))
    assert window.organization_caption.text().replace("\n", " ") == other.name
    assert repo.get_setting("ui.context.organization_id") == other.id
    for page in _pages(window):
        window.navigation.setCurrentRow(window.pages.indexOf(page))
        workflows._settle(desktop.app)
        assert page.organization_combo.currentData() == other.id
        if page is not window.houses_page:
            assert page.organization_combo.isHidden()
            assert page.house_combo.currentData() == other_house.id
    assert window.houses_page.organization_context.isHidden()
    window.navigation.setCurrentRow(0)
    selector.setCurrentIndex(selector.findData(desktop.organization.id))
    for page in _pages(window):
        assert page.organization_combo.currentData() == desktop.organization.id
    assert repo.get_house(desktop.house.id).organization_id == desktop.organization.id
    assert repo.get_work_list(desktop.work_list.id).house_id == desktop.house.id


@pytest.mark.parametrize("blocked_by", ["save", "operation"])
def test_switch_is_rejected_before_any_page_context_is_replaced(desktop, monkeypatch, blocked_by):
    window, repo = desktop.window, desktop.repository
    other = repo.create_organization(name="Другая УО", ogrn="1022600000542")
    selector = window.organizations_page.organization_combo
    window.organizations_page.refresh_organizations()
    address = "г. Тестовый, локальная правка, д. 1"
    window.houses_page.address_edit.setText(address)
    window.houses_page.address_edit.textEdited.emit(address)
    house_before = window.houses_page._current_house_id
    before = [(page.organization_combo.currentData(),
               getattr(page, "_current_work_list_id", None)) for page in _pages(window)]
    selected_settings = dict(window._shared_context)
    page = window.fixation_page
    original_flush = page.flush_pending_changes
    if blocked_by == "save":
        monkeypatch.setattr(page, "flush_pending_changes", lambda: False)
    else:
        page._integration_busy = True
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    selector.setCurrentIndex(selector.findData(other.id))
    assert selector.currentData() == desktop.organization.id
    assert before == [(p.organization_combo.currentData(),
                       getattr(p, "_current_work_list_id", None)) for p in _pages(window)]
    assert window._shared_context == selected_settings
    assert window.organization_caption.text().replace("\n", " ") == desktop.organization.name
    assert window.houses_page._current_house_id == house_before
    if blocked_by == "operation":
        assert messages and "операции" in messages[0]
        window.organizations_page.refresh_availability()
        assert not selector.isEnabled()
        page._integration_busy = False
    else:
        monkeypatch.setattr(page, "flush_pending_changes", original_flush)


def test_switch_flushes_local_edits_and_independent_houses_remain_independent(desktop):
    window, repo = desktop.window, desktop.repository
    window.settings_page.independent_radio.setChecked(True)
    extra_house = repo.create_house(organization_id=desktop.organization.id, address="г. Тестовый, д. 2")
    page = window.planning_page
    page.refresh_organizations()
    page.house_combo.setCurrentIndex(page.house_combo.findData(extra_house.id))
    for target in (window.fixation_page, page, window.quality_page, page):
        window.navigation.setCurrentRow(window.pages.indexOf(target))
        workflows._settle(desktop.app)
    assert page.house_combo.currentData() == extra_house.id
    assert window.fixation_page.house_combo.currentData() == desktop.house.id
    other = repo.create_organization(name="Третья УО", ogrn="1022600000543")
    window.organizations_page.refresh_organizations()
    window.navigation.setCurrentRow(1)
    address = "г. Тестовый, ул. Исправленная, д. 1"
    window.houses_page.address_edit.setText(address)
    window.houses_page.address_edit.textEdited.emit(address)
    selector = window.organizations_page.organization_combo
    selector.setCurrentIndex(selector.findData(other.id))
    assert repo.get_house(desktop.house.id).address == address


def test_switch_keeps_act_photo_unplanned_work_and_plan(desktop):
    window, repo = desktop.window, desktop.repository
    _, quality_item = workflows._include_quality(window.quality_page, 1)
    workflows._add_acceptance_act(window.quality_page, quality_item)
    acts_before = window.quality_page.store.list_acts(quality_item.id)
    fixation_period = repo.get_work_fixation_period_for_month(desktop.work_list.id, year=2026, month=1)
    fact = repo.list_work_fixation_items(fixation_period.id)[0]
    repo.add_work_fixation_photo(fixation_item_id=fact.id, file_name="photo.jpg",
        content_type="image/jpeg", content=b"FICTIONAL PHOTO", description=None)
    repo.add_work_fixation_unplanned_item(fixation_period.id, work_code="20",
        work_name="Вымышленная работа", work_guid="fictional", fact_count=1, fact_total_cost="42")
    photos_before = repo.list_work_fixation_photos(fixation_item_id=fact.id)
    unplanned_before = repo.list_work_fixation_unplanned_items(fixation_period.id)
    plan = repo.get_or_create_work_plan(work_list_id=desktop.work_list.id, year=2026)
    items_before = repo.list_work_plan_items(plan.id)
    months_before = repo.list_work_plan_months(plan.id)
    other = repo.create_organization(name="Проверочная УО", ogrn="1022600000544")
    window.organizations_page.refresh_organizations()
    selector = window.organizations_page.organization_combo
    selector.setCurrentIndex(selector.findData(other.id))
    selector.setCurrentIndex(selector.findData(desktop.organization.id))
    assert window.quality_page.store.list_acts(quality_item.id) == acts_before
    assert repo.list_work_fixation_photos(fixation_item_id=fact.id) == photos_before
    assert repo.list_work_fixation_unplanned_items(fixation_period.id) == unplanned_before
    assert repo.list_work_plan_items(plan.id) == items_before
    assert repo.list_work_plan_months(plan.id) == months_before


@pytest.mark.parametrize("shared", [True, False])
def test_saved_organization_is_restored_on_startup(qt_app, tmp_path, monkeypatch, shared):
    repo = workflows.Repository(tmp_path / "restore.db")
    repo.create_organization(name="А первая УО", ogrn="1022600000540")
    selected = repo.create_organization(name="Я выбранная УО", ogrn="1022600000541")
    house = repo.create_house(organization_id=selected.id, address="г. Тестовый, д. 10")
    repo.set_setting("ui.context.organization_id", selected.id)
    repo.set_setting("ui.shared_context_enabled", "1" if shared else "0")
    monkeypatch.setattr(main_window, "LocalIntegrationGateway", workflows.FakeGateway)
    window = main_window.MainWindow(repo)
    try:
        assert window.organization_caption.text().replace("\n", " ") == selected.name
        for page in _pages(window):
            window.navigation.setCurrentRow(window.pages.indexOf(page))
            assert page.organization_combo.currentData() == selected.id
            if page is not window.houses_page:
                assert page.house_combo.currentData() == house.id
    finally:
        window.close()
        window.deleteLater()
        qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_branded_themes_executable_icon_and_address_space(desktop):
    window = desktop.window
    ico = icons.ASSET_ROOT.parent / "branding/app.ico"
    header = ico.read_bytes()
    assert struct.unpack_from("<HHH", header) == (0, 1, 7)
    sizes = {(header[6 + 16 * i] or 256, header[7 + 16 * i] or 256) for i in range(7)}
    assert sizes == {(n, n) for n in (16, 24, 32, 48, 64, 128, 256)}
    pixmaps = []
    window.resize(1000, 768)
    for theme in ("dark", "light"):
        (window.settings_page.dark_radio if theme == "dark" else window.settings_page.light_radio).setChecked(True)
        workflows._settle(desktop.app)
        assert icons.asset_path("app-logo").name == f"logo-{theme}.svg"
        assert not window.windowIcon().isNull()
        pixmaps.append(window.app_logo.pixmap().toImage())
        for page in (window.work_lists_page, window.planning_page, window.fixation_page, window.quality_page):
            window.navigation.setCurrentRow(window.pages.indexOf(page))
            workflows._settle(desktop.app)
            assert page.context_card.controls[0] is page.house_combo
            assert page.house_combo.width() >= 350
            if hasattr(page, "year_combo"):
                assert page.house_combo.width() > 2 * page.year_combo.width()
            assert not page.house_combo.isHidden()
    assert pixmaps[0] != pixmaps[1]
