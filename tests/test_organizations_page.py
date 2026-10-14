"""Moved organization controls retain their data and context contracts."""
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton

import test_ui_workflow as workflows
from act_test_data import populate_act_requisites
from gis_jkh_assistant.app import act_requisites_dialog, organizations_page
from gis_jkh_assistant.database.act_requisites_store import ActRequisitesStore

desktop = workflows.desktop
qt_app = workflows.qt_app


def test_sections_and_direct_organization_commands(desktop):
    window = desktop.window
    assert [window.navigation.item(i).text() for i in range(window.navigation.count())] == [
        "УО", "Дома", "Перечни работ и услуг", "Планирование",
        "Фиксация работ и услуг", "Оценка качества", "Настройки",
    ]
    page = window.organizations_page
    assert window.pages.currentWidget() is page
    assert not window.navigation.item(0).icon().isNull()
    window.settings_page.next_step_panel_checkbox.click()
    assert page.workflow.isHidden()
    window.settings_page.next_step_panel_checkbox.click()
    assert page.workflow.isVisibleTo(page)
    for button in (page.add_org_button, page.load_organization_button, page.save_button,
                   page.delete_org_button, page.requisites.load_button):
        assert button.isVisibleTo(page)
        assert button.menu() is None
    QTest.keyClick(page.requisites.table, Qt.Key.Key_Escape)
    assert page.requisites.isVisibleTo(page)
    assert page.organization_combo is window.houses_page.organization_combo
    assert page.organization_combo.parentWidget() is page.page_content
    page.form.name_edit.setText("Отменяемая правка")
    page.cancel_button.click()
    assert page.form.name_edit.text() == desktop.organization.name
    assert desktop.repository.get_organization(desktop.organization.id).name == desktop.organization.name
    assert not hasattr(window.houses_page, "organization_actions_button")
    page.workflow.primary.click()
    workflows._settle(desktop.app)
    assert window.pages.currentWidget() is window.houses_page
    assert window.houses_page.organization_context_label.isVisibleTo(window.houses_page)


@pytest.mark.parametrize("shared", [True, False])
def test_edit_and_switch_preserve_houses_and_context(desktop, shared):
    window, repo = desktop.window, desktop.repository
    (window.settings_page.shared_radio if shared else window.settings_page.independent_radio).setChecked(True)
    page = window.organizations_page
    other = repo.create_organization(name="Вторая вымышленная УО", ogrn="1022600000541")
    page.refresh_organizations(select_id=desktop.organization.id)
    page.form.name_edit.setText("УО после правки")
    page.form.comment_edit.setPlainText("Локальный комментарий")
    page.organization_combo.setCurrentIndex(page.organization_combo.findData(other.id))
    assert repo.get_organization(desktop.organization.id).name == "УО после правки"
    assert repo.get_organization(desktop.organization.id).comment == "Локальный комментарий"
    assert repo.get_house(desktop.house.id).organization_id == desktop.organization.id
    assert repo.get_work_list(desktop.work_list.id).house_id == desktop.house.id
    window.navigation.setCurrentRow(1)
    assert window.houses_page.organization_combo.currentData() == other.id
    assert window.houses_page.house_list.count() == 0
    window.navigation.setCurrentRow(0)
    page.organization_combo.setCurrentIndex(page.organization_combo.findData(desktop.organization.id))
    window.navigation.setCurrentRow(1)
    assert window.houses_page._current_house_id == desktop.house.id
    assert window.houses_page.organization_context_label.text() == "УО после правки"


def test_failed_basic_save_keeps_edits_and_blocks_selection_navigation_and_close(desktop, monkeypatch):
    page, repo = desktop.window.organizations_page, desktop.repository
    other = repo.create_organization(name="Вторая УО", ogrn="1022600000541")
    page.refresh_organizations()
    original = repo.update_organization
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: messages.append(a[2]))
    def fail(*a, **kw):
        raise sqlite3.OperationalError("TEST locked")
    monkeypatch.setattr(repo, "update_organization", fail)
    page.form.name_edit.setText("Несохранённое название")
    page.organization_combo.setCurrentIndex(page.organization_combo.findData(other.id))
    assert page.organization_combo.currentData() == desktop.organization.id
    assert page.form.name_edit.text() == "Несохранённое название"
    desktop.window.navigation.setCurrentRow(1)
    assert desktop.window.pages.currentWidget() is page
    from PySide6.QtGui import QCloseEvent
    close = QCloseEvent()
    desktop.window.closeEvent(close)
    assert not close.isAccepted()
    assert repo.get_organization(desktop.organization.id).name == desktop.organization.name
    assert messages
    monkeypatch.setattr(repo, "update_organization", original)
    page.save_button.click()
    desktop.window.navigation.setCurrentRow(1)
    assert desktop.window.pages.currentWidget() is desktop.window.houses_page


