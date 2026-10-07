"""Reverse synchronization of one work plan from GIS ЖКХ."""

from __future__ import annotations

from typing import Any

from gis_jkh_assistant.database.repository import Repository

from .gis_match import match_remote_rows


def apply_remote_work_plan(
    repository: Repository,
    *,
    work_list_id: str,
    year: int,
    remote: dict[str, Any],
) -> dict[str, int]:
    if not remote.get("found"):
        raise ValueError("План работ в ГИС ЖКХ не найден.")

    plan = repository.get_or_create_work_plan(
        work_list_id=work_list_id,
        year=int(year),
    )
    local_items = repository.list_work_plan_items(plan.id)
    remote_items = [
        row for row in list(remote.get("items") or []) if isinstance(row, dict)
    ]
    matched = match_remote_rows(local_items, remote_items)

    repository.apply_work_plan_distribution(
        plan.id,
        [
            (item.id, month, 0)
            for item in local_items
            for month in range(1, 13)
        ],
    )

    filled_months = 0
    for item in local_items:
        row = matched[item.id]
        for month in row.get("monthlyWorks") or []:
            month_number = int(month.get("month") or 0)
            count = int(month.get("count") or 0)
            dates = [
                str(value)
                for value in (month.get("dates") or [])
                if str(value).strip()
            ]
            if not 1 <= month_number <= 12:
                raise ValueError("ГИС ЖКХ вернул некорректный месяц плана.")
            repository.update_work_plan_month(
                item.id,
                month=month_number,
                count=count,
                dates=dates,
            )
            if count or dates:
                filled_months += 1

    repository.update_work_plan_gis_state(
        plan.id,
        gis_work_plan_guid=str(remote.get("workPlanGuid") or "").strip() or None,
        gis_status=str(remote.get("gisStatus") or "").strip() or "SAVED",
        gis_last_error=None,
    )
    return {"items": len(local_items), "months": filled_months}


def local_plan_has_data(repository: Repository, plan_id: str) -> bool:
    return bool(repository.list_work_plan_months(plan_id))
