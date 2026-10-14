"""Partial customer data is rendered honestly without weakening executor checks."""
import pytest
from PySide6.QtPdf import QPdfDocument

import test_ui_workflow as workflows
from test_standard_act_requisites import source as source_fixture
from gis_jkh_assistant.acceptance_act_details import (
    CUSTOMER_NAME_PLACEHOLDER, CUSTOMER_PREMISES_PLACEHOLDER, resolve_act_details,
)
from gis_jkh_assistant.acceptance_act_pdf import AcceptanceActWorkRow, build_acceptance_act_pdf
from gis_jkh_assistant.app.act_requisites_dialog import ActPersonDialog
from gis_jkh_assistant.app.quality_bulk_dialogs import GenerateAcceptanceActDialog
from gis_jkh_assistant.database.act_requisites_store import ActRequisitesStore
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.gis_files.house_data import write_house_data_template, parse_house_data_template
from gis_jkh_assistant.gis_files.house_extra_data import prepare_person_change
from gis_jkh_assistant.quality_automation import generate_acceptance_act_for_period

source = source_fixture
qt_app = workflows.qt_app


def _replace_customer(repo, work_list, **fields):
    repo.connection.execute("DELETE FROM act_people WHERE house_id=?", (work_list.house_id,))
    repo.connection.commit()
    if fields:
        return ActRequisitesStore(repo).save_person(fields, house_id=work_list.house_id)
    return None


@pytest.mark.parametrize("fields", [
    {},
    {"full_name": "Иванова Мария Ивановна", "premises": "1"},
    {"full_name": "Иванова Мария Ивановна"},
    {"full_name": "Иванова Мария Ивановна", "premises": "1", "basis_kind": "Решение ОСС"},
    {"full_name": "Иванова Мария Ивановна", "basis_kind": "Решение ОСС", "basis_number": "17"},
    {"full_name": "Иванова Мария Ивановна", "basis_kind": "Решение ОСС", "basis_date": "2020-01-01"},
    {"full_name": "Иванова Мария Ивановна", "premises": "1", "basis_kind": "Решение ОСС",
     "basis_number": "17", "basis_date": "2020-01-01"},
    {"full_name": "Иванова Мария Ивановна", "basis_kind": "Иное", "basis_text": "письменного поручения собственников"},
])
def test_pdf_omits_only_unknown_customer_fragments(source, qt_app, tmp_path, fields):
    repo, work_list = source
    _replace_customer(repo, work_list, **fields)
    details = resolve_act_details(repo, work_list.house_id, "31.01.2026")
    assert not details["issues"]
    assert details["customer_data_missing"] is (not bool(fields))
    content = build_acceptance_act_pdf(organization_name="Тестовая УО", house_address="Тестовый дом",
        act_number="1", act_date="31.01.2026", period_from="01.01.2026", period_to="31.01.2026",
        details=details, work_rows=[AcceptanceActWorkRow(name="Работа", actual_cost="100")])
    file = tmp_path / "customer.pdf"
    file.write_bytes(content)
    document = QPdfDocument()
    document.load(str(file))
    text = " ".join(document.getAllText(0).text().split())
    document.close()
    customer_preamble = text.split("с одной стороны, и", 1)[0]
    assert "в лице уполномоченного представителя" in customer_preamble
    assert "ПРОЕКТ" not in text
    assert "в лице директора Иванова Ивана Ивановича, действующего на основании Устава" in text
    if not fields:
        assert text.count(CUSTOMER_NAME_PLACEHOLDER) == 2
        assert f"помещения № {CUSTOMER_PREMISES_PLACEHOLDER}" in customer_preamble
        assert "Заказчик — " + CUSTOMER_NAME_PLACEHOLDER in text
    else:
        assert CUSTOMER_NAME_PLACEHOLDER not in text
        assert "Ивановой Марии Ивановны" in customer_preamble
        assert "Заказчик — Иванова Мария Ивановна" in text
        assert ("собственником помещения" in customer_preamble) == bool(fields.get("premises"))
        assert CUSTOMER_PREMISES_PLACEHOLDER not in text
    assert ("действующего на основании" in customer_preamble) == bool(details["customer"].get("basis"))
    assert ("протокол № 17 от 01.01.2020" in customer_preamble) == bool(fields.get("basis_number") and fields.get("basis_date"))


def test_missing_customer_does_not_mask_ambiguous_or_invalid_explicit_selection(source):
    repo, work_list = source
    store = ActRequisitesStore(repo)
    original = store.list_people(house_id=work_list.house_id)[0]
    store.save_person(dict(original, full_name="Сидорова Анна Ивановна"), house_id=work_list.house_id)
    assert any("Выберите одного" in issue for issue in resolve_act_details(repo, work_list.house_id, "31.01.2026")["issues"])
    assert not resolve_act_details(repo, work_list.house_id, "31.01.2026", customer_id=original["id"])["issues"]
    assert resolve_act_details(repo, work_list.house_id, "31.01.2026", customer_id="missing-person")["issues"]