def test_entering_uo_applies_shared_selection_deferred_while_houses_were_busy(desktop):
    window, repo = desktop.window, desktop.repository
    other = repo.create_organization(name="Другая вымышленная УО", ogrn="1022600000541")
    repo.create_house(organization_id=other.id, address="Вымышленная улица, 2")
    window.houses_page.refresh_organizations()
    window.navigation.setCurrentRow(2)
    window.houses_page._workflow_busy = True
    window.work_lists_page.refresh_organizations()
    combo = window.work_lists_page.organization_combo
    combo.setCurrentIndex(combo.findData(other.id))
    assert window._shared_context["organization_id"] == other.id
    assert window.organizations_page.organization_combo.currentData() == desktop.organization.id
    window.houses_page._workflow_busy = False
    window.navigation.setCurrentRow(0)
    assert window.organizations_page.organization_combo.currentData() == other.id
    assert window.organizations_page.form.name_edit.text() == other.name


def test_manual_add_and_delete_keep_existing_confirmation_and_other_data(desktop, monkeypatch):
    page, repo = desktop.window.organizations_page, desktop.repository
    monkeypatch.setattr(organizations_page.OrganizationDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(organizations_page.OrganizationDialog, "values", lambda _: dict(
        name="Новая тестовая УО", ogrn="1022600000541", kpp=None, comment="Тест"))
    page.add_org_button.click()
    created = repo.get_organization_by_ogrn("1022600000541")
    assert page.organization_combo.currentData() == created.id
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    page.delete_org_button.click()
    assert repo.get_organization(created.id)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    page.delete_org_button.click()
    assert repo.get_organization(created.id) is None
    assert repo.get_house(desktop.house.id).organization_id == desktop.organization.id


def test_embedded_requisites_use_existing_history_options_and_preserve_documents(desktop, monkeypatch):
    page, repo = desktop.window.organizations_page, desktop.repository
    populate_act_requisites(repo, desktop.work_list)
    store = ActRequisitesStore(repo)
    person = store.list_people(organization_id=desktop.organization.id)[0]
    store.save_signature(person["id"], b"existing-signature", "signature.png")
    store.save_options(desktop.organization.id, stamp=b"existing-stamp", stamp_name="stamp.png")
    quality = desktop.window.quality_page.store
    period = quality.get_or_create_period(work_list_id=desktop.work_list.id, year=2026, month=1)
    item = quality.list_items(period.id)[0]
    act = quality.add_act(item_id=item.id, act_type="ACCEPTANCE_ACT", number="OLD", reg_date="31.01.2026",
        period_from="01.01.2026", period_to="31.01.2026", file_name="old.pdf", description=None,
        content_type="application/pdf", content=b"UNCHANGED USER ACT")
    fixation = repo.get_work_fixation_period_for_month(desktop.work_list.id, year=2026, month=1)
    fact = repo.list_work_fixation_items(fixation.id)[0]
    photo = repo.add_work_fixation_photo(fixation_item_id=fact.id, file_name="photo.jpg",
        content_type="image/jpeg", content=b"UNCHANGED PHOTO", description=None)
    page.refresh_current()
    editor = page.requisites
    assert not editor.isWindow()
    assert editor.table.rowCount() == 1
    assert "signature.png" in editor.image_info.text()
    assert "stamp.png" in editor.image_info.text()
    editor.insert_images.click()
    editor.customer_signature_placeholder.click()
    assert store.options(desktop.organization.id)["insert_images"] == 1
    assert store.options(desktop.organization.id)["customer_signature_placeholder"] == 1
    monkeypatch.setattr(act_requisites_dialog.ActPersonDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(act_requisites_dialog.ActPersonDialog, "values", lambda _: {
        **person, "position": "Генеральный директор"})
    edit = next(b for b in editor.findChildren(QPushButton) if b.text() == "Изменить запись…")
    edit.click()
    assert store.list_people(organization_id=desktop.organization.id)[0]["position"] == "Генеральный директор"
    assert store.list_people(organization_id=desktop.organization.id)[0]["signature"] == b"existing-signature"
    assert quality.get_act(act.id).content == b"UNCHANGED USER ACT"
    assert quality.act_link_item_ids(act.id) == [item.id]
    saved_photo = repo.list_work_fixation_photos(fixation_item_id=fact.id)[0]
    assert (saved_photo.id, saved_photo.content) == (photo.id, b"UNCHANGED PHOTO")


def test_embedded_chief_read_locks_context_and_finishes_before_close(desktop, monkeypatch):
    page = desktop.window.organizations_page
    monkeypatch.setattr(act_requisites_dialog, "GisIntegrationWorker", workflows.ControlledWorker)
    original_status = desktop.gateway.status
    from dataclasses import replace
    monkeypatch.setattr(desktop.gateway, "status", lambda: replace(original_status(), helper_version="0.1.32"))
    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: notices.append(a[2]))
    monkeypatch.setattr(page.requisites, "_edit", lambda **kw: notices.append(kw["candidate"]))
    page.requisites.load_button.click()
    worker = page.requisites._worker
    assert worker.command_type == "get_organization_act_signatory"
    assert not page.organization_combo.isEnabled()
    assert not page.basic.isEnabled()
    desktop.window.navigation.setCurrentRow(1)
    assert desktop.window.pages.currentWidget() is page
    worker.succeeded.emit({"ogrn": desktop.organization.ogrn, "candidates": [
        {"fullName": "Тестов Иван Иванович", "position": "Директор"}]})
    worker.finish()
    workflows._settle(desktop.app)
    assert page.requisites._worker is None
    assert page.organization_combo.isEnabled()
    assert notices[-1]["full_name"] == "Тестов Иван Иванович"
    assert not desktop.window._active_workers


def test_close_waits_for_embedded_chief_worker(desktop, monkeypatch):
    page, window = desktop.window.organizations_page, desktop.window
    monkeypatch.setattr(act_requisites_dialog, "GisIntegrationWorker", workflows.ControlledWorker)
    original_status = desktop.gateway.status
    from dataclasses import replace
    monkeypatch.setattr(desktop.gateway, "status", lambda: replace(original_status(), helper_version="0.1.32"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: None)
    page.requisites.load_button.click()
    worker = page.requisites._worker
    window.close()
    assert window._close_requested and not window._closing
    assert desktop.repository.get_organization(desktop.organization.id)
    worker.failed.emit("Изолированный тест: ответ недоступен")
    worker.finish()
    QTest.qWait(30)
    workflows._settle(desktop.app)
    assert window._closing
    assert not desktop.gateway.started


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_organization_page_at_100_percent_has_accessible_fields_and_buttons(desktop, theme):
    window, page = desktop.window, desktop.window.organizations_page
    getattr(window.settings_page, theme + "_radio").click()
    window.resize(1366, 768)
    workflows._settle(desktop.app)
    assert page.page_scroll.horizontalScrollBar().maximum() == 0
    page.form.name_edit.setFocus()
    QTest.keyClick(page.form.name_edit, Qt.Key.Key_Tab)
    assert desktop.app.focusWidget() is page.form.ogrn_edit
    for field in (page.form.name_edit, page.form.ogrn_edit, page.form.kpp_edit,
                  page.form.comment_edit, page.requisites.table, page.requisites.customer_signature_placeholder):
        page.page_scroll.ensureWidgetVisible(field)
        workflows._settle(desktop.app)
        assert field.isVisibleTo(page)
        assert field.width() > 100
    for button in page.findChildren(QPushButton):
        if button.isVisibleTo(page) and button.text():
            assert button.width() >= button.minimumSizeHint().width(), button.text()
