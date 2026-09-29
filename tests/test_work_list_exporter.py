from dataclasses import dataclass

import pytest
from openpyxl import Workbook, load_workbook

from gis_jkh_assistant.gis_files.work_lists import (
    WorkListExportError,
    export_work_list_xlsx,
)


@dataclass
class Item:
    work_code: str
    work_name: str
    work_guid: str
    price: str | None = None
    volume: str | None = None
    quantity: str | None = None
    cost: str | None = None


def _source_template(path) -> None:
    wb = Workbook()
    main = wb.active
    main.title = "Перечень работ и услуг"
    main.append(
        [
            "Работа (услуга)",
            "Цена, руб.",
            "Объем",
            "Кол-во",
            "Итого-стоимость, руб.",
            "Статус обработки",
        ]
    )

    options = wb.create_sheet("ОпцииПеречня")
    options.append(["houseGuid", "old-house-guid", "Ссылка на дом"])
    options.append(["yearFrom", 2020, 'Период "с"'])
    options.append(["monthFrom", 1, None])
    options.append(["yearFrom", 2020, 'Период "по"'])
    options.append(["monthFrom", 12, None])

    lookup = wb.create_sheet("СпрРабУсл")
    lookup.append(["12.Услуга по управлению", "guid-12"])
    lookup.append(["13.Техническое обслуживание", "guid-13"])

    conf = wb.create_sheet("conf")
    conf.append(["TYPE", "PFRPERv2"])
    conf.append(["VERSION", "11.5.0.2"])
    wb.save(path)


def test_export_patches_house_period_and_only_filled_rows(tmp_path) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "result.xlsx"
    _source_template(source)

    result = export_work_list_xlsx(
        source_template_path=source,
        output_path=output,
        house_guid="new-house-guid",
        start_year=2026,
        start_month=5,
        end_year=2026,
        end_month=12,
        items=[
            Item(
                "12",
                "Услуга по управлению",
                "guid-12",
                quantity="1",
                cost="1250,50",
            ),
            Item(
                "13",
                "Техническое обслуживание",
                "guid-13",
                price="10",
                volume="2",
                quantity="3",
            ),
            Item("13", "Техническое обслуживание", "guid-13"),
        ],
    )

    assert result.exported_rows == 2

    wb = load_workbook(output, data_only=False)
    options = wb["ОпцииПеречня"]
    assert options["B1"].value == "new-house-guid"
    assert options["B2"].value == 2026
    assert options["B3"].value == 5
    assert options["B4"].value == 2026
    assert options["B5"].value == 12

    main = wb["Перечень работ и услуг"]
    assert main["A2"].value == "12.Услуга по управлению"
    assert main["D2"].value == 1
    assert main["E2"].value == 1250.50
    assert main["B2"].value is None
    assert main["C2"].value is None

    assert main["A3"].value == "13.Техническое обслуживание"
    assert main["B3"].value == 10
    assert main["C3"].value == 2
    assert main["D3"].value == 3
    assert main["E3"].value is None
    wb.close()


def test_export_rejects_quantity_without_a_complete_mode(tmp_path) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "result.xlsx"
    _source_template(source)

    with pytest.raises(WorkListExportError, match="одного количества недостаточно"):
        export_work_list_xlsx(
            source_template_path=source,
            output_path=output,
            house_guid="house-guid",
            start_year=2026,
            start_month=1,
            end_year=2026,
            end_month=12,
            items=[
                Item(
                    "12",
                    "Услуга по управлению",
                    "guid-12",
                    quantity="1",
                )
            ],
        )
