"""Planned-total ("lazy") work-list calculation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable


MONEY_QUANT = Decimal("0.01")
PERCENT_TOTAL = Decimal("100")


class LazyDistributionError(ValueError):
    """Raised when a percentage distribution cannot be calculated."""


@dataclass(frozen=True, slots=True)
class AllocationInput:
    item_id: str
    allocation_percent: str | None


@dataclass(frozen=True, slots=True)
class AllocationResult:
    item_id: str
    allocation_percent: str | None
    quantity: str | None
    cost: str | None


@dataclass(frozen=True, slots=True)
class LazyDistributionResult:
    planned_total: str
    months_count: int
    percent_total: str
    rows: tuple[AllocationResult, ...]


def period_months(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> int:
    months = (int(end_year) - int(start_year)) * 12 + int(end_month) - int(start_month) + 1
    if months <= 0:
        raise LazyDistributionError("Период перечня задан некорректно.")
    return months


def calculate_distribution(
    *,
    planned_total: str,
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
    allocations: Iterable[AllocationInput],
) -> LazyDistributionResult:
    total = _decimal(planned_total, "Итоговая сумма")
    if total <= 0:
        raise LazyDistributionError("Итоговая сумма должна быть больше нуля.")
    total = total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    inputs = list(allocations)
    parsed: list[Decimal] = []
    for allocation in inputs:
        value = _decimal(allocation.allocation_percent or "0", "Процент")
        if value < 0:
            raise LazyDistributionError("Процент распределения не может быть отрицательным.")
        parsed.append(value)

    percent_sum = sum(parsed, Decimal("0"))
    if percent_sum != PERCENT_TOTAL:
        raise LazyDistributionError(
            f"Сумма процентов должна быть ровно 100%. Сейчас: {_storage(percent_sum)}%."
        )

    months = period_months(start_year, start_month, end_year, end_month)

    costs: list[Decimal] = []
    for percent in parsed:
        if percent == 0:
            costs.append(Decimal("0"))
        else:
            costs.append(
                (total * percent / PERCENT_TOTAL).quantize(
                    MONEY_QUANT,
                    rounding=ROUND_HALF_UP,
                )
            )

    residual = total - sum(costs, Decimal("0"))
    if residual:
        positive_indexes = [index for index, percent in enumerate(parsed) if percent > 0]
        if not positive_indexes:
            raise LazyDistributionError("Не задано ни одной позиции с процентом больше нуля.")
        target_index = max(positive_indexes, key=lambda index: parsed[index])
        costs[target_index] += residual

    rows: list[AllocationResult] = []
    for allocation, percent, cost in zip(inputs, parsed, costs, strict=True):
        if percent == 0:
            rows.append(
                AllocationResult(
                    item_id=allocation.item_id,
                    allocation_percent=None,
                    quantity=None,
                    cost=None,
                )
            )
        else:
            rows.append(
                AllocationResult(
                    item_id=allocation.item_id,
                    allocation_percent=_storage(percent),
                    quantity=str(months),
                    cost=_storage(cost),
                )
            )

    return LazyDistributionResult(
        planned_total=_storage(total),
        months_count=months,
        percent_total=_storage(percent_sum),
        rows=tuple(rows),
    )


def preview_cost(planned_total: str, percent: str | None) -> str:
    """Return one rounded preview amount without requiring the full 100% sum."""

    total = _decimal(planned_total or "0", "Итоговая сумма")
    part = _decimal(percent or "0", "Процент")
    if total < 0 or part < 0:
        raise LazyDistributionError("Сумма и процент не могут быть отрицательными.")
    value = (total * part / PERCENT_TOTAL).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    return _storage(value)


def _decimal(value: str, label: str) -> Decimal:
    normalized = str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
    if not normalized:
        return Decimal("0")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise LazyDistributionError(f"{label}: «{value}» не является числом.") from exc


def _storage(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
