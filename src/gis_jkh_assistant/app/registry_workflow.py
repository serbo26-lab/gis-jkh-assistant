"""Registry workflow presentation, without changing GIS commands or domain models."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from PySide6.QtWidgets import QInputDialog

from gis_jkh_assistant.gis_files.work_lists import WorkListExportError
from gis_jkh_assistant.gis_files.work_lists.exporter import _prepare_items


def work_list_fingerprint(page, work_list_id: str) -> str:
    """Track the actual composition and period, ignoring server metadata changes."""
    work_list = page.repository.get_work_list(work_list_id)
    if work_list is None:
        return ""
    items = page.repository.list_work_list_items(work_list_id)
    data = {
        "period": (
            work_list.start_year,
            work_list.start_month,
            work_list.end_year,
            work_list.end_month,
        ),
        "items": [
            (
                item.position,
                item.work_guid,
                item.work_code,
                item.work_name,
                item.price,
                item.volume,
                item.quantity,
                item.cost,
            )
            for item in items
        ],
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()


def mark_work_list_saved(page, work_list_id: str, fingerprint: str | None = None) -> None:
    page.repository.set_setting(
        f"ui_work_list_saved:{work_list_id}",
        fingerprint if fingerprint is not None else work_list_fingerprint(page, work_list_id),
    )


def can_initialize_draft_baseline(page, work_list) -> bool:
    """Only a metadata-only remote draft can acquire a baseline without a save.

    Existing row edits have no timestamps in the domain model, so registry/status
    synchronization cannot prove that an older local composition matches GIS.
    """
    if page.repository.list_work_list_items(work_list.id):
        return False
    try:
        updated = datetime.fromisoformat(work_list.updated_at or "")
        synced = datetime.fromisoformat(work_list.gis_last_sync_at or "")
    except ValueError:
        return False
    updated = updated.replace(tzinfo=updated.tzinfo or timezone.utc)
    synced = synced.replace(tzinfo=synced.tzinfo or timezone.utc)
    return updated <= synced


def check_work_list(page) -> None:
    """Use the exporter's established row checks for the explicit local review step."""
    page._save_period()
    if page._period_dirty:
        page._composition_review_error = "Начало периода не может быть позже окончания."
        page.operation_status.set_text("Проверьте период • " + page._composition_review_error)
        page.operation_status.show()
        refresh_work_lists(page)
        return
    work_list_id = page._current_work_list_id
    if not work_list_id:
        return
    work_list = page.repository.get_work_list(work_list_id)
    if work_list is None:
        return
    error = page.repository.work_list_management_period_error(
        work_list.house_id,
        start_year=work_list.start_year,
        start_month=work_list.start_month,
        end_year=work_list.end_year,
        end_month=work_list.end_month,
    )
    items = page.repository.list_work_list_items(work_list_id)
    if not error:
        try:
            prepared = _prepare_items(items)
            if not prepared:
                error = "Заполните значения хотя бы одной работы или услуги."
        except WorkListExportError as exc:
            error = str(exc)
    if error:
        page._composition_review_error = str(error)
        page.repository.set_setting(f"ui_work_list_checked:{work_list_id}", None)
        page.operation_status.set_text("Проверьте состав • " + str(error))
        page.operation_status.setProperty("tone", "warning")
        page.operation_status.show()
    else:
        page._composition_review_error = ""
        page.repository.set_setting(
            f"ui_work_list_checked:{work_list_id}",
            work_list_fingerprint(page, work_list_id),
        )
        page.operation_status.set_text(
            f"Состав проверен • заполнено {len(prepared)} из {len(items)} строк. "
            "Справочник ГИС будет проверен при сохранении проекта."
        )
        page.operation_status.setProperty("tone", "success")
        page.operation_status.show()
    refresh_work_lists(page)


def replace_from_template(page) -> None:
    """Select a composition template at the table, separately from list creation."""
    labels = [page.template_combo.itemText(i) for i in range(1, page.template_combo.count())]
    if not labels:
        page._manage_templates()
        return
    label, accepted = QInputDialog.getItem(
        page,
        "Заменить состав по шаблону",
        "Шаблон состава:",
        labels,
        0,
        False,
    )
    if accepted:
        page.template_combo.setCurrentIndex(labels.index(label) + 1)
        page._replace_current_from_template()


