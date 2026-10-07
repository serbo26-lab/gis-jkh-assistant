"""Common matching helpers for GIS ЖКХ reverse synchronization."""

from __future__ import annotations

from typing import Any, Iterable


def match_remote_rows(
    local_rows: Iterable[Any],
    remote_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Strictly match GIS rows to local rows by GUID, code and position."""

    locals_list = list(local_rows)
    used: set[int] = set()
    result: dict[str, dict[str, Any]] = {}

    for local in locals_list:
        local_guid = str(getattr(local, "work_guid", None) or "").strip()
        local_code = str(getattr(local, "work_code", None) or "").strip()
        local_position = int(getattr(local, "position", 0) or 0)

        candidates: list[tuple[int, dict[str, Any]]] = []
        if local_guid:
            candidates = [
                (index, row)
                for index, row in enumerate(remote_rows)
                if index not in used
                and str(row.get("workGuid") or "").strip() == local_guid
            ]
        if not candidates and local_code:
            candidates = [
                (index, row)
                for index, row in enumerate(remote_rows)
                if index not in used
                and str(row.get("workCode") or "").strip() == local_code
            ]
        if len(candidates) > 1 and local_position:
            by_position = [
                pair
                for pair in candidates
                if int(pair[1].get("sortIndex") or pair[1].get("position") or 0)
                == local_position
            ]
            if len(by_position) == 1:
                candidates = by_position
        if not candidates and local_position:
            candidates = [
                (index, row)
                for index, row in enumerate(remote_rows)
                if index not in used
                and int(row.get("sortIndex") or row.get("position") or 0)
                == local_position
            ]

        if len(candidates) != 1:
            name = str(getattr(local, "work_name", "") or local_code or local_guid)
            raise ValueError(
                "Не удалось однозначно сопоставить работу с ГИС ЖКХ: " + name
            )

        index, remote = candidates[0]
        used.add(index)
        result[str(local.id)] = remote

    if len(used) != len(remote_rows):
        raise ValueError(
            "Состав данных в ГИС ЖКХ отличается от локального перечня. "
            "Сначала обновите перечень работ и услуг из ГИС ЖКХ."
        )
    return result
