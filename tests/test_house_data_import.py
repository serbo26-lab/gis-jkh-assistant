from gis_jkh_assistant.period_dates import parse_period_date


def test_period_date_precision() -> None:
    month = parse_period_date("08.2025", boundary="start")
    exact = parse_period_date("10.08.2025", boundary="start")
    assert month.value.isoformat() == "2025-08-01"
    assert month.precision == "month"
    assert exact.value.isoformat() == "2025-08-10"
    assert exact.precision == "day"
