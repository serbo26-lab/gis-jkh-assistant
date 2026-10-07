"""Shared GIS ЖКХ work-list synchronization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gis_jkh_assistant.database.repository import Repository


@dataclass(slots=True)
class GisWorkListSyncPreview:
    """Classified remote work lists before any local changes are applied."""

    received: int
    new_rows: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    linked_rows: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class GisWorkListSyncResult:
    """Result of applying a prepared synchronization preview."""

    imported: int = 0
    updated: int = 0
    imported_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _period_key(remote: dict[str, Any]) -> tuple[int, int, int, int] | None:
    period = remote.get("period") if isinstance(remote.get("period"), dict) else {}
    period_from = period.get("from") if isinstance(period.get("from"), dict) else {}
    period_to = period.get("to") if isinstance(period.get("to"), dict) else {}
    try:
        start_year = int(period_from.get("year"))
        start_month = int(period_from.get("month"))
        end_year = int(period_to.get("year"))
        end_month = int(period_to.get("month"))
    except (TypeError, ValueError):
        return None
    if not 1 <= start_month <= 12 or not 1 <= end_month <= 12:
        return None
    if (start_year, start_month) > (end_year, end_month):
        return None
    return start_year, start_month, end_year, end_month


def prepare_gis_work_list_sync(
    repository: Repository,
    organization_id: str,
    remote_items: list[dict[str, Any]],
    *,
    target_house_id: str | None = None,
) -> GisWorkListSyncPreview:
    """Match GIS work lists to local houses and protect local period conflicts."""

    houses = repository.list_houses(organization_id)
    if target_house_id is not None:
        houses = [house for house in houses if house.id == target_house_id]

    houses_by_guid = {}
    for house in houses:
        for value in (
            house.house_object_guid,
            house.gis_house_guid,
            house.fias_house_guid,
        ):
            key = str(value or "").strip()
            if key:
                houses_by_guid[key] = house

    preview = GisWorkListSyncPreview(received=len(remote_items))
    candidates: list[tuple[str, dict[str, Any], tuple[str, int, int, int, int]]] = []

    for remote in remote_items:
        if not isinstance(remote, dict):
            preview.conflicts.append("ГИС ЖКХ вернул некорректную запись перечня.")
            continue

        gis_guid = str(remote.get("guid") or "").strip()
        if not gis_guid:
            preview.conflicts.append("ГИС ЖКХ не вернул GUID одного из перечней.")
            continue

        existing = repository.find_work_list_by_gis_guid(gis_guid)
        if existing is not None:
            if target_house_id is None or existing.house_id == target_house_id:
                preview.linked_rows.append((existing.id, remote))
            continue

        house_guid = str(remote.get("houseGuid") or "").strip()
        house = houses_by_guid.get(house_guid)
        if house is None:
            preview.unmatched.append(remote)
            continue

        period = _period_key(remote)
        if period is None:
            preview.conflicts.append(
                f"Перечень {gis_guid}: ГИС ЖКХ вернул некорректный период."
            )
            continue
        start_year, start_month, end_year, end_month = period

        local_period = repository.find_work_lists_for_exact_period(
            house.id,
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
            end_month=end_month,
        )
        if local_period:
            preview.conflicts.append(
                f"{house.address}: локально уже есть перечень "
                f"{start_month:02d}.{start_year} — {end_month:02d}.{end_year} "
                "с другим GUID ГИС ЖКХ."
            )
            continue

        candidates.append(
            (
                house.id,
                remote,
                (house.id, start_year, start_month, end_year, end_month),
            )
        )

    grouped: dict[
        tuple[str, int, int, int, int],
        list[tuple[str, dict[str, Any]]],
    ] = {}
    for house_id, remote, key in candidates:
        grouped.setdefault(key, []).append((house_id, remote))

    for rows in grouped.values():
        if len(rows) == 1:
            preview.new_rows.append(rows[0])
            continue

        for house_id, remote in rows:
            house = repository.get_house(house_id)
            address = house.address if house is not None else "Дом"
            preview.conflicts.append(
                f"{address}: в ГИС ЖКХ найдено несколько перечней "
                "на один и тот же период."
            )

    return preview


def apply_gis_work_list_sync(
    repository: Repository,
    preview: GisWorkListSyncPreview,
) -> GisWorkListSyncResult:
    """Apply only conflict-free rows from a synchronization preview."""

    result = GisWorkListSyncResult()

    for house_id, remote in preview.new_rows:
        try:
            imported = repository.import_gis_work_list(
                house_id=house_id,
                remote=remote,
            )
        except (ValueError, OSError) as exc:
            result.errors.append(str(exc))
            continue
        result.imported += 1
        result.imported_ids.append(imported.id)

    for local_id, remote in preview.linked_rows:
        try:
            repository.update_work_list_gis_state(
                local_id,
                gis_work_list_guid=str(remote.get("guid") or "").strip(),
                gis_status=str(remote.get("status") or "").strip() or None,
                gis_last_error=None,
            )
        except (ValueError, OSError) as exc:
            result.errors.append(str(exc))
            continue
        result.updated += 1

    return result
