"""Generate a GIS ЖКХ work-list XLSX while preserving the original package.

The writer patches only the workbook sheets that contain the work-list rows and
house/period options. All other XLSX parts are copied byte-for-byte from the
user's original GIS ЖКХ template.
"""

from __future__ import annotations

import os
import posixpath
import tempfile
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_X14_NS = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
_X14AC_NS = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
_XM_NS = "http://schemas.microsoft.com/office/excel/2006/main"

_NS = {"m": _MAIN_NS, "r": _REL_NS}

for prefix, namespace in (
    ("", _MAIN_NS),
    ("r", _REL_NS),
    ("mc", _MC_NS),
    ("x14", _X14_NS),
    ("x14ac", _X14AC_NS),
    ("xm", _XM_NS),
):
    ET.register_namespace(prefix, namespace)


class WorkListExportError(ValueError):
    """Raised when a local work list cannot be represented in the GIS template."""


@dataclass(frozen=True, slots=True)
class WorkListExportResult:
    output_path: Path
    exported_rows: int


def export_work_list_xlsx(
    *,
    source_template_path: str | Path,
    output_path: str | Path,
    house_guid: str,
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
    items: Iterable[object],
) -> WorkListExportResult:
    """Create a filled XLSX for subsequent upload to GIS ЖКХ."""

    source = Path(source_template_path)
    output = Path(output_path)

    if not source.exists():
        raise WorkListExportError("Исходный XLSX-шаблон не найден.")
    if not house_guid.strip():
        raise WorkListExportError("У дома отсутствует GUID объекта жилищного фонда.")
    if (int(start_year), int(start_month)) > (int(end_year), int(end_month)):
        raise WorkListExportError("Начало периода не может быть позже окончания.")

    prepared_items = _prepare_items(items)
    if not prepared_items:
        raise WorkListExportError(
            "В перечне нет заполненных строк для выгрузки. "
            "Заполните хотя бы одну работу/услугу."
        )

    with zipfile.ZipFile(source, "r") as src:
        names = set(src.namelist())
        sheet_paths = _sheet_paths(src)
        main_path = sheet_paths.get("Перечень работ и услуг")
        options_path = sheet_paths.get("ОпцииПеречня")
        lookup_path = sheet_paths.get("СпрРабУсл")

        if not main_path or main_path not in names:
            raise WorkListExportError("В шаблоне нет листа «Перечень работ и услуг».")
        if not options_path or options_path not in names:
            raise WorkListExportError("В шаблоне нет листа «ОпцииПеречня».")
        if not lookup_path or lookup_path not in names:
            raise WorkListExportError("В шаблоне нет листа «СпрРабУсл».")

        shared_strings = _shared_strings(src)
        labels_by_guid, available_labels = _read_lookup(
            src.read(lookup_path), shared_strings
        )

        rows = []
        for prepared in prepared_items:
            source_item = prepared[0]
            work_guid = _text(getattr(source_item, "work_guid", None))
            if work_guid and work_guid in labels_by_guid:
                label = labels_by_guid[work_guid]
            else:
                code = _text(getattr(source_item, "work_code", None))
                name = _text(getattr(source_item, "work_name", None))
                if not name:
                    raise WorkListExportError("У строки перечня отсутствует наименование работы.")
                label = f"{code}.{name}" if code else name
                if label not in available_labels:
                    raise WorkListExportError(
                        f"Работа «{label}» отсутствует в справочнике исходного шаблона."
                    )
            rows.append((label, *prepared[1:]))

        modified: dict[str, bytes] = {
            main_path: _patch_main_sheet(src.read(main_path), rows),
            options_path: _patch_options_sheet(
                src.read(options_path),
                house_guid=house_guid.strip(),
                start_year=int(start_year),
                start_month=int(start_month),
                end_year=int(end_year),
                end_month=int(end_month),
            ),
        }

        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=output.stem + "-",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as tmp_file:
            temp_path = Path(tmp_file.name)

        try:
            with zipfile.ZipFile(temp_path, "w") as dst:
                for info in src.infolist():
                    payload = modified.get(info.filename)
                    if payload is None:
                        payload = src.read(info.filename)
                    dst.writestr(info, payload)
            os.replace(temp_path, output)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    return WorkListExportResult(output_path=output, exported_rows=len(rows))


def _prepare_items(items: Iterable[object]) -> list[tuple[object, str, str, str, str]]:
    result: list[tuple[object, str, str, str, str]] = []

    for item in items:
        price = _text(getattr(item, "price", None))
        volume = _text(getattr(item, "volume", None))
        quantity = _text(getattr(item, "quantity", None))
        cost = _text(getattr(item, "cost", None))

        if not any((price, volume, quantity, cost)):
            continue

        if price or volume:
            if not (price and volume and quantity):
                raise WorkListExportError(
                    f"Строка «{getattr(item, 'work_name', '')}»: "
                    "для варианта «Цена + Объем + Кол-во» необходимо заполнить все три поля."
                )
            if cost:
                raise WorkListExportError(
                    f"Строка «{getattr(item, 'work_name', '')}»: "
                    "нельзя одновременно заполнять стоимость и цену/объем."
                )
        elif cost:
            if not quantity:
                raise WorkListExportError(
                    f"Строка «{getattr(item, 'work_name', '')}»: "
                    "при заполненной стоимости необходимо указать количество."
                )
        else:
            raise WorkListExportError(
                f"Строка «{getattr(item, 'work_name', '')}»: "
                "одного количества недостаточно. Заполните цену и объем либо стоимость."
            )

        result.append(
            (
                item,
                _number(price),
                _number(volume),
                _number(quantity),
                _number(cost),
            )
        )

    return result


