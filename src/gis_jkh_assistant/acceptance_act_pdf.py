"""PDF generator for standard monthly acceptance acts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
)


_FONT_NAME = "GisJkhActSans"
_FONT_BOLD_NAME = "GisJkhActSansBold"


@dataclass(frozen=True, slots=True)
class AcceptanceActWorkRow:
    name: str
    unit_name: str | None = None
    periodicity: str | int | None = None
    actual_cost: str | None = None
    unit_cost: str | None = None
    actual_price: str | None = None
    actual_amount: str | None = None


def _font_candidates() -> tuple[tuple[str, str], ...]:
    return (
        (
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ),
        (
            "/Library/Fonts/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ),
    )


def _ensure_fonts() -> tuple[str, str]:
    if _FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return _FONT_NAME, _FONT_BOLD_NAME

    for regular, bold in _font_candidates():
        regular_path = Path(regular)
        bold_path = Path(bold)
        if regular_path.is_file() and bold_path.is_file():
            pdfmetrics.registerFont(TTFont(_FONT_NAME, str(regular_path)))
            pdfmetrics.registerFont(TTFont(_FONT_BOLD_NAME, str(bold_path)))
            return _FONT_NAME, _FONT_BOLD_NAME

    raise RuntimeError(
        "Не найден системный шрифт с поддержкой кириллицы для формирования PDF."
    )


def acceptance_act_headers(
    work_rows: list[AcceptanceActWorkRow],
) -> tuple[str, ...]:
    """The prescribed five columns remain present even when a value is unknown."""
    return ("Наименование вида работы (услуги)", "Периодичность / количественный показатель",
            "Ед. измерения", "Стоимость / сметная стоимость за единицу, руб.",
            "Цена выполненной работы (услуги), руб.")


def _format_actual_cost(value: str | None) -> str:
    if value is None or not str(value).strip():
        return ""
    source = str(value).strip()
    normalized = source.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return source
    return f"{number:,.2f}".replace(",", " ").replace(".", ",")


def _paragraph(
    text: object,
    *,
    style: ParagraphStyle,
) -> Paragraph:
    source = "" if text is None else str(text)
    safe = source.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(safe, style)


def build_acceptance_act_pdf(
    *, organization_name: str, house_address: str, act_number: str, act_date: str,
    period_from: str, period_to: str, work_rows: list[AcceptanceActWorkRow] | None = None,
    work_names: list[str] | None = None, details: dict | None = None,
) -> bytes:
    from .standard_acceptance_pdf import build_standard_pdf
    rows = work_rows if work_rows is not None else [AcceptanceActWorkRow(name=name) for name in (work_names or [])]
    return build_standard_pdf(organization_name=organization_name, house_address=house_address,
        act_number=act_number, act_date=act_date, period_from=period_from,
        period_to=period_to, work_rows=rows, details=details)
