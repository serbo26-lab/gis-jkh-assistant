"""Common matching helpers for GIS ЖКХ reverse synchronization."""

from __future__ import annotations

from typing import Any, Iterable


def identities_compatible(local, remote):
    for attribute, key in (("work_guid", "workGuid"), ("work_code", "workCode")):
        left = str(getattr(local, attribute, None) or "").strip()
        right = str(remote.get(key) or "").strip()
        if left and right and left != right:
            return False
    return True


def match_remote_rows_allow_missing(
    local_rows: Iterable[Any],
    remote_rows: list[dict[str, Any]],
) -> list[tuple[Any | None, dict[str, Any]]]:
    """Match each GIS row to at most one local row and allow historical gaps."""

    locals_list = list(local_rows)
    used: set[int] = set()
    result: list[tuple[Any | None, dict[str, Any]]] = []

    for remote in remote_rows:
        remote_guid = str(remote.get("workGuid") or "").strip()
        remote_code = str(remote.get("workCode") or "").strip()
        remote_position = int(remote.get("sortIndex") or remote.get("position") or 0)

        candidates: list[tuple[int, Any]] = []
        if remote_guid:
            candidates = [
                (index, row)
                for index, row in enumerate(locals_list)
                if index not in used
                and str(getattr(row, "work_guid", None) or "").strip() == remote_guid
            ]
        if not candidates and remote_code:
            candidates = [
                (index, row)
                for index, row in enumerate(locals_list)
                if index not in used
                and str(getattr(row, "work_code", None) or "").strip() == remote_code
                and identities_compatible(row, remote)
            ]
        if len(candidates) > 1 and remote_position:
            by_position = [
                pair
                for pair in candidates
                if int(getattr(pair[1], "position", 0) or 0) == remote_position
            ]
            if len(by_position) == 1:
                candidates = by_position
        if not candidates and remote_position:
            candidates = [
                (index, row)
                for index, row in enumerate(locals_list)
                if index not in used
                and int(getattr(row, "position", 0) or 0) == remote_position
                and identities_compatible(row, remote)
            ]

        if len(candidates) > 1:
            raise ValueError(
                "Не удалось однозначно сопоставить работу из ГИС ЖКХ: "
                + str(remote.get("workName") or remote_code or remote_guid or remote_position)
            )
        if not candidates:
            result.append((None, remote))
            continue

        index, local = candidates[0]
        used.add(index)
        result.append((local, remote))

    return result
