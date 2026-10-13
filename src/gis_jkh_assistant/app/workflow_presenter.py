"""Present the next useful step from persisted state and existing GIS rules."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from PySide6.QtCore import Qt
from gis_jkh_assistant.gis_integration.organization_context import (
    MIN_ORGANIZATION_ROUTING_HELPER_VERSION, helper_supports_organization_routing,
)

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
    if not helper_supports_organization_routing(status.helper_version):
        minimum = ".".join(map(str, MIN_ORGANIZATION_ROUTING_HELPER_VERSION))
        return f"Для безопасного выбора вкладки УО требуется браузерный помощник {minimum}+."
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
        return f"{active[0]:02d}–{active[-1]:02d}.{year} • Таблица: {MONTHS[page._current_month - 1]}" if active and page._current_month else "Нет доступных месяцев"
    month = page._current_month
    return "" if month and year else "Выберите месяц"


def refresh_monthly(page, kind: str):
    if not hasattr(page, "workflow"):
        return
    quality = kind == "quality"
    page.stop_queue_button.setVisible(page._queue_running)
    page.stop_queue_button.setEnabled(not getattr(page, "_stop_queue_requested", False))
    work_list, year, house = _target(page)
    active = active_months_for_work_list(work_list, year) if work_list and year else ()
    owner = getattr(page.operation_status, "_operation_owner", None)
    if owner is not None and owner != (page._current_work_list_id, year) and not busy(page):
        page.operation_status.hide()
    if hasattr(page, "data_actions"):
        month_label = f"Открытый месяц — {MONTHS[page._current_month - 1]} {year}" if page._current_month and year else "Открытый месяц — не выбран"
        period_label = f"Доступные месяцы {year} года — {active[0]:02d}–{active[-1]:02d} ({len(active)})" if active else "Доступных месяцев нет"
        page.month_actions.menu_model.setTitle(month_label)
        page.period_actions.menu_model.setTitle(period_label)
    locked = busy(page)
    context_lock(page, locked)
    has_context = bool(active and page._current_period_id)
    local = has_context and not locked
    page.table.setEnabled(local)
    if not quality:
        page.unplanned_table.setEnabled(local)
    if quality:
        table_month = MONTHS[page._current_month - 1] if page._current_month else "—"
        page.scope_hint.setText(
            f"Таблица: {table_month} {year}" if year else "Таблица: месяц не выбран"
        )
    else:
        page.scope_hint.setText(_scope_label(page, active, year))
    page.scope_hint.setVisible(bool(page.scope_hint.text()))
    page.empty_hint.setVisible(not has_context)
    supported = helper_supports_quality_assessment if quality else helper_supports_fixations
    net_reason = connection_reason(page, supported)
    net_ready = local and not net_reason
    load = page.load_gis_button if quality else page.load_month_button
    load_reason = net_reason
    if work_list and (not house or not (house.fias_house_guid or house.gis_house_guid)):
        load_reason = "Получите идентификаторы дома в разделе «Дома»."
    for button in (load, page.load_period_button):
        button.setEnabled(local and not load_reason)
        button.setToolTip(load_reason or "Получить данные из ГИС; локальные изменения будут проверены перед заменой.")
    page.load_period_button.setToolTip(load_reason or "Проверить все доступные месяцы в границах перечня; загрузить найденные в ГИС данные.")
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
            validation = ((page._validation_error(period.id, require_acts=True) if quality
                           else page._validation_error(period.id)) if has_data else None)
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
        page.month_combo.setItemText(month - 1, MONTHS[month - 1])
        from PySide6.QtCore import Qt
        page.month_combo.setItemData(month - 1, f"{MONTHS[month - 1]}: {suffix}", Qt.ItemDataRole.ToolTipRole)
        button.setToolTip(error or (f"{MONTHS[month - 1]} {year}: {suffix}" if year else suffix))
        if button.property("tone") != tone:
            button.setProperty("tone", tone)
            repolish(button)
        if month in active:
            all_states.append(MonthState(month, status, has_data, validation, error))
    # The compact picker shares the same selected month and real availability.
    page.month_combo.blockSignals(True)
    for index in range(page.month_combo.count()):
        page.month_combo.model().item(index).setEnabled(index + 1 in active)
    page.month_combo.setCurrentIndex(page.month_combo.findData(page._current_month))
    page.month_combo.setEnabled(bool(active) and not locked)
    page.month_combo.blockSignals(False)
    page._workflow_month_snapshot = (snapshot_key, cached_states)
    states = all_states if page._scope_is_period() else [s for s in all_states if s.month == page._current_month]
    failed_id = page._queue_failed_period_id
    failed_period = (page.store.get_period(failed_id) if quality else page.repository.get_work_fixation_period(failed_id)) if failed_id else None
    retry_here = bool(failed_period and work_list and failed_period.work_list_id == work_list.id and failed_period.year == year)
    page.continue_queue_button.setVisible(retry_here)
    page.continue_queue_button.setEnabled(retry_here and not locked and not net_reason)
    if retry_here:
        page.continue_queue_button.setText(f"Продолжить с {MONTHS[failed_period.month - 1]} {year}")
    for name in (("form_month_button", "form_period_button", "auto_act_month_button", "auto_act_period_button",
                  "clear_values_button", "clear_period_values_button", "include_all_button", "exclude_all_button",
                  "month_acts_button", "create_act_button", "upload_acts_button")
                 if quality else ("fill_month_button", "fill_period_button", "clear_month_button", "clear_period_button")):
        getattr(page, name).setEnabled(local)
    fill_button = page.form_month_button if quality else page.fill_month_button
    fill_button.setToolTip("Заполнить данные выбранного месяца.")
    period_fill = page.form_period_button if quality else page.fill_period_button
    period_fill.setToolTip("Обновить данные всех доступных месяцев в границах перечня, включая ранее заполненные и размещённые. Перед заменой появится подтверждение.")
    filled = [s for s in states if s.has_data]
    ids_reason = ""
    if work_list and not work_list.gis_work_list_guid:
        ids_reason = "Сначала получите статус размещённого перечня."
    if quality and work_list and (not house or not house.fias_house_guid or not house.house_object_guid):
        ids_reason = "Для обмена нужны ФИАС GUID и GUID объекта дома. Получите их в разделе «Дома»."
    if not quality and work_list and (not house or not (house.fias_house_guid or house.gis_house_guid)):
        ids_reason = "Получите идентификаторы дома в разделе «Дома»."
    current = by_month.get(page._current_month)
    month_filled = [s for s in all_states if s.month == page._current_month and s.has_data]
    period_filled = [s for s in all_states if s.has_data]
    eligible_by_scope = {"month": month_filled, "period": period_filled}
    if quality:
        publish_ids = {p.id for p in page._periods_with_quality_for_year("approve_quality_assessment")}
        eligible_by_scope["period"] = [s for s in period_filled if by_month[s.month].id in publish_ids]
        allowed = current and (not current.estimation_guid or str(current.gis_status or "LOCAL").upper() in {"DRAFT", "MODIFIED", "CANCELLED", "LOCAL"})
        eligible_by_scope["month"] = month_filled if allowed else []
    publish_reasons = {}
    for scope, button, targets in (
        ("month", page.publish_button if quality else page.publish_month_button, month_filled),
        ("period", page.publish_period_button, period_filled),
    ):
        candidates = eligible_by_scope[scope]
        validation_error = next((s.publish_error for s in candidates if s.publish_error), "")
        reason = net_reason or ids_reason or validation_error
        if not reason and not targets:
            reason = "Сначала заполните выбранный месяц." if scope == "month" else "В доступном периоде нет заполненных месяцев."
        if not reason and not candidates:
            reason = "Оценки уже размещены. Загрузите их из ГИС для проверки текущего состояния."
        button.setEnabled(net_ready and bool(candidates) and not reason)
        period_tip = f"Отправить {len(candidates)} заполненных мес. в границах перечня. " + (
            "Пустые и уже размещённые оценки пропускаются." if quality else
            "Ранее размещённые заполненные месяцы будут обновлены; пустые месяцы пропускаются."
        )
        button.setToolTip(reason or ("Отправить выбранный месяц." if scope == "month" else period_tip))
        publish_reasons[scope] = reason
    scope = "period" if page._scope_is_period() else "month"
    eligible = eligible_by_scope[scope]
    validation = next((s.publish_error for s in eligible if s.publish_error), "")
    publish_reason = publish_reasons[scope]
    save_for_scope = None
    if quality:
        month_save_allowed = bool(month_filled) and bool(current) and (not current.estimation_guid or str(current.gis_status or "LOCAL").upper() in {"DRAFT", "MODIFIED", "LOCAL"})
        period_save_allowed = bool(page._periods_with_quality_for_year("save_quality_assessment"))
        for button, allowed, targets in ((page.save_button, month_save_allowed, month_filled),
                                        (page.save_period_button, period_save_allowed, period_filled)):
            button.setEnabled(net_ready and allowed and not ids_reason)
            reason = net_reason or ids_reason
            if not targets:
                reason = reason or "Включите работы в оценку."
            elif not allowed:
                reason = reason or "Размещённую оценку нельзя сохранить как проект; для изменений используйте размещение новой версии."
            button.setToolTip(reason or "Проект можно сохранить без актов.")
        save_for_scope = page.save_period_button if page._scope_is_period() else page.save_button
        cancellable = bool(current and current.estimation_guid and current.gis_status in {"DRAFT", "APPROVED", "MODIFIED"})
        page.cancel_button.setEnabled(net_ready and cancellable)
    else:
        page.add_unplanned_button.setEnabled(net_ready and not ids_reason)
    step = quality_step(states) if quality else fixation_step(states)
    key, title, action, detail, stage, done = step.key, step.title, step.action, step.detail, step.stage, step.done
    reason = ""
    if not has_context:
        key, title, action, detail, stage, done = "context", "Выберите рабочий контекст", "К перечням", "Для работы нужен размещённый перечень и год.", 0, frozenset()
        page.workflow.register("context", lambda: page.workflow.navigateRequested.emit(1))
    elif retry_here:
        key, title, action, detail = "resume", "Очередь остановлена", page.continue_queue_button.text(), failed_period.gis_last_error or "Проверьте причину в строке операции и повторите оставшиеся месяцы."
        reason = net_reason
    elif quality and key == "save" and not save_for_scope.isEnabled() and not net_reason and not ids_reason:
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
    recommended_commands = {
        "form": ("form_month_button", "form_period_button"),
        "acts": ("auto_act_month_button", "auto_act_period_button"),
        "save": ("save_button", "save_period_button"),
        "publish": ("publish_button", "publish_period_button") if quality
                   else ("publish_month_button", "publish_period_button"),
        "fill": ("fill_month_button", "fill_period_button"),
    }
    if key in recommended_commands:
        month_command, period_command = recommended_commands[key]
        action = getattr(page, period_command if page._scope_is_period() else month_command).text()
    page.workflow.show_step(stage, title, detail, key, action, reason=reason, done=set(done))
    if quality:
        # Label the *actual period commands* independently of the displayed
        # month. The next-step button can recommend a different local action.
        if page._scope_is_period():
            if key == "publish":
                target_months = sorted(s.month for s in eligible_by_scope["period"])
            elif key == "save":
                target_months = sorted(
                    p.month for p in page._periods_with_quality_for_year(
                        "save_quality_assessment"
                    )
                )
            else:
                target_months = sorted(active)
        else:
            target_months = [page._current_month] if page._current_month else []
        if not target_months or year is None:
            page.action_scope_hint.setText("Действие: нет доступных месяцев")
        elif page._scope_is_period():
            start, end = target_months[0], target_months[-1]
            page.action_scope_hint.setText(
                f"Действие: {start:02d}–{end:02d}.{year} "
                f"• месяцев: {len(target_months)}"
            )
        else:
            page.action_scope_hint.setText(
                f"Действие: {MONTHS[target_months[0] - 1]} {year}"
            )
        gaps = bool(
            page._scope_is_period() and target_months
            and target_months != list(range(target_months[0], target_months[-1] + 1))
        )
        page.action_months_button.setVisible(gaps)
        page.action_months_detail.setText(
            "Состав: " + ", ".join(MONTHS[m - 1] for m in target_months)
            if gaps else ""
        )
        if not gaps:
            page.action_months_button.setChecked(False)
            page.action_months_detail.hide()


def plan_fingerprint(page, plan_id: str) -> str:
    rows = [asdict(row) for row in page.repository.list_work_plan_months(plan_id)]
    # The month values and dates represent the editable plan; timestamps do not.
    value = [(r["work_plan_item_id"], r["month"], r["count"], r.get("dates_json")) for r in rows]
    return hashlib.sha256(json.dumps(sorted(value), ensure_ascii=False).encode()).hexdigest()


def mark_plan_synced(page, plan_id: str, fingerprint: str | None = None):
    page.repository.set_setting(
        f"ui.plan.synced.{plan_id}",
        plan_fingerprint(page, plan_id) if fingerprint is None else fingerprint,
    )


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
    cell = page.table.currentItem()
    page.dates_button.setEnabled(bool(local and cell and cell.column() >= 3 and cell.flags() & Qt.ItemFlag.ItemIsEditable))
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
    # Legacy timestamps alone do not prove that current values match the server.
    # Only a successfully applied load or the exact published snapshot is trusted.
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
