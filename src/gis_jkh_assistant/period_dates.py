"""Parsing helpers for month-level and exact day dates used by GIS ЖКХ data."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedPeriodDate:
    value: date
    precision: str

    @property
    def input_label(self) -> str:
        if self.precision == "month":
            return f"{self.value.month:02d}.{self.value.year}"
        return self.value.strftime("%d.%m.%Y")


_MONTH_RE = re.compile(r"^(0?[1-9]|1[0-2])[.\-/](\d{4})$")
_DAY_RE = re.compile(
    r"^(0?[1-9]|[12]\d|3[01])[.\-/](0?[1-9]|1[0-2])[.\-/](\d{4})$"
)
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_period_date(value: Any, *, boundary: str = "start") -> ParsedPeriodDate:
    """Parse MM.YYYY or DD.MM.YYYY while preserving the user's precision.

    Month-only values are expanded to the first day for a start boundary and
    the last day for an end boundary. Exact dates remain exact.
    """

    if boundary not in {"start", "end"}:
        raise ValueError("boundary must be 'start' or 'end'")

    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return ParsedPeriodDate(value=value, precision="day")

    text = str(value or "").strip()
    if not text:
        raise ValueError("Дата не заполнена.")

    match = _MONTH_RE.fullmatch(text)
    if match:
        month = int(match.group(1))
        year = int(match.group(2))
        day = 1 if boundary == "start" else calendar.monthrange(year, month)[1]
        return ParsedPeriodDate(date(year, month, day), "month")

    match = _DAY_RE.fullmatch(text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))
        try:
            return ParsedPeriodDate(date(year, month, day), "day")
        except ValueError as exc:
            raise ValueError(f"Некорректная дата: {text}.") from exc

    match = _ISO_RE.fullmatch(text)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return ParsedPeriodDate(date(year, month, day), "day")
        except ValueError as exc:
            raise ValueError(f"Некорректная дата: {text}.") from exc

    raise ValueError(
        "Используйте дату в формате ММ.ГГГГ или ДД.ММ.ГГГГ, "
        f"например 08.2025 или 10.08.2025. Получено: {text!r}."
    )
