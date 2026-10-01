"""Pure helpers for local work-plan scheduling."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from gis_jkh_assistant.database.models import WorkList


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
    indexes = balanced_month_distribution(total, tuple(range(1, len(points) + 1)))
    return {
        point: indexes[index]
        for index, point in enumerate(points, start=1)
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

    result = {month: 0 for month in ordered}
    base, remainder = divmod(total, len(ordered))
    for month in ordered:
        result[month] = base

    for index in _even_indices(len(ordered), remainder):
        result[ordered[index]] += 1
    return result


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
