"""Explain recovery without claiming that a failed remote operation rolled back."""
from __future__ import annotations

import sqlite3


READ_ONLY_GIS_COMMANDS = frozenset({
    "get_current_organization", "list_managed_houses", "get_house_management_info",
    "list_house_management_info", "list_work_lists", "sync_work_list", "load_work_plan",
    "load_work_fixation", "load_quality_assessment", "load_management_fees",
    "load_fee_protocols", "list_organization_work_options", "list_unplanned_work_options",
    "resolve_planned_work_units", "get_organization_act_signatory", "ping",
})


def gis_error_text(
    error, *, command_type: str = "", context: str = "", result_apply_failed: bool = False,
    effect: str = "", recovery: str = "",
) -> str:
    """Distinguish a failed read from an uncertain remote write and local apply."""
    if command_type in READ_ONLY_GIS_COMMANDS:
        if result_apply_failed:
            default_effect = (
                "Не удалось полностью применить полученные данные в программе. "
                "Некоторые записи могли сохраниться локально. Изменения в ГИС ЖКХ не отправлялись при этой загрузке."
            )
            default_recovery = (
                "Проверьте локальные данные и устраните причину ошибки, "
                "затем повторите загрузку или продолжите очередь."
            )
        else:
            default_effect = (
                "Получение данных из ГИС ЖКХ не завершено. "
                "Сохранённые локальные данные остаются в программе. Изменения в ГИС ЖКХ не отправлялись при этой загрузке."
            )
            default_recovery = (
                "Проверьте подключение помощника и открытую вкладку нужной УО, затем повторите загрузку."
            )
    elif result_apply_failed:
        default_effect = (
            "Не удалось сохранить результат в программе. Некоторые записи могли сохраниться; "
            "изменения в ГИС не отменяются автоматически."
        )
        default_recovery = (
            "Проверьте локальные данные. Перед повторной отправкой проверьте состояние сведений в ГИС. "
            "Затем повторите загрузку или продолжите очередь."
        )
    else:
        default_effect = (
            "Операция не завершилась. Проверьте результат в ГИС перед повторной отправкой: "
            "там могли сохраниться изменения."
        )
        default_recovery = ""
    return error_text(
        error, context=context, effect=effect or default_effect, recovery=recovery or default_recovery,
    )


def context_text(widget) -> str:
    """Read the visible selectors; never query a possibly unavailable database."""
    values = []
    for name in ("organization_combo", "house_combo", "registry_combo", "work_list_combo", "year_combo", "month_combo"):
        control = getattr(widget, name, None)
        if control is not None and control.currentText():
            value = control.currentText().replace("\n", " · ")
            if value not in values:
                values.append(value)
    return " • ".join(values)


def error_text(error, *, context: str = "", effect: str = "", recovery: str = "") -> str:
    detail = str(error)
    lowered = detail.lower()
    if isinstance(error, sqlite3.DatabaseError) or "database" in lowered:
        if "locked" in lowered or "busy" in lowered:
            reason = "Локальная база занята другой операцией."
            default_recovery = "Дождитесь её завершения и повторите сохранение."
        elif "readonly" in lowered or "read-only" in lowered:
            reason = "Нет доступа для записи в локальную базу."
            default_recovery = "Проверьте доступ к каталогу базы и повторите сохранение."
        elif "full" in lowered:
            reason = "Недостаточно места для записи в локальную базу."
            default_recovery = "Освободите место на диске и повторите сохранение."
        else:
            reason = "Не удалось выполнить операцию с локальной базой."
            default_recovery = "Повторите операцию. Если ошибка повторится, сохраните технические сведения для поддержки."
    elif isinstance(error, FileNotFoundError):
        reason = "Выбранный файл больше не доступен."
        default_recovery = "Выберите существующий файл и повторите действие."
    elif isinstance(error, PermissionError):
        reason = "Нет доступа к выбранному файлу или каталогу."
        default_recovery = "Проверьте права доступа или выберите другой каталог."
    elif isinstance(error, TimeoutError) or any(value in lowered for value in (
        "timeout", "timed out", "connection refused", "disconnected", "не ответил вовремя", "тайм-аут",
    )):
        reason = "Помощник не ответил вовремя или связь с браузером прервалась."
        default_recovery = "Проверьте помощник в «Настройках» и обновите вкладку ГИС. Перед повторной отправкой проверьте, сохранились ли сведения в ГИС."
    elif isinstance(error, OSError):
        reason = "Не удалось прочитать или записать файл."
        default_recovery = "Проверьте доступ к файлу и свободное место на диске, затем повторите действие."
    else:
        reason = detail or "Операция не завершена."
        default_recovery = "Проверьте указанные данные и повторите действие. При ошибке обмена проверьте соединение с помощником."
    parts = [reason]
    if context:
        parts.append("Данные: " + context)
    parts.append(effect or "Операция не завершилась. Проверьте результат перед повторным запуском.")
    parts.append(recovery or default_recovery)
    if detail and reason != detail:
        parts.append("Технические сведения: " + detail)
    return "\n\n".join(parts)
