"""Pure helpers for monthly work/service fixation."""

from __future__ import annotations

import base64
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Mapping

from gis_jkh_assistant.database.models import (
    WorkFixationItem,
    WorkFixationPhoto,
    WorkFixationUnplannedItem,
)


MIN_FIXATION_HELPER_VERSION = (0, 1, 9)


def helper_supports_fixations(version: str | None) -> bool:
    """Return whether the browser helper supports fixation upload commands."""

    try:
        parts = tuple(int(part) for part in str(version or "").split("."))
    except ValueError:
        return False
    return parts >= MIN_FIXATION_HELPER_VERSION


def calculate_fact_total_cost(
    price: str | None,
    amount: str | None,
    count: int | str | None,
) -> str | None:
    """Return price × amount × count rounded to two decimals, or None if incomplete."""

    if price is None or amount is None or count is None:
        return None
    if not str(price).strip() or not str(amount).strip() or not str(count).strip():
        return None
    try:
        price_value = _decimal(str(price))
        amount_value = _decimal(str(amount))
        count_value = Decimal(str(int(count)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if price_value < 0 or amount_value < 0 or count_value < 0:
        return None
    value = (price_value * amount_value * count_value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return format(value, ".2f")


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
    unplanned_items: Iterable[WorkFixationUnplannedItem] = (),
    photos_by_planned: Mapping[str, Iterable[WorkFixationPhoto]] | None = None,
    photos_by_unplanned: Mapping[str, Iterable[WorkFixationPhoto]] | None = None,
    managed_unplanned_guids_json: str | None = None,
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

    planned_photos = photos_by_planned or {}
    unplanned_photos = photos_by_unplanned or {}

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
                "managedPhotoContentGuids": _json_string_list(
                    item.managed_photo_content_guids_json
                ),
                "photos": [
                    _photo_payload(photo)
                    for photo in planned_photos.get(item.id, ())
                ],
            }
        )

    result_unplanned: list[dict] = []
    for item in unplanned_items:
        result_unplanned.append(
            {
                "localItemId": item.id,
                "gisGuid": item.gis_fixed_work_guid,
                "workGuid": item.work_guid,
                "workCode": item.work_code,
                "workName": item.work_name,
                "workTypeCode": item.work_type_code,
                "workTypeGuid": item.work_type_guid,
                "unitName": item.unit_name,
                "factPrice": _clean(item.fact_price),
                "factAmount": _clean(item.fact_amount),
                "factCount": (
                    int(item.fact_count)
                    if item.fact_count is not None
                    else None
                ),
                "factTotalCost": _clean(item.fact_total_cost),
                "comment": _clean(item.comment),
                "managedPhotoContentGuids": _json_string_list(
                    item.managed_photo_content_guids_json
                ),
                "photos": [
                    _photo_payload(photo)
                    for photo in unplanned_photos.get(item.id, ())
                ],
            }
        )

    return {
        "fiasHouseCode": fias,
        "workListGuid": work_list,
        "year": year,
        "month": month,
        "items": result_items,
        "unplannedItems": result_unplanned,
        "managedUnplannedGuids": _json_string_list(
            managed_unplanned_guids_json
        ),
    }


def _photo_payload(photo: WorkFixationPhoto) -> dict:
    result = {
        "localPhotoId": photo.id,
        "fileName": photo.file_name,
        "description": photo.description or photo.file_name,
        "contentType": photo.content_type,
        "fileSize": int(photo.file_size),
        "remoteContentGuid": photo.remote_content_guid,
        "remotePhotoGuid": photo.remote_photo_guid,
    }
    if photo.content is not None and not photo.remote_content_guid:
        result["fileBase64"] = base64.b64encode(photo.content).decode("ascii")
    return result


def _json_string_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


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
