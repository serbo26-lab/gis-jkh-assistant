from decimal import Decimal

import pytest

from gis_jkh_assistant.work_lists import (
    AllocationInput,
    LazyDistributionError,
    calculate_distribution,
    period_months,
)


def test_example_distribution_matches_uploaded_workbook() -> None:
    result = calculate_distribution(
        planned_total="100000",
        start_year=2026,
        start_month=1,
        end_year=2026,
        end_month=12,
        allocations=[
            AllocationInput("13", "10"),
            AllocationInput("15", "6"),
            AllocationInput("18", "13"),
            AllocationInput("19", "10"),
            AllocationInput("16", "9"),
            AllocationInput("17", "12"),
            AllocationInput("14", "18"),
            AllocationInput("12", "22"),
        ],
    )

    assert result.months_count == 12
    assert result.percent_total == "100"
    assert [row.quantity for row in result.rows] == ["12"] * 8
    assert [row.cost for row in result.rows] == [
        "10000",
        "6000",
        "13000",
        "10000",
        "9000",
        "12000",
        "18000",
        "22000",
    ]


def test_partial_period_uses_inclusive_month_count() -> None:
    assert period_months(2026, 5, 2026, 12) == 8
    assert period_months(2025, 6, 2026, 5) == 12


def test_rounding_keeps_exact_total() -> None:
    result = calculate_distribution(
        planned_total="100",
        start_year=2026,
        start_month=1,
        end_year=2026,
        end_month=12,
        allocations=[
            AllocationInput("a", "33.33"),
            AllocationInput("b", "33.33"),
            AllocationInput("c", "33.34"),
        ],
    )

    assert sum(Decimal(row.cost) for row in result.rows if row.cost) == Decimal("100.00")


def test_zero_percent_leaves_position_empty() -> None:
    result = calculate_distribution(
        planned_total="1000",
        start_year=2026,
        start_month=1,
        end_year=2026,
        end_month=12,
        allocations=[
            AllocationInput("a", "100"),
            AllocationInput("b", "0"),
        ],
    )

    assert result.rows[1].allocation_percent is None
    assert result.rows[1].quantity is None
    assert result.rows[1].cost is None


def test_percent_sum_must_be_exactly_100() -> None:
    with pytest.raises(LazyDistributionError, match="ровно 100"):
        calculate_distribution(
            planned_total="100000",
            start_year=2026,
            start_month=1,
            end_year=2026,
            end_month=12,
            allocations=[
                AllocationInput("a", "60"),
                AllocationInput("b", "30"),
            ],
        )
