"""Pure helpers for quality assessment of completed works and services."""

from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

MIN_QUALITY_HELPER_VERSION = (0, 1, 27)


def helper_supports_quality_assessment(version: str | None) -> bool:
    """Return whether the browser helper supports quality-assessment commands."""

    try:
        parts = tuple(int(part) for part in str(version or "").split("."))
    except ValueError:
        return False
    return parts >= MIN_QUALITY_HELPER_VERSION


def quality_reduction(
    cost: str | None,
    days_count: int | str | None,
    days_per_month: int,
) -> str | None:
    """Calculate the payment reduction shown by ГИС ЖКХ."""

    if cost is None or days_count is None:
        return None
    if not str(cost).strip() or not str(days_count).strip():
        return None
    try:
        normalized = str(cost).replace("\u00a0", "").replace(" ", "").replace(",", ".")
        cost_value = Decimal(normalized)
        days = Decimal(str(int(days_count)))
        month_days = Decimal(str(int(days_per_month)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if cost_value < 0 or days < 0 or month_days <= 0 or days > month_days:
        return None
    # ГИС ЖКХ sends the exact ratio in the save payload (for example
    # 1 / 31 -> 0.03225806451612903) and only rounds it for visual display.
    # Keep the unrounded decimal locally; the browser helper converts it to
    # a JSON number exactly as the web interface does.
    result = cost_value * days / month_days
    return format(result, "f")


def build_quality_command_payload(
    *,
    fias_house_code: str,
    house_object_guid: str,
    work_list_guid: str,
    year: int,
    month: int,
    items: Iterable[object],
    acts_by_item: Mapping[str, Iterable[object]],
) -> dict:
    """Build the short-lived browser-helper payload from local quality data."""

    result_items: list[dict] = []
    for item in items:
        if not bool(getattr(item, "included", False)):
            continue
        item_id = str(getattr(item, "id"))
        acts = []
        removed_remote_act_guids = []
        removed_remote_content_guids = []
        for act in acts_by_item.get(item_id, ()):
            if bool(getattr(act, "deleted", False)) or bool(
                getattr(act, "link_deleted", False)
            ):
                remote_act_guid = getattr(act, "remote_act_guid", None)
                remote_content_guid = getattr(act, "remote_content_guid", None)
                if remote_act_guid:
                    removed_remote_act_guids.append(str(remote_act_guid))
                if remote_content_guid:
                    removed_remote_content_guids.append(str(remote_content_guid))
                continue

            content = getattr(act, "content", None)
            acts.append(
                {
                    "localActId": str(getattr(act, "id")),
                    "actType": str(getattr(act, "act_type")),
                    "number": str(getattr(act, "number")),
                    "regDate": str(getattr(act, "reg_date")),
                    "periodFrom": str(getattr(act, "period_from")),
                    "periodTo": str(getattr(act, "period_to")),
                    "fileName": str(getattr(act, "file_name")),
                    "description": getattr(act, "description", None),
                    "contentType": str(getattr(act, "content_type")),
                    "fileSize": int(getattr(act, "file_size")),
                    "fileBase64": (
                        base64.b64encode(content).decode("ascii")
                        if content is not None
                        else None
                    ),
                    "remoteContentGuid": getattr(act, "remote_content_guid", None),
                    "remoteActGuid": getattr(act, "remote_act_guid", None),
                }
            )
        result_items.append(
            {
                "localItemId": item_id,
                "workListItemId": getattr(item, "work_list_item_id", None),
                "workCode": getattr(item, "work_code", None),
                "workGuid": getattr(item, "work_guid", None),
                "workName": getattr(item, "work_name", None),
                "cost": getattr(item, "cost", None),
                "daysCount": getattr(item, "days_count", None),
                "daysPerMonth": int(getattr(item, "days_per_month")),
                "costReduction": getattr(item, "cost_reduction", None),
                "acts": acts,
                "removedRemoteActGuids": removed_remote_act_guids,
                "removedRemoteContentGuids": removed_remote_content_guids,
            }
        )

    return {
        "fiasHouseCode": str(fias_house_code),
        "houseObjectGuid": str(house_object_guid),
        "workListGuid": str(work_list_guid),
        "year": int(year),
        "month": int(month),
        "items": result_items,
    }
