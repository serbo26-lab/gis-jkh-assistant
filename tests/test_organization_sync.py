"""Organization import uses anonymized data and an isolated local database."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget  # noqa: E402

from gis_jkh_assistant.app import organization_sync as sync  # noqa: E402
from gis_jkh_assistant.database import Repository  # noqa: E402


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def repository(tmp_path):
    repository = Repository(tmp_path / "organizations.db")
    yield repository
    repository.close()


def result(**fields):
    return {"organization": {
        "name": "  УК Пример  ", "ogrn": "1022600000000", "kpp": "260001001",
        "organizationGuid": "version-guid", "organizationRootGuid": "root-guid", **fields,
    }}


def test_normalization_keeps_version_and_root_identifiers_separate():
    imported = sync.normalize_organization_result(result())
    assert imported.name == "УК Пример"
    assert imported.gis_organization_guid == "version-guid"
    assert imported.gis_organization_root_guid == "root-guid"
    assert not hasattr(imported, "inn")


@pytest.mark.parametrize("reply", [
    {}, {"organization": "<html>Sign in</html>"}, result(name=""), result(ogrn="bad"),
    result(kpp="123"), result(organizationGuid=""), result(organizationRootGuid=None),
])
def test_invalid_details_cannot_create_an_organization(repository, reply):
    with pytest.raises(ValueError):
        sync.save_imported_organization(repository, sync.normalize_organization_result(reply))
    assert repository.list_organizations() == []


def test_import_adds_identifiers_without_extra_personal_data(repository):
    imported = sync.normalize_organization_result(result(orgEmail="example@example.invalid"))
    saved = sync.save_imported_organization(repository, imported)
    assert saved.name == "УК Пример"
    assert saved.ogrn == imported.ogrn
    assert saved.kpp == imported.kpp
    assert saved.gis_organization_guid == "version-guid"
    assert saved.gis_organization_root_guid == "root-guid"
    assert saved.comment is None
    assert len(repository.list_organizations()) == 1


def test_same_ogrn_preserves_manual_name_kpp_comment_houses_and_id(repository):
    original = repository.create_organization(
        name="Моё название УО", ogrn="1022600000000", kpp="260002002", comment="Моя заметка"
    )
    house = repository.create_house(organization_id=original.id, address="Примерная улица, 1")
    imported = sync.normalize_organization_result(result())
    saved = sync.save_imported_organization(repository, imported)
    assert saved.id == original.id
    assert (saved.name, saved.kpp, saved.comment) == (original.name, original.kpp, original.comment)
    assert repository.get_house(house.id).organization_id == original.id
    assert len(repository.list_organizations()) == 1
    assert saved.gis_organization_guid == imported.gis_organization_guid
    assert saved.gis_organization_root_guid == imported.gis_organization_root_guid
    assert sync.organization_import_updates(saved, imported) == {}


def test_existing_organization_gets_missing_kpp_and_keeps_comment(repository):
    original = repository.create_organization(name="Пример", ogrn="1022600000000", comment="Заметка")
    imported = sync.normalize_organization_result(result())
    saved = sync.save_imported_organization(repository, imported)
    assert saved.id == original.id
    assert saved.kpp == "260001001"
    assert saved.comment == "Заметка"


def test_other_ogrn_is_added_without_relinking_existing_houses(repository):
    original = repository.create_organization(name="Другая УО", ogrn="1022600000001")
    house = repository.create_house(organization_id=original.id, address="Примерная улица, 2")
    saved = sync.save_imported_organization(repository, sync.normalize_organization_result(result()))
    assert saved.id != original.id
    assert len(repository.list_organizations()) == 2
    assert repository.get_house(house.id).organization_id == original.id


def test_organization_update_rejects_different_ogrn(repository):
    other = repository.create_organization(name="Другая УО", ogrn="1022600000001")
    with pytest.raises(ValueError, match="ОГРН"):
        sync.organization_import_updates(other, sync.normalize_organization_result(result()))


def gateway(**updates):
    status = SimpleNamespace(**{
        "running": True, "helper_connected": True, "gis_page_ready": True,
        "helper_version": "0.1.30", **updates,
    })
    return SimpleNamespace(status=lambda: status)


@pytest.mark.parametrize("updates, fragment", [
    ({"running": False}, "Перезапустите"),
    ({"helper_connected": False}, "Включите"),
    ({"gis_page_ready": False}, "войдите"),
    ({"helper_version": "0.1.28"}, "0.1.30"),
    ({"helper_version": None}, "0.1.30"),
])
def test_connection_prerequisites_are_explained(updates, fragment):
    page = SimpleNamespace(integration_gateway=gateway(**updates))
    assert fragment in sync.organization_import_reason(page)


def test_ready_import_needs_no_manually_created_organization():
    page = SimpleNamespace(integration_gateway=gateway())
    assert sync.organization_import_reason(page) == ""
    page._bulk_operation_name = "houses"
    assert "Дождитесь" in sync.organization_import_reason(page)


class Worker(QObject):
    succeeded = Signal(dict)
    failed = Signal(str)
    progressed = Signal(dict)
    finished = Signal()
    created = []

    def __init__(self, gateway, command_type, payload, **options):
        super().__init__(options.get("parent"))
        self.command_type, self.payload = command_type, payload
        self.started = False
        self.created.append(self)

    def start(self):
        self.started = True

    def isRunning(self):  # noqa: N802 - QThread-compatible test double
        return self.started


class Status:
    def __init__(self):
        self.value = ""

    def set_text(self, text):
        self.value = text

    def text(self):
        return self.value

    def show(self):
        pass


class Page(QWidget):
    def __init__(self, repository):
        super().__init__()
        self.repository = repository
        self.integration_gateway = gateway()
        self.operation_status = self.bulk_operation_status = Status()
        self._bulk_operation_name = None
        self.selections = []

    def _flush_autosave(self):
        pass

    def _begin_bulk_operation(self, name, status):
        self._bulk_operation_name = name
        self._set_bulk_status(status)
        return True

    def _finish_bulk_operation(self, status):
        self._bulk_operation_name = None
        self._set_bulk_status(status)

    def _set_bulk_status(self, text):
        self.bulk_operation_status.set_text(text)

    def refresh_organizations(self, select_id):
        self.selections.append(select_id)


@pytest.fixture
def page(repository, application, monkeypatch):
    Worker.created.clear()
    monkeypatch.setattr(sync, "GisIntegrationWorker", Worker)
    page = Page(repository)
    yield page
    page.deleteLater()
    application.processEvents()


@pytest.mark.parametrize("accepted", [True, False])
def test_worker_import_previews_before_save_and_holds_lock_through_dialog(page, application, monkeypatch, accepted):
    calls = []

    def preview(dialog):
        calls.append(dialog.windowTitle())
        assert page.repository.list_organizations() == []
        assert page._bulk_operation_name == "organization"
        # The worker can finish while the modal preview runs its nested event loop.
        Worker.created[0].finished.emit()
        assert page._bulk_operation_name == "organization"
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr(sync.OrganizationImportDialog, "exec", preview)
    sync.load_organization_from_gis(page)
    worker = Worker.created[0]
    assert worker.started
    assert worker.command_type == "get_current_organization"
    assert worker.payload == {}
    assert page.repository.list_organizations() == []
    worker.succeeded.emit(result())
    application.processEvents()
    assert calls == ["Управляющая организация из ГИС ЖКХ"]
    assert page._bulk_operation_name is None
    assert page._organization_sync_worker is None
    organizations = page.repository.list_organizations()
    assert len(organizations) == (1 if accepted else 0)
    assert page.selections == ([organizations[0].id] if accepted else [])


def test_auth_failure_never_saves_and_releases_busy_lock(page, monkeypatch):
    failures = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: failures.append(args[-1]))
    sync.load_organization_from_gis(page)
    worker = Worker.created[0]
    worker.failed.emit("Войдите в личный кабинет ГИС ЖКХ.")
    worker.finished.emit()
    assert page.repository.list_organizations() == []
    assert page.selections == []
    assert page._bulk_operation_name is None
    assert len(failures) == 1
    assert failures[0].startswith("Войдите в личный кабинет ГИС ЖКХ.")
    assert "Загрузка организации не завершена" in failures[0]
    assert "Проверьте локальный список" in failures[0]
    assert "нужной УО" in failures[0]
    assert "повторите загрузку" in failures[0]


def test_invalid_helper_reply_does_not_open_preview_or_save(page, monkeypatch):
    failures = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: failures.append(args[-1]))
    sync.load_organization_from_gis(page)
    worker = Worker.created[0]
    worker.succeeded.emit({})
    worker.finished.emit()
    assert page.repository.list_organizations() == []
    assert failures
    assert page._bulk_operation_name is None


def test_old_helper_explains_upgrade_without_enqueuing(page):
    page.integration_gateway = gateway(helper_version="0.1.28")
    sync.load_organization_from_gis(page)
    assert Worker.created == []
    assert "0.1.30" in page.operation_status.text()


@pytest.mark.parametrize("existing_org", [True, False])
def test_real_window_import_adopts_new_org_and_preserves_existing_house_and_list(
    repository, application, monkeypatch, existing_org
):
    from gis_jkh_assistant.app import main_window
    from gis_jkh_assistant.app.operation_presenter import observe_worker
    from gis_jkh_assistant.gis_integration.local_gateway import IntegrationStatus

    class ReadyGateway:
        def start(self):
            pass

        def stop(self):
            pass

        def status(self):
            return IntegrationStatus(
                running=True, helper_connected=True, gis_page_ready=True, helper_version="0.1.30"
            )

    class ObservedWorker(Worker):
        def __init__(self, *args, **options):
            super().__init__(*args, **options)
            observe_worker(self, options.get("parent"))

    original = house = work_list = None
    repository.set_setting("ui.shared_context_enabled", "1")
    if existing_org:
        original = repository.create_organization(name="Моя УО", ogrn="1022600000000")
        house = repository.create_house(organization_id=original.id, address="Примерная улица, 1")
        work_list = repository.create_work_list(house_id=house.id, base_year=2026, items=[])
        for field, value in {
            "organization_id": original.id, "house_id": house.id,
            "work_list_id": work_list.id, "year": "2026",
        }.items():
            repository.set_setting(f"ui.context.{field}", value)
    Worker.created.clear()
    monkeypatch.setattr(main_window, "LocalIntegrationGateway", ReadyGateway)
    monkeypatch.setattr(sync, "GisIntegrationWorker", ObservedWorker)
    monkeypatch.setattr(sync.OrganizationImportDialog, "exec", lambda _: QDialog.DialogCode.Accepted)
    window = main_window.MainWindow(repository)
    try:
        page = window.houses_page
        assert page.load_organization_button.isEnabled()
        page.load_organization_button.click()
        worker = Worker.created[0]
        worker.succeeded.emit(result())
        worker.started = False
        worker.finished.emit()
        application.processEvents()
        application.processEvents()
        saved = repository.get_organization_by_ogrn("1022600000000")
        assert page.organization_combo.currentData() == saved.id
        assert window._shared_context["organization_id"] == saved.id
        assert not window._page_is_busy(page)
        if existing_org:
            assert saved.id == original.id
            assert page._current_house_id == house.id
            assert window._shared_context["house_id"] == house.id
            assert window._shared_context["work_list_id"] == work_list.id
        else:
            assert window._shared_context["house_id"] is None
            assert window._shared_context["work_list_id"] is None
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()
