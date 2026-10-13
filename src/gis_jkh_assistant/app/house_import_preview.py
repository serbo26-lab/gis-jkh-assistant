"""Review every import change and explicitly resolve ambiguous house rows."""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

AREA_LABELS = {"charge_area": "К начислению", "residential_area": "Жилая площадь",
               "non_residential_area": "Нежилая площадь"}


class HouseImportPreview(QDialog):
    def __init__(self, areas, tariffs, summary, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проверка импорта площадей и тарифов")
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
            self.table.setItem(row, column, QTableWidgetItem(str(value)))


class HouseMatchDialog(QDialog):
    def __init__(self, unresolved, houses, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сопоставление домов перед импортом")
        self.resize(900, 500)
        self.choices = []
        body = QVBoxLayout(self)
        hint = QLabel("Выберите дом выбранной УО для каждой неоднозначной строки. До проверки и подтверждения импорт ничего не изменяет.")
        hint.setWordWrap(True)
        body.addWidget(hint)
        table = QTableWidget(len(unresolved), 3)
        table.setHorizontalHeaderLabels(["Строка файла / адрес", "Причина", "Дом в программе"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for index, (row, message) in enumerate(unresolved):
            table.setItem(index, 0, QTableWidgetItem(f"Строка {row.row_number}: {row.address}"))
            table.setItem(index, 1, QTableWidgetItem(message))
            combo = QComboBox()
            combo.addItem("Выберите дом…", None)
            for house in houses:
                combo.addItem(house.address, house.id)
            table.setCellWidget(index, 2, combo)
            self.choices.append((row, combo))
            combo.currentIndexChanged.connect(self._enabled)
        body.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply.setText("Проверить изменения")
        self.apply.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)

    def _enabled(self):
        self.apply.setEnabled(all(combo.currentData() for _, combo in self.choices))

    def mapping(self):
        return {id(row): combo.currentData() for row, combo in self.choices}