def _connection_reason(page) -> str:
    gateway = page.integration_gateway
    if gateway is None:
        return "Подключите браузерный помощник и откройте личный кабинет ГИС ЖКХ."
    status = gateway.status()
    if not status.running:
        return "Локальный шлюз не запущен. Перезапустите приложение и проверьте подключение в настройках."
    if not status.helper_connected:
        return "Подключите браузерный помощник в личном кабинете ГИС ЖКХ."
    if not status.gis_page_ready:
        return "Откройте личный кабинет ГИС ЖКХ в подключённом браузере."
    return ""


def refresh_work_lists(page) -> None:
    if not hasattr(page, "workflow"):
        return
    selected = page._current_work_list_id
    work_list = page.repository.get_work_list(selected) if selected else None
    house_id = page.house_combo.currentData()
    house = page.repository.get_house(house_id) if house_id else None
    busy = bool(page._integration_busy or getattr(page, "_workflow_busy", False))
    for widget in (
        page.organization_combo,
        page.house_combo,
        page.registry_combo,
        page.year_spin,
        page.template_combo,
    ):
        widget.setEnabled(not busy)
    connection = _connection_reason(page)
    remote_reason = "Дождитесь завершения текущей операции." if busy else connection
    if not remote_reason and (not house or not house.house_object_guid):
        remote_reason = "Загрузите сведения о доме в разделе «Дома»: нужен идентификатор ГИС."
    page.network_hint.setText(remote_reason)
    page.network_hint.setVisible(bool(remote_reason))
    page.load_gis_lists_button.setToolTip(remote_reason or "Загрузить все перечни выбранного дома из ГИС ЖКХ.")
    page.sync_gis_button.setToolTip(remote_reason or "Проверить текущее состояние выбранного перечня в ГИС ЖКХ.")
    status = str(work_list.gis_status or "").upper() if work_list else ""
    editable = bool(work_list and status in {"", "NOT_FOUND", "CANCELLED", "DRAFT"})
    for button in (page.create_button, page.creation_toggle):
        button.setEnabled(bool(house) and not busy)
    page.templates_button.setEnabled(bool(page.organization_combo.currentData()) and not busy)
    page.create_from_current_button.setEnabled(bool(work_list) and not busy)
    page.delete_button.setEnabled(bool(work_list) and not busy)
    page.export_button.setEnabled(bool(work_list) and not busy)
    page.save_as_template_button.setEnabled(bool(work_list) and not busy)
    for widget in (
        page.start_month,
        page.start_year,
        page.end_month,
        page.end_year,
        page.items_table,
        page.add_works_button,
        page.remove_works_button,
        page.clear_works_button,
        page.replace_from_template_button,
        page.lazy_fill_button,
    ):
        widget.setEnabled(editable and not busy)
    for widget in (page.add_works_button, page.replace_from_template_button):
        widget.setEnabled(editable and not busy and not connection)
        widget.setToolTip(
            connection or "Работы проверяются по актуальному справочнику УО в ГИС ЖКХ."
        )
    page.remote_danger.sync()
    page.remote_danger.setEnabled(page.cancel_gis_button.isEnabled() and not busy)
    page.remote_danger.setVisible(page.cancel_gis_button.isEnabled())
    page.local_list_danger.sync()
    page.local_list_danger.setEnabled(bool(work_list) and not busy)
    page.composition_menu.sync()
    page.composition_danger.sync()
    page.composition_menu.setEnabled(bool(work_list) and not busy)
    page.composition_danger.setEnabled(editable and not busy)
    page.project_actions.setEnabled(bool(work_list) and not busy)
    page.project_actions.sync()
    page.xlsx_network_actions.sync()
    page.xlsx_network_actions.setEnabled(not busy and not connection)
    page.context_status.setVisible(bool(work_list))
    page.items_hint.setText(
        "Локальные изменения сохраняются автоматически. Заполните цену, объём и количество "
        "либо количество и стоимость."
        if editable
        else "Состав доступен для просмотра. Чтобы изменить размещённый перечень, сначала "
        "отмените его в меню «Опасные действия»; связанные данные будут удалены."
        if work_list
        else "Выберите существующий перечень или создайте новый."
    )
    if page.creation_toggle.isChecked():
        page.workflow.show_step(
            0,
            "Создание нового перечня",
            "Укажите год и начальный состав в карточке ниже.",
            "create",
            "Создать перечень",
            reason=""
            if house and not busy
            else "Выберите дом для нового перечня."
            if not house
            else "Дождитесь завершения операции.",
        )
        return
    if work_list is None:
        page.workflow.show_step(
            0,
            "Выберите перечень дома",
            "Выберите существующий перечень в контексте сверху "
            "или создайте новый. Перечни из ГИС доступны через загрузку.",
            "new",
            "Создать новый перечень",
            reason=""
            if house and not busy
            else "Сначала выберите управляющую организацию и дом."
            if not house
            else "Дождитесь завершения операции.",
        )
        return
    historical = (work_list.end_year, work_list.end_month) < (date.today().year, date.today().month)
    state = page._work_list_status_text(work_list)
    qualifiers = ["Исторический период"] if historical else []
    qualifiers.append("Редактируется локально" if editable else "Только просмотр")
    page.context_status.setText(" • ".join([work_list.period_label, state, *qualifiers]))
    items = page.repository.list_work_list_items(work_list.id)
    fingerprint = work_list_fingerprint(page, work_list.id)
    checked = (
        page.repository.get_setting(f"ui_work_list_checked:{work_list.id}") == fingerprint
        and not page._period_dirty
    )
    saved = page.repository.get_setting(f"ui_work_list_saved:{work_list.id}")
    dirty = bool(
        (saved != fingerprint if status == "DRAFT" else saved and saved != fingerprint)
        or page._period_dirty
    )
    if status == "APPROVED":
        page.workflow.show_step(
            3,
            "Перечень размещён",
            "Состав доступен для просмотра. Следующий этап — планирование "
            "по этому же дому и перечню.",
            "next",
            "Перейти к планированию",
            reason="Дождитесь завершения операции." if busy else "",
            completed=True,
        )
    elif status == "IMPORT_SENT":
        page.workflow.show_step(
            2,
            "ГИС обрабатывает перечень",
            "Файл принят. Проверьте готовность проекта перед размещением.",
            "sync",
            "Проверить готовность проекта",
            reason=remote_reason,
        )
    elif status == "DRAFT" and not dirty:
        page.workflow.show_step(
            3,
            "Проект в ГИС готов к размещению",
            "Проверьте параметры проекта. После размещения станет доступно планирование.",
            "publish",
            "Разместить",
            reason=remote_reason,
        )
    elif status in {"", "NOT_FOUND", "CANCELLED", "DRAFT"}:
        if not items:
            page.workflow.show_step(
                0,
                "Заполните состав перечня",
                "Добавьте работы из справочника УО, затем заполните значения в таблице.",
                "add",
                "Добавить работу",
                reason=connection or ("Дождитесь завершения операции." if busy else ""),
            )
        elif not checked:
            page.workflow.show_step(
                1,
                "Проверьте локальный состав" + (" • есть изменения" if dirty else ""),
                "Проверка периода и заполненных значений выполняется до отправки в ГИС.",
                "check",
                "Проверить состав",
                reason="Дождитесь завершения операции." if busy else "",
            )
            if getattr(page, "_composition_review_error", ""):
                page.workflow.detail.setText(page._composition_review_error)
        else:
            page.workflow.show_step(
                2,
                "Состав проверен • сохраните проект",
                "Сохранение создаёт проект в ГИС. Размещение будет отдельным следующим шагом.",
                "save" if status in {"CANCELLED", "DRAFT"} else "prepare",
                "Сохранить проект",
                reason=remote_reason,
            )
    else:
        page.workflow.show_step(
            2,
            "Проверьте состояние в ГИС",
            "Статус перечня требует проверки перед следующим шагом.",
            "sync",
            "Проверить статус",
            reason=remote_reason,
        )


