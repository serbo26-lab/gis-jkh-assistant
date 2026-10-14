"""Observe existing workers without changing their commands or queue execution."""
from PySide6.QtCore import QTimer

from .page_layouts import MONTHS

TITLES = {
    "get_organization_act_signatory": "Получение руководителя УО",
    "resolve_planned_work_units": "Подготовка PDF • уточнение единиц измерения",
    "get_current_organization": "Загрузка управляющей организации",
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
    hook = getattr(page.window(), "_observe_context_operation", None)
    strip = getattr(page, "operation_status", None)
    if strip is None or not hasattr(strip, "bind_worker"):
        if hook:
            hook(page, worker)
        return
    page._workflow_busy = True
    title = TITLES.get(worker.command_type, "Обмен с ГИС ЖКХ")
    month = worker.payload.get("month")
    year = worker.payload.get("year")
    context = f"{MONTHS[int(month) - 1]} {year}" if month and year else (str(year) if year else "")
    ids = getattr(page, "_queue_period_ids", [])
    queue = (page._queue_index, len(ids)) if getattr(page, "_queue_running", False) and ids and page._queue_index < len(ids) and not page._queue_failed_period_id else None
    strip.bind_worker(worker, title, context=context, queue=queue)
    selector = getattr(page, "year_combo", None)
    strip._operation_owner = (getattr(page, "_current_work_list_id", None), selector.currentData() if selector is not None else None)
    refresh(page)
    def finished():
        def settled():
            if getattr(page.window(), "_closing", False):
                return
            page._workflow_busy = False
            refresh(page)
        QTimer.singleShot(0, settled)
    worker.finished.connect(finished)
    if hook:
        hook(page, worker)
