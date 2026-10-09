"""Read the authorized organization, preview it and save it locally by OGRN."""

from __future__ import annotations

from .error_messages import context_text, error_text

import re
import sqlite3
from dataclasses import dataclass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from gis_jkh_assistant.database import ManagementOrganization, Repository
from gis_jkh_assistant.gis_integration.organization_context import (
    MIN_ORGANIZATION_ROUTING_HELPER_VERSION, helper_supports_organization_routing,
)

from .gis_integration_worker import GisIntegrationWorker, connect_result


@dataclass(frozen=True, slots=True)
class OrganizationImport:
    name: str
    ogrn: str
    kpp: str | None
    gis_organization_guid: str
    gis_organization_root_guid: str


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_organization_result(result: dict) -> OrganizationImport:
    """Accept the helper's narrow result, rejecting login/empty/invalid replies."""
    item = result.get("organization") if isinstance(result, dict) else None
    if not isinstance(item, dict):
        raise ValueError("ГИС ЖКХ не вернул сведения об управляющей организации.")
    name = _text(item.get("name"))
    ogrn = _text(item.get("ogrn"))
    kpp = _text(item.get("kpp")) or None
    guid = _text(item.get("organizationGuid"))
    root_guid = _text(item.get("organizationRootGuid"))
    if not name or not re.fullmatch(r"[0-9]{13}(?:[0-9]{2})?", ogrn):
        raise ValueError("В сведениях ГИС ЖКХ отсутствуют корректные наименование или ОГРН.")
    if kpp and not re.fullmatch(r"[0-9]{9}", kpp):
        raise ValueError("ГИС ЖКХ вернул некорректный КПП организации.")
    if not guid or not root_guid:
        raise ValueError("ГИС ЖКХ не вернул идентификаторы организации. Повторите загрузку.")
    return OrganizationImport(name, ogrn, kpp, guid, root_guid)


def organization_import_updates(
    organization: ManagementOrganization, imported: OrganizationImport
) -> dict[str, str]:
    """Link the existing OGRN, filling missing fields without replacing manual data."""
    if organization.ogrn != imported.ogrn:
        raise ValueError("ОГРН локальной организации отличается от ОГРН в ГИС ЖКХ.")
    updates = {
        "gis_organization_guid": imported.gis_organization_guid,
        "gis_organization_root_guid": imported.gis_organization_root_guid,
    }
    if not organization.name.strip():
        updates["name"] = imported.name
    if not (organization.kpp or "").strip() and imported.kpp:
        updates["kpp"] = imported.kpp
    return {key: value for key, value in updates.items() if getattr(organization, key) != value}


def save_imported_organization(
    repository: Repository, imported: OrganizationImport
) -> ManagementOrganization:
    """Deduplicate by OGRN, retaining the original id and its houses and comments."""
    existing = repository.get_organization_by_ogrn(imported.ogrn)
    if existing is None:
        existing = repository.create_organization(
            name=imported.name, ogrn=imported.ogrn, kpp=imported.kpp
        )
    updates = organization_import_updates(existing, imported)
    if updates:
        repository.update_organization(existing.id, **updates)
    saved = repository.get_organization(existing.id)
    assert saved is not None
    return saved


def organization_import_reason(page) -> str:
    """Explain prerequisites without requiring a manually created organization."""
    if getattr(page, "_bulk_operation_name", None) is not None or getattr(page, "_workflow_busy", False):
        return "Дождитесь завершения текущей операции."
    gateway = page.integration_gateway
    if gateway is None:
        return "Подключите браузерный помощник и откройте личный кабинет ГИС ЖКХ."
    status = gateway.status()
    if not status.running:
        return "Перезапустите программу: локальное подключение к браузеру не запущено."
    if not status.helper_connected:
        return "Включите «ГИС ЖКХ помощник» в браузере с открытым личным кабинетом."
    if not status.gis_page_ready:
        return "Откройте личный кабинет ГИС ЖКХ и войдите под своей учётной записью."
    version = _text(getattr(status, "helper_version", None))
    if not helper_supports_organization_routing(version):
        minimum = ".".join(map(str, MIN_ORGANIZATION_ROUTING_HELPER_VERSION))
        return f"Обновите «ГИС ЖКХ помощник» до версии {minimum} и нажмите «Перезагрузить» в расширениях браузера."
    return ""