def refresh_houses(page) -> None:
    if not hasattr(page, "workflow"):
        return
    organization_id = page.organization_combo.currentData()
    house = page.repository.get_house(page._current_house_id) if page._current_house_id else None
    busy = bool(
        page._bulk_operation_name
        or page._bulk_worker_running()
        or getattr(page, "_workflow_busy", False)
    )
    for widget in (page.organization_combo, page.house_list, page.search_edit):
        widget.setEnabled(not busy)
    page.card_widget.setEnabled(not busy and bool(house))
    connection = _connection_reason(page)
    ready = bool(organization_id and not busy and not connection)
    from .organization_sync import organization_import_reason
    organization_reason = organization_import_reason(page)
    page.load_organization_button.setEnabled(not organization_reason)
    page.load_organization_button.setToolTip(organization_reason or "Реквизиты из открытого кабинета ГИС ЖКХ. Перед добавлением можно проверить.")
    page.add_house_button.setEnabled(bool(organization_id) and not busy)
    page.add_org_button.setEnabled(not busy)
    page.edit_org_button.setEnabled(bool(organization_id) and not busy)
    page.delete_org_button.setEnabled(bool(organization_id) and not busy)
    page.organization_danger.setEnabled(bool(organization_id) and not busy)
    page.organization_danger.sync()
    page.organization_add_action.setEnabled(not busy)
    page.organization_edit_action.setEnabled(bool(organization_id) and not busy)
    page.organization_delete_action.setEnabled(bool(organization_id) and not busy)
    page.organization_actions_button.setEnabled(not busy)
    page.gis_actions_button.setEnabled(ready)
    for button, action in (
        (page.sync_houses_button, page.sync_houses_action),
        (page.sync_all_management_button, page.sync_management_action),
        (page.sync_all_work_lists_button, page.sync_work_lists_action),
    ):
        button.setEnabled(ready)
        button.setToolTip(connection or "Для всех домов выбранной управляющей организации.")
        action.setEnabled(ready)
    page.sync_management_button.setEnabled(bool(house and house.house_object_guid) and ready)
    page.delete_house_button.setEnabled(bool(house) and not busy)
    if not organization_id:
        from_gis = not organization_reason
        page.workflow.show_step(
            0,
            "Добавьте управляющую организацию",
            "Возьмите реквизиты из открытого кабинета ГИС и проверьте их перед добавлением. "
            "Затем загрузите дома этой организации. Ручное добавление также доступно ниже."
            if from_gis else "Работа начинается с УО. Добавьте её реквизиты вручную. " + organization_reason,
            "organization_load" if from_gis else "organization",
            "Добавить УО из ГИС" if from_gis else "Добавить УО вручную",
            reason="Дождитесь завершения операции." if busy else "",
        )
    elif not page.house_list.count():
        searching = bool(page.search_edit.text().strip())
        page.workflow.show_step(
            1,
            "Поиск не дал результатов" if searching else "Загрузите реестр домов УО",
            "Измените адрес в поиске."
            if searching
            else "Синхронизация загрузит все дома выбранной УО. Дом также можно добавить вручную.",
            "reset_search" if searching else "load",
            "Сбросить поиск" if searching else "Загрузить дома из ГИС",
            reason=("Дождитесь завершения операции." if busy else "")
            if searching
            else ("Дождитесь завершения операции." if busy else connection),
        )
    elif not house:
        page.workflow.show_step(
            1,
            "Выберите дом",
            "Выберите адрес в списке слева, чтобы открыть карточку.",
            None,
            "Выберите дом слева",
            reason="Дом пока не выбран.",
        )
    elif not house.house_object_guid:
        page.workflow.show_step(
            2,
            "Свяжите дом с ГИС ЖКХ",
            "Для обмена перечнями нужны сведения о доме из ГИС. "
            "Локальная карточка сохраняется автоматически.",
            "load",
            "Загрузить сведения о доме",
            reason="Дождитесь завершения операции." if busy else connection,
        )
    else:
        detail = "Проверьте период управления, площадь и тарифы в карточке. "
        detail += "Выбранный дом будет сохранён при переходе к перечням."
        page.workflow.show_step(
            3,
            "Дом выбран • продолжите работу с перечнями",
            detail,
            "next",
            "Перейти к перечням",
            reason="Дождитесь завершения операции." if busy else "",
        )
