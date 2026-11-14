"""Use the same finite dates in the house editor and a single GIS project."""
from datetime import date

import test_ui_workflow as workflows
from gis_jkh_assistant.app.dialogs import HouseTariffDialog
from gis_jkh_assistant.app import management_fees_page

desktop, qt_app = workflows.desktop, workflows.qt_app


def test_editor_suggests_annual_end_and_keeps_custom_date(qt_app):
    dialog = HouseTariffDialog(default_start_year=2026, default_start_month=4)
    try:
        assert dialog.end_date_edit.text() == "31.03.2027"
        assert dialog.auto_renew_checkbox.isChecked()
        dialog.start_date_edit.setText("08.2026")
        assert dialog.end_date_edit.text() == "31.07.2027"
        dialog.end_date_edit.setText("30.06.2027")
        dialog.start_date_edit.setText("09.2026")
        assert dialog.end_date_edit.text() == "30.06.2027"
        dialog.auto_renew_checkbox.setChecked(False)
        assert dialog.values()["auto_renew"] is False
    finally:
        dialog.close()


def test_single_project_copies_anniversary_end_and_saves_continuation(desktop, monkeypatch):
    repo = desktop.repository
    page = desktop.window.management_fees_page
    house = desktop.house
    repo.create_house_tariff(house_id=house.id, amount="24.41", start_year=2024, start_month=4)
    page.refresh_organizations()
    page.house_combo.setCurrentIndex(page.house_combo.findData(house.id))
    labels_seen = []

    def choose(_parent, _title, _label, labels, *_args):
        labels_seen.extend(labels)
        return next(label for label in labels if label.startswith("04.2026")), True

    monkeypatch.setattr(management_fees_page.QInputDialog, "getItem", choose)
    page._new()
    records = page.store.list(house.id)
    assert len(records) == 1
    assert records[0]["document"]["start"] == "2026-04-01"
    assert records[0]["document"]["end"] == "2027-03-31"
    assert any("03.2027" in label for label in labels_seen)
    local = repo.list_house_tariffs(house.id)
    assert any(t.effective_from_date == date(2026, 4, 1) and t.effective_to_date == date(2027, 3, 31) for t in local)
