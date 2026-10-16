"""Printable acceptance-act form with five prescribed columns and both parties."""
from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Flowable, Image, KeepTogether, LongTable, SimpleDocTemplate, Spacer, Table, TableStyle

from .acceptance_act_details import (
    CUSTOMER_NAME_PLACEHOLDER, CUSTOMER_PREMISES_PLACEHOLDER, suggested_position_genitive,
)
from .act_images import (
    SIGNATURE_HEIGHT_MM, SIGNATURE_LINE_MM, SIGNATURE_WIDTH_MM, fitted_size, image_png,
    normalize_image_settings, prepare_act_image, signature_placement,
)


class _SignatureField(Flowable):
    """Image and line share one reserved field; neither can overflow its row."""

    def __init__(self, encoded, settings=None):
        super().__init__()
        self.width, self.height = SIGNATURE_WIDTH_MM * mm, SIGNATURE_HEIGHT_MM * mm
        self.image = prepare_act_image(base64.b64decode(encoded), settings, kind="signature")
        self.placement = signature_placement(self.image.size, settings)

    def draw(self):
        x, y, width, height = self.placement
        self.canv.drawImage(ImageReader(BytesIO(image_png(self.image))), x * mm, y * mm,
                            width=width * mm, height=height * mm, mask="auto")
        # Draw last so even a user-retained opaque background cannot hide the line.
        self.canv.setStrokeColor(colors.black)
        self.canv.setLineWidth(.4)
        self.canv.line(0, SIGNATURE_LINE_MM * mm, self.width, SIGNATURE_LINE_MM * mm)


