"""Resolve repeated tariff periods once without merging conflicting house identities."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

import test_ui_workflow as workflows
from gis_jkh_assistant.app.house_import_preview import HouseMatchDialog
from gis_jkh_assistant.app.widgets import SafeComboBox

qt_app = workflows.qt_app


def _row(**fields):
    values = dict(house_id="old-local", address="г. Тестовый, ул. Учебная, д. 1",
                  house_object_guid="object-a", fias_house_guid="fias-a",
                  gis_unique_house_number="0001", row_number=2)
    values.update(fields)
    return SimpleNamespace(**values)


def _houses():
    return [SimpleNamespace(id="local-a", address="г. Тестовый, ул. Учебная, д. 1"),
            SimpleNamespace(id="local-b", address="г. Тестовый, ул. Учебная, д. 2")]


def test_one_choice_maps_one_hundred_tariff_periods(qt_app):
    rows = [_row() for _ in range(100)]
    # The same house can also appear on other sheets and source row numbers.
    rows.append(_row(row_number=8, address="  Г. ТЕСТОВЫЙ,  ул. Учебная, д. 1 "))
    dialog = HouseMatchDialog([(row, "Идентификаторы требуют проверки") for row in rows], _houses())
    try:
        assert dialog.table.rowCount() == 1
        assert len(dialog.choices) == 101
        assert len(dialog.findChildren(SafeComboBox)) == 1
        combo = dialog.table.cellWidget(0, 2)
        assert isinstance(combo, SafeComboBox)
        assert not dialog.apply.isEnabled()
        assert "Сопоставлений: 1; записей файла: 101" in dialog.summary.text()
        assert "Строки файла: 2, 8" in dialog.table.item(0, 0).toolTip()
        assert dialog.table.item(0, 1).toolTip() == "Идентификаторы требуют проверки"
        combo.setCurrentIndex(combo.findData("local-a"))
        assert dialog.apply.isEnabled()
        assert dialog.mapping() == {id(row): "local-a" for row in rows}
        dialog.apply.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("field,value", [
    ("house_id", "other-local"),
    ("house_object_guid", "other-object"),
    ("fias_house_guid", "other-fias"),
    ("gis_unique_house_number", "0002"),
    ("house_object_guid", None),
    ("address", "г. Тестовый, ул. Учебная, д. 2"),
])
def test_different_identity_evidence_is_not_grouped(qt_app, field, value):
    rows = [_row(), _row(**{field: value})]
    dialog = HouseMatchDialog([(row, "Проверьте дом") for row in rows], _houses())
    try:
        assert dialog.table.rowCount() == 2
        first, second = (dialog.table.cellWidget(index, 2) for index in range(2))
        first.setCurrentIndex(first.findData("local-a"))
        assert not dialog.apply.isEnabled()
        second.setCurrentIndex(second.findData("local-b"))
        assert dialog.apply.isEnabled()
        assert dialog.mapping() == {id(rows[0]): "local-a", id(rows[1]): "local-b"}
        second.setCurrentIndex(0)
        assert not dialog.apply.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_accept_requires_all_groups_and_cancel_keeps_dialog_rejected(qt_app):
    rows = [_row(), _row(house_object_guid="other-object")]
    dialog = HouseMatchDialog([(row, "Проверьте дом") for row in rows], _houses())
    try:
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Rejected
        first = dialog.table.cellWidget(0, 2)
        first.setCurrentIndex(first.findData("local-a"))
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Rejected
        second = dialog.table.cellWidget(1, 2)
        second.setCurrentIndex(second.findData("local-b"))
        assert dialog.apply.isEnabled()
        buttons = dialog.findChild(QDialogButtonBox)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()
        dialog.deleteLater()
