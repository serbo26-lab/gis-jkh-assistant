"""Dialog for filling a work list from one planned total amount."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
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
    calculate_planned_total,
    period_months,
    preview_cost,
    validate_quantity,
)


class _PercentEntryDelegate(QStyledItemDelegate):
    """Keep corrections in place, but advance after filling a previously empty cell."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table

    def createEditor(self, parent, option, index):  # noqa: ANN001
        editor = super().createEditor(parent, option, index)
        if editor is not None:
            editor.setProperty("percent_row", index.row())
            editor.setProperty(
                "percent_started_empty",
                not str(index.data() or "").strip(),
            )
        return editor

    def eventFilter(self, editor, event) -> bool:  # noqa: ANN001
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            row = int(editor.property("percent_row") or 0)
            started_empty = bool(editor.property("percent_started_empty"))
            new_text = editor.text().strip() if hasattr(editor, "text") else ""

            self.commitData.emit(editor)
            self.closeEditor.emit(
                editor,
                QAbstractItemDelegate.EndEditHint.NoHint,
            )
            QTimer.singleShot(
                0,
                lambda: self._after_enter(row, started_empty, new_text),
            )
            return True

        return super().eventFilter(editor, event)

    def _after_enter(self, row: int, started_empty: bool, new_text: str) -> None:
        if started_empty and new_text:
            for next_row in range(row + 1, self.table.rowCount()):
                item = self.table.item(next_row, 1)
                if item is not None and not item.text().strip():
                    self.table.setCurrentCell(next_row, 1)
                    self.table.scrollToItem(item)
                    return

        self.table.setCurrentCell(row, 1)


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
        self.resize(980, 720)
        self.setMinimumSize(780, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Распределение планируемой суммы")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.period_month_count = period_months(
            work_list.start_year,
            work_list.start_month,
            work_list.end_year,
            work_list.end_month,
        )

        house = repository.get_house(work_list.house_id)
        house_tariff = repository.find_house_tariff_covering_period(
            work_list.house_id,
            start_year=work_list.start_year,
            start_month=work_list.start_month,
            end_year=work_list.end_year,
            end_month=work_list.end_month,
        )
        initial_tariff = (
            work_list.planned_tariff
            or (house_tariff.amount if house_tariff else "")
        )
        initial_area = (
            work_list.planned_area
            or (house.charge_area if house and house.charge_area else "")
        )

        description = QLabel(
            f"Период: {work_list.period_label}. По умолчанию количество равно числу "
            f"месяцев периода: {self.period_month_count}. Это только предзаполнение: "
            "количество можно изменить сразу для всех позиций или отдельно в каждой строке. "
            "Планируемую сумму можно ввести готовой или рассчитать как "
            "«тариф × площадь × месяцы». Сумма процентов должна составлять 100%."
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

        controls = QGridLayout()
        controls.setHorizontalSpacing(10)
        controls.setVerticalSpacing(8)

        controls.addWidget(QLabel("Планируемая сумма, руб."), 0, 0)
        self.total_edit = QLineEdit(work_list.planned_total or "")
        self.total_edit.setPlaceholderText("Например: 118869,89")
        self.total_edit.setMinimumWidth(220)
        controls.addWidget(self.total_edit, 0, 1, 1, 3)

        calculator_label = QLabel("Рассчитать сумму")
        calculator_label.setObjectName("Muted")
        controls.addWidget(calculator_label, 1, 0)

        self.tariff_edit = QLineEdit(initial_tariff)
        self.tariff_edit.setPlaceholderText("Тариф")
        tariff_tooltip = "Тариф, руб. за м² в месяц"
        if not work_list.planned_tariff and house_tariff:
            tariff_tooltip += (
                f". Подставлен из карточки дома за период {house_tariff.period_label}"
            )
        self.tariff_edit.setToolTip(tariff_tooltip)
        controls.addWidget(self.tariff_edit, 1, 1)

        multiply_1 = QLabel("×")
        multiply_1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(multiply_1, 1, 2)

        self.area_edit = QLineEdit(initial_area)
        self.area_edit.setPlaceholderText("Площадь, м²")
        if not work_list.planned_area and house and house.charge_area:
            self.area_edit.setToolTip("Подставлено из поля «Площадь к начислению» карточки дома.")
        controls.addWidget(self.area_edit, 1, 3)

        multiply_2 = QLabel("×")
        multiply_2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(multiply_2, 1, 4)

        initial_calc_months = work_list.planned_months or str(self.period_month_count)
        self.months_edit = QLineEdit(initial_calc_months)
        self.months_edit.setPlaceholderText("Месяцы")
        self.months_edit.setMaximumWidth(100)
        controls.addWidget(self.months_edit, 1, 5)

        self.calculate_total_button = QPushButton("Рассчитать")
        controls.addWidget(self.calculate_total_button, 1, 6)

        initial_default_quantity = work_list.planned_months or str(self.period_month_count)
        controls.addWidget(QLabel("Количество по умолчанию"), 2, 0)
        self.default_quantity_edit = QLineEdit(initial_default_quantity)
        self.default_quantity_edit.setMinimumWidth(120)
        self.default_quantity_edit.setMaximumWidth(180)
        self.default_quantity_edit.setToolTip(
            "Например, для полного года обычно 12. "
            "Значение можно применить ко всем строкам, а затем отдельные строки изменить."
        )
        controls.addWidget(self.default_quantity_edit, 2, 1)

        self.apply_quantity_to_all_button = QPushButton("Применить количество ко всем")
        controls.addWidget(self.apply_quantity_to_all_button, 2, 3, 1, 2)
        controls.setColumnStretch(3, 1)
        layout.addLayout(controls)

        self._last_applied_default_quantity = initial_default_quantity

        self.table = QTableWidget(len(self.items), 4)
        self.table.setHorizontalHeaderLabels(
            ["Работа / услуга", "Доля, %", "Сумма, руб.", "Количество"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegateForColumn(1, _PercentEntryDelegate(self.table))
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

            if item.allocation_percent and item.quantity:
                initial_quantity = item.quantity
            else:
                initial_quantity = initial_default_quantity
            quantity = QTableWidgetItem(initial_quantity)
            quantity.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            quantity.setToolTip(
                "Предзаполнено количеством по умолчанию. "
                "Можно изменить для конкретной работы, например покос травы — 3."
            )
            self.table.setItem(row, 3, quantity)

        layout.addWidget(self.table, 1)

        summary = QHBoxLayout()
        self.percent_status = QLabel("Распределено: 0%")
        self.quantity_status = QLabel("")
        self.amount_status = QLabel("Распределено: 0 руб.")
        summary.addWidget(self.percent_status)
        summary.addWidget(self.quantity_status)
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
        self.calculate_total_button.clicked.connect(self._calculate_total)
        self.months_edit.returnPressed.connect(self._calculate_total)
        self.default_quantity_edit.returnPressed.connect(self._apply_quantity_to_all)
        self.apply_quantity_to_all_button.clicked.connect(self._apply_quantity_to_all)
        self.table.itemChanged.connect(self._table_item_changed)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self._apply)

        self._refresh_preview()

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() in (1, 3):
            self._refresh_preview()

    def _calculate_total(self) -> None:
        try:
            total = calculate_planned_total(
                tariff=self.tariff_edit.text(),
                area=self.area_edit.text(),
                months=self.months_edit.text(),
            )
            months = validate_quantity(self.months_edit.text())
        except LazyDistributionError as exc:
            QMessageBox.warning(self, "Проверьте расчет", str(exc))
            return

        previous_default = self._last_applied_default_quantity
        self.total_edit.setText(total)
        self.default_quantity_edit.setText(months)

        self._apply_quantity_to_rows(
            months,
            only_if_matches=previous_default,
        )
        self._last_applied_default_quantity = months
        self._refresh_preview()

    def _apply_quantity_to_all(self) -> None:
        try:
            quantity = validate_quantity(self.default_quantity_edit.text())
        except LazyDistributionError as exc:
            QMessageBox.warning(self, "Проверьте количество", str(exc))
            return

        self._apply_quantity_to_rows(quantity, only_if_matches=None)
        self._last_applied_default_quantity = quantity
        self._refresh_preview()

    def _apply_quantity_to_rows(
        self,
        quantity: str,
        *,
        only_if_matches: str | None,
    ) -> None:
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                quantity_item = self.table.item(row, 3)
                if quantity_item is None:
                    continue
                current = quantity_item.text().strip()
                if only_if_matches is not None and current not in {"", only_if_matches}:
                    continue
                quantity_item.setText(quantity)
                quantity_item.setBackground(QBrush())
                quantity_item.setToolTip(
                    "Предзаполнено массово. При необходимости измените количество "
                    "для этой позиции отдельно."
                )
        finally:
            self.table.blockSignals(False)

    def _refresh_preview(self) -> None:
        total_text = self.total_edit.text().strip()
        percent_sum = Decimal("0")
        amount_sum = Decimal("0")
        valid = True
        quantities_valid = True

        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                percent_item = self.table.item(row, 1)
                amount_item = self.table.item(row, 2)
                quantity_item = self.table.item(row, 3)

                percent_text = percent_item.text().strip() if percent_item else ""
                quantity_text = quantity_item.text().strip() if quantity_item else ""

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
                        quantity_item.setBackground(QBrush())
                        if percent > 0:
                            try:
                                validate_quantity(quantity_text)
                                quantity_item.setToolTip(
                                    "Количество будет записано в эту позицию перечня."
                                )
                            except LazyDistributionError:
                                quantities_valid = False
                                quantity_item.setBackground(QBrush(QColor("#fff0f0")))
                                quantity_item.setToolTip(
                                    "Для позиции с процентом больше 0 укажите "
                                    "количество больше нуля."
                                )
                        else:
                            quantity_item.setToolTip(
                                "Позиция с 0% не будет заполняться. "
                                "Количество останется только предзаполнением формы."
                            )
                except (InvalidOperation, LazyDistributionError):
                    valid = False
                    if amount_item:
                        amount_item.setText("Ошибка")
        finally:
            self.table.blockSignals(False)

        self.percent_status.setText(f"Распределено: {_display_decimal(percent_sum)}%")
        self.amount_status.setText(f"Предварительно: {_display_money(amount_sum)} руб.")

        if quantities_valid:
            self.quantity_status.setText("")
        else:
            self.quantity_status.setText("• Проверьте количество")
            self.quantity_status.setStyleSheet("color: #b42318;")

        try:
            total_valid = _decimal_or_zero(total_text) > 0
        except InvalidOperation:
            total_valid = False

        exact = valid and percent_sum == Decimal("100")
        self.apply_button.setEnabled(exact and total_valid and quantities_valid)

        if exact:
            self.percent_status.setStyleSheet("color: #107c10;")
        else:
            self.percent_status.setStyleSheet("color: #b42318;")

    def _allocation_inputs(self) -> list[AllocationInput]:
        result: list[AllocationInput] = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            percent_item = self.table.item(row, 1)
            quantity_item = self.table.item(row, 3)
            result.append(
                AllocationInput(
                    item_id=str(name_item.data(Qt.ItemDataRole.UserRole)),
                    allocation_percent=percent_item.text().strip() or None,
                    quantity=quantity_item.text().strip() or None,
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
            planned_tariff=self.tariff_edit.text().strip() or None,
            planned_area=self.area_edit.text().strip() or None,
            planned_months=self.months_edit.text().strip() or None,
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
