"""Pure helpers for local work-plan scheduling."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Iterable

from gis_jkh_assistant.database.models import (
    WorkList,
    WorkPlanItem,
    WorkPlanMonth,
)


MIN_WORK_PLAN_HELPER_VERSION = (0, 1, 5)


def helper_supports_work_plans(version: str | None) -> bool:
    """Return whether the browser helper supports direct work-plan publishing."""

    try:
        parts = tuple(int(part) for part in str(version or "").split("."))
    except ValueError:
        return False
    return parts >= MIN_WORK_PLAN_HELPER_VERSION


def build_gis_work_plan_command_payload(
    *,
    work_list_guid: str,
    year: int,
    items: Iterable[WorkPlanItem],
    months: Iterable[WorkPlanMonth],
) -> dict:
    """Build the compact plan state sent to the browser helper.

    The browser helper enriches these local rows with current GIS work-list
    references before calling save/workplan. Missing months are intentional:
    the local plan is the complete desired state for the selected year.
    """

    guid = str(work_list_guid or "").strip()
    if not guid:
        raise ValueError("У размещенного перечня отсутствует GUID ГИС ЖКХ.")

    year = int(year)
    month_rows = list(months)
    grouped: dict[str, list[dict]] = {}
    for row in month_rows:
        count = int(row.count)
        if count <= 0:
            continue
        dates = _decode_dates_json(row.dates_json)
        grouped.setdefault(row.work_plan_item_id, []).append(
            {
                "month": int(row.month),
                "count": count,
                "dates": dates,
            }
        )

    result_items: list[dict] = []
    for item in items:
        monthly = sorted(
            grouped.get(item.id, []),
            key=lambda value: int(value["month"]),
        )
        result_items.append(
            {
                "workGuid": item.work_guid,
                "workCode": item.work_code,
                "sortIndex": int(item.position),
                "monthlyWorks": monthly,
            }
        )

    return {
        "workListGuid": guid,
        "year": year,
        "items": result_items,
    }


def _decode_dates_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return sorted(str(item) for item in value if str(item).strip())


def active_months_for_work_list(work_list: WorkList, year: int) -> tuple[int, ...]:
    """Return months of *year* covered by a work-list period."""

    year = int(year)
    months: list[int] = []
    for month in range(1, 13):
        point = (year, month)
        if (
            (work_list.start_year, work_list.start_month)
            <= point
            <= (work_list.end_year, work_list.end_month)
        ):
            months.append(month)
    return tuple(months)


def period_months(work_list: WorkList) -> tuple[tuple[int, int], ...]:
    """Return every (year, month) point covered by the work-list period."""

    result: list[tuple[int, int]] = []
    year = int(work_list.start_year)
    month = int(work_list.start_month)
    end = (int(work_list.end_year), int(work_list.end_month))
    while (year, month) <= end:
        result.append((year, month))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return tuple(result)


def balanced_period_distribution(
    total: int,
    work_list: WorkList,
) -> dict[tuple[int, int], int]:
    """Spread total across the full work-list period, including transition years."""

    points = period_months(work_list)
    values = _balanced_slots(int(total), len(points))
    return {
        point: values[index]
        for index, point in enumerate(points)
    }


def integral_quantity(value: str | None) -> int | None:
    """Return a non-negative integer quantity or None when it is not integral."""

    text = str(value or "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text:
        return 0
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def balanced_month_distribution(
    total: int,
    months: tuple[int, ...] | list[int],
) -> dict[int, int]:
    """Spread an integer total across allowed months as evenly as possible."""

    total = int(total)
    ordered = tuple(int(month) for month in months)
    if total < 0:
        raise ValueError("Количество не может быть отрицательным.")
    if not ordered:
        if total:
            raise ValueError("Нет месяцев, доступных для распределения.")
        return {}
    if any(month < 1 or month > 12 for month in ordered):
        raise ValueError("Месяц должен быть от 1 до 12.")
    if len(set(ordered)) != len(ordered):
        raise ValueError("Месяцы распределения не должны повторяться.")

    values = _balanced_slots(total, len(ordered))
    return {
        month: values[index]
        for index, month in enumerate(ordered)
    }


def _balanced_slots(total: int, size: int) -> tuple[int, ...]:
    if total < 0:
        raise ValueError("Количество не может быть отрицательным.")
    if size <= 0:
        if total:
            raise ValueError("Нет периодов, доступных для распределения.")
        return ()

    base, remainder = divmod(total, size)
    values = [base] * size
    for index in _even_indices(size, remainder):
        values[index] += 1
    return tuple(values)


def _even_indices(size: int, count: int) -> tuple[int, ...]:
    if count <= 0:
        return ()
    if count >= size:
        return tuple(range(size))
    if count == 1:
        return (size // 2,)

    raw = [round(index * (size - 1) / (count - 1)) for index in range(count)]
    used: set[int] = set()
    result: list[int] = []
    for candidate in raw:
        candidate = max(0, min(size - 1, candidate))
        if candidate not in used:
            used.add(candidate)
            result.append(candidate)

    # Rounding can theoretically collide for small arrays; fill any gaps safely.
    for candidate in range(size):
        if len(result) >= count:
            break
        if candidate not in used:
            used.add(candidate)
            result.append(candidate)
    return tuple(sorted(result))