def test_undated_and_dated_records_still_require_a_choice_and_respect_known_end_dates(source):
    repo, work_list = source
    store = ActRequisitesStore(repo)
    old = store.list_people(house_id=work_list.house_id)[0]
    store.save_person(dict(old, valid_to="2026-01-31"), house_id=work_list.house_id, person_id=old["id"])
    new = store.save_person({"full_name": "Иванова Мария Ивановна"}, house_id=work_list.house_id)
    assert resolve_act_details(repo, work_list.house_id, "31.01.2026")["issues"]
    february = resolve_act_details(repo, work_list.house_id, "28.02.2026")
    assert not february["issues"]
    assert february["customer"]["id"] == new
    assert february["customer_notices"]


def test_executor_requirements_are_not_relaxed(source):
    repo, work_list = source
    store = ActRequisitesStore(repo)
    organization_id = repo.get_house(work_list.house_id).organization_id
    for fields, message in [({"full_name": "Иванов Иван Иванович"}, "дату начала"),
                            ({"full_name": "Иванов Иван Иванович", "valid_from": "2020-01-01"}, "основание")]:
        with pytest.raises(ValueError, match=message):
            store.save_person(fields, organization_id=organization_id)
    repo.connection.execute("DELETE FROM act_people WHERE organization_id=?", (organization_id,))
    assert any("подписант УО" in issue for issue in resolve_act_details(repo, work_list.house_id, "31.01.2026")["issues"])


def test_house_form_allows_fio_only_and_generation_explains_placeholder(source, qt_app):
    repo, work_list = source
    dialog = ActPersonDialog(organization=False)
    dialog.edits["full_name"].setText("Иванова Мария Ивановна")
    values = dialog.values()
    assert values["basis_kind"] == "" and values["valid_from"] == ""
    assert values["role"] == "Уполномоченный собственник"
    assert dialog.gender.itemText(dialog.gender.findData("female")) == "Женский"
    _replace_customer(repo, work_list)
    store = QualityAssessmentStore(repo)
    period = store.get_or_create_period(work_list_id=work_list.id, year=2026, month=1)
    generate = GenerateAcceptanceActDialog(items=store.list_items(period.id), year=2026, month=1,
                                          repository=repo, house_id=work_list.house_id)
    assert "подстановки" in generate.customer_combo.currentText()
    assert "подстановки" in generate.party_status.text()
    assert not generate._details()["issues"]
    dialog.close()
    dialog.deleteLater()
    generate.close()
    generate.deleteLater()


def test_partial_customer_excel_roundtrip_does_not_duplicate_undated_record(source, tmp_path):
    repo, work_list = source
    person_id = _replace_customer(repo, work_list, full_name="Иванова Мария Ивановна")
    house = repo.get_house(work_list.house_id)
    file = tmp_path / "customer.xlsx"
    write_house_data_template(file, houses=[house], tariffs_by_house={}, repository=repo)
    row = next(p for p in parse_house_data_template(file).people if p.house_id == house.id)
    row.person_id = None  # Simulate a file without a local UUID.
    matched, data, previous = prepare_person_change(repo, row, house_id=house.id)
    assert matched == person_id
    assert data["basis_kind"] == "" and data["valid_from"] == ""
    assert previous["full_name"] == data["full_name"]
    ActRequisitesStore(repo).save_person(data, house_id=house.id, person_id=matched)
    assert len(ActRequisitesStore(repo).list_people(house_id=house.id)) == 1


@pytest.mark.parametrize("missing", [False, True])
def test_auto_generation_preserves_partial_customer_snapshot_when_live_data_changes(source, missing):
    repo, work_list = source
    _replace_customer(repo, work_list, **({} if missing else {"full_name": "Иванова Мария Ивановна"}))
    store = QualityAssessmentStore(repo)
    period = store.get_or_create_period(work_list_id=work_list.id, year=2026, month=1)
    item = store.list_items(period.id)[0]
    store.update_item(item.id, included=True, cost=None, days_count=None, cost_reduction=None)
    fixation = repo.get_or_create_work_fixation_period(work_list_id=work_list.id, year=2026, month=1)
    repo.update_work_fixation_item(repo.list_work_fixation_items(fixation.id)[0].id,
        fact_price="100", fact_amount="1", fact_count=1, fact_total_cost="100")
    generate_acceptance_act_for_period(store, period_id=period.id, organization_name="УО", house_address="Дом")
    act = store.list_period_acts(period.id)[0]
    requisites = ActRequisitesStore(repo)
    snapshot = requisites.snapshot(act.id)
    assert snapshot["customer_data_missing"] is missing
    assert not snapshot["customer"].get("basis")
    before = act.content
    _replace_customer(repo, work_list, full_name="Другой Представитель")
    assert requisites.snapshot(act.id) == snapshot
    assert store.get_act(act.id).content == before