class OrganizationImportDialog(QDialog):
    """A plain review step with a clearly named local save action."""

    def __init__(self, imported: OrganizationImport, existing: ManagementOrganization | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Управляющая организация из ГИС ЖКХ")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Получены сведения из открытого личного кабинета. Проверьте организацию перед добавлением."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        name = QLabel(imported.name)
        name.setWordWrap(True)
        form.addRow("Организация в ГИС ЖКХ:", name)
        form.addRow("ОГРН:", QLabel(imported.ogrn))
        form.addRow("КПП:", QLabel(imported.kpp or "Не указан"))
        layout.addLayout(form)
        if existing:
            if existing.kpp and existing.kpp != imported.kpp:
                form.addRow("КПП в программе (сохранится):", QLabel(existing.kpp))
            message = (
                f"В программе уже есть УО с этим ОГРН: {existing.name}.\n"
                "Новая карточка не создаётся. Сохраняются её название, заполненный КПП, комментарий и все дома. "
                "Будут добавлены идентификаторы ГИС ЖКХ и недостающий КПП."
            )
            changed_identity = (
                existing.gis_organization_guid
                and existing.gis_organization_guid != imported.gis_organization_guid
            ) or (
                existing.gis_organization_root_guid
                and existing.gis_organization_root_guid != imported.gis_organization_root_guid
            )
            if changed_identity:
                message += "\nИдентификаторы ГИС ЖКХ изменились: после сохранения они будут обновлены."
        else:
            message = "Организация будет добавлена в программу. Сведения в ГИС ЖКХ не изменяются."
        note = QLabel(message)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Сохранить связь с ГИС ЖКХ" if existing else "Добавить УО в программу"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def load_organization_from_gis(page) -> None:
    """Use the existing async worker; never save credentials or write to GIS."""
    reason = organization_import_reason(page)
    if reason:
        page.operation_status.set_text(reason)
        page.operation_status.show()
        return
    page._flush_autosave()
    if not page._begin_bulk_operation("organization", "Загрузка управляющей организации из ГИС ЖКХ…"):
        return
    worker = GisIntegrationWorker(
        page.integration_gateway, "get_current_organization", {}, timeout=60.0, parent=page
    )
    page._organization_sync_worker = worker
    state = {"processing": False, "finished": False, "cleaned": False, "selected_id": None}

    def select_saved_organization() -> None:
        if getattr(page.window(), "_closing", False) or getattr(page.window(), "_close_requested", False):
            return
        combo = getattr(page, "organization_combo", None)
        previous_id = combo.currentData() if combo is not None else None
        page.refresh_organizations(select_id=state["selected_id"])
        if combo is not None and previous_id != combo.currentData():
            # refresh_organizations deliberately blocks its rebuilding signals.
            # Notify the ordinary organization/context listeners for this explicit
            # selection, including the first organization when the registry is empty.
            combo.currentIndexChanged.emit(combo.currentIndex())

    def cleanup() -> None:
        if state["cleaned"] or not state["finished"] or state["processing"]:
            return
        state["cleaned"] = True
        page._organization_sync_worker = None
        page._finish_bulk_operation(page.bulk_operation_status.text())
        if state["selected_id"]:
            # Wait for the worker observer to release its shared-context lock.
            QTimer.singleShot(0, select_saved_organization)

    def succeeded(result: dict) -> None:
        state["processing"] = True
        try:
            imported = normalize_organization_result(result)
            existing = page.repository.get_organization_by_ogrn(imported.ogrn)
            dialog = OrganizationImportDialog(imported, existing, page)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            dialog.deleteLater()
            if not accepted:
                page._set_bulk_status("Добавление УО отменено. Локальные данные не изменены.")
                return
            saved = save_imported_organization(page.repository, imported)
            state["selected_id"] = saved.id
            page._set_bulk_status(
                ("Связь УО с ГИС ЖКХ сохранена" if existing else "УО добавлена из ГИС ЖКХ")
                + f" • {saved.name}. Теперь можно загрузить дома."
            )
        except (ValueError, sqlite3.Error) as exc:
            page._set_bulk_status("Не удалось добавить управляющую организацию.")
            QMessageBox.critical(page, "Не удалось добавить УО", error_text(exc, context=context_text(page)))
        finally:
            state["processing"] = False
            cleanup()

    def failed(message: str) -> None:
        state["processing"] = True
        try:
            page._set_bulk_status("Ошибка загрузки УО • " + message)
            QMessageBox.critical(page, "Не удалось загрузить УО из ГИС ЖКХ", error_text(
                message, context=context_text(page),
                effect="Загрузка организации не завершена. Проверьте локальный список перед повтором.",
                recovery="Проверьте подключение помощника и авторизованную вкладку нужной УО, затем повторите загрузку.",
            ))
        finally:
            state["processing"] = False
            cleanup()

    def finished() -> None:
        state["finished"] = True
        cleanup()

    connect_result(worker, worker.succeeded, succeeded)
    connect_result(worker, worker.failed, failed)
    worker.finished.connect(finished)
    worker.start()
