"""Reverse synchronization of one quality assessment from GIS ЖКХ."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from gis_jkh_assistant.database.quality_assessment_store import (
    QualityAssessmentStore,
)

from .gis_match import match_remote_rows_allow_missing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def apply_remote_quality_assessment(
    store: QualityAssessmentStore,
    *,
    work_list_id: str,
    year: int,
    month: int,
    remote: dict[str, Any],
) -> dict[str, int]:
    if not remote.get("found"):
        raise ValueError("Оценка качества в ГИС ЖКХ не найдена.")

    period = store.get_or_create_period(
        work_list_id=work_list_id,
        year=int(year),
        month=int(month),
    )
    local_items = store.list_items(period.id)
    remote_items = [
        row for row in list(remote.get("items") or []) if isinstance(row, dict)
    ]
    pairs = match_remote_rows_allow_missing(local_items, remote_items)

    timestamp = _now()
    connection = store.connection
    applied_rows: list[tuple[str, dict[str, Any]]] = []
    with connection:
        connection.execute(
            """
            DELETE FROM quality_assessment_acts
            WHERE id IN (
                SELECT DISTINCT a.id
                FROM quality_assessment_acts a
                JOIN quality_assessment_act_links l ON l.act_id = a.id
                JOIN quality_assessment_items i ON i.id = l.quality_item_id
                WHERE i.quality_period_id = ?
            )
            """,
            (period.id,),
        )
        connection.execute(
            """
            UPDATE quality_assessment_items
            SET included = 0,
                cost = NULL,
                days_count = NULL,
                cost_reduction = NULL,
                gis_estimation_item_guid = NULL
            WHERE quality_period_id = ?
            """,
            (period.id,),
        )

        for index, (local, row) in enumerate(pairs, start=1):
            days_per_month = int(
                row.get("daysPerMonth")
                or (local.days_per_month if local is not None else 0)
                or 0
            )
            if local is None:
                local_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO quality_assessment_items ("
                    "id, quality_period_id, work_list_item_id, position, work_code, "
                    "work_name, work_guid, included, cost, days_count, days_per_month, "
                    "cost_reduction, gis_estimation_item_guid) "
                    "VALUES (?, ?, NULL, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                    (
                        local_id, period.id, int(row.get("position") or index),
                        _clean(row.get("workCode")),
                        str(row.get("workName") or row.get("workCode") or "Работа из ГИС ЖКХ"),
                        _clean(row.get("workGuid")), _clean(row.get("cost")),
                        int(row["daysCount"]) if row.get("daysCount") is not None else None,
                        days_per_month, _clean(row.get("costReduction")), _clean(row.get("guid")),
                    ),
                )
            else:
                local_id = str(local.id)
                connection.execute(
                    "UPDATE quality_assessment_items SET included = 1, cost = ?, "
                    "days_count = ?, days_per_month = ?, cost_reduction = ?, "
                    "gis_estimation_item_guid = ? WHERE id = ?",
                    (
                        _clean(row.get("cost")),
                        int(row["daysCount"]) if row.get("daysCount") is not None else None,
                        days_per_month, _clean(row.get("costReduction")),
                        _clean(row.get("guid")), local_id,
                    ),
                )
            applied_rows.append((local_id, row))
        acts: dict[str, dict[str, Any]] = {}
        for local_id, row in applied_rows:
            for act in row.get("acts") or []:
                if not isinstance(act, dict):
                    continue
                key = (
                    str(act.get("guid") or "").strip()
                    or str(act.get("contentGuid") or "").strip()
                    or "|".join(
                        (
                            str(act.get("actType") or ""),
                            str(act.get("number") or ""),
                            str(act.get("regDate") or ""),
                            str(act.get("periodFrom") or ""),
                            str(act.get("periodTo") or ""),
                        )
                    )
                )
                entry = acts.setdefault(
                    key,
                    {
                        "id": str(uuid.uuid4()),
                        "act": act,
                        "local_ids": set(),
                    },
                )
                entry["local_ids"].add(local_id)

        for entry in acts.values():
            act = entry["act"]
            linked_ids = set(entry["local_ids"])
            owner_id = sorted(linked_ids)[0]
            connection.execute(
                """
                INSERT INTO quality_assessment_acts (
                    id, quality_item_id, act_type, number, reg_date,
                    period_from, period_to, file_name, description,
                    content_type, file_size, content,
                    remote_content_guid, remote_act_guid,
                    deleted, generated, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, 0, ?)
                """,
                (
                    entry["id"],
                    owner_id,
                    str(act.get("actType") or "ACCEPTANCE_ACT"),
                    str(act.get("number") or ""),
                    str(act.get("regDate") or ""),
                    str(act.get("periodFrom") or ""),
                    str(act.get("periodTo") or ""),
                    str(act.get("fileName") or "Файл акта из ГИС ЖКХ"),
                    _clean(act.get("description")),
                    str(act.get("contentType") or "application/octet-stream"),
                    int(act.get("fileSize") or 0),
                    _clean(act.get("contentGuid")),
                    _clean(act.get("guid")),
                    timestamp,
                ),
            )
            for local_id in linked_ids:
                connection.execute(
                    """
                    INSERT INTO quality_assessment_act_links (
                        act_id, quality_item_id, deleted, created_at
                    ) VALUES (?, ?, 0, ?)
                    """,
                    (entry["id"], local_id, timestamp),
                )

        connection.execute(
            """
            UPDATE quality_assessment_periods
            SET reporting_period_guid = ?,
                estimation_guid = ?,
                gis_status = ?,
                gis_last_sync_at = ?,
                gis_last_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                _clean(remote.get("reportingPeriodGuid")),
                _clean(remote.get("estimationGuid")),
                _clean(remote.get("gisStatus")) or "DRAFT",
                timestamp,
                timestamp,
                period.id,
            ),
        )

    return {"items": len(remote_items), "acts": len(acts)}


def local_quality_has_data(store: QualityAssessmentStore, period_id: str) -> bool:
    for row in store.list_items(period_id):
        if (
            row.included
            or row.cost is not None
            or row.days_count is not None
            or row.cost_reduction is not None
            or store.list_acts(row.id)
        ):
            return True
    return False
