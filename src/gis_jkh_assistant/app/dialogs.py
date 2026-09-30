"""Small edit dialogs for organizations and manual houses."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtWidgets import (
    QDialog,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from gis_jkh_assistant.database.models import House, HouseTariff, ManagementOrganization

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
        self.charge_area_edit = QLineEdit((house.charge_area or "") if house else "")
        self.charge_area_edit.setPlaceholderText("Например: 395,6")
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
        form.addRow("Площадь к начислению, м²", self.charge_area_edit)
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
            "charge_area": self.charge_area_edit.text().strip() or None,
            "residential_premises_count": self.residential_spin.value(),
            "non_residential_premises_count": self.non_residential_spin.value(),
        }

    def _validate_and_accept(self) -> None:
        if not self.address_edit.text().strip():
            QMessageBox.warning(self, "Не заполнено поле", "Укажите адрес дома.")
            self.address_edit.setFocus()
            return
        super().accept()



class HouseTariffDialog(QDialog):
    MONTHS = (
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
    )

    def __init__(
        self,
        parent=None,
        tariff: HouseTariff | None = None,
        *,
        default_start_year: int | None = None,
        default_start_month: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Тариф дома")
        self.setMinimumWidth(560)

        current_year = datetime.now().year

        self.amount_edit = QLineEdit(tariff.amount if tariff else "")
        self.amount_edit.setPlaceholderText("Например: 25,04")

        self.start_month_combo = QComboBox()
        for number, name in enumerate(self.MONTHS, start=1):
            self.start_month_combo.addItem(f"{number:02d} — {name}", number)

        self.start_year_spin = SafeSpinBox()
        self.start_year_spin.setRange(2010, 2100)

        if tariff:
            start_year = tariff.start_year
            start_month = tariff.start_month
        else:
            start_year = default_start_year or current_year
            start_month = default_start_month or 1

        self.start_month_combo.setCurrentIndex(max(0, min(11, start_month - 1)))
        self.start_year_spin.setValue(start_year)

        explanation = QLabel(
            "Укажите дату, с которой меняется тариф. Он будет действовать дальше "
            "автоматически — до следующей добавленной записи об изменении тарифа. "
            "Если в следующем году тариф не менялся, новую строку добавлять не нужно."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("Muted")

        form = QFormLayout()
        form.addRow("Тариф, руб./м² в месяц *", self.amount_edit)

        start_row = QHBoxLayout()
        start_row.addWidget(self.start_month_combo, 1)
        start_row.addWidget(self.start_year_spin)
        form.addRow("Действует с", start_row)

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
        layout.addWidget(explanation)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        return {
            "amount": self.amount_edit.text().strip(),
            "start_year": self.start_year_spin.value(),
            "start_month": int(self.start_month_combo.currentData()),
        }

    def _validate_and_accept(self) -> None:
        raw = (
            self.amount_edit.text()
            .replace("\u00a0", "")
            .replace(" ", "")
            .replace(",", ".")
        )
        try:
            amount = Decimal(raw)
        except InvalidOperation:
            amount = Decimal("0")
        if amount <= 0:
            QMessageBox.warning(
                self,
                "Проверьте тариф",
                "Укажите тариф больше нуля.",
            )
            self.amount_edit.setFocus()
            return

        super().accept()

