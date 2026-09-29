from openpyxl import Workbook

from gis_jkh_assistant.gis_files.housing_objects import parse_housing_objects_export


HEADERS = [
    "Адрес ОЖФ",
    "HOUSEGUID (Идентификационный код дома в ГИС ЖКХ)",
    "HOUSEGUID (Глобальный уникальный идентификатор дома по ФИАС)",
    "ОГРН управляющей организации",
    "КПП управляющей организации",
    "Краткое наименование управляющей организации",
    "Номер жилого помещения",
    "Номер нежилого помещения",
    "Номер комнаты",
    "Уникальный номер дома",
    "Уникальный номер помещения",
    "Уникальный номер комнаты",
    "Кадастровый номер ",
    "Глобальный уникальный идентификатор объекта жилищного фонда",
]


def test_parser_aggregates_house_and_deduplicates_room_parent(tmp_path) -> None:
    path = tmp_path / "objects.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Данные"
    ws.append(HEADERS)
    ws.append([])
    ws.append([
        "г Ставрополь, ул Ленина, д. 289",
        "gis-guid",
        "fias-guid",
        "1112651027092",
        "263401001",
        'ООО "УК"',
        None,
        None,
        None,
        "EPy00010",
        None,
        None,
        "26:12:030108:156",
        "house-object-guid",
    ])
    ws.append([
        'г Ставрополь, ул Ленина, д. 289, помещение "1"',
        "gis-guid",
        "fias-guid",
        "1112651027092",
        "263401001",
        'ООО "УК"',
        "1",
        None,
        None,
        "EPy00010",
        "premise-1",
        None,
        None,
        "premise-guid-1",
    ])
    ws.append([
        'г Ставрополь, ул Ленина, д. 289, помещение "1", комната "1"',
        "gis-guid",
        "fias-guid",
        "1112651027092",
        "263401001",
        'ООО "УК"',
        "1",
        None,
        "1",
        "EPy00010",
        "premise-1",
        "room-1",
        None,
        "room-guid-1",
    ])
    ws.append([
        'г Ставрополь, ул Ленина, д. 289, нежилое помещение "1"',
        "gis-guid",
        "fias-guid",
        "1112651027092",
        "263401001",
        'ООО "УК"',
        None,
        "1",
        None,
        "EPy00010",
        "nonres-1",
        None,
        None,
        "nonres-guid-1",
    ])

    conf = wb.create_sheet("conf")
    conf.append(["TYPE", "HCS-OBJ"])
    conf.append(["VERSION", "99.1.0"])
    wb.save(path)

    result = parse_housing_objects_export(path)

    assert result.file_type == "HCS-OBJ"
    assert result.version == "99.1.0"
    assert len(result.organizations) == 1
    assert len(result.houses) == 1
    house = result.houses[0]
    assert house.house_object_guid == "house-object-guid"
    assert house.residential_premises_count == 1
    assert house.non_residential_premises_count == 1
