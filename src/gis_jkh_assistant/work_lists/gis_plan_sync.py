"""Reverse synchronization of one work plan from GIS ЖКХ."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from gis_jkh_assistant.database.repository import Repository

from .gis_match import match_remote_rows_allow_missing


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _remote_quantity(row: dict[str, Any]) -> str:
    explicit = _clean(row.get("listQuantity"))
    if explicit is not None:
        return explicit
    total = sum(
        int(month.get("count") or 0)
        for month in row.get("monthlyWorks") or []
        if isinstance(month, dict)
    )
    return str(total)


def apply_remote_work_plan(
    repository: Repository,
    *,
    work_list_id: str,
    year: int,
    remote: dict[str, Any],
) -> dict[str, int]:
    if not remote.get("found"):
        raise ValueError("План работ в ГИС ЖКХ не найден.")

    # Validate the whole response before creating a plan or removing any values.
    normalized = []
    for index, row in enumerate(remote.get("items") or [], start=1):
        if not isinstance(row, dict):
            raise ValueError("ГИС ЖКХ вернул некорректную строку плана.")
        work = _clean(row.get("workName")) or _clean(row.get("workCode")) or f"Строка {index}"
        months = []
        seen = set()
        for value in row.get("monthlyWorks") or []:
            if not isinstance(value, dict):
                raise ValueError("ГИС ЖКХ вернул некорректное значение месяца плана.")
            number = int(value.get("month") or 0)
            count = int(value.get("count") or 0)
            if not 1 <= number <= 12 or number in seen:
                raise ValueError("ГИС ЖКХ вернул некорректный месяц плана.")
            if count < 0:
                raise ValueError("Количество в плане не может быть отрицательным.")
            seen.add(number)
            dates = sorted({str(v).strip() for v in value.get("dates") or [] if str(v).strip()})
            for text in dates:
                try:
                    parsed = date.fromisoformat(text)
                except ValueError as exc:
                    raise ValueError(
                        f"В плане ГИС ЖКХ за {int(year)} год у работы «{work}» "
                        f"(строка {index}, месяц {number:02d}.{int(year)}) "
                        f"указана некорректная дата: {text}."
                    ) from exc
                if parsed.year != int(year) or parsed.month != number:
                    raise ValueError(
                        f"В плане ГИС ЖКХ за {int(year)} год у работы «{work}» "
                        f"(строка {index}) указана дата {parsed:%d.%m.%Y} "
                        f"в месяце {number:02d}.{int(year)}. "
                        "Дата должна относиться к этому месяцу и году. "
                        "Импорт отменён целиком; локальный план не изменён."
                    )
            if len(dates) > count:
                raise ValueError("Количество в месяце не может быть меньше числа выбранных дат.")
            months.append(dict(value, month=number, count=count, dates=dates))
        normalized.append(dict(row, monthlyWorks=months))
    with repository.connection.atomic():
        return _apply_remote_work_plan(
            repository, work_list_id=work_list_id, year=year,
            remote=dict(remote, items=normalized),
        )


def _apply_remote_work_plan(repository, *, work_list_id, year, remote):

    plan = repository.get_or_create_work_plan(
        work_list_id=work_list_id,
        year=int(year),
    )
    local_items = repository.list_work_plan_items(plan.id)
    remote_items = [
        row for row in list(remote.get("items") or []) if isinstance(row, dict)
    ]
    pairs = match_remote_rows_allow_missing(local_items, remote_items)

    connection = repository.connection
    item_pairs: list[tuple[str, dict[str, Any]]] = []
    matched_ids: set[str] = set()
    preserved_historical = [item for item in local_items if item.work_list_item_id is None
                            and all(local is None or local.id != item.id for local, _ in pairs)]
    historical = 0

    with connection:
        connection.execute(
            "DELETE FROM work_plan_months WHERE work_plan_item_id IN "
            "(SELECT id FROM work_plan_items WHERE work_plan_id = ? AND work_list_item_id IS NOT NULL)",
            (plan.id,),
        )

        for index, (local, row) in enumerate(pairs, start=1):
            position = int(row.get("sortIndex") or row.get("position") or index)
            work_code = _clean(row.get("workCode"))
            work_guid = _clean(row.get("workGuid"))
            work_name = (
                _clean(row.get("workName"))
                or (local.work_name if local is not None else None)
                or work_code
                or work_guid
                or f"Работа {position}"
            )
            quantity = _remote_quantity(row)

            if local is None:
                historical += 1
                item_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO work_plan_items "
                    "(id, work_plan_id, work_list_item_id, position, work_code, "
                    "work_name, work_guid, list_quantity) "
                    "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                    (item_id, plan.id, position, work_code, work_name, work_guid, quantity),
                )
            else:
                item_id = str(local.id)
                connection.execute("DELETE FROM work_plan_months WHERE work_plan_item_id = ?", (item_id,))
                connection.execute(
                    "UPDATE work_plan_items SET position = ?, "
                    "work_code = COALESCE(?, work_code), work_name = ?, "
                    "work_guid = COALESCE(?, work_guid), list_quantity = ? WHERE id = ?",
                    (position, work_code, work_name, work_guid, quantity, item_id),
                )

            matched_ids.add(item_id)
            item_pairs.append((item_id, row))

    filled_months = 0
    for item_id, row in item_pairs:
        for month in row.get("monthlyWorks") or []:
            month_number = int(month.get("month") or 0)
            count = int(month.get("count") or 0)
            dates = [str(value) for value in (month.get("dates") or []) if str(value).strip()]
            if not 1 <= month_number <= 12:
                raise ValueError("ГИС ЖКХ вернул некорректный месяц плана.")
            repository.update_work_plan_month(
                item_id, month=month_number, count=count, dates=dates
            )
            if count or dates:
                filled_months += 1

    repository.update_work_plan_gis_state(
        plan.id,
        gis_work_plan_guid=str(remote.get("workPlanGuid") or "").strip() or None,
        gis_status="MODIFIED" if preserved_historical else str(remote.get("gisStatus") or "").strip() or "SAVED",
        gis_last_error=None,
    )
    return {"items": len(remote_items), "months": filled_months, "historical": historical,
            "preserved_local": len(preserved_historical)}

def local_plan_has_data(repository: Repository, plan_id: str) -> bool:
    return bool(repository.list_work_plan_months(plan_id))
