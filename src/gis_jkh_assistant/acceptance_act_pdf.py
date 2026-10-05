"""PDF generator for standard monthly acceptance acts."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


_FONT_NAME = "GisJkhActSans"
_FONT_BOLD_NAME = "GisJkhActSansBold"


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


def _wrap_text(text: str, *, font_name: str, font_size: float, width: float) -> list[str]:
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def build_acceptance_act_pdf(
    *,
    organization_name: str,
    house_address: str,
    act_number: str,
    act_date: str,
    period_from: str,
    period_to: str,
    work_names: list[str],
) -> bytes:
    """Build a neutral monthly acceptance act without inventing signatories."""

    if not work_names:
        raise ValueError("Для формирования акта нужно выбрать хотя бы одну работу.")

    font_name, font_bold = _ensure_fonts()
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4)
    page_width, page_height = A4
    left = 20 * mm
    right = 20 * mm
    usable_width = page_width - left - right
    bottom = 25 * mm
    y = page_height - 20 * mm

    def new_page() -> None:
        nonlocal y
        pdf.showPage()
        y = page_height - 20 * mm

    def ensure_height(required: float) -> None:
        if y - required < bottom:
            new_page()

    pdf.setTitle(f"Акт выполненных работ № {act_number}")
    pdf.setAuthor(str(organization_name or "Управляющая организация"))

    pdf.setFont(font_bold, 14)
    pdf.drawCentredString(page_width / 2, y, "АКТ ВЫПОЛНЕННЫХ РАБОТ")
    y -= 8 * mm

    pdf.setFont(font_name, 10.5)
    pdf.drawCentredString(
        page_width / 2,
        y,
        f"№ {act_number} от {act_date} г.",
    )
    y -= 10 * mm

    label_width = 42 * mm
    for label, value in (
        ("Управляющая организация", organization_name),
        ("Адрес дома", house_address),
        ("Отчётный период", f"{period_from} - {period_to}"),
    ):
        lines = _wrap_text(
            value,
            font_name=font_name,
            font_size=9.5,
            width=usable_width - label_width,
        )
        ensure_height(max(7 * mm, len(lines) * 5 * mm))
        pdf.setFont(font_bold, 9.5)
        pdf.drawString(left, y, f"{label}:")
        pdf.setFont(font_name, 9.5)
        value_x = left + label_width
        for line_index, line in enumerate(lines):
            pdf.drawString(value_x, y - line_index * 5 * mm, line)
        y -= max(7 * mm, len(lines) * 5 * mm)

    y -= 3 * mm
    ensure_height(12 * mm)
    pdf.setFont(font_bold, 10)
    pdf.drawString(left, y, "Выполненные работы и услуги:")
    y -= 7 * mm

    for index, work_name in enumerate(work_names, start=1):
        lines = _wrap_text(
            work_name,
            font_name=font_name,
            font_size=9.2,
            width=usable_width - 8 * mm,
        )
        required = max(5 * mm, len(lines) * 5 * mm)
        ensure_height(required + 2 * mm)
        pdf.setFont(font_name, 9.2)
        pdf.drawString(left, y, f"{index}. {lines[0]}")
        y -= 5 * mm
        for line in lines[1:]:
            pdf.drawString(left + 7 * mm, y, line)
            y -= 5 * mm

    ensure_height(42 * mm)
    y -= 8 * mm
    pdf.setFont(font_name, 9.5)
    pdf.drawString(
        left,
        y,
        "Работы и услуги за указанный период выполнены.",
    )
    y -= 18 * mm
    pdf.drawString(
        left,
        y,
        "Представитель управляющей организации: __________________ / __________________",
    )

    pdf.save()
    return stream.getvalue()
