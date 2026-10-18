"""Planning outcomes belong to the house and year that produced them."""

from PySide6.QtWidgets import QMessageBox

import test_ui_workflow as workflows
from gis_jkh_assistant.app import planning_page


desktop = workflows.desktop
qt_app = workflows.qt_app


def test_loaded_plan_outcome_clears_when_the_user_selects_another_house(desktop, monkeypatch):
    repository = desktop.repository
    other_house = repository.create_house(
        organization_id=desktop.organization.id, address="Другой тестовый дом",
    )
    other_list = repository.create_work_list(house_id=other_house.id, base_year=2026)
    repository.update_work_list_gis_state(
        other_list.id, gis_work_list_guid="other-placed-list", gis_status="APPROVED",
    )
    page = workflows._select_page(desktop, 2)
    page.refresh_organizations()
    workers = []

    def create_worker(*args, **kwargs):
        worker = workflows.ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(planning_page, "GisIntegrationWorker", create_worker)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)

    page.load_gis_button.click()
    worker = workers[0]
    worker.progressed.emit({"processed": 1, "total": 12})
    page._refresh_integration_status()
    assert not page.operation_status.isHidden(), "the running operation stays visible"
    assert not page.house_combo.isEnabled()
    assert "1 из 12" in page.operation_status.label.text()
    work = repository.list_work_list_items(desktop.work_list.id)[0]
    worker.succeeded.emit({
        "found": True, "workPlanGuid": "loaded-test-plan", "gisStatus": "SAVED",
        "items": [{
            "workGuid": work.work_guid, "workName": work.work_name,
            "monthlyWorks": [{"month": month, "count": 1} for month in range(1, 13)],
        }],
    })
    worker.finish()
    workflows._settle(desktop.app)
    original_plan = page._current_plan_id
    assert page.operation_status.property("tone") == "success"
    assert "План загружен: работ 1, месячных значений 12" in page.operation_status.label.text()
    page._refresh_integration_status()
    assert not page.operation_status.isHidden(), "refreshing the same plan retains its result"
    assert page.house_combo.isEnabled()

    page.house_combo.setCurrentIndex(page.house_combo.findData(other_house.id))
    workflows._settle(desktop.app)
    assert page._current_work_list_id == other_list.id
    assert page._current_plan_id != original_plan
    assert page.operation_status.isHidden(), "another house must not show the previous import's success"
    assert repository.get_work_plan(original_plan).gis_work_plan_guid == "loaded-test-plan"
