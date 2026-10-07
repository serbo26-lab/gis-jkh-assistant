"""Present the next useful step from persisted state and existing GIS rules."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from gis_jkh_assistant.quality_assessment import helper_supports_quality_assessment
from gis_jkh_assistant.work_fixation import helper_supports_fixations, item_has_fact
from gis_jkh_assistant.work_planning import (
    active_months_for_work_list, build_gis_work_plan_command_payload,
    helper_supports_work_plans,
)

from .page_layouts import MONTHS
from .workflow_state import MonthState, fixation_step, quality_step
from .workflow_widgets import repolish


def connection_reason(page, supports) -> str:
    status = page.integration_gateway.status()
    if not status.running:
        return "Локальный шлюз не запущен. Перезапустите приложение и проверьте подключение в настройках."
    if not status.helper_connected:
        return "Подключите браузерный помощник к шлюзу."
    if not status.gis_page_ready:
        return "Откройте ГИС ЖКХ в браузере и войдите в систему."
    if not supports(status.helper_version):
        return "Обновите браузерный помощник для этого сценария."
    return ""


def busy(page) -> bool:
    return bool(getattr(page, "_integration_busy", False)
                or getattr(page, "_workflow_busy", False)
                or getattr(page, "_queue_running", False))


def context_lock(page, locked: bool):
    for name in ("organization_combo", "house_combo", "work_list_combo", "year_combo", "scope_switcher"):
        control = getattr(page, name, None)
        if control is not None:
            control.setEnabled(not locked)


def _target(page):
    work_list = page.repository.get_work_list(page._current_work_list_id) if page._current_work_list_id else None
    year = page.year_combo.currentData()
    house = page.repository.get_house(work_list.house_id) if work_list else None
    return work_list, int(year) if year is not None else None, house


def _scope_label(page, active, year):
    if page._scope_is_period():
        return f"Действия: {year}, {len(active)} мес. • Таблица: {MONTHS[page._current_month - 1]}" if active and page._current_month else "Нет доступных месяцев"
    month = page._current_month
    return f"{MONTHS[month - 1]} {year}" if month and year else "Выберите месяц"


def refresh_monthly(page, kind: str):
    if not hasattr(page, "workflow"):
        return
    quality = kind == "quality"
    work_list, year, house = _target(page)
    active = active_months_for_work_list(work_list, year) if work_list and year else ()
    locked = busy(page)
    context_lock(page, locked)
    has_context = bool(active and page._current_period_id)
    local = has_context and not locked
    page.table.setEnabled(local)
    if not quality:
        page.unplanned_table.setEnabled(local)
    page.scope_hint.setText(_scope_label(page, active, year))
    page.empty_hint.setVisible(not has_context)
    supported = helper_supports_quality_assessment if quality else helper_supports_fixations
    net_reason = connection_reason(page, supported)
    net_ready = local and not net_reason
    load = page.load_gis_button if quality else page.load_month_button
    load_reason = net_reason
    if work_list and (not house or not (house.fias_house_guid or house.gis_house_guid)):
        load_reason = "Получите идентификаторы дома в разделе «Дома»."
    load.setEnabled(local and not load_reason)
    load.setToolTip(load_reason or "Получить данные для выбранной области; локальные изменения будут проверены перед заменой.")
    load.setText("Загрузить из ГИС")
    all_states = []
    periods = (page.store.list_periods(work_list.id, year=year) if quality
               else page.repository.list_work_fixation_periods(work_list.id, year=year)) if work_list and year else []
    by_month = {p.month: p for p in periods}
    snapshot_key = (page.repository.connection.total_changes, work_list.id if work_list else None, year)
    cached_key, cached_states = getattr(page, "_workflow_month_snapshot", (None, {}))
    if cached_key != snapshot_key:
        cached_states = {}
    for month, button in page.month_buttons.items():
        button.setEnabled(month in active and not locked)
        button.setChecked(month == page._current_month)
        period = by_month.get(month)
        status = str(period.gis_status or "LOCAL").upper() if period else "LOCAL"
        error = period.gis_last_error if period else None
        if month in cached_states:
            has_data, validation = cached_states[month]
        else:
            has_data = (any(i.included for i in page.store.list_items(period.id)) if quality
                        else page._period_has_data(period.id)) if period else False
            validation = page._validation_error(period.id, require_acts=True) if quality and has_data else None
            cached_states[month] = (has_data, validation)
        if month not in active:
            suffix, tone = "вне периода", "muted"
        elif error or status == "ERROR":
            suffix, tone = "ошибка", "danger"
        elif status in {"SAVED", "APPROVED"}:
            suffix, tone = "размещено", "success"
        elif status == "DRAFT":
            suffix, tone = "проект в ГИС", "accent"
        elif status in {"MODIFIED", "CANCELLED"}:
            suffix, tone = ("изменено" if status == "MODIFIED" else "аннулировано"), "warning"
        else:
            suffix, tone = ("локально" if has_data else "пусто"), ("accent" if has_data else "muted")
        button.setText(f"{MONTHS[month - 1]}\n{suffix}")
        button.setToolTip(error or (f"{MONTHS[month - 1]} {year}: {suffix}" if year else suffix))
        if button.property("tone") != tone:
            button.setProperty("tone", tone)
            repolish(button)
        if month in active:
            all_states.append(MonthState(month, status, has_data, validation, error))
    page._workflow_month_snapshot = (snapshot_key, cached_states)
    states = all_states if page._scope_is_period() else [s for s in all_states if s.month == page._current_month]
    failed_id = page._queue_failed_period_id
    failed_period = (page.store.get_period(failed_id) if quality else page.repository.get_work_fixation_period(failed_id)) if failed_id else None
    retry_here = bool(failed_period and work_list and failed_period.work_list_id == work_list.id and failed_period.year == year)
    page.continue_queue_button.setVisible(retry_here)
    page.continue_queue_button.setEnabled(retry_here and not locked and not net_reason)
    if retry_here:
        page.continue_queue_button.setText(f"Продолжить с {MONTHS[failed_period.month - 1]} {year}")
    for name in (("form_month_button", "auto_act_month_button", "clear_values_button", "include_all_button", "exclude_all_button", "month_acts_button")
                 if quality else ("fill_month_button", "clear_month_button")):
        getattr(page, name).setEnabled(local)
    fill_button = page.form_month_button if quality else page.fill_month_button
    fill_button.setToolTip("Обновить данные всех доступных месяцев, включая ранее заполненные и размещённые. Для дозаполнения откройте нужный месяц." if page._scope_is_period() else "Заполнить данные выбранного месяца.")
    filled = [s for s in states if s.has_data]
    publish = page.publish_button if quality else page.publish_month_button
    ids_reason = ""
    if work_list and not work_list.gis_work_list_guid:
        ids_reason = "Сначала получите статус размещённого перечня."
    if quality and work_list and (not house or not house.fias_house_guid or not house.house_object_guid):
        ids_reason = "Для обмена нужны ФИАС GUID и GUID объекта дома. Получите их в разделе «Дома»."
    if not quality and work_list and (not house or not (house.fias_house_guid or house.gis_house_guid)):
        ids_reason = "Получите идентификаторы дома в разделе «Дома»."
    eligible = filled
    if quality:
        current = by_month.get(page._current_month)
        if page._scope_is_period():
            publish_ids = {p.id for p in page._periods_with_quality_for_year("approve_quality_assessment")}
        else:
            allowed = current and (not current.estimation_guid or str(current.gis_status or "LOCAL").upper() in {"DRAFT", "MODIFIED", "CANCELLED", "LOCAL"})
            publish_ids = {current.id} if allowed else set()
        eligible = [s for s in filled if by_month[s.month].id in publish_ids]
    validation = next((s.publish_error for s in eligible if s.publish_error), "")
    publish_reason = net_reason or ids_reason or validation or ("Сначала заполните данные выбранной области." if not filled else "")
    publish.setEnabled(net_ready and bool(eligible) and not publish_reason)
    if filled and not eligible and not publish_reason:
        publish_reason = "Оценка уже размещена. Загрузите её из ГИС для проверки текущего состояния."
    publish.setToolTip(publish_reason)
    if quality:
        period = by_month.get(page._current_month)
        save_allowed = (bool(page._periods_with_quality_for_year("save_quality_assessment"))
                        if page._scope_is_period() else bool(filled) and
                        (not period.estimation_guid or period.gis_status in {"DRAFT", "MODIFIED", "LOCAL"}))
        page.save_button.setEnabled(net_ready and save_allowed and not ids_reason)
        page.save_button.setToolTip(net_reason or ids_reason or ("Включите работы в оценку." if not filled else "Проект можно сохранить без актов."))
        page.save_button.setText("Сохранить проекты" if page._scope_is_period() else "Сохранить проект")
        cancellable = bool(period and period.estimation_guid and period.gis_status in {"DRAFT", "APPROVED", "MODIFIED"} and not page._scope_is_period())
        page.cancel_button.setEnabled(net_ready and cancellable)
        page.remote_danger.setVisible(cancellable)
    else:
        page.add_unplanned_button.setEnabled(net_ready and not ids_reason)
    page.network_menu.sync()
    step = quality_step(states) if quality else fixation_step(states)
    key, title, action, detail, stage, done = step.key, step.title, step.action, step.detail, step.stage, step.done
    reason = ""
    if not has_context:
        key, title, action, detail, stage, done = "context", "Выберите рабочий контекст", "К перечням", "Для работы нужен размещённый перечень и год.", 0, frozenset()
        page.workflow.register("context", lambda: page.workflow.navigateRequested.emit(1))
    elif retry_here:
        key, title, action, detail = "resume", "Очередь остановлена", page.continue_queue_button.text(), failed_period.gis_last_error or "Проверьте причину в строке операции и повторите оставшиеся месяцы."
        reason = net_reason
    elif quality and key == "save" and not page.save_button.isEnabled() and not net_reason and not ids_reason:
        # An annulled remote version cannot be edited as a project by the existing helper.
        key, stage, title, action = ("acts", 1, "Подготовьте акты для новой версии", "Подготовить акты") if validation else ("publish", 3, "Оценка готова к новой версии", "Разместить")
    if quality and key == "acts" and validation and "но не указано" in validation:
        key, action = "review_acts", "Проверить акты и дни"
        detail += " Выберите указанный месяц и заполните дни в рабочей таблице."
        invalid_month = next(s.month for s in states if s.publish_error and "но не указано" in s.publish_error)
        def review():
            page._select_month(invalid_month)
            page._open_month_acts()
        page.workflow.register("review_acts", review)
    if quality and key == "next":
        unfinished = [s.month for s in all_states if s.month != page._current_month and s.status != "APPROVED"]
        following = sorted(unfinished, key=lambda month: (month <= (page._current_month or 0), month))
        if following:
            page.workflow.register("next", lambda: page._select_month(following[0]))
            detail = f"Следующая оценка: {MONTHS[following[0] - 1]} {year}."
        else:
            key, action = None, "Все оценки размещены"
    if quality and key == "form":
        fixation = page.repository.list_work_fixation_periods(work_list.id, year=year) if work_list else []
        source_months = {p.month for p in fixation if any(item_has_fact(i) for i in page.repository.list_work_fixation_items(p.id))}
        missing = [s.month for s in states if not s.has_data and s.month in source_months]
        if not missing and not filled:
            key, title, action, detail = "fixation", "Сначала заполните фиксацию", "К фиксации", "Для автоматического формирования нужны фактические показатели этого месяца/периода. Работы можно также включить вручную в таблице."
            page.workflow.register("fixation", lambda: page.workflow.navigateRequested.emit(3))
        elif not missing and filled:
            applicable = [s for s in states if s.has_data]
            partial = quality_step(applicable)
            key, title, action, detail, stage, done = partial.key, partial.title, partial.action, partial.detail, partial.stage, partial.done
            detail += f" Месяцев без оценки и факта: {len(states) - len(applicable)}; они не входят в отправку."
            if key == "next":
                key, title, action = "fixation", "Все сформированные оценки размещены", "К фиксации"
                page.workflow.register("fixation", lambda: page.workflow.navigateRequested.emit(3))
    if page._scope_is_period() and filled and key in {"form", "fill"}:
        empty_months = [s.month for s in states if not s.has_data and (not quality or s.month in source_months)]
        target_month = empty_months[0]
        def open_missing():
            page._select_month(target_month)
            page.scope_switcher.set_scope("month")
        page.workflow.register("missing", open_missing)
        key, action = "missing", f"Открыть {MONTHS[target_month - 1]}"
        detail = (f"Пустых месяцев: {len(empty_months)}. Откройте один месяц для дозаполнения. "
                  "Заполнение всего периода заменяет данные и в уже заполненных месяцах.")
    if not quality and key == "fill" and work_list:
        plan = page.repository.get_work_plan_for_list_year(work_list.id, year)
        plan_months = page.repository.list_work_plan_months(plan.id) if plan else []
        selected_months = {s.month for s in states}
        if not any(row.month in selected_months and (row.count or row.dates_json) for row in plan_months):
            key, stage, title, action, detail, done = "plan", 0, "Сначала заполните план", "К планированию", "В выбранной области план ещё пуст. Заполните план или внесите фактические показатели вручную в таблице.", frozenset()
            page.workflow.register("plan", lambda: page.workflow.navigateRequested.emit(2))
    if key in {"save", "publish"}:
        reason = net_reason or ids_reason or (publish_reason if key == "publish" else "")
    if locked:
        reason = "Операция выполняется. Контекст и данные доступны после её завершения."
    elif retry_here:
        page.operation_status.setProperty("tone", "danger")
        page.operation_status.hide_progress()
        repolish(page.operation_status)
    page.workflow.show_step(stage, title, detail, key, action, reason=reason, done=set(done))


def plan_fingerprint(page, plan_id: str) -> str:
    rows = [asdict(row) for row in page.repository.list_work_plan_months(plan_id)]
    # The month values and dates represent the editable plan; timestamps do not.
    value = [(r["work_plan_item_id"], r["month"], r["count"], r.get("dates_json")) for r in rows]
    return hashlib.sha256(json.dumps(sorted(value), ensure_ascii=False).encode()).hexdigest()


def mark_plan_synced(page, plan_id: str):
    page.repository.set_setting(f"ui.plan.synced.{plan_id}", plan_fingerprint(page, plan_id))


def refresh_planning(page):
    if not hasattr(page, "workflow"):
        return
    work_list, year, _house = _target(page)
    plan = page.repository.get_work_plan(page._current_plan_id) if page._current_plan_id else None
    locked = busy(page)
    context_lock(page, locked)
    local = bool(plan) and not locked
    page.table.setEnabled(local)
    page.distribute_button.setEnabled(local)
    page.clear_button.setEnabled(local)
    page.empty_hint.setVisible(not plan)
    net_reason = connection_reason(page, helper_supports_work_plans)
    page.load_gis_button.setEnabled(local and not net_reason)
    page.load_gis_button.setToolTip(net_reason)
    reason = ""
    if not plan or not work_list:
        page.publish_button.setEnabled(False)
        page.workflow.register("context", lambda: page.workflow.navigateRequested.emit(1))
        page.workflow.show_step(0, "Нужен размещённый перечень", "Выберите перечень и год, чтобы открыть план.", "context", "К перечням")
        if not locked:
            page.plan_status.setText("План ещё не выбран")
        return
    items = page.repository.list_work_plan_items(plan.id)
    months = page.repository.list_work_plan_months(plan.id)
    filled = any(row.count or row.dates_json for row in months)
    if any(item.work_list_item_id is None for item in items):
        reason = "Исторический план содержит работы вне текущего перечня. Доступны загрузка и просмотр."
    else:
        try:
            build_gis_work_plan_command_payload(work_list_guid=work_list.gis_work_list_guid or "", year=plan.year, items=items, months=months)
        except ValueError as exc:
            reason = str(exc)
    fingerprint = plan_fingerprint(page, plan.id)
    saved = page.repository.get_setting(f"ui.plan.synced.{plan.id}")
    # Imported legacy plans have no UI snapshot. Establish it only when there is
    # no later local edit, then compare values (also catches edits within a second).
    if saved is None and plan.gis_work_plan_guid and plan.gis_last_sync_at and plan.updated_at and plan.updated_at <= plan.gis_last_sync_at:
        mark_plan_synced(page, plan.id)
        saved = fingerprint
    placed = bool(plan.gis_work_plan_guid and plan.gis_status == "SAVED" and saved == fingerprint and not plan.gis_last_error)
    page.publish_button.setEnabled(local and not net_reason and not reason)
    page.publish_button.setToolTip(net_reason or reason)
    if placed:
        key, stage, title, action, detail, done = "next", 3, "План размещён в ГИС", "К фиксации", "Следующий этап — фактические показатели по месяцам.", {0, 1, 2, 3}
    elif not filled:
        key, stage, title, action, detail, done = "fill", 1, "Заполните план на год", "Автораспределить", "Распределите количество из перечня по месяцам или введите его в таблице.", {0}
    else:
        key, stage, title, action, detail, done = "publish", 2, "Проверьте распределение по месяцам", ("Обновить план в ГИС" if plan.gis_work_plan_guid else "Разместить план"), "План сохранён локально. При размещении ГИС получит текущие значения всего года.", {0, 1}
    blocking = (net_reason or reason) if key == "publish" else ""
    if locked:
        blocking = "Дождитесь завершения текущей операции."
    page.workflow.show_step(stage, title, detail, key, action, reason=blocking, done=done)
    if not locked:
        status = "размещён" if placed else ("изменён локально" if plan.gis_work_plan_guid else "локальный")
        page.plan_status.setText(f"План {year} • {status}" + (f" • ошибка: {plan.gis_last_error}" if plan.gis_last_error else ""))
        page.plan_status.setToolTip(f"GUID: {plan.gis_work_plan_guid or '—'}\nПоследний обмен: {plan.gis_last_sync_at or '—'}")
