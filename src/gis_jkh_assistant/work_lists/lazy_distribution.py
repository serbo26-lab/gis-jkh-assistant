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
    quantity: str | None = None


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


def calculate_planned_total(
    *,
    tariff: str,
    area: str,
    months: str,
) -> str:
    """Calculate tariff × area × months and round the planned total to kopecks."""

    tariff_value = _decimal(tariff, "Тариф")
    area_value = _decimal(area, "Площадь")
    months_value = _decimal(months, "Месяцы")

    if tariff_value <= 0:
        raise LazyDistributionError("Тариф должен быть больше нуля.")
    if area_value <= 0:
        raise LazyDistributionError("Площадь должна быть больше нуля.")
    if months_value <= 0:
        raise LazyDistributionError("Количество месяцев должно быть больше нуля.")
    if months_value != months_value.to_integral_value():
        raise LazyDistributionError("Количество месяцев должно быть целым числом.")

    total = (tariff_value * area_value * months_value).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )
    return _storage(total)


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

    months = period_months(start_year, start_month, end_year, end_month)
    inputs = list(allocations)

    parsed_percents: list[Decimal] = []
    parsed_quantities: list[Decimal | None] = []

    for allocation in inputs:
        percent = _decimal(allocation.allocation_percent or "0", "Процент")
        if percent < 0:
            raise LazyDistributionError("Процент распределения не может быть отрицательным.")
        parsed_percents.append(percent)

        if percent == 0:
            parsed_quantities.append(None)
            continue

        quantity_text = allocation.quantity or str(months)
        quantity = _decimal(quantity_text, "Количество")
        if quantity <= 0:
            raise LazyDistributionError(
                "Для каждой позиции с процентом больше нуля количество должно быть больше нуля."
            )
        parsed_quantities.append(quantity)

    percent_sum = sum(parsed_percents, Decimal("0"))
    if percent_sum != PERCENT_TOTAL:
        raise LazyDistributionError(
            f"Сумма процентов должна быть ровно 100%. Сейчас: {_storage(percent_sum)}%."
        )

    costs: list[Decimal] = []
    for percent in parsed_percents:
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
        positive_indexes = [
            index for index, percent in enumerate(parsed_percents) if percent > 0
        ]
        if not positive_indexes:
            raise LazyDistributionError("Не задано ни одной позиции с процентом больше нуля.")
        target_index = max(
            positive_indexes,
            key=lambda index: parsed_percents[index],
        )
        costs[target_index] += residual

    rows: list[AllocationResult] = []
    for allocation, percent, quantity, cost in zip(
        inputs,
        parsed_percents,
        parsed_quantities,
        costs,
        strict=True,
    ):
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
            assert quantity is not None
            rows.append(
                AllocationResult(
                    item_id=allocation.item_id,
                    allocation_percent=_storage(percent),
                    quantity=_storage(quantity),
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
    value = (total * part / PERCENT_TOTAL).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )
    return _storage(value)


def validate_quantity(value: str | None) -> str:
    """Normalize a positive quantity entered by the user."""

    quantity = _decimal(value or "0", "Количество")
    if quantity <= 0:
        raise LazyDistributionError("Количество должно быть больше нуля.")
    return _storage(quantity)


def _decimal(value: str, label: str) -> Decimal:
    normalized = (
        str(value)
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )
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
