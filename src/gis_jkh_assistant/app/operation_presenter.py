"""Observe existing workers without changing their commands or queue execution."""
from PySide6.QtCore import QTimer

from .page_layouts import MONTHS

TITLES = {
    "save_work_plan": "Размещение плана", "load_work_plan": "Загрузка плана",
    "load_work_fixation": "Загрузка фиксации", "save_work_fixation": "Размещение фиксации",
    "load_quality_assessment": "Загрузка оценки", "save_quality_assessment": "Сохранение проектов",
    "approve_quality_assessment": "Размещение оценки", "cancel_quality_assessment": "Аннулирование оценки",
    "load_work_lists": "Получение перечней", "get_work_list_status": "Проверка перечня",
    "save_editable_work_list": "Сохранение проекта перечня", "upload_import_work_list": "Создание проекта перечня",
    "approve_work_list": "Размещение перечня", "load_houses": "Загрузка домов",
    "list_work_lists": "Загрузка перечней", "sync_work_list": "Проверка статуса перечня",
    "cancel_work_list": "Отмена перечня", "list_managed_houses": "Загрузка домов УО",
    "list_house_management_info": "Загрузка управления УО",
    "get_house_management_info": "Обновление управления дома",
    "download_work_list_export_template": "Подготовка проекта • загрузка основы",
    "list_organization_work_options": "Получение справочника УО",
}


def refresh(page):
    callback = getattr(page, "_refresh_workflow", None)
    if callback:
        callback()
    elif hasattr(page, "_refresh_gis_buttons"):
        page._refresh_gis_buttons()
    elif hasattr(page, "_refresh_gis_action_button"):
        page._refresh_gis_action_button()


def observe_worker(worker, page):
    if page is None:
        return
    strip = getattr(page, "operation_status", None)
    if strip is None or not hasattr(strip, "bind_worker"):
        return
    page._workflow_busy = True
    title = TITLES.get(worker.command_type, "Обмен с ГИС ЖКХ")
    month = worker.payload.get("month")
    year = worker.payload.get("year")
    context = f"{MONTHS[int(month) - 1]} {year}" if month and year else (str(year) if year else "")
    ids = getattr(page, "_queue_period_ids", [])
    queue = (page._queue_index, len(ids)) if getattr(page, "_queue_running", False) and ids and page._queue_index < len(ids) and not page._queue_failed_period_id else None
    strip.bind_worker(worker, title, context=context, queue=queue)
    refresh(page)
    plan_id = getattr(page, "_current_plan_id", None)
    succeeded = {"value": False}
    worker.succeeded.connect(lambda _result: succeeded.update(value=True))

    def finished():
        def settled():
            page._workflow_busy = False
            if succeeded["value"] and plan_id and worker.command_type in {"load_work_plan", "save_work_plan"}:
                from .workflow_presenter import mark_plan_synced
                mark_plan_synced(page, plan_id)
            refresh(page)
        QTimer.singleShot(0, settled)
    worker.finished.connect(finished)
    hook = getattr(page.window(), "_observe_context_operation", None)
    if hook:
        hook(page, worker)
