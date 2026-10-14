"""Explain recovery without claiming that a failed remote operation rolled back."""
from __future__ import annotations

import sqlite3


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
    elif isinstance(error, OSError):
        reason = "Не удалось прочитать или записать файл."
        default_recovery = "Проверьте доступ к файлу и свободное место на диске, затем повторите действие."
    elif any(value in lowered for value in ("timeout", "timed out", "connection refused", "disconnected")):
        reason = "Помощник не ответил вовремя или связь с браузером прервалась."
        default_recovery = "Проверьте помощник в «Настройках» и обновите вкладку ГИС. Перед повторной отправкой проверьте, сохранились ли сведения в ГИС."
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
