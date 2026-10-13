"""Selection dialog for works from the organization's GIS ЖКХ directory."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class WorkDirectorySelectionDialog(QDialog):
    def __init__(
        self,
        options: list[dict],
        *,
        existing_guids: set[str] | None = None,
        existing_codes: set[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.options = options
        self.existing_guids = {str(value) for value in (existing_guids or set()) if value}
        self.existing_codes = {str(value) for value in (existing_codes or set()) if value}
        self.setWindowTitle("Добавить работы из справочника ГИС ЖКХ")
        self.resize(900, 620)

        root = QVBoxLayout(self)

        hint = QLabel(
            "Выберите работы и услуги из справочника текущей управляющей организации. "
            "Позиции, уже добавленные в перечень, отмечены как недоступные."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        root.addWidget(hint)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Поиск"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Наименование, код, вид работы или единица измерения")
        search_row.addWidget(self.search_edit, 1)
        root.addLayout(search_row)

        quick = QHBoxLayout()
        self.select_visible_button = QPushButton("Выбрать все видимые")
        self.clear_button = QPushButton("Снять все")
        self.counter_label = QLabel("Выбрано: 0")
        self.counter_label.setObjectName("Muted")
        quick.addWidget(self.select_visible_button)
        quick.addWidget(self.clear_button)
        quick.addStretch(1)
        quick.addWidget(self.counter_label)
        root.addLayout(quick)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "Код", "Наименование работы / услуги", "Вид работы", "Ед. изм.", "Состояние"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 42)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 430)
        self.table.setColumnWidth(3, 220)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 110)
        root.addWidget(self.table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Добавить выбранные")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.select_visible_button.clicked.connect(self._select_visible)
        self.clear_button.clicked.connect(self._clear)
        self.table.itemChanged.connect(self._update_counter)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        self._reload()

    @staticmethod
    def _option_key(option: dict) -> tuple[str, str]:
        return (
            str(option.get("guid") or "").strip(),
            str(option.get("code") or "").strip(),
        )

    def _reload(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.options))
        for row, option in enumerate(self.options):
            guid, code = self._option_key(option)
            existing = (
                (guid and guid in self.existing_guids)
                or (code and code in self.existing_codes)
            )

            selector = QTableWidgetItem()
            selector.setData(Qt.ItemDataRole.UserRole, option)
            selector.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            selector.setCheckState(Qt.CheckState.Unchecked)
            if existing:
                selector.setFlags(Qt.ItemFlag.ItemIsSelectable)
                selector.setCheckState(Qt.CheckState.Checked)
                selector.setToolTip("Уже добавлено в перечень")
            self.table.setItem(row, 0, selector)

            values = (
                code,
                option.get("name") or "",
                option.get("workTypeName") or "",
                option.get("unitName") or "",
                "В перечне" if existing else "Доступна",
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if existing:
                    item.setToolTip("Уже добавлено в перечень")
                self.table.setItem(row, column, item)

        self.table.blockSignals(False)
        self._update_counter()

    def _apply_filter(self) -> None:
        query = self.search_edit.text().strip().casefold()
        for row in range(self.table.rowCount()):
            haystack = " ".join(
                self.table.item(row, column).text()
                for column in range(1, 5)
                if self.table.item(row, column) is not None
            ).casefold()
            self.table.setRowHidden(row, bool(query and query not in haystack))

    def _select_visible(self) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            selector = self.table.item(row, 0)
            if selector and selector.flags() & Qt.ItemFlag.ItemIsEnabled:
                selector.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self._update_counter()

    def _clear(self) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            selector = self.table.item(row, 0)
            if selector and selector.flags() & Qt.ItemFlag.ItemIsEnabled:
                selector.setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_counter()

    def _update_counter(self, *_args) -> None:
        self.counter_label.setText(f"Выбрано: {len(self.selected_options())}")

    def selected_options(self) -> list[dict]:
        result: list[dict] = []
        for row in range(self.table.rowCount()):
            selector = self.table.item(row, 0)
            if (
                selector
                and selector.flags() & Qt.ItemFlag.ItemIsEnabled
                and selector.checkState() == Qt.CheckState.Checked
            ):
                result.append(dict(selector.data(Qt.ItemDataRole.UserRole) or {}))
        return result
