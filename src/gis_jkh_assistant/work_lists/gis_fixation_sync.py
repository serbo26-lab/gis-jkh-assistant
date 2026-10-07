"""Reverse synchronization of one fixation month from GIS ЖКХ."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from gis_jkh_assistant.database.repository import Repository

from .gis_match import match_remote_rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
    local_items = repository.list_work_fixation_items(period.id)
    remote_items = [
        row
        for row in list(remote.get("plannedItems") or [])
        if isinstance(row, dict)
    ]
    matched = match_remote_rows(local_items, remote_items)

    timestamp = _now()
    connection = repository.connection
    with connection:
        for local in local_items:
            row = matched[local.id]
            connection.execute(
                """
                UPDATE work_fixation_items
                SET fact_price = ?,
                    fact_amount = ?,
                    fact_count = ?,
                    fact_total_cost = ?,
                    gis_fixed_work_guid = ?
                WHERE id = ?
                """,
                (
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