def amount_decimal(value):
    try:
        result = Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Некорректная стоимость работы в акте.") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Стоимость работы должна быть конечным неотрицательным числом.")
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money_in_words(amount):
    value = amount_decimal(amount)
    rubles, kopeks = int(value), int((value % 1) * 100)
    if rubles >= 10 ** 15:
        raise ValueError("Сумма акта слишком велика.")
    ones = ("", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
    teens = ("десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать")
    tens = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
    hundreds = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")

    def ending(number, variants):
        return variants[2 if 11 <= number % 100 <= 14 else 0 if number % 10 == 1 else 1 if 2 <= number % 10 <= 4 else 2]

    scales = (("рубль", "рубля", "рублей"), ("тысяча", "тысячи", "тысяч"),
              ("миллион", "миллиона", "миллионов"), ("миллиард", "миллиарда", "миллиардов"),
              ("триллион", "триллиона", "триллионов"))
    groups, left = [], rubles
    for scale, variants in enumerate(scales):
        number = left % 1000
        left //= 1000
        if number:
            units = ("", "одна", "две", *ones[3:]) if scale == 1 else ones
            words = [hundreds[number // 100]]
            rest = number % 100
            words += [teens[rest - 10]] if 10 <= rest <= 19 else [tens[rest // 10], units[rest % 10]]
            groups.insert(0, " ".join(w for w in (*words, ending(number, variants)) if w))
        elif scale == 0:
            groups.insert(0, "рублей")
    if rubles == 0:
        groups.insert(0, "ноль")
    return (" ".join(groups) + f" {kopeks:02d} " + ending(kopeks, ("копейка", "копейки", "копеек"))).capitalize()


def _customer_signature_placeholder(font_name):
    # Match the supplied two-line 440 x 84 SVG as printable vector graphics.
    # The customer's unused seal cell gives the text enough room to stay legible.
    scale = 64 * mm / 440
    drawing = Drawing(440 * scale, 84 * scale)
    drawing.add(Rect(scale, scale, 438 * scale, 82 * scale,
                     fillColor=colors.white, strokeColor=colors.black, strokeWidth=2 * scale))
    for baseline, text in ((48, "Данные скрыты в целях безопасности"), (22, "персональных данных")):
        drawing.add(String(220 * scale, baseline * scale, text, fontName=font_name,
                           fontSize=20 * scale, textAnchor="middle", fillColor=colors.black))
    return drawing


def _customer_preamble(customer):
    if not customer.get("full_name"):
        return (f"в лице уполномоченного представителя {CUSTOMER_NAME_PLACEHOLDER}, "
                f"являющегося собственником помещения № {CUSTOMER_PREMISES_PLACEHOLDER}, "
                "находящегося в данном многоквартирном доме")
    name = customer.get("name_genitive") or customer["full_name"]
    text = "в лице уполномоченного представителя " + name
    if customer.get("premises"):
        text += (f", являющегося собственником помещения № {customer['premises']}, "
                 "находящегося в данном многоквартирном доме")
    if customer.get("basis"):
        text += ", действующего на основании " + customer["basis"]
    return text


def _customer_signature_label(customer):
    if not customer.get("full_name"):
        return "Заказчик — " + CUSTOMER_NAME_PLACEHOLDER
    role = customer.get("role")
    return "Заказчик — " + (role + ", " if role else "") + customer["full_name"]


def build_standard_pdf(*, organization_name, house_address, act_number, act_date,
                       period_from, period_to, work_rows, details=None):
    from .acceptance_act_pdf import _ensure_fonts, _paragraph, _format_actual_cost, acceptance_act_headers
    if not work_rows:
        raise ValueError("Для формирования акта нужно выбрать хотя бы одну работу.")
    details = details or {"issues": ["Не заполнены реквизиты сторон."], "executor": {}, "customer": {}}
    organization_name = details.get("organization_name") or organization_name
    house_address = details.get("house_address") or house_address
    executor, customer = details.get("executor", {}), details.get("customer", {})
    regular, bold = _ensure_fonts()
    body = ParagraphStyle("ActBody", fontName=regular, fontSize=9.5, leading=12.2, spaceAfter=5)
    heading = ParagraphStyle("ActHeading", parent=body, fontName=bold, fontSize=11.5, leading=14, alignment=TA_CENTER)
    small = ParagraphStyle("ActSmall", parent=body, fontSize=8, leading=10, spaceAfter=0)
    header = ParagraphStyle("ActHeader", parent=small, fontName=bold, alignment=TA_CENTER)
    date_style = ParagraphStyle("ActDate", parent=body, alignment=TA_RIGHT)
    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                                 topMargin=14*mm, bottomMargin=17*mm,
                                 title=f"Акт приемки № {act_number}", author=organization_name)
    def p(value, style=body):
        return _paragraph(value, style=style)
    blank = "________________________"
    story = []
    if details.get("issues"):
        story += [p("ПРОЕКТ — реквизиты требуют проверки", heading), p(" ".join(details["issues"]), small), Spacer(1, 3*mm)]
    story += [p(f"АКТ № {act_number}", heading),
              p("приемки оказанных услуг и (или) выполненных работ по содержанию и текущему ремонту общего имущества в многоквартирном доме", heading),
              Spacer(1, 3*mm)]
    story.append(Table([[p(details.get("locality") or blank), p(act_date, date_style)]], colWidths=[110*mm, 64*mm]))
    executor_name = executor.get("name_genitive") or blank
    executor_position_genitive = executor.get("position_genitive") or suggested_position_genitive(executor.get("position")) or blank
    executor_position = executor.get("position") or blank
    if len(executor_position) > 1 and executor_position[1].islower():
        executor_position = executor_position[0].lower() + executor_position[1:]
    executor_acting = "действующей" if executor.get("gender") == "female" else "действующего"
    story += [p(f"Собственники помещений в многоквартирном доме, расположенном по адресу: {house_address}, "
                f"именуемые в дальнейшем «Заказчик», {_customer_preamble(customer)}, "
                f"с одной стороны, и {organization_name} (далее — «Исполнитель»), "
                f"в лице {executor_position_genitive} {executor_name}, "
                f"{executor_acting} на основании {executor.get('basis') or blank}, с другой стороны, "
                "совместно именуемые «Стороны», составили настоящий Акт о нижеследующем:"),
              p(f"1. Исполнителем предъявлены к приемке следующие оказанные на основании договора управления "
                f"многоквартирным домом № {details.get('agreement_number') or blank} от {details.get('agreement_date') or blank} "
                f"услуги и (или) выполненные работы по содержанию и текущему ремонту общего имущества "
                f"в многоквартирном доме № {details.get('house_number') or blank}, расположенном по адресу: "
                f"{details.get('address_without_number') or house_address}:"), Spacer(1, 2*mm)]
    data = [[p(text, header) for text in acceptance_act_headers(work_rows)]]
    total, all_costs = Decimal(0), True
    for row in work_rows:
        indicator = "" if row.periodicity is None else "Выполнений: " + str(row.periodicity)
        if row.actual_amount not in (None, ""):
            indicator += ("; " if indicator else "") + "объём: " + str(row.actual_amount)
        unit_cost = _format_actual_cost(row.unit_cost)
        if row.actual_price not in (None, "") and row.unit_cost not in (None, "") and amount_decimal(row.actual_price) != amount_decimal(row.unit_cost):
            unit_cost = "По договору: " + unit_cost + "; факт: " + _format_actual_cost(row.actual_price)
        elif not unit_cost and row.actual_price not in (None, ""):
            unit_cost = "Факт: " + _format_actual_cost(row.actual_price)
        if row.actual_cost in (None, ""):
            all_costs = False
        else:
            total += amount_decimal(row.actual_cost)
        data.append([p(row.name, small), p(indicator, small), p(row.unit_name or "", small),
                     p(unit_cost, small), p(_format_actual_cost(row.actual_cost), small)])
    table = LongTable(data, colWidths=[67*mm, 28*mm, 17*mm, 34*mm, 28*mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .5, colors.black),
                              ("VALIGN", (0,0), (-1,-1), "TOP"),
                              ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
                              ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
    story += [table, Spacer(1, 3*mm),
              p(f"2. Всего за период с {period_from} по {period_to} выполнено работ (оказано услуг) "
                f"на общую сумму {_format_actual_cost(str(total)) if all_costs else blank} руб. "
                f"({money_in_words(total) if all_costs else 'сумма не определена — не заполнена стоимость работ'})."),
              p("3. Работы (услуги) выполнены (оказаны) полностью, в установленные сроки, с надлежащим качеством."),
              p("4. Претензий по выполнению условий Договора Стороны друг к другу не имеют."),
              p("Настоящий Акт составлен в 2-х экземплярах, имеющих одинаковую юридическую силу, по одному для каждой из Сторон.")]

    signature = (_SignatureField(executor["signature_base64"], executor.get("signature_settings"))
                 if details.get("insert_images") and executor.get("signature_base64")
                 else p("________________", small))
    stamp, stamp_column_mm = p("", small), 30
    if details.get("insert_images") and details.get("stamp_base64"):
        stamp_settings = normalize_image_settings(details.get("stamp_settings"), kind="stamp")
        prepared = prepare_act_image(base64.b64decode(details["stamp_base64"]), stamp_settings, kind="stamp")
        size = stamp_settings["size_mm"]
        width, height = fitted_size(prepared.size, size, size)
        stamp = Image(BytesIO(image_png(prepared)), width=width * mm, height=height * mm)
        stamp_column_mm = max(28, size + 2)  # also leave room for the two-line customer placeholder
    customer_signature = (_customer_signature_placeholder(regular)
                          if details.get("customer_signature_placeholder") else p("________________", small))
    signatures = Table([
        [p("Исполнитель — " + executor_position + " " + organization_name + " " + (executor.get("full_name") or blank), small), signature, stamp],
        [p(_customer_signature_label(customer), small), customer_signature, p("", small)],
    ], colWidths=[(134 - stamp_column_mm)*mm, 40*mm, stamp_column_mm*mm])
    signatures.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
    signatures.setStyle(TableStyle([("LEFTPADDING", (1,0), (-1,-1), 1*mm),
                                    ("RIGHTPADDING", (1,0), (-1,-1), 1*mm)]))
    if details.get("customer_signature_placeholder"):
        signatures.setStyle(TableStyle([("SPAN", (1,1), (2,1))]))
    story.append(KeepTogether([Spacer(1, 3*mm), p("Подписи Сторон", heading), signatures]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.drawRightString(192*mm, 9*mm, f"{doc.page}")
        canvas.restoreState()
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
