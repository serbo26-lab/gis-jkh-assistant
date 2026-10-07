"""Reverse synchronization of one fixation month from GIS ЖКХ."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from gis_jkh_assistant.database.repository import Repository

from .gis_match import match_remote_rows_allow_missing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ensure_plan_rows(repository: Repository, period_id: str) -> None:
    period = repository.get_work_fixation_period(period_id)
    if period is None:
        return
    plan = repository.get_work_plan_for_list_year(period.work_list_id, period.year)
    if plan is None:
        return

    existing = repository.list_work_fixation_items(period_id)
    existing_guids = {str(item.work_guid or "") for item in existing if item.work_guid}
    existing_codes = {str(item.work_code or "") for item in existing if item.work_code}
    plan_months = {
        row.work_plan_item_id: row
        for row in repository.list_work_plan_months(plan.id)
        if row.month == period.month
    }
    list_items = {item.id: item for item in repository.list_work_list_items(period.work_list_id)}

    with repository.connection:
        for plan_item in repository.list_work_plan_items(plan.id):
            if (plan_item.work_guid and str(plan_item.work_guid) in existing_guids) or (
                plan_item.work_code and str(plan_item.work_code) in existing_codes
            ):
                continue
            list_item = list_items.get(plan_item.work_list_item_id) if plan_item.work_list_item_id else None
            month_row = plan_months.get(plan_item.id)
            repository.connection.execute(
                "INSERT INTO work_fixation_items ("
                "id, fixation_period_id, work_list_item_id, work_plan_item_id, position, "
                "work_code, work_name, work_guid, unit_name, planned_price, planned_amount, "
                "planned_count, planned_total_cost) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), period_id,
                    plan_item.work_list_item_id, plan_item.id, int(plan_item.position),
                    _clean(plan_item.work_code), plan_item.work_name, _clean(plan_item.work_guid),
                    _clean(list_item.unit_name) if list_item else None,
                    _clean(list_item.price) if list_item else None,
                    _clean(list_item.volume) if list_item else None,
                    int(month_row.count) if month_row else 0,
                    None,
                ),
            )

def apply_remote_work_fixation(
    repository: Repository,
    *,
    work_list_id: str,
    year: int,
    month: int,
    remote: dict[str, Any],
) -> dict[str, int]:
    if not remote.get("found"):
        raise ValueError("Фиксация работ в ГИС ЖКХ не найдена.")

    period = repository.get_or_create_work_fixation_period(
        work_list_id=work_list_id,
        year=int(year),
        month=int(month),
    )
    repository.refresh_work_fixation_plan_snapshot(period.id)
    _ensure_plan_rows(repository, period.id)
    local_items = repository.list_work_fixation_items(period.id)
    remote_items = [
        row
        for row in list(remote.get("plannedItems") or [])
        if isinstance(row, dict)
    ]
    pairs = match_remote_rows_allow_missing(local_items, remote_items)

    timestamp = _now()
    connection = repository.connection
    with connection:
        connection.execute(
            """
            UPDATE work_fixation_items
            SET fact_price = NULL,
                fact_amount = NULL,
                fact_count = NULL,
                fact_total_cost = NULL,
                gis_fixed_work_guid = NULL
            WHERE fixation_period_id = ?
            """,
            (period.id,),
        )

        for index, (local, row) in enumerate(pairs, start=1):
            if local is None:
                item_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO work_fixation_items (
                        id, fixation_period_id,
                        work_list_item_id, work_plan_item_id,
                        position, work_code, work_name, work_guid, unit_name,
                        planned_price, planned_amount, planned_count,
                        planned_total_cost, fact_price, fact_amount, fact_count,
                        fact_total_cost, gis_fixed_work_guid
                    ) VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        period.id,
                        int(row.get("position") or index),
                        _clean(row.get("workCode")),
                        str(row.get("workName") or row.get("workCode") or "Работа из ГИС ЖКХ"),
                        _clean(row.get("workGuid")),
                        _clean(row.get("unitName")),
                        _clean(row.get("plannedPrice")),
                        _clean(row.get("plannedAmount")),
                        int(row["plannedCount"])
                        if row.get("plannedCount") is not None
                        else 0,
                        _clean(row.get("plannedTotalCost")),
                        _clean(row.get("factPrice")),
                        _clean(row.get("factAmount")),
                        int(row["factCount"])
                        if row.get("factCount") is not None
                        else None,
                        _clean(row.get("factTotalCost")),
                        _clean(row.get("guid")),
                    ),
                )
                continue

            connection.execute(
                """
                UPDATE work_fixation_items
                SET planned_price = COALESCE(?, planned_price),
                    planned_amount = COALESCE(?, planned_amount),
                    planned_count = COALESCE(?, planned_count),
                    planned_total_cost = COALESCE(?, planned_total_cost),
                    fact_price = ?,
                    fact_amount = ?,
                    fact_count = ?,
                    fact_total_cost = ?,
                    gis_fixed_work_guid = ?
                WHERE id = ?
                """,
                (
                    _clean(row.get("plannedPrice")),
                    _clean(row.get("plannedAmount")),
                    int(row["plannedCount"])
                    if row.get("plannedCount") is not None
                    else None,
                    _clean(row.get("plannedTotalCost")),
                    _clean(row.get("factPrice")),
                    _clean(row.get("factAmount")),
                    int(row["factCount"])
                    if row.get("factCount") is not None
                    else None,
                    _clean(row.get("factTotalCost")),
                    _clean(row.get("guid")),
                    local.id,
                ),
            )

        connection.execute(
            """
            DELETE FROM work_fixation_photos
            WHERE unplanned_item_id IN (
                SELECT id
                FROM work_fixation_unplanned_items
                WHERE fixation_period_id = ?
            )
            """,
            (period.id,),
        )
        connection.execute(
            """
            DELETE FROM work_fixation_unplanned_items
            WHERE fixation_period_id = ?
            """,
            (period.id,),
        )

        remote_unplanned = [
            row
            for row in list(remote.get("unplannedItems") or [])
            if isinstance(row, dict)
        ]
        managed_guids: list[str] = []
        for position, row in enumerate(remote_unplanned, start=1):
            guid = _clean(row.get("guid"))
            if guid:
                managed_guids.append(guid)
            connection.execute(
                """
                INSERT INTO work_fixation_unplanned_items (
                    id, fixation_period_id, position,
                    work_code, work_name, work_guid,
                    work_type_code, work_type_guid, work_type_name, unit_name,
                    fact_price, fact_amount, fact_count, fact_total_cost,
                    comment, gis_fixed_work_guid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    period.id,
                    int(row.get("position") or position),
                    str(row.get("workCode") or ""),
                    str(row.get("workName") or ""),
                    str(row.get("workGuid") or ""),
                    _clean(row.get("workTypeCode")),
                    _clean(row.get("workTypeGuid")),
                    _clean(row.get("workTypeName")),
                    _clean(row.get("unitName")),
                    _clean(row.get("factPrice")),
                    _clean(row.get("factAmount")),
                    int(row["factCount"])
                    if row.get("factCount") is not None
                    else None,
                    _clean(row.get("factTotalCost")),
                    _clean(row.get("comment")),
                    guid,
                ),
            )

        connection.execute(
            """
            UPDATE work_fixation_periods
            SET reporting_period_guid = ?,
                gis_status = ?,
                gis_last_sync_at = ?,
                gis_last_error = NULL,
                managed_unplanned_guids_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                _clean(remote.get("reportingPeriodGuid")),
                _clean(remote.get("gisStatus")) or "SAVED",
                timestamp,
                json.dumps(managed_guids, ensure_ascii=False)
                if managed_guids
                else None,
                timestamp,
                period.id,
            ),
        )

    filled = sum(
        1
        for row in remote_items
        if any(
            row.get(key) not in (None, "")
            for key in ("factPrice", "factAmount", "factCount", "factTotalCost")
        )
    )
    return {
        "planned": len(remote_items),
        "filled": filled,
        "unplanned": len(remote_unplanned),
    }


def local_fixation_has_data(repository: Repository, period_id: str) -> bool:
    rows = repository.list_work_fixation_items(period_id)
    if any(
        any(
            value not in (None, "")
            for value in (
                row.fact_price,
                row.fact_amount,
                row.fact_count,
                row.fact_total_cost,
            )
        )
        for row in rows
    ):
        return True
    return bool(repository.list_work_fixation_unplanned_items(period_id))
