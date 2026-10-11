"""Explicit queue scope and cooperative stop between remote operations."""
from .error_messages import context_text


def preview(page, periods):
    from .page_layouts import MONTHS
    labels = {"LOCAL": "локально", "MODIFIED": "локальные изменения", "ERROR": "ошибка",
              "DRAFT": "проект в ГИС", "SAVED": "размещён; повторная отправка",
              "APPROVED": "размещён", "CANCELLED": "аннулирован"}
    rows = [f"• {MONTHS[p.month - 1]} {p.year} — {labels.get(str(p.gis_status or 'LOCAL').upper(), p.gis_status or 'локально')}"
            for p in periods]
    return context_text(page) + "\n\nТочные месяцы операции:\n" + "\n".join(rows)


def request_stop(page):
    page._stop_queue_requested = True
    page.stop_queue_button.setEnabled(False)
    page.queue_label.setText("Остановка после текущего месяца… Результат текущего запроса будет сохранён.")
    if page._integration_worker is None:
        stop_before_next(page)


def stop_before_next(page):
    if not getattr(page, '_stop_queue_requested', False):
        return False
    page._queue_running = False
    page._integration_busy = False
    ids, index = page._queue_period_ids, page._queue_index
    page._queue_failed_period_id = ids[index] if index < len(ids) else None
    page.continue_queue_button.setVisible(index < len(ids))
    page.continue_queue_button.setText("Продолжить оставшиеся месяцы")
    page.queue_label.setText(f"Очередь остановлена. Успешно: {page._queue_successes}; "
                            f"пропущено: {page._queue_skipped}; не начато: {len(ids) - index}.")
    page._refresh_gis_buttons()
    return True
