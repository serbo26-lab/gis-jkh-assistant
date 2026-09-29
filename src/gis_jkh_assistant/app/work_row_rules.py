"""Input-mode rules for one row of a work and service list."""

from __future__ import annotations

from enum import StrEnum


class WorkRowMode(StrEnum):
    EMPTY = "empty"
    COMPONENTS = "components"
    TOTAL = "total"


def normalize_text(value: str | None) -> str:
    return (value or "").strip()


def infer_work_row_mode(
    *,
    price: str | None,
    volume: str | None,
    cost: str | None,
) -> WorkRowMode:
    """Infer the GIS-compatible input mode from persisted values.

    Quantity exists in both valid schemes, so it does not determine the mode.

    COMPONENTS:
        price + volume + quantity are editable, cost is read-only/empty.

    TOTAL:
        quantity + cost are editable, price and volume are read-only/empty.
    """

    if normalize_text(price) or normalize_text(volume):
        return WorkRowMode.COMPONENTS
    if normalize_text(cost):
        return WorkRowMode.TOTAL
    return WorkRowMode.EMPTY


def editable_columns_for_mode(mode: WorkRowMode) -> frozenset[int]:
    """Return editable QTableWidget column indexes for one row."""

    if mode == WorkRowMode.COMPONENTS:
        return frozenset({0, 1, 2, 3})
    if mode == WorkRowMode.TOTAL:
        return frozenset({0, 3, 4})
    return frozenset({0, 1, 2, 3, 4})
