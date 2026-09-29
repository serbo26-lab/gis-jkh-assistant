"""Small edit dialogs for organizations and manual houses."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from gis_jkh_assistant.database.models import House, ManagementOrganization


class OrganizationDialog(QDialog):
    def __init__(self, parent=None, organization: ManagementOrganization | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Управляющая организация")
        self.setMinimumWidth(520)

        self.name_edit = QLineEdit(organization.name if organization else "")
        self.ogrn_edit = QLineEdit(organization.ogrn if organization else "")
        self.kpp_edit = QLineEdit((organization.kpp or "") if organization else "")
        self.comment_edit = QPlainTextEdit((organization.comment or "") if organization else "")
        self.comment_edit.setMaximumHeight(90)

        form = QFormLayout()
        form.addRow("Наименование", self.name_edit)
        form.addRow("ОГРН", self.ogrn_edit)
        form.addRow("КПП", self.kpp_edit)
        form.addRow("Комментарий", self.comment_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str | None]:
        return {
            "name": self.name_edit.text().strip(),
            "ogrn": self.ogrn_edit.text().strip(),
            "kpp": self.kpp_edit.text().strip() or None,
            "comment": self.comment_edit.toPlainText().strip() or None,
        }

    def accept(self) -> None:
        if not self.name_edit.text().strip() or not self.ogrn_edit.text().strip():
            return
        super().accept()


class HouseDialog(QDialog):
    def __init__(self, parent=None, house: House | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Дом")
        self.setMinimumWidth(620)

        self.address_edit = QLineEdit(house.address if house else "")
        self.object_guid_edit = QLineEdit((house.house_object_guid or "") if house else "")
        self.gis_guid_edit = QLineEdit((house.gis_house_guid or "") if house else "")
        self.fias_guid_edit = QLineEdit((house.fias_house_guid or "") if house else "")
        self.unique_number_edit = QLineEdit((house.gis_unique_house_number or "") if house else "")
        self.cadastral_edit = QLineEdit((house.cadastral_number or "") if house else "")
        self.residential_spin = QSpinBox()
        self.residential_spin.setRange(0, 100000)
        self.residential_spin.setValue(house.residential_premises_count if house else 0)
        self.non_residential_spin = QSpinBox()
        self.non_residential_spin.setRange(0, 100000)
        self.non_residential_spin.setValue(house.non_residential_premises_count if house else 0)

        form = QFormLayout()
        form.addRow("Адрес", self.address_edit)
        form.addRow("GUID объекта жилищного фонда", self.object_guid_edit)
        form.addRow("HOUSEGUID ГИС ЖКХ", self.gis_guid_edit)
        form.addRow("HOUSEGUID ФИАС", self.fias_guid_edit)
        form.addRow("Уникальный номер дома", self.unique_number_edit)
        form.addRow("Кадастровый номер", self.cadastral_edit)
        form.addRow("Жилых помещений", self.residential_spin)
        form.addRow("Нежилых помещений", self.non_residential_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        return {
            "address": self.address_edit.text().strip(),
            "house_object_guid": self.object_guid_edit.text().strip() or None,
            "gis_house_guid": self.gis_guid_edit.text().strip() or None,
            "fias_house_guid": self.fias_guid_edit.text().strip() or None,
            "gis_unique_house_number": self.unique_number_edit.text().strip() or None,
            "cadastral_number": self.cadastral_edit.text().strip() or None,
            "residential_premises_count": self.residential_spin.value(),
            "non_residential_premises_count": self.non_residential_spin.value(),
        }

    def accept(self) -> None:
        if not self.address_edit.text().strip():
            return
        super().accept()
