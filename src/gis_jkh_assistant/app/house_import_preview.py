"""Review every import change and explicitly resolve ambiguous house rows."""
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from gis_jkh_assistant.gis_files.house_extra_data import HOUSE_COLUMNS
from .widgets import SafeComboBox

AREA_LABELS = {"charge_area": "К начислению", "residential_area": "Жилая площадь",
               "non_residential_area": "Нежилая площадь"}
AREA_LABELS.update(HOUSE_COLUMNS)


class HouseImportPreview(QDialog):
    def __init__(self, areas, tariffs, summary, parent=None, other_changes=()):
        super().__init__(parent)
        self.setWindowTitle("Проверка импорта данных домов и актов")
        self.resize(900, 500)
        body = QVBoxLayout(self)
        text = QLabel(summary)
        text.setWordWrap(True)
        body.addWidget(text)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Дом", "Поле / период", "Было", "Будет"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        body.addWidget(self.table, 1)
        for house, changes, previous in areas:
            for field, value in changes.items():
                self._row(house.address, AREA_LABELS[field], previous[field], value)
        for house, row, previous in tariffs:
            self._row(house.address, "Тариф с " + row.start.input_label,
                      previous.amount if previous else None, row.amount)
        for owner, field, before, after in other_changes:
            self._row(owner, field, before, after)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Применить изменения")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)

    def _row(self, address, field, before, after):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate((address, field, "Не указано" if before is None else before, after)):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            self.table.setItem(row, column, item)

    def showEvent(self, event):  # noqa: N802 - Qt API
        super().showEvent(event)
        self.table.resizeRowsToContents()


class HouseMatchDialog(QDialog):
    def __init__(self, unresolved, houses, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сопоставление домов перед импортом")
        self.resize(900, 500)
        self.choices = []
        groups = {}
        for row, message in unresolved:
            key = (
                str(getattr(row, "house_id", None) or "").strip().casefold(),
                " ".join(row.address.split()).casefold(),
                *(str(getattr(row, field, None) or "").strip().casefold()
                  for field in ("house_object_guid", "fias_house_guid", "gis_unique_house_number")),
            )
            groups.setdefault(key, []).append((row, message))
        houses = list(houses)
        body = QVBoxLayout(self)
        hint = QLabel(
            "Выберите соответствующий дом выбранной УО. Один выбор применяется ко всем "
            "периодам и связанным сведениям этой группы. "
            "До проверки и подтверждения импорт ничего не изменяет."
        )
        hint.setWordWrap(True)
        body.addWidget(hint)
        self.summary = QLabel(
            f"Сопоставлений: {len(groups)}; записей файла: "
            f"{sum(len(rows) for rows in groups.values())}."
        )
        body.addWidget(self.summary)
        self.table = QTableWidget(len(groups), 3)
        self.table.setHorizontalHeaderLabels(["Дом / записи файла", "Причина", "Дом в программе"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for index, rows in enumerate(groups.values()):
            row = rows[0][0]
            numbers = ", ".join(str(number) for number in sorted({r.row_number for r, _ in rows}))
            address = QTableWidgetItem(f"{row.address}\nЗаписей: {len(rows)}")
            address.setToolTip(f"{row.address}\nСтроки файла: {numbers}\nЗаписей: {len(rows)}")
            self.table.setItem(index, 0, address)
            messages = "\n".join(dict.fromkeys(message for _, message in rows))
            reason = QTableWidgetItem(messages)
            reason.setToolTip(messages)
            self.table.setItem(index, 1, reason)
            combo = SafeComboBox()
            combo.addItem("Выберите дом…", None)
            for house in houses:
                combo.addItem(house.address, house.id)
            self.table.setCellWidget(index, 2, combo)
            self.choices.extend((source, combo) for source, _ in rows)
            combo.currentIndexChanged.connect(self._enabled)
        self.table.resizeRowsToContents()
        body.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply.setText("Проверить изменения")
        self.apply.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)

    def _enabled(self):
        self.apply.setEnabled(bool(self.choices) and all(combo.currentData() for _, combo in self.choices))

    def accept(self):
        if self.choices and all(combo.currentData() for _, combo in self.choices):
            super().accept()

    def mapping(self):
        return {id(row): combo.currentData() for row, combo in self.choices}
