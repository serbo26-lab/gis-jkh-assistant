"""Portable XLSX template for house charge areas and tariff history."""

from __future__ import annotations

from dataclasses import dataclass
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
        "Лист «Тарифы»: одна строка означает одну точку изменения тарифа.",
        "Дата «08.2025» означает начало с 01.08.2025.",
        "Дата «10.08.2025» сохраняется как точная дата 10.08.2025.",
        "Конечную дату тарифа вводить не нужно: она определяется следующим изменением.",
        "Столбец _house_id служебный и скрыт. Адрес можно использовать для проверки.",
    ]
    for line in instructions:
        instruction.append([line])
    instruction.column_dimensions["A"].width = 105

    areas = workbook.create_sheet(AREA_SHEET)
    areas.append(["_house_id", "Адрес", "Площадь к начислению, м²"])
    _style_header(areas)

    tariffs = workbook.create_sheet(TARIFF_SHEET)
    tariffs.append(["_house_id", "Адрес", "Действует с", "Тариф, руб./м² в месяц"])
    _style_header(tariffs)

    sorted_houses = sorted(houses, key=lambda item: item.address.casefold())
    for house in sorted_houses:
        areas.append([house.id, house.address, house.charge_area or ""])
        existing = list(tariffs_by_house.get(house.id, ()))
        if existing:
            for tariff in existing:
                tariffs.append(
                    [house.id, house.address, tariff.effective_from_label, tariff.amount]
                )
        else:
            tariffs.append([house.id, house.address, "", ""])

    for row in range(2, areas.max_row + 1):
        areas.cell(row, 3).number_format = "@"
    for row in range(2, tariffs.max_row + 1):
        tariffs.cell(row, 3).number_format = "@"
        tariffs.cell(row, 4).number_format = "@"

    for sheet in (areas, tariffs):
        sheet.column_dimensions["A"].hidden = True
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
    areas.column_dimensions["B"].width = 70
    areas.column_dimensions["C"].width = 28
    tariffs.column_dimensions["B"].width = 70
    tariffs.column_dimensions["C"].width = 20
    tariffs.column_dimensions["D"].width = 28

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
        _require_headers(
            tariffs_sheet,
            ["_house_id", "Адрес", "Действует с", "Тариф, руб./м² в месяц"],
        )

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
        for row_number, values in enumerate(
            tariffs_sheet.iter_rows(min_row=2, values_only=True),
            start=2,
        ):
            house_id = _text(_at(values, 0))
            address = _text(_at(values, 1)) or ""
            raw_start = _at(values, 2)
            raw_amount = _at(values, 3)
            start_blank = raw_start is None or not str(raw_start).strip()
            amount_blank = raw_amount is None or not str(raw_amount).strip()
            if start_blank and amount_blank:
                continue
            if start_blank or amount_blank:
                raise HouseDataImportError(
                    f"Лист «{TARIFF_SHEET}», строка {row_number}: "
                    "заполните одновременно «Действует с» и тариф."
                )
            if not house_id and not address:
                raise HouseDataImportError(
                    f"Лист «{TARIFF_SHEET}», строка {row_number}: не указан дом."
                )
            try:
                start = parse_period_date(raw_start, boundary="start")
                amount = normalize_positive_decimal(raw_amount)
            except ValueError as exc:
                raise HouseDataImportError(
                    f"Лист «{TARIFF_SHEET}», строка {row_number}: {exc}"
                ) from exc

            identity = house_id or _normalize_address(address)
            duplicate_key = (identity, start.value.isoformat())
            if duplicate_key in seen_tariffs:
                raise HouseDataImportError(
                    f"Лист «{TARIFF_SHEET}», строка {row_number}: "
                    "для этого дома уже есть строка с такой датой начала."
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
    if decimal <= 0:
        raise ValueError("значение должно быть больше нуля.")
    result = format(decimal, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result.replace(".", ",")


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
