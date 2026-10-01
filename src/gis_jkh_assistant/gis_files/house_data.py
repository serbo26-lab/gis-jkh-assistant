"""Portable XLSX template for house charge areas and tariff history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from gis_jkh_assistant.database.models import House, HouseTariff
from gis_jkh_assistant.period_dates import ParsedPeriodDate, parse_period_date


class HouseDataImportError(ValueError):
    """Raised when a house-data workbook is structurally invalid."""


@dataclass(frozen=True, slots=True)
class ImportedAreaRow:
    house_id: str | None
    address: str
    area: str
    row_number: int


@dataclass(frozen=True, slots=True)
class ImportedTariffRow:
    house_id: str | None
    address: str
    start: ParsedPeriodDate
    amount: str
    row_number: int


@dataclass(frozen=True, slots=True)
class HouseDataImportResult:
    areas: tuple[ImportedAreaRow, ...]
    tariffs: tuple[ImportedTariffRow, ...]
    warnings: tuple[str, ...] = ()


AREA_SHEET = "Площади"
TARIFF_SHEET = "Тарифы"
INSTRUCTION_SHEET = "Инструкция"
MIN_TARIFF_PAIRS = 10


def write_house_data_template(
    path: str | Path,
    *,
    houses: Iterable[House],
    tariffs_by_house: dict[str, Iterable[HouseTariff]],
) -> None:
    """Write a reusable workbook prefilled with the current houses."""

    workbook = Workbook()
    instruction = workbook.active
    instruction.title = INSTRUCTION_SHEET
    instruction.append(["Импорт площадей и тарифов"])
    instruction["A1"].font = Font(bold=True, size=14)
    instructions = [
        "Лист «Площади»: заполните площадь к начислению для нужных домов.",
        "Лист «Тарифы»: один дом занимает одну строку, история идет вправо парами "
        "«Действует с» / «Тариф». Пустые пары можно пропускать.",
        "Дата «08.2025» означает начало с 01.08.2025.",
        "Дата «10.08.2025» сохраняется как точная дата 10.08.2025.",
        "Конечную дату тарифа вводить не нужно: она определяется следующим изменением.",
        "Если тариф не менялся, повторять его каждый год не обязательно.",
        "Старый вертикальный формат (одна строка = одна точка тарифа) тоже поддерживается.",
        "Столбец _house_id служебный и скрыт. Адрес можно использовать для проверки.",
    ]
    for line in instructions:
        instruction.append([line])
    instruction.column_dimensions["A"].width = 105

    areas = workbook.create_sheet(AREA_SHEET)
    areas.append(["_house_id", "Адрес", "Площадь к начислению, м²"])
    _style_header(areas)

    sorted_houses = sorted(houses, key=lambda item: item.address.casefold())
    histories = {
        house.id: sorted(
            list(tariffs_by_house.get(house.id, ())),
            key=lambda item: item.effective_from_date,
        )
        for house in sorted_houses
    }
    pair_count = max(
        MIN_TARIFF_PAIRS,
        max((len(items) for items in histories.values()), default=0),
    )

    tariffs = workbook.create_sheet(TARIFF_SHEET)
    tariff_header = ["_house_id", "Адрес"]
    for _ in range(pair_count):
        tariff_header.extend(["Действует с", "Тариф, руб./м² в месяц"])
    tariffs.append(tariff_header)
    _style_header(tariffs)

    for house in sorted_houses:
        areas.append([house.id, house.address, house.charge_area or ""])

        tariff_row: list[object] = [house.id, house.address]
        existing = histories[house.id]
        for index in range(pair_count):
            if index < len(existing):
                tariff = existing[index]
                tariff_row.extend([tariff.effective_from_label, tariff.amount])
            else:
                tariff_row.extend(["", ""])
        tariffs.append(tariff_row)

    for row in range(2, areas.max_row + 1):
        areas.cell(row, 3).number_format = "@"
    for row in range(2, tariffs.max_row + 1):
        for column in range(3, tariffs.max_column + 1):
            tariffs.cell(row, column).number_format = "@"

    for sheet in (areas, tariffs):
        sheet.column_dimensions["A"].hidden = True
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
    areas.column_dimensions["B"].width = 70
    areas.column_dimensions["C"].width = 28
    tariffs.column_dimensions["B"].width = 70
    for column in range(3, tariffs.max_column + 1):
        tariffs.column_dimensions[
            tariffs.cell(1, column).column_letter
        ].width = 20 if column % 2 else 28

    workbook.save(Path(path))


def parse_house_data_template(path: str | Path) -> HouseDataImportResult:
    """Parse the application house-data workbook."""

    workbook = load_workbook(Path(path), read_only=True, data_only=True)
    try:
        missing = [name for name in (AREA_SHEET, TARIFF_SHEET) if name not in workbook.sheetnames]
        if missing:
            raise HouseDataImportError(
                "В файле отсутствуют обязательные листы: " + ", ".join(missing)
            )

        areas_sheet = workbook[AREA_SHEET]
        tariffs_sheet = workbook[TARIFF_SHEET]
        _require_headers(
            areas_sheet,
            ["_house_id", "Адрес", "Площадь к начислению, м²"],
        )
        tariff_pairs = _tariff_column_pairs(tariffs_sheet)

        area_rows: list[ImportedAreaRow] = []
        tariff_rows: list[ImportedTariffRow] = []
        warnings: list[str] = []

        for row_number, values in enumerate(
            areas_sheet.iter_rows(min_row=2, values_only=True),
            start=2,
        ):
            house_id = _text(_at(values, 0))
            address = _text(_at(values, 1)) or ""
            raw_area = _at(values, 2)
            if raw_area is None or not str(raw_area).strip():
                continue
            if not house_id and not address:
                raise HouseDataImportError(
                    f"Лист «{AREA_SHEET}», строка {row_number}: не указан дом."
                )
            try:
                area = normalize_positive_decimal(raw_area)
            except ValueError as exc:
                raise HouseDataImportError(
                    f"Лист «{AREA_SHEET}», строка {row_number}: {exc}"
                ) from exc
            area_rows.append(ImportedAreaRow(house_id, address, area, row_number))

        seen_tariffs: set[tuple[str, str]] = set()
        for row_number, cells in enumerate(
            tariffs_sheet.iter_rows(min_row=2),
            start=2,
        ):
            house_id = _text(_cell_value(cells, 1))
            address = _text(_cell_value(cells, 2)) or ""

            for pair_number, (start_column, amount_column) in enumerate(
                tariff_pairs,
                start=1,
            ):
                start_cell = _cell(cells, start_column)
                amount_cell = _cell(cells, amount_column)
                raw_start = start_cell.value if start_cell is not None else None
                raw_amount = amount_cell.value if amount_cell is not None else None
                start_blank = raw_start is None or not str(raw_start).strip()
                amount_blank = raw_amount is None or not str(raw_amount).strip()

                if start_blank and amount_blank:
                    continue
                if start_blank or amount_blank:
                    raise HouseDataImportError(
                        f"Лист «{TARIFF_SHEET}», строка {row_number}, "
                        f"пара {pair_number}: заполните одновременно "
                        "«Действует с» и тариф."
                    )
                if not house_id and not address:
                    raise HouseDataImportError(
                        f"Лист «{TARIFF_SHEET}», строка {row_number}: не указан дом."
                    )

                try:
                    start = _parse_tariff_start_cell(start_cell)
                    amount = normalize_positive_decimal(raw_amount)
                except ValueError as exc:
                    raise HouseDataImportError(
                        f"Лист «{TARIFF_SHEET}», строка {row_number}, "
                        f"пара {pair_number}: {exc}"
                    ) from exc

                identity = house_id or _normalize_address(address)
                duplicate_key = (identity, start.value.isoformat())
                if duplicate_key in seen_tariffs:
                    raise HouseDataImportError(
                        f"Лист «{TARIFF_SHEET}», строка {row_number}, "
                        f"пара {pair_number}: для этого дома уже есть тариф "
                        "с такой датой начала."
                    )
                seen_tariffs.add(duplicate_key)
                tariff_rows.append(
                    ImportedTariffRow(house_id, address, start, amount, row_number)
                )

        if not area_rows and not tariff_rows:
            warnings.append("В файле нет заполненных площадей или тарифов.")

        return HouseDataImportResult(
            areas=tuple(area_rows),
            tariffs=tuple(tariff_rows),
            warnings=tuple(warnings),
        )
    finally:
        workbook.close()


def normalize_positive_decimal(value: object) -> str:
    text = str(value or "").replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
    try:
        decimal = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"некорректное число: {value!r}.") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError("значение должно быть конечным числом больше нуля.")
    result = format(decimal, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result.replace(".", ",")


def _tariff_column_pairs(sheet) -> list[tuple[int, int]]:  # noqa: ANN001
    headers = [str(cell.value or "").strip() for cell in sheet[1]]
    while headers and not headers[-1]:
        headers.pop()

    if headers[:2] != ["_house_id", "Адрес"] or len(headers) < 4:
        raise HouseDataImportError(
            f"Лист «{sheet.title}» имеет неизвестную структуру. "
            "Сформируйте новый шаблон в приложении."
        )

    tariff_headers = headers[2:]
    if len(tariff_headers) % 2:
        raise HouseDataImportError(
            f"Лист «{sheet.title}»: после адреса должны идти пары "
            "«Действует с» / «Тариф, руб./м² в месяц»."
        )

    pairs: list[tuple[int, int]] = []
    for offset in range(0, len(tariff_headers), 2):
        if tariff_headers[offset : offset + 2] != [
            "Действует с",
            "Тариф, руб./м² в месяц",
        ]:
            pair_number = offset // 2 + 1
            raise HouseDataImportError(
                f"Лист «{sheet.title}»: неверные заголовки в паре {pair_number}. "
                "Ожидаются «Действует с» и «Тариф, руб./м² в месяц»."
            )
        start_column = offset + 3
        pairs.append((start_column, start_column + 1))
    return pairs


def _parse_tariff_start_cell(cell) -> ParsedPeriodDate:  # noqa: ANN001
    if cell is None:
        raise ValueError("Дата не заполнена.")

    value = cell.value
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        number_format = str(getattr(cell, "number_format", "") or "").lower()
        number_format = number_format.replace("\\", "")
        month_only_format = (
            "m" in number_format
            and "y" in number_format
            and "d" not in number_format
        )
        if month_only_format:
            return ParsedPeriodDate(
                value=date(value.year, value.month, 1),
                precision="month",
            )
        return ParsedPeriodDate(value=value, precision="day")

    return parse_period_date(value, boundary="start")


def _cell(cells: tuple[object, ...], column: int):  # noqa: ANN001
    index = column - 1
    return cells[index] if 0 <= index < len(cells) else None


def _cell_value(cells: tuple[object, ...], column: int) -> object | None:
    cell = _cell(cells, column)
    return cell.value if cell is not None else None


def _style_header(sheet) -> None:  # noqa: ANN001
    for cell in sheet[1]:
        cell.font = Font(bold=True)


def _require_headers(sheet, expected: list[str]) -> None:  # noqa: ANN001
    actual = [str(cell.value or "").strip() for cell in sheet[1]]
    if actual[: len(expected)] != expected:
        raise HouseDataImportError(
            f"Лист «{sheet.title}» имеет неизвестную структуру. "
            "Сформируйте новый шаблон в приложении."
        )


def _at(values: tuple[object, ...], index: int) -> object | None:
    return values[index] if index < len(values) else None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_address(value: str) -> str:
    return " ".join(value.split()).casefold()
