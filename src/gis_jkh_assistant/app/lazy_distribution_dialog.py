"""Dialog for filling a work list from one planned total amount."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gis_jkh_assistant.database.models import WorkList
from gis_jkh_assistant.database.repository import Repository
from gis_jkh_assistant.work_lists import (
    AllocationInput,
    LazyDistributionError,
    calculate_distribution,
    period_months,
    preview_cost,
)


class LazyDistributionDialog(QDialog):
    """Calculate quantity+cost rows from a planned total and percentages."""

    def __init__(
        self,
        repository: Repository,
        work_list: WorkList,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.work_list = work_list
        self.items = repository.list_work_list_items(work_list.id)

        self.setWindowTitle("Заполнение от планируемой суммы")
        self.resize(900, 650)
        self.setMinimumSize(720, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Распределение планируемой суммы")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        months = period_months(
            work_list.start_year,
            work_list.start_month,
            work_list.end_year,
            work_list.end_month,
        )
        description = QLabel(
            f"Период: {work_list.period_label}. Количество для заполненных позиций "
            f"будет установлено автоматически: {months}. "
            "Введите общую планируемую сумму и процент каждой работы. "
            "Сумма процентов должна составлять 100%."
        )
        description.setWordWrap(True)
        description.setObjectName("Muted")
        layout.addWidget(description)

        warning = QLabel(
            "После применения расчет заменит текущие значения «Цена», «Объем», "
            "«Количество» и «Стоимость» в этом перечне. Позиции с 0% останутся пустыми."
        )
        warning.setWordWrap(True)
        warning.setObjectName("Muted")
        layout.addWidget(warning)

        total_row = QHBoxLayout()
        total_row.addWidget(QLabel("Планируемая сумма, руб."))
        self.total_edit = QLineEdit(work_list.planned_total or "")
        self.total_edit.setPlaceholderText("Например: 100000")
        self.total_edit.setMinimumWidth(220)
        total_row.addWidget(self.total_edit)
        total_row.addStretch(1)
        layout.addLayout(total_row)

        self.table = QTableWidget(len(self.items), 4)
        self.table.setHorizontalHeaderLabels(
            ["Работа / услуга", "Доля, %", "Сумма, руб.", "Количество"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        for row, item in enumerate(self.items):
            name = QTableWidgetItem(item.work_name)
            name.setData(Qt.ItemDataRole.UserRole, item.id)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name)

            percent = QTableWidgetItem(item.allocation_percent or "")
            percent.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 1, percent)

            amount = QTableWidgetItem("")
            amount.setFlags(amount.flags() & ~Qt.ItemFlag.ItemIsEditable)
            amount.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 2, amount)

            quantity = QTableWidgetItem(str(months))
            quantity.setFlags(quantity.flags() & ~Qt.ItemFlag.ItemIsEditable)
            quantity.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, quantity)

        layout.addWidget(self.table, 1)

        summary = QHBoxLayout()
        self.percent_status = QLabel("Распределено: 0%")
        self.amount_status = QLabel("Распределено: 0 руб.")
        summary.addWidget(self.percent_status)
        summary.addStretch(1)
        summary.addWidget(self.amount_status)
        layout.addLayout(summary)

        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Отмена")
        self.apply_button = QPushButton("Рассчитать и заполнить перечень")
        self.apply_button.setObjectName("PrimaryButton")
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

        self.total_edit.textChanged.connect(self._refresh_preview)
        self.table.itemChanged.connect(self._table_item_changed)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self._apply)

        self._refresh_preview()

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 1:
            self._refresh_preview()

    def _refresh_preview(self) -> None:
        total_text = self.total_edit.text().strip()
        percent_sum = Decimal("0")
        amount_sum = Decimal("0")
        valid = True

        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                percent_item = self.table.item(row, 1)
                amount_item = self.table.item(row, 2)
                quantity_item = self.table.item(row, 3)
                percent_text = percent_item.text().strip() if percent_item else ""

                try:
                    percent = _decimal_or_zero(percent_text)
                    if percent < 0:
                        raise InvalidOperation
                    percent_sum += percent
                    preview = preview_cost(total_text or "0", percent_text or "0")
                    amount = _decimal_or_zero(preview)
                    amount_sum += amount
                    if amount_item:
                        amount_item.setText(_display_money(amount))
                    if quantity_item:
                        quantity_item.setText(
                            str(
                                period_months(
                                    self.work_list.start_year,
                                    self.work_list.start_month,
                                    self.work_list.end_year,
                                    self.work_list.end_month,
                                )
                            )
                            if percent > 0
                            else "—"
                        )
                except (InvalidOperation, LazyDistributionError):
                    valid = False
                    if amount_item:
                        amount_item.setText("Ошибка")
        finally:
            self.table.blockSignals(False)

        self.percent_status.setText(f"Распределено: {_display_decimal(percent_sum)}%")
        self.amount_status.setText(f"Предварительно: {_display_money(amount_sum)} руб.")

        exact = valid and percent_sum == Decimal("100")
        self.apply_button.setEnabled(exact and _decimal_or_zero(total_text) > 0)
        if exact:
            self.percent_status.setStyleSheet("color: #107c10;")
        else:
            self.percent_status.setStyleSheet("color: #b42318;")

    def _allocation_inputs(self) -> list[AllocationInput]:
        result: list[AllocationInput] = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            percent_item = self.table.item(row, 1)
            result.append(
                AllocationInput(
                    item_id=str(name_item.data(Qt.ItemDataRole.UserRole)),
                    allocation_percent=percent_item.text().strip() or None,
                )
            )
        return result

    def _apply(self) -> None:
        try:
            result = calculate_distribution(
                planned_total=self.total_edit.text(),
                start_year=self.work_list.start_year,
                start_month=self.work_list.start_month,
                end_year=self.work_list.end_year,
                end_month=self.work_list.end_month,
                allocations=self._allocation_inputs(),
            )
        except LazyDistributionError as exc:
            QMessageBox.warning(self, "Проверьте расчет", str(exc))
            return

        self.repository.apply_lazy_distribution(
            self.work_list.id,
            planned_total=result.planned_total,
            rows=result.rows,
        )
        self.accept()


def _decimal_or_zero(value: str) -> Decimal:
    normalized = (
        str(value)
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )
    if not normalized:
        return Decimal("0")
    return Decimal(normalized)


def _display_decimal(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _display_money(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"))
    text = f"{quantized:,.2f}".replace(",", " ").replace(".", ",")
    return text
