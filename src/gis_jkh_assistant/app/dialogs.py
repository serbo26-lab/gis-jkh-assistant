"""Small edit dialogs for organizations and manual houses."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from gis_jkh_assistant.database.models import House, HouseTariff, ManagementOrganization
from gis_jkh_assistant.period_dates import parse_period_date

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
        self.management_start_edit = QLineEdit(
            (house.management_start_date or "") if house else ""
        )
        self.management_start_edit.setPlaceholderText("ДД.ММ.ГГГГ")
        self.management_end_edit = QLineEdit(
            (house.management_end_date or "") if house else ""
        )
        self.management_end_edit.setPlaceholderText("ДД.ММ.ГГГГ или пусто")
        self.management_agreement_number_edit = QLineEdit(
            (house.management_agreement_number or "") if house else ""
        )
        self.management_agreement_date_edit = QLineEdit(
            (house.management_agreement_date or "") if house else ""
        )
        self.management_agreement_date_edit.setPlaceholderText("ДД.ММ.ГГГГ")
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
        form.addRow("Начало управления", self.management_start_edit)
        form.addRow("Окончание управления", self.management_end_edit)
        form.addRow("Номер договора управления", self.management_agreement_number_edit)
        form.addRow("Дата договора управления", self.management_agreement_date_edit)
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
            "management_start_date": self.management_start_edit.text().strip() or None,
            "management_end_date": self.management_end_edit.text().strip() or None,
            "management_agreement_number": self.management_agreement_number_edit.text().strip() or None,
            "management_agreement_date": self.management_agreement_date_edit.text().strip() or None,
            "management_data_source": "manual",
            "residential_premises_count": self.residential_spin.value(),
            "non_residential_premises_count": self.non_residential_spin.value(),
        }

    def _validate_and_accept(self) -> None:
        if not self.address_edit.text().strip():
            QMessageBox.warning(self, "Не заполнено поле", "Укажите адрес дома.")
            self.address_edit.setFocus()
            return
        for label, widget in (
            ("Начало управления", self.management_start_edit),
            ("Окончание управления", self.management_end_edit),
            ("Дата договора управления", self.management_agreement_date_edit),
        ):
            value = widget.text().strip()
            if not value:
                continue
            try:
                datetime.strptime(value, "%d.%m.%Y")
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Проверьте дату",
                    f"Поле «{label}» должно быть в формате ДД.ММ.ГГГГ.",
                )
                widget.setFocus()
                return
        super().accept()



class HouseTariffDialog(QDialog):
    def __init__(
        self,
        parent=None,
        tariff: HouseTariff | None = None,
        *,
        default_start_year: int | None = None,
        default_start_month: int | None = None,
        default_start_day: int = 1,
        default_start_precision: str = "month",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Тариф дома")
        self.setMinimumWidth(560)

        self.amount_edit = QLineEdit(tariff.amount if tariff else "")
        self.amount_edit.setPlaceholderText("Например: 25,04")

        if tariff:
            start_text = tariff.effective_from_label
        elif default_start_year and default_start_month:
            if default_start_precision == "day":
                start_text = (
                    f"{int(default_start_day):02d}."
                    f"{int(default_start_month):02d}.{int(default_start_year)}"
                )
            else:
                start_text = f"{int(default_start_month):02d}.{int(default_start_year)}"
        else:
            today = date.today()
            start_text = f"{today.month:02d}.{today.year}"

        self.start_date_edit = QLineEdit(start_text)
        self.start_date_edit.setPlaceholderText("08.2025 или 10.08.2025")
        self.start_date_edit.setToolTip(
            "ММ.ГГГГ означает первый день месяца. "
            "ДД.ММ.ГГГГ сохраняет точную дату."
        )

        explanation = QLabel(
            "Укажите дату, с которой меняется тариф. Можно использовать ММ.ГГГГ "
            "(например 08.2025 — это 01.08.2025) или точную дату ДД.ММ.ГГГГ "
            "(например 10.08.2025). Тариф действует до дня перед следующим "
            "изменением. Если тариф не менялся, новую строку добавлять не нужно."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("Muted")

        form = QFormLayout()
        form.addRow("Тариф, руб./м² в месяц *", self.amount_edit)
        form.addRow("Действует с *", self.start_date_edit)

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
        parsed = parse_period_date(self.start_date_edit.text(), boundary="start")
        return {
            "amount": self.amount_edit.text().strip(),
            "start_year": parsed.value.year,
            "start_month": parsed.value.month,
            "start_day": parsed.value.day,
            "start_precision": parsed.precision,
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

        try:
            parse_period_date(self.start_date_edit.text(), boundary="start")
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте дату", str(exc))
            self.start_date_edit.setFocus()
            return

        super().accept()
