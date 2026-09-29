"""Small edit dialogs for organizations and manual houses."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from gis_jkh_assistant.database.models import House, ManagementOrganization

from .widgets import SafeSpinBox


class OrganizationDialog(QDialog):
    def __init__(self, parent=None, organization: ManagementOrganization | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Управляющая организация")
        self.setMinimumWidth(500)

        self.name_edit = QLineEdit(organization.name if organization else "")
        self.ogrn_edit = QLineEdit(organization.ogrn if organization else "")
        self.kpp_edit = QLineEdit((organization.kpp or "") if organization else "")
        self.comment_edit = QPlainTextEdit((organization.comment or "") if organization else "")
        self.comment_edit.setMaximumHeight(90)

        form = QFormLayout()
        form.addRow("Наименование *", self.name_edit)
        form.addRow("ОГРН *", self.ogrn_edit)
        form.addRow("КПП", self.kpp_edit)
        form.addRow("Комментарий", self.comment_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        save_button.setText("Сохранить")
        save_button.setObjectName("PrimaryButton")
        cancel_button.setText("Отмена")
        save_button.clicked.connect(self._validate_and_accept)
        cancel_button.clicked.connect(self.reject)

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

    def _validate_and_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Не заполнено поле", "Укажите наименование УО.")
            self.name_edit.setFocus()
            return
        if not self.ogrn_edit.text().strip():
            QMessageBox.warning(self, "Не заполнено поле", "Укажите ОГРН УО.")
            self.ogrn_edit.setFocus()
            return
        super().accept()


class HouseDialog(QDialog):
    def __init__(self, parent=None, house: House | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Дом")
        self.setMinimumWidth(560)

        self.address_edit = QLineEdit(house.address if house else "")
        self.object_guid_edit = QLineEdit((house.house_object_guid or "") if house else "")
        self.gis_guid_edit = QLineEdit((house.gis_house_guid or "") if house else "")
        self.fias_guid_edit = QLineEdit((house.fias_house_guid or "") if house else "")
        self.unique_number_edit = QLineEdit((house.gis_unique_house_number or "") if house else "")
        self.cadastral_edit = QLineEdit((house.cadastral_number or "") if house else "")
        self.residential_spin = SafeSpinBox()
        self.residential_spin.setRange(0, 100000)
        self.residential_spin.setValue(house.residential_premises_count if house else 0)
        self.non_residential_spin = SafeSpinBox()
        self.non_residential_spin.setRange(0, 100000)
        self.non_residential_spin.setValue(house.non_residential_premises_count if house else 0)

        form = QFormLayout()
        form.addRow("Адрес *", self.address_edit)
        form.addRow("GUID объекта жилищного фонда", self.object_guid_edit)
        form.addRow("HOUSEGUID ГИС ЖКХ", self.gis_guid_edit)
        form.addRow("HOUSEGUID ФИАС", self.fias_guid_edit)
        form.addRow("Уникальный номер дома", self.unique_number_edit)
        form.addRow("Кадастровый номер", self.cadastral_edit)
        form.addRow("Жилых помещений", self.residential_spin)
        form.addRow("Нежилых помещений", self.non_residential_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        save_button.setText("Сохранить")
        save_button.setObjectName("PrimaryButton")
        cancel_button.setText("Отмена")
        save_button.clicked.connect(self._validate_and_accept)
        cancel_button.clicked.connect(self.reject)

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

    def _validate_and_accept(self) -> None:
        if not self.address_edit.text().strip():
            QMessageBox.warning(self, "Не заполнено поле", "Укажите адрес дома.")
            self.address_edit.setFocus()
            return
        super().accept()
