from gis_jkh_assistant.acceptance_act_pdf import (
    AcceptanceActWorkRow,
    acceptance_act_headers,
    build_acceptance_act_pdf,
)


def test_acceptance_act_omits_unit_column_when_units_are_unknown() -> None:
    rows = [
        AcceptanceActWorkRow(
            name="Услуга по управлению",
            periodicity=1,
            actual_cost="26151.38",
        ),
        AcceptanceActWorkRow(
            name="Уборка лестничных клеток",
            periodicity=4,
            actual_cost="11886,99",
        ),
    ]

    assert acceptance_act_headers(rows) == (
        "№ п/п",
        "Наименование работы (услуги)",
        "Периодичность",
        "Стоимость факт., руб.",
    )


def test_acceptance_act_includes_unit_column_when_available() -> None:
    rows = [
        AcceptanceActWorkRow(
            name="Услуга",
            unit_name="мес.",
            periodicity=1,
            actual_cost="100",
        ),
    ]

    assert "Ед. измерения" in acceptance_act_headers(rows)


def test_acceptance_act_pdf_is_generated_with_fact_table() -> None:
    pdf = build_acceptance_act_pdf(
        organization_name="ООО УК Тест",
        house_address="г Ставрополь, ул Тестовая, д. 1",
        act_number="1",
        act_date="31.01.2026",
        period_from="01.01.2026",
        period_to="31.01.2026",
        work_rows=[
            AcceptanceActWorkRow(
                name="Услуга по управлению",
                periodicity=1,
                actual_cost="26151.38",
            ),
            AcceptanceActWorkRow(
                name="Техническое обслуживание инженерных сетей",
                periodicity=None,
                actual_cost=None,
            ),
        ],
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
