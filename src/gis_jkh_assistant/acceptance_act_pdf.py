"""PDF generator for standard monthly acceptance acts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)


_FONT_NAME = "GisJkhActSans"
_FONT_BOLD_NAME = "GisJkhActSansBold"


@dataclass(frozen=True, slots=True)
class AcceptanceActWorkRow:
    name: str
    unit_name: str | None = None
    periodicity: str | int | None = None
    actual_cost: str | None = None


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
    """Return table headers, omitting units when none are available."""

    has_units = any(str(row.unit_name or "").strip() for row in work_rows)
    headers = ["№ п/п", "Наименование работы (услуги)"]
    if has_units:
        headers.append("Ед. измерения")
    headers.extend(["Периодичность", "Стоимость факт., руб."])
    return tuple(headers)


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
    *,
    organization_name: str,
    house_address: str,
    act_number: str,
    act_date: str,
    period_from: str,
    period_to: str,
    work_rows: list[AcceptanceActWorkRow] | None = None,
    work_names: list[str] | None = None,
) -> bytes:
    """Build a neutral monthly acceptance act from fixation facts."""

    if work_rows is None:
        work_rows = [
            AcceptanceActWorkRow(name=name)
            for name in (work_names or [])
        ]
    if not work_rows:
        raise ValueError("Для формирования акта нужно выбрать хотя бы одну работу.")

    font_name, font_bold = _ensure_fonts()
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"Акт выполненных работ № {act_number}",
        author=str(organization_name or "Управляющая организация"),
    )

    title_style = ParagraphStyle(
        "ActTitle",
        fontName=font_bold,
        fontSize=14,
        leading=17,
        alignment=TA_CENTER,
        spaceAfter=5 * mm,
    )
    subtitle_style = ParagraphStyle(
        "ActSubtitle",
        fontName=font_name,
        fontSize=10.5,
        leading=13,
        alignment=TA_CENTER,
        spaceAfter=7 * mm,
    )
    label_style = ParagraphStyle(
        "ActLabel",
        fontName=font_bold,
        fontSize=9.5,
        leading=12,
        alignment=TA_LEFT,
    )
    body_style = ParagraphStyle(
        "ActBody",
        fontName=font_name,
        fontSize=9.5,
        leading=12,
        alignment=TA_LEFT,
    )
    table_header_style = ParagraphStyle(
        "ActTableHeader",
        fontName=font_bold,
        fontSize=8.3,
        leading=10,
        alignment=TA_CENTER,
    )
    table_text_style = ParagraphStyle(
        "ActTableText",
        fontName=font_name,
        fontSize=8.5,
        leading=10.5,
        alignment=TA_LEFT,
    )
    table_center_style = ParagraphStyle(
        "ActTableCenter",
        fontName=font_name,
        fontSize=8.5,
        leading=10.5,
        alignment=TA_CENTER,
    )
    table_money_style = ParagraphStyle(
        "ActTableMoney",
        fontName=font_name,
        fontSize=8.5,
        leading=10.5,
        alignment=TA_RIGHT,
    )

    story = [
        _paragraph("АКТ ВЫПОЛНЕННЫХ РАБОТ", style=title_style),
        _paragraph(f"№ {act_number} от {act_date} г.", style=subtitle_style),
    ]

    for label, value in (
        ("Управляющая организация:", organization_name),
        ("Адрес дома:", house_address),
        ("Отчётный период:", f"{period_from} - {period_to}"),
    ):
        details = LongTable(
            [[
                _paragraph(label, style=label_style),
                _paragraph(value, style=body_style),
            ]],
            colWidths=[42 * mm, 128 * mm],
        )
        details.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(details)

    story.extend(
        [
            Spacer(1, 4 * mm),
            _paragraph("Выполненные работы и услуги:", style=label_style),
            Spacer(1, 2 * mm),
        ]
    )

    headers = acceptance_act_headers(work_rows)
    has_units = "Ед. измерения" in headers
    data: list[list[object]] = [
        [_paragraph(header, style=table_header_style) for header in headers]
    ]

    for index, row in enumerate(work_rows, start=1):
        values: list[object] = [
            _paragraph(index, style=table_center_style),
            _paragraph(row.name, style=table_text_style),
        ]
        if has_units:
            values.append(_paragraph(row.unit_name or "", style=table_center_style))
        values.extend(
            [
                _paragraph(
                    "" if row.periodicity is None else row.periodicity,
                    style=table_center_style,
                ),
                _paragraph(
                    _format_actual_cost(row.actual_cost),
                    style=table_money_style,
                ),
            ]
        )
        data.append(values)

    if has_units:
        col_widths = [12 * mm, 77 * mm, 22 * mm, 31 * mm, 28 * mm]
    else:
        col_widths = [12 * mm, 97 * mm, 31 * mm, 30 * mm]

    table = LongTable(
        data,
        colWidths=col_widths,
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.55, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDED")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.extend(
        [
            Spacer(1, 8 * mm),
            _paragraph(
                "Работы и услуги за указанный период выполнены.",
                style=body_style,
            ),
            Spacer(1, 15 * mm),
            _paragraph(
                "Представитель управляющей организации: "
                "__________________ / __________________",
                style=body_style,
            ),
        ]
    )

    document.build(story)
    return stream.getvalue()