def _sheet_paths(workbook: zipfile.ZipFile) -> dict[str, str]:
    workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
    rels_xml = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))

    relationships = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_xml.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
    }

    result: dict[str, str] = {}
    sheets = workbook_xml.find(f"{{{_MAIN_NS}}}sheets")
    if sheets is None:
        return result

    for sheet in sheets:
        relationship_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
        target = relationships.get(relationship_id or "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        result[sheet.attrib.get("name", "")] = path
    return result


def _shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    result: list[str] = []
    for item in root.findall(f"{{{_MAIN_NS}}}si"):
        text = "".join(
            node.text or "" for node in item.iter(f"{{{_MAIN_NS}}}t")
        )
        result.append(text)
    return result


def _read_lookup(
    xml_bytes: bytes,
    shared_strings: list[str],
) -> tuple[dict[str, str], set[str]]:
    root = ET.fromstring(xml_bytes)
    labels_by_guid: dict[str, str] = {}
    labels: set[str] = set()

    sheet_data = root.find(f"{{{_MAIN_NS}}}sheetData")
    if sheet_data is None:
        return labels_by_guid, labels

    for row in sheet_data.findall(f"{{{_MAIN_NS}}}row"):
        cells = {
            _column_name(cell.attrib.get("r", "")): _cell_value(cell, shared_strings)
            for cell in row.findall(f"{{{_MAIN_NS}}}c")
        }
        label = _text(cells.get("A"))
        guid = _text(cells.get("B"))
        if label:
            labels.add(label)
            if guid:
                labels_by_guid[guid] = label

    return labels_by_guid, labels


def _patch_main_sheet(
    xml_bytes: bytes,
    rows: list[tuple[str, str, str, str, str]],
) -> bytes:
    root = ET.fromstring(xml_bytes)
    sheet_data = root.find(f"{{{_MAIN_NS}}}sheetData")
    if sheet_data is None:
        raise WorkListExportError("Поврежден лист «Перечень работ и услуг».")

    for row in list(sheet_data):
        if int(row.attrib.get("r", "0")) > 1:
            sheet_data.remove(row)

    for row_number, values in enumerate(rows, start=2):
        row = ET.SubElement(
            sheet_data,
            f"{{{_MAIN_NS}}}row",
            {"r": str(row_number), "spans": "1:6"},
        )
        _inline_cell(row, f"A{row_number}", values[0])
        for column, value in zip(("B", "C", "D", "E"), values[1:]):
            if value:
                _numeric_cell(row, f"{column}{row_number}", value)

    dimension = root.find(f"{{{_MAIN_NS}}}dimension")
    if dimension is not None:
        last_row = max(1, len(rows) + 1)
        dimension.set("ref", f"A1:F{last_row}")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _patch_options_sheet(
    xml_bytes: bytes,
    *,
    house_guid: str,
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> bytes:
    root = ET.fromstring(xml_bytes)
    sheet_data = root.find(f"{{{_MAIN_NS}}}sheetData")
    if sheet_data is None:
        raise WorkListExportError("Поврежден лист «ОпцииПеречня».")

    values: dict[int, tuple[str, bool]] = {
        1: (house_guid, False),
        2: (str(start_year), True),
        3: (str(start_month), True),
        4: (str(end_year), True),
        5: (str(end_month), True),
    }

    for row_number, (value, numeric) in values.items():
        row = _get_or_create_row(sheet_data, row_number)
        cell = _get_or_create_cell(row, f"B{row_number}")
        _clear_cell(cell)
        if numeric:
            cell.set("t", "n")
            ET.SubElement(cell, f"{{{_MAIN_NS}}}v").text = value
        else:
            cell.set("t", "inlineStr")
            inline = ET.SubElement(cell, f"{{{_MAIN_NS}}}is")
            ET.SubElement(inline, f"{{{_MAIN_NS}}}t").text = value

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _get_or_create_row(sheet_data: ET.Element, row_number: int) -> ET.Element:
    for row in sheet_data.findall(f"{{{_MAIN_NS}}}row"):
        if int(row.attrib.get("r", "0")) == row_number:
            return row
    return ET.SubElement(sheet_data, f"{{{_MAIN_NS}}}row", {"r": str(row_number)})


def _get_or_create_cell(row: ET.Element, reference: str) -> ET.Element:
    for cell in row.findall(f"{{{_MAIN_NS}}}c"):
        if cell.attrib.get("r") == reference:
            return cell
    return ET.SubElement(row, f"{{{_MAIN_NS}}}c", {"r": reference})


def _inline_cell(row: ET.Element, reference: str, value: str) -> None:
    cell = ET.SubElement(
        row,
        f"{{{_MAIN_NS}}}c",
        {"r": reference, "t": "inlineStr"},
    )
    inline = ET.SubElement(cell, f"{{{_MAIN_NS}}}is")
    ET.SubElement(inline, f"{{{_MAIN_NS}}}t").text = value


def _numeric_cell(row: ET.Element, reference: str, value: str) -> None:
    cell = ET.SubElement(row, f"{{{_MAIN_NS}}}c", {"r": reference, "t": "n"})
    ET.SubElement(cell, f"{{{_MAIN_NS}}}v").text = value


def _clear_cell(cell: ET.Element) -> None:
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop("t", None)


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{_MAIN_NS}}}is")
        if inline is None:
            return None
        return "".join(node.text or "" for node in inline.iter(f"{{{_MAIN_NS}}}t"))

    value = cell.find(f"{{{_MAIN_NS}}}v")
    if value is None or value.text is None:
        return None
    if cell_type == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError):
            return None
    return value.text


def _column_name(reference: str) -> str:
    return "".join(character for character in reference if character.isalpha())


def _number(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as exc:
        raise WorkListExportError(f"«{value}» не является числом.") from exc
    return format(parsed, "f")


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
