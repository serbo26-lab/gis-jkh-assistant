"""Pure helpers for monthly work/service fixation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from gis_jkh_assistant.database.models import WorkFixationItem


MIN_FIXATION_HELPER_VERSION = (0, 1, 6)


def helper_supports_fixations(version: str | None) -> bool:
    """Return whether the browser helper supports fixation upload commands."""

    try:
        parts = tuple(int(part) for part in str(version or "").split("."))
    except ValueError:
        return False
    return parts >= MIN_FIXATION_HELPER_VERSION


def proportional_month_cost(
    total_cost: str | None,
    total_quantity: str | None,
    month_count: int,
) -> str | None:
    """Allocate list cost proportionally to the planned count of one month."""

    count = int(month_count)
    if count <= 0 or not total_cost or not total_quantity:
        return None

    try:
        cost = _decimal(total_cost)
        quantity = _decimal(total_quantity)
    except InvalidOperation:
        return None
    if quantity <= 0:
        return None

    value = (cost * Decimal(count) / quantity).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return _decimal_text(value)


def build_fixation_command_payload(
    *,
    fias_house_code: str,
    work_list_guid: str,
    year: int,
    month: int,
    items: Iterable[WorkFixationItem],
) -> dict:
    """Build one monthly desired fixation state for the browser helper."""

    fias = str(fias_house_code or "").strip()
    work_list = str(work_list_guid or "").strip()
    if not fias:
        raise ValueError("У дома отсутствует ФИАС GUID для обмена с ГИС ЖКХ.")
    if not work_list:
        raise ValueError("У перечня отсутствует GUID ГИС ЖКХ.")

    year = int(year)
    month = int(month)
    if not 1 <= month <= 12:
        raise ValueError("Месяц фиксации должен быть от 1 до 12.")

    result_items: list[dict] = []
    for item in items:
        result_items.append(
            {
                "localItemId": item.id,
                "workGuid": item.work_guid,
                "workCode": item.work_code,
                "sortIndex": int(item.position),
                "factPrice": _clean(item.fact_price),
                "factAmount": _clean(item.fact_amount),
                "factCount": (
                    int(item.fact_count)
                    if item.fact_count is not None
                    else None
                ),
                "factTotalCost": _clean(item.fact_total_cost),
            }
        )

    return {
        "fiasHouseCode": fias,
        "workListGuid": work_list,
        "year": year,
        "month": month,
        "items": result_items,
    }


def item_has_fact(item: WorkFixationItem) -> bool:
    return any(
        (
            _clean(item.fact_price),
            _clean(item.fact_amount),
            item.fact_count is not None,
            _clean(item.fact_total_cost),
        )
    )


def _decimal(value: str) -> Decimal:
    text = (
        str(value or "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )
    return Decimal(text)


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
