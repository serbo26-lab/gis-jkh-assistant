"""Quality assessment page for completed works and services."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.gis_integration import IntegrationError, LocalIntegrationGateway
from gis_jkh_assistant.work_lists.gis_quality_sync import (
    apply_remote_quality_assessment,
    local_quality_has_data,
)
from gis_jkh_assistant.quality_automation import (
    fill_quality_period_from_fixation,
    generate_acceptance_act_for_period,
)
from gis_jkh_assistant.quality_assessment import (
    build_quality_command_payload,
    helper_supports_quality_assessment,
    quality_reduction,
)
from gis_jkh_assistant.work_planning import active_months_for_work_list

from .gis_integration_worker import GisIntegrationWorker
from .quality_bulk_dialogs import QualityMonthActsDialog
from .quality_dialogs import QualityActsDialog
from .widgets import SafeComboBox


_MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
_ITEM_ID_ROLE = Qt.ItemDataRole.UserRole


class QualityAssessmentPage(QWidget):
    def __init__(
        self,
        repository: Repository,
        integration_gateway: LocalIntegrationGateway,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.store = QualityAssessmentStore(repository)
        self.integration_gateway = integration_gateway
        self._loading = False
        self._integration_worker: GisIntegrationWorker | None = None
        self._integration_busy = False
        self._current_work_list_id: str | None = None
        self._current_period_id: str | None = None
        self._current_month: int | None = None
        self._month_button_columns = 12
        self._queue_period_ids: list[str] = []
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_failed_period_id: str | None = None
        self._queue_command: str | None = None
        self._queue_title = ""
        self._queue_running = False
        self._build_ui()
        self.refresh_organizations()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("PageContent")
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        title = QLabel("Оценка качества")
        title.setObjectName("Title")
        root.addWidget(title)
        intro = QLabel(
            "Оценка качества формируется по месяцам размещённого перечня. "
            "Включите нужные работы, при необходимости укажите сведения о "
            "ненадлежащем качестве и добавьте один или несколько актов. "
            "Сначала оценку можно сохранить как проект, затем разместить."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        context = QFrame()
        context.setObjectName("Card")
        grid = QGridLayout(context)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Управляющая организация"), 0, 0)
        self.organization_combo = SafeComboBox()
        grid.addWidget(self.organization_combo, 0, 1, 1, 3)
        grid.addWidget(QLabel("Дом"), 1, 0)
        self.house_combo = SafeComboBox()
        grid.addWidget(self.house_combo, 1, 1, 1, 3)
        grid.addWidget(QLabel("Размещенный перечень"), 2, 0)
        self.work_list_combo = SafeComboBox()
        grid.addWidget(self.work_list_combo, 2, 1, 1, 3)
        grid.addWidget(QLabel("Год"), 3, 0)
        self.year_combo = SafeComboBox()
        self.year_combo.setFixedWidth(120)
        grid.addWidget(self.year_combo, 3, 1)
        grid.setColumnStretch(1, 2)
        root.addWidget(context)

        self.empty_hint = QLabel(
            "Для выбранного дома нет размещённых перечней. Сначала разместите "
            "перечень работ и услуг."
        )
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setStyleSheet("font-weight: 600;")
        root.addWidget(self.empty_hint)

        month_card = QFrame()
        month_card.setObjectName("Card")
        month_layout = QVBoxLayout(month_card)
        month_layout.setContentsMargins(12, 12, 12, 12)
        month_layout.setSpacing(8)
        self.month_buttons_layout = QGridLayout()
        self.month_buttons_layout.setHorizontalSpacing(6)
        self.month_buttons_layout.setVerticalSpacing(6)
        self.month_buttons: dict[int, QPushButton] = {}
        for month, name in enumerate(_MONTHS, start=1):
            button = QPushButton(f"{name}\n—")
            button.setObjectName("MonthButton")
            button.setCheckable(True)
            button.setMinimumWidth(72)
            button.clicked.connect(
                lambda _checked=False, value=month: self._select_month(value)
            )
            self.month_buttons[month] = button
        month_layout.addLayout(self.month_buttons_layout)
        self._relayout_month_buttons()

        self.month_status = QLabel("Период: —")
        self.month_status.setObjectName("Muted")
        month_layout.addWidget(self.month_status)

        automation = QGridLayout()
        automation.setHorizontalSpacing(8)
        automation.setVerticalSpacing(6)
        self.form_month_button = QPushButton("Сформировать оценку за месяц")
        self.form_year_button = QPushButton(
            "Сформировать оценку за доступные месяцы года"
        )
        self.auto_act_month_button = QPushButton("Сформировать акт месяца")
        self.auto_act_year_button = QPushButton(
            "Сформировать акты доступных месяцев года"
        )
        automation.addWidget(self.form_month_button, 0, 0)
        automation.addWidget(self.form_year_button, 0, 1)
        automation.addWidget(self.auto_act_month_button, 1, 0)
        automation.addWidget(self.auto_act_year_button, 1, 1)
        automation.setColumnStretch(0, 1)
        automation.setColumnStretch(1, 1)
        month_layout.addLayout(automation)

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(6)
        self.load_gis_button = QPushButton("Загрузить оценку качества из ГИС ЖКХ")
        self.load_gis_button.setToolTip(
            "Получить существующую оценку качества выбранного месяца из ГИС ЖКХ."
        )
        self.save_button = QPushButton("Сохранить проект месяца")
        self.publish_button = QPushButton("Разместить месяц")
        self.save_year_button = QPushButton("Сохранить проекты года")
        self.publish_year_button = QPushButton(
            "Разместить доступные месяцы года"
        )
        self.publish_year_button.setObjectName("PrimaryButton")
        self.cancel_button = QPushButton("Аннулировать месяц")
        self.continue_queue_button = QPushButton("Продолжить очередь")
        self.continue_queue_button.setVisible(False)

        actions.addWidget(self.load_gis_button, 0, 0)
        actions.addWidget(self.save_button, 0, 1)
        actions.addWidget(self.publish_button, 0, 2)
        actions.addWidget(self.save_year_button, 1, 0)
        actions.addWidget(self.publish_year_button, 1, 1)
        actions.addWidget(self.cancel_button, 1, 2)
        for column in range(3):
            actions.setColumnStretch(column, 1)
        month_layout.addLayout(actions)

        self.queue_progress = QProgressBar()
        self.queue_progress.setVisible(False)
        self.queue_progress.setTextVisible(True)
        month_layout.addWidget(self.queue_progress)

        queue_row = QHBoxLayout()
        self.queue_label = QLabel("")
        self.queue_label.setObjectName("Muted")
        self.queue_label.setWordWrap(True)
        queue_row.addWidget(self.queue_label, 1)
        queue_row.addWidget(self.continue_queue_button)
        month_layout.addLayout(queue_row)
        root.addWidget(month_card)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(12, 12, 12, 12)
        note = QLabel(
            "Для акта нарушения качества укажите количество дней ненадлежащего "
            "качества. Размер уменьшения платы рассчитывается автоматически. "
            "Для акта выполненных работ эти поля можно оставить пустыми."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        table_layout.addWidget(note)

        bulk_actions = QHBoxLayout()
        self.include_all_button = QPushButton("Включить все работы")
        self.exclude_all_button = QPushButton("Снять все")
        self.month_acts_button = QPushButton("Акты месяца…")
        self.month_acts_summary = QLabel("Акты месяца: —")
        self.month_acts_summary.setObjectName("Muted")
        bulk_actions.addWidget(self.include_all_button)
        bulk_actions.addWidget(self.exclude_all_button)
        bulk_actions.addSpacing(12)
        bulk_actions.addWidget(self.month_acts_button)
        bulk_actions.addWidget(self.month_acts_summary)
        bulk_actions.addStretch(1)
        table_layout.addLayout(bulk_actions)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "В оценку",
                "Работа / услуга",
                "Стоимость в ежемесячной плате",
                "Дней ненадлежащего качества",
                "Дней в месяце",
                "Уменьшение платы",
                "Акты",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        table_layout.addWidget(self.table)
        root.addWidget(table_card)
        root.addStretch(1)

        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.house_combo.currentIndexChanged.connect(self._house_changed)
        self.work_list_combo.currentIndexChanged.connect(self._work_list_changed)
        self.year_combo.currentIndexChanged.connect(self._year_changed)
        self.table.itemChanged.connect(self._table_item_changed)
        self.form_month_button.clicked.connect(self._form_month_from_fixation)
        self.form_year_button.clicked.connect(self._form_year_from_fixation)
        self.auto_act_month_button.clicked.connect(self._auto_generate_month_act)
        self.auto_act_year_button.clicked.connect(self._auto_generate_year_acts)
        self.load_gis_button.clicked.connect(self._load_from_gis)
        self.save_button.clicked.connect(self._save_project)
        self.publish_button.clicked.connect(self._publish)
        self.save_year_button.clicked.connect(self._save_year_projects)
        self.publish_year_button.clicked.connect(self._publish_year)
        self.continue_queue_button.clicked.connect(self._continue_queue)
        self.cancel_button.clicked.connect(self._cancel)
        self.include_all_button.clicked.connect(
            lambda: self._set_all_included(True)
        )
        self.exclude_all_button.clicked.connect(
            lambda: self._set_all_included(False)
        )
        self.month_acts_button.clicked.connect(self._open_month_acts)
        self._set_enabled(False)

    def refresh_organizations(self) -> None:
        current = self.organization_combo.currentData()
        self.organization_combo.blockSignals(True)
        self.organization_combo.clear()
        for organization in self.repository.list_organizations():
            self.organization_combo.addItem(organization.name, organization.id)
        if current:
            index = self.organization_combo.findData(current)
            if index >= 0:
                self.organization_combo.setCurrentIndex(index)
        self.organization_combo.blockSignals(False)
        self._organization_changed()

    def _organization_changed(self) -> None:
        organization_id = self.organization_combo.currentData()
        current = self.house_combo.currentData()
        self.house_combo.blockSignals(True)
        self.house_combo.clear()
        if organization_id:
            for house in self.repository.list_houses(str(organization_id)):
                self.house_combo.addItem(house.address, house.id)
        if current:
            index = self.house_combo.findData(current)
            if index >= 0:
                self.house_combo.setCurrentIndex(index)
        self.house_combo.blockSignals(False)
        self._house_changed()

    def _house_changed(self) -> None:
        house_id = self.house_combo.currentData()
        current = self.work_list_combo.currentData()
        self.work_list_combo.blockSignals(True)
        self.work_list_combo.clear()
        if house_id:
            for work_list in self.repository.list_placed_work_lists(str(house_id)):
                self.work_list_combo.addItem(
                    f"{work_list.period_label} • размещен",
                    work_list.id,
                )
        if current:
            index = self.work_list_combo.findData(current)
            if index >= 0:
                self.work_list_combo.setCurrentIndex(index)
        self.work_list_combo.blockSignals(False)
        has_lists = self.work_list_combo.count() > 0
        self.empty_hint.setVisible(not has_lists)
        if has_lists and self.work_list_combo.currentIndex() < 0:
            self.work_list_combo.setCurrentIndex(0)
        self._work_list_changed()

    def _work_list_changed(self) -> None:
        value = self.work_list_combo.currentData()
        self._current_work_list_id = str(value) if value else None
        self._current_period_id = None
        self._current_month = None
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if self._current_work_list_id
            else None
        )
        if work_list:
            for year in range(work_list.start_year, work_list.end_year + 1):
                self.year_combo.addItem(str(year), year)
            index = self.year_combo.findData(work_list.base_year)
            self.year_combo.setCurrentIndex(index if index >= 0 else 0)
        self.year_combo.blockSignals(False)
        self._year_changed()

    def _year_changed(self) -> None:
        work_list = (
            self.repository.get_work_list(self._current_work_list_id)
            if self._current_work_list_id
            else None
        )
        year = self.year_combo.currentData()
        active_months = (
            active_months_for_work_list(work_list, int(year))
            if work_list and year is not None
            else ()
        )
        for month, button in self.month_buttons.items():
            button.setEnabled(month in active_months)
        if self._current_month not in active_months:
            self._current_month = active_months[0] if active_months else None
        if self._current_month is None:
            self._current_period_id = None
            self.table.setRowCount(0)
            self.month_status.setText("Период: —")
            self._refresh_act_summary()
            self._set_enabled(False)
            self._refresh_year_status()
            return
        self._month_changed()
        self._refresh_year_status()

    def _select_month(self, month: int) -> None:
        button = self.month_buttons.get(int(month))
        if button is None or not button.isEnabled():
            return
        self._current_month = int(month)
        self._month_changed()

    def _month_changed(self) -> None:
        if (
            not self._current_work_list_id
            or self.year_combo.currentData() is None
            or self._current_month is None
        ):
            return
        try:
            period = self.store.get_or_create_period(
                work_list_id=self._current_work_list_id,
                year=int(self.year_combo.currentData()),
                month=int(self._current_month),
            )
        except ValueError as exc:
            self._current_period_id = None
            self.table.setRowCount(0)
            self.month_status.setText(str(exc))
            self._set_enabled(False)
            return
        self._current_period_id = period.id
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()
        self._set_enabled(True)

    def _load_table(self) -> None:
        if not self._current_period_id:
            self.table.setRowCount(0)
            return
        items = self.store.list_items(self._current_period_id)
        self._loading = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        try:
            for row, item in enumerate(items):
                include = QTableWidgetItem("")
                include.setFlags(include.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                include.setCheckState(
                    Qt.CheckState.Checked if item.included else Qt.CheckState.Unchecked
                )
                include.setData(_ITEM_ID_ROLE, item.id)
                self.table.setItem(row, 0, include)

                name = QTableWidgetItem(item.work_name)
                name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 1, name)

                cost = QTableWidgetItem(item.cost or "")
                days = QTableWidgetItem(
                    "" if item.days_count is None else str(item.days_count)
                )
                self.table.setItem(row, 2, cost)
                self.table.setItem(row, 3, days)

                month_days = QTableWidgetItem(str(item.days_per_month))
                month_days.setFlags(month_days.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 4, month_days)

                reduction_text = ""
                if item.cost_reduction:
                    try:
                        reduction_text = f"{float(item.cost_reduction):.2f}"
                    except ValueError:
                        reduction_text = item.cost_reduction
                reduction = QTableWidgetItem(reduction_text)
                reduction.setFlags(reduction.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 5, reduction)

                acts = self.store.list_acts(item.id)
                button = QPushButton(f"Акты: {len(acts)}")
                button.clicked.connect(
                    lambda _checked=False, item_id=item.id: self._open_acts(item_id)
                )
                self.table.setCellWidget(row, 6, button)
        finally:
            self.table.blockSignals(False)
            self._loading = False
        self._resize_table()

    def _table_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._loading or cell.column() not in (0, 2, 3):
            return
        id_cell = self.table.item(cell.row(), 0)
        item_id = id_cell.data(_ITEM_ID_ROLE) if id_cell else None
        if not item_id:
            return
        item = self.store.get_item(str(item_id))
        if item is None:
            return
        included = id_cell.checkState() == Qt.CheckState.Checked
        cost_text = self.table.item(cell.row(), 2).text().strip()
        days_text = self.table.item(cell.row(), 3).text().strip()
        try:
            days_count = int(days_text) if days_text else None
        except ValueError:
            QMessageBox.warning(
                self,
                "Проверьте количество дней",
                "Количество дней должно быть целым числом.",
            )
            self._load_table()
            return
        reduction = quality_reduction(
            cost_text or None,
            days_count,
            item.days_per_month,
        )
        try:
            self.store.update_item(
                str(item_id),
                included=included,
                cost=cost_text or None,
                days_count=days_count,
                cost_reduction=reduction,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте данные", str(exc))
            self._load_table()
            return
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()

    def _set_all_included(self, included: bool) -> None:
        if not self._current_period_id:
            return
        self.store.set_items_included(self._current_period_id, included)
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()

    def _active_months(self) -> tuple[int, ...]:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return ()
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return ()
        return active_months_for_work_list(
            work_list,
            int(self.year_combo.currentData()),
        )

    def _form_period_from_fixation(self, period_id: str):
        return fill_quality_period_from_fixation(self.store, period_id)

    def _form_month_from_fixation(self) -> None:
        if not self._current_period_id:
            return
        answer = QMessageBox.question(
            self,
            "Сформировать оценку за месяц?",
            "В оценку будут включены работы, по которым в выбранном месяце "
            "есть фактические показатели в разделе «Фиксация работ и услуг». "
            "Стоимость будет взята из «Стоимость факт.».\n\n"
            "Строки с пользовательскими или импортированными актами "
            "останутся без изменений. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self._form_period_from_fixation(self._current_period_id)
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()
        QMessageBox.information(
            self,
            "Оценка сформирована",
            f"Включено работ: {result.included}.\n"
            f"Исключено без факта: {result.excluded}.\n"
            f"Защищено пользовательскими актами: {result.protected}.",
        )

    def _form_year_from_fixation(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        months = self._active_months()
        if not months:
            return
        answer = QMessageBox.question(
            self,
            "Сформировать оценку за доступные месяцы года?",
            f"Будут последовательно сформированы месяцы: {len(months)}. "
            "Для каждого месяца используются фактические показатели из фиксации. "
            "Строки с пользовательскими или импортированными актами "
            "не изменяются. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        year = int(self.year_combo.currentData())
        included = excluded = protected = 0
        for month in months:
            period = self.store.get_or_create_period(
                work_list_id=self._current_work_list_id,
                year=year,
                month=month,
            )
            result = self._form_period_from_fixation(period.id)
            included += result.included
            excluded += result.excluded
            protected += result.protected

        self._month_changed()
        self._refresh_year_status()
        QMessageBox.information(
            self,
            "Оценка за год сформирована",
            f"Обработано месяцев: {len(months)}.\n"
            f"Включено строк: {included}.\n"
            f"Исключено без факта: {excluded}.\n"
            f"Защищено пользовательскими актами: {protected}.",
        )

    def _auto_generate_act_for_period(self, period_id: str):
        return generate_acceptance_act_for_period(
            self.store,
            period_id=period_id,
            organization_name=self.organization_combo.currentText(),
            house_address=self.house_combo.currentText(),
        )

    def _auto_generate_month_act(self) -> None:
        if not self._current_period_id:
            return
        self._sync_work_units_for_act()
        try:
            result = self._auto_generate_act_for_period(self._current_period_id)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "Не удалось сформировать акт",
                str(exc),
            )
            return
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()
        if result.created:
            text = (
                f"Акт сформирован и применён к работам: {result.linked_items}."
            )
            if result.protected_items:
                text += (
                    f"\nСтрок с пользовательскими/импортированными актами "
                    f"пропущено: {result.protected_items}."
                )
            if result.missing_costs:
                text += (
                    f"\nБез значения «Стоимость факт.»: {result.missing_costs}."
                )
        else:
            text = (
                "Нет строк для автоматического акта. "
                "Включённые строки уже защищены пользовательскими/импортированными "
                "актами либо оценка ещё не сформирована."
            )
        QMessageBox.information(self, "Автоматический акт месяца", text)

    def _auto_generate_year_acts(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        months = self._active_months()
        answer = QMessageBox.question(
            self,
            "Сформировать акты за доступные месяцы года?",
            f"Будут обработаны сформированные оценки в доступных месяцах "
            f"выбранного года ({len(months)} мес. в периоде перечня). "
            "Пользовательские и импортированные акты не изменяются. "
            "Ранее сформированные программой акты будут заменены. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._sync_work_units_for_act()
        year = int(self.year_combo.currentData())
        created = linked = protected = missing = 0
        processed = 0
        for month in months:
            period = self.store.get_period_for_month(
                self._current_work_list_id,
                year=year,
                month=month,
            )
            if period is None:
                continue
            if not any(item.included for item in self.store.list_items(period.id)):
                continue
            try:
                result = self._auto_generate_act_for_period(period.id)
            except (OSError, RuntimeError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    "Массовое формирование актов остановлено",
                    f"{_MONTHS[month - 1]} {year}: {exc}",
                )
                break
            processed += 1
            created += result.created
            linked += result.linked_items
            protected += result.protected_items
            missing += result.missing_costs

        self._month_changed()
        self._refresh_year_status()
        QMessageBox.information(
            self,
            "Акты за год сформированы",
            f"Обработано месяцев: {processed}.\n"
            f"Сформировано актов: {created}.\n"
            f"Связей с работами: {linked}.\n"
            f"Защищено пользовательскими/импортированными актами: {protected}.\n"
            f"Строк без фактической стоимости: {missing}.",
        )

    def _sync_work_units_for_act(self) -> None:
        if not self._current_work_list_id:
            return
        status = self.integration_gateway.status()
        if not (
            status.running
            and status.helper_connected
            and status.gis_page_ready
            and helper_supports_quality_assessment(status.helper_version)
        ):
            return

        items = [
            item
            for item in self.repository.list_work_list_items(
                self._current_work_list_id
            )
            if not str(item.unit_name or "").strip()
        ]
        if not items:
            return
        payload = {
            "items": [
                {
                    "localItemId": item.id,
                    "workCode": item.work_code,
                    "workGuid": item.work_guid,
                }
                for item in items
                if item.work_code or item.work_guid
            ]
        }
        if not payload["items"]:
            return

        try:
            result = self.integration_gateway.execute(
                "resolve_planned_work_units",
                payload,
                timeout=45.0,
            )
        except IntegrationError:
            return

        units = {
            str(row.get("localItemId")): str(row.get("unitName")).strip()
            for row in (result.get("items") or [])
            if row.get("localItemId") and str(row.get("unitName") or "").strip()
        }
        self.repository.update_work_list_unit_names(
            self._current_work_list_id,
            units,
        )

    def _open_month_acts(self) -> None:
        if (
            not self._current_period_id
            or self._current_month is None
            or self.year_combo.currentData() is None
        ):
            return
        self._sync_work_units_for_act()
        dialog = QualityMonthActsDialog(
            self.store,
            period_id=self._current_period_id,
            organization_name=self.organization_combo.currentText(),
            house_address=self.house_combo.currentText(),
            year=int(self.year_combo.currentData()),
            month=int(self._current_month),
            parent=self,
        )
        dialog.exec()
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()

    def _refresh_act_summary(self) -> None:
        if not self._current_period_id:
            self.month_acts_summary.setText("Акты месяца: —")
            return
        acts = self.store.list_period_acts(self._current_period_id)
        links = sum(self.store.count_act_links(act.id) for act in acts)
        self.month_acts_summary.setText(
            f"Акты месяца: {len(acts)} • связей с работами: {links}"
        )

    def _open_acts(self, item_id: str) -> None:
        if not self._current_period_id or self._current_month is None:
            return
        dialog = QualityActsDialog(
            self.store,
            item_id=item_id,
            year=int(self.year_combo.currentData()),
            month=int(self._current_month),
            editable=True,
            parent=self,
        )
        dialog.exec()
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()

    def _validation_error(self, period_id: str) -> str | None:
        selected = [
            item for item in self.store.list_items(period_id) if item.included
        ]
        if not selected:
            return "Нет работ, включённых в оценку качества."
        for item in selected:
            acts = self.store.list_acts(item.id)
            if not acts:
                return (
                    f"Для работы «{item.work_name}» нужно добавить "
                    "хотя бы один акт."
                )
            if any(act.act_type == "QUALITY_VIOLATION_ACT" for act in acts):
                if item.days_count is None:
                    return (
                        f"Для работы «{item.work_name}» добавлен акт нарушения "
                        "качества, но не указано количество дней "
                        "ненадлежащего качества."
                    )
        return None

    def _validate_local(self) -> bool:
        if not self._current_period_id:
            return False
        error = self._validation_error(self._current_period_id)
        if error:
            QMessageBox.warning(self, "Оценка не готова", error)
            return False
        return True

    def _payload_for_period(
        self,
        period_id: str,
        *,
        show_error: bool = True,
    ) -> dict | None:
        period = self.store.get_period(period_id)
        if period is None:
            return None
        work_list = self.repository.get_work_list(period.work_list_id)
        if work_list is None:
            return None
        house = self.repository.get_house(work_list.house_id)
        if (
            house is None
            or not house.fias_house_guid
            or not house.house_object_guid
            or not work_list.gis_work_list_guid
        ):
            if show_error:
                QMessageBox.warning(
                    self,
                    "Не хватает идентификаторов",
                    "Для обмена с ГИС ЖКХ нужны ФИАС GUID дома, GUID объекта "
                    "жилищного фонда и GUID размещённого перечня.",
                )
            return None

        items = self.store.list_items(period.id)
        acts = {
            item.id: self.store.list_acts(item.id, include_deleted=True)
            for item in items
        }
        payload = build_quality_command_payload(
            fias_house_code=house.fias_house_guid,
            house_object_guid=house.house_object_guid,
            work_list_guid=work_list.gis_work_list_guid,
            year=period.year,
            month=period.month,
            items=items,
            acts_by_item=acts,
        )
        payload["knownEstimationGuid"] = period.estimation_guid
        payload["knownGisStatus"] = period.gis_status
        return payload

    def _payload(self) -> dict | None:
        if not self._current_period_id:
            return None
        return self._payload_for_period(self._current_period_id)

    def _apply_gis_result(self, period_id: str, result: dict) -> None:
        if result.get("cancelled"):
            self.store.mark_cancelled(period_id)
            return

        item_guids = {
            str(row.get("localItemId")): str(row.get("guid"))
            for row in (result.get("items") or [])
            if row.get("localItemId") and row.get("guid")
        }
        act_guids = {}
        for row in result.get("acts") or []:
            local_id = row.get("localActId")
            if local_id:
                act_guids[str(local_id)] = (
                    row.get("contentGuid"),
                    row.get("guid"),
                )
        self.store.set_gis_state(
            period_id,
            reporting_period_guid=result.get("reportingPeriodGuid"),
            estimation_guid=result.get("estimationGuid"),
            gis_status=result.get("gisStatus"),
            item_guids=item_guids,
            act_guids=act_guids,
        )

    def _load_from_gis(self) -> None:
        if not self._current_period_id or not self._current_work_list_id:
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        period = self.store.get_period(self._current_period_id)
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if period is None or work_list is None:
            return
        house = self.repository.get_house(work_list.house_id)
        if house is None:
            return
        fias = house.fias_house_guid or house.gis_house_guid
        if not fias or not work_list.gis_work_list_guid:
            QMessageBox.warning(
                self,
                "Не хватает идентификаторов",
                "Для загрузки оценки нужны ФИАС GUID дома и GUID размещенного перечня.",
            )
            return

        period_id = period.id
        self._integration_busy = True
        self._refresh_gis_buttons()
        self.month_status.setText(
            f"{_MONTHS[period.month - 1]} {period.year}: загрузка оценки из ГИС ЖКХ…"
        )

        worker = GisIntegrationWorker(
            self.integration_gateway,
            "load_quality_assessment",
            {
                "fiasHouseCode": fias,
                "workListGuid": work_list.gis_work_list_guid,
                "year": period.year,
                "month": period.month,
            },
            timeout=150.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            if not result.get("found"):
                QMessageBox.information(
                    self,
                    "Оценка качества не найдена",
                    "Для выбранного перечня и месяца оценка качества "
                    "в ГИС ЖКХ не найдена.",
                )
                return

            items = list(result.get("items") or [])
            acts_count = sum(
                len(row.get("acts") or [])
                for row in items
                if isinstance(row, dict)
            )
            question = (
                f"В ГИС ЖКХ найдена оценка качества.\n"
                f"Работ в оценке: {len(items)}.\n"
                f"Связей с актами: {acts_count}.\n"
                f"Статус: {result.get('gisStatus') or '—'}.\n\n"
            )
            if local_quality_has_data(self.store, period_id):
                question += (
                    "Локальная оценка выбранного месяца будет заменена "
                    "сведениями из ГИС ЖКХ. Продолжить?"
                )
            else:
                question += "Загрузить эти данные в программу?"

            answer = QMessageBox.question(
                self,
                "Загрузить оценку качества из ГИС ЖКХ?",
                question,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

            try:
                applied = apply_remote_quality_assessment(
                    self.store,
                    work_list_id=self._current_work_list_id,
                    year=period.year,
                    month=period.month,
                    remote=result,
                )
            except ValueError as exc:
                QMessageBox.critical(
                    self,
                    "Не удалось применить оценку качества",
                    str(exc),
                )
                return

            self._load_table()
            self._refresh_act_summary()
            self._refresh_period_status()
            self._refresh_year_status()
            QMessageBox.information(
                self,
                "Оценка качества загружена",
                f"Загружено работ: {applied['items']}.\n"
                f"Загружено актов: {applied['acts']}.",
            )

        def progressed(progress: dict) -> None:
            if str(progress.get("phase") or "") == "queued":
                self.month_status.setText(
                    "Ожидание предыдущей операции с ГИС ЖКХ…"
                )

        def failed(message: str) -> None:
            QMessageBox.critical(
                self,
                "Не удалось загрузить оценку качества из ГИС ЖКХ",
                message,
            )

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_period_status()
            self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.progressed.connect(progressed)
        worker.finished.connect(finished)
        worker.start()

    def _save_project(self) -> None:
        if not self._validate_local():
            return
        self._run_gis("save_quality_assessment", "Проект оценки качества сохранён в ГИС ЖКХ.")

    def _publish(self) -> None:
        if not self._validate_local():
            return
        period = (
            self.store.get_period(self._current_period_id)
            if self._current_period_id
            else None
        )
        if period and period.estimation_guid:
            question = (
                "Изменения будут отправлены в ГИС ЖКХ как новая версия "
                "оценки качества. Продолжить?"
            )
            success = "Изменения оценки качества размещены в ГИС ЖКХ."
        else:
            question = (
                "Программа сначала сохранит актуальные данные как проект, "
                "затем разместит их. Продолжить?"
            )
            success = "Оценка качества размещена в ГИС ЖКХ."
        answer = QMessageBox.question(
            self,
            "Разместить оценку качества?",
            question,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_gis("approve_quality_assessment", success)

    def _periods_with_quality_for_year(self, command: str) -> list:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return []
        year = int(self.year_combo.currentData())
        result = []
        for period in self.store.list_periods(
            self._current_work_list_id,
            year=year,
        ):
            if not any(item.included for item in self.store.list_items(period.id)):
                continue
            status = str(period.gis_status or "LOCAL").upper()
            has_remote = bool(period.estimation_guid)
            if command == "save_quality_assessment":
                if has_remote and status not in {"DRAFT", "MODIFIED", "LOCAL"}:
                    continue
            elif command == "approve_quality_assessment":
                if has_remote and status not in {
                    "DRAFT",
                    "MODIFIED",
                    "CANCELLED",
                    "LOCAL",
                }:
                    continue
            result.append(period)
        return result

    def _save_year_projects(self) -> None:
        self._start_year_queue(
            "save_quality_assessment",
            title="Сохранение проектов оценки качества",
            confirmation=(
                "Сохранить проекты оценки качества для всех сформированных "
                "месяцев выбранного года?"
            ),
        )

    def _publish_year(self) -> None:
        self._start_year_queue(
            "approve_quality_assessment",
            title="Размещение оценки качества",
            confirmation=(
                "Разместить оценку качества для всех сформированных месяцев "
                "выбранного года? Месяцы будут обработаны последовательно."
            ),
        )

    def _start_year_queue(
        self,
        command: str,
        *,
        title: str,
        confirmation: str,
    ) -> None:
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return
        periods = self._periods_with_quality_for_year(command)
        if not periods:
            QMessageBox.information(
                self,
                "Нет месяцев для обработки",
                "Нет сформированных месяцев, которым требуется выбранное действие. "
                "Уже размещённые и не изменённые месяцы повторно не отправляются.",
            )
            return

        errors = []
        for period in periods:
            error = self._validation_error(period.id)
            if error:
                errors.append(
                    f"{_MONTHS[period.month - 1]} {period.year}: {error}"
                )
        if errors:
            details = "\n".join(f"• {value}" for value in errors[:6])
            if len(errors) > 6:
                details += f"\n… ещё месяцев: {len(errors) - 6}"
            QMessageBox.warning(
                self,
                "Не все месяцы готовы",
                "Перед массовой отправкой нужно исправить следующие месяцы:\n\n"
                + details,
            )
            return

        answer = QMessageBox.question(
            self,
            title,
            confirmation
            + f"\n\nБудет обработано месяцев: {len(periods)}. "
            "При ошибке очередь остановится на проблемном месяце.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._queue_period_ids = [period.id for period in periods]
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_failed_period_id = None
        self._queue_command = command
        self._queue_title = title
        self._queue_running = True
        self.queue_progress.setVisible(True)
        self.queue_progress.setRange(0, len(self._queue_period_ids))
        self.queue_progress.setValue(0)
        self.continue_queue_button.setVisible(False)
        self.queue_label.setText("Очередь запущена…")
        self._start_next_queue_item()

    def _continue_queue(self) -> None:
        if not self._queue_failed_period_id:
            return
        try:
            failed_index = self._queue_period_ids.index(
                self._queue_failed_period_id
            )
        except ValueError:
            return
        self._queue_index = failed_index
        self._queue_failed_period_id = None
        self._queue_running = True
        self.continue_queue_button.setVisible(False)
        self.queue_label.setText("Продолжаем очередь…")
        self._start_next_queue_item()

    def _start_next_queue_item(self) -> None:
        if self._queue_index >= len(self._queue_period_ids):
            self._queue_running = False
            self._integration_busy = False
            self._refresh_gis_buttons()
            self.queue_progress.setValue(len(self._queue_period_ids))
            self.queue_label.setText(
                f"Очередь завершена. Успешно обработано месяцев: "
                f"{self._queue_successes}."
            )
            self._refresh_year_status()
            if self._current_period_id:
                self._load_table()
                self._refresh_act_summary()
                self._refresh_period_status()
            return

        period_id = self._queue_period_ids[self._queue_index]
        period = self.store.get_period(period_id)
        if period is None or self._queue_command is None:
            self._queue_index += 1
            QTimer.singleShot(0, self._start_next_queue_item)
            return

        action = (
            "Сохраняется проект"
            if self._queue_command == "save_quality_assessment"
            else "Размещается"
        )
        self.queue_label.setText(
            f"{action}: {_MONTHS[period.month - 1]} {period.year} "
            f"({self._queue_index + 1} из {len(self._queue_period_ids)})…"
        )
        self._send_queue_period(period_id)

    def _send_queue_period(self, period_id: str) -> None:
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return
        if self._queue_command is None:
            return
        payload = self._payload_for_period(period_id, show_error=False)
        period = self.store.get_period(period_id)
        if payload is None or period is None:
            self._queue_running = False
            self._queue_failed_period_id = period_id
            self.queue_label.setText(
                "Очередь остановлена: не хватает идентификаторов ГИС ЖКХ."
            )
            self.continue_queue_button.setVisible(True)
            self._refresh_gis_buttons()
            return

        self._integration_busy = True
        self._refresh_gis_buttons()
        command_succeeded = {"value": False}
        worker = GisIntegrationWorker(
            self.integration_gateway,
            self._queue_command,
            payload,
            timeout=180.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            command_succeeded["value"] = True
            self._apply_gis_result(period_id, result)
            self._queue_successes += 1
            self._queue_index += 1
            self.queue_progress.setValue(self._queue_index)
            self._refresh_year_status()
            if self._current_period_id == period_id:
                self._load_table()
                self._refresh_act_summary()
                self._refresh_period_status()

        def failed(message: str) -> None:
            self.store.set_error(period_id, message)
            self._queue_running = False
            self._queue_failed_period_id = period_id
            self.queue_label.setText(
                f"Очередь остановлена на {_MONTHS[period.month - 1]} "
                f"{period.year}: {message}"
            )
            self.continue_queue_button.setText(
                f"Продолжить с {_MONTHS[period.month - 1]} {period.year}"
            )
            self.continue_queue_button.setVisible(True)
            self._refresh_year_status()
            if self._current_period_id == period_id:
                self._refresh_period_status()

        def finished() -> None:
            self._integration_worker = None
            if command_succeeded["value"] and self._queue_failed_period_id is None:
                # Keep all GIS buttons disabled between queue items.
                self._integration_busy = True
                self._refresh_integration_status()
                QTimer.singleShot(250, self._start_next_queue_item)
            else:
                self._integration_busy = False
                self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _cancel(self) -> None:
        if not self._current_period_id:
            return
        period = self.store.get_period(self._current_period_id)
        if period is None or not period.estimation_guid:
            return
        answer = QMessageBox.question(
            self,
            "Аннулировать оценку качества?",
            "ГИС ЖКХ аннулирует размещённую оценку качества выбранного месяца. "
            "Локальные данные останутся в программе, чтобы при необходимости "
            "сформировать оценку заново. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_gis(
            "cancel_quality_assessment",
            "Оценка качества аннулирована в ГИС ЖКХ.",
        )

    def _run_gis(self, command: str, success_message: str) -> None:
        payload = self._payload()
        if payload is None or not self._current_period_id:
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return
        self._integration_busy = True
        self._refresh_gis_buttons()
        period_id = self._current_period_id
        worker = GisIntegrationWorker(
            self.integration_gateway,
            command,
            payload,
            timeout=180.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            self._apply_gis_result(period_id, result)
            self._load_table()
            self._refresh_act_summary()
            self._refresh_period_status()
            self._refresh_year_status()
            QMessageBox.information(self, "ГИС ЖКХ", success_message)

        def failed(message: str) -> None:
            self.store.set_error(period_id, message)
            self._refresh_period_status()
            self._refresh_year_status()
            QMessageBox.critical(self, "Ошибка ГИС ЖКХ", message)

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_integration_status()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def _refresh_period_status(self) -> None:
        if not self._current_period_id:
            self.month_status.setText("Период: —")
            return
        period = self.store.get_period(self._current_period_id)
        if period is None:
            return
        labels = {
            "LOCAL": "локально",
            "DRAFT": "проект в ГИС ЖКХ",
            "APPROVED": "размещено в ГИС ЖКХ",
            "MODIFIED": "изменено локально",
            "CANCELLED": "аннулировано в ГИС ЖКХ",
        }
        status = labels.get(str(period.gis_status or "LOCAL").upper(), str(period.gis_status or "локально"))
        text = f"{_MONTHS[period.month - 1]} {period.year}: {status}"
        if period.gis_last_error:
            text += f" • последняя ошибка: {period.gis_last_error}"
        self.month_status.setText(text)

    def _refresh_year_status(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        year = int(self.year_combo.currentData())
        periods = {
            period.month: period
            for period in self.store.list_periods(self._current_work_list_id, year=year)
        }
        work_list = self.repository.get_work_list(self._current_work_list_id)
        active = set(active_months_for_work_list(work_list, year)) if work_list else set()
        for month, button in self.month_buttons.items():
            if month not in active:
                suffix = "вне периода"
            else:
                period = periods.get(month)
                if period is None:
                    suffix = "—"
                else:
                    status = str(period.gis_status or "LOCAL").upper()
                    if status == "DRAFT":
                        suffix = "проект"
                    elif status == "APPROVED":
                        suffix = "размещено"
                    elif status == "MODIFIED":
                        suffix = "изменено"
                    elif status == "CANCELLED":
                        suffix = "аннулировано"
                    elif period.gis_last_error:
                        suffix = "ошибка"
                    else:
                        touched = any(
                            item.included or self.store.list_acts(item.id)
                            for item in self.store.list_items(period.id)
                        )
                        suffix = "локально" if touched else "—"
            button.setText(f"{_MONTHS[month - 1]}\n{suffix}")
            button.setChecked(month == self._current_month)

    def _refresh_integration_status(self) -> None:
        self._refresh_gis_buttons()

    def _refresh_gis_buttons(self) -> None:
        status = self.integration_gateway.status()
        base_ready = (
            status.running
            and status.helper_connected
            and status.gis_page_ready
            and helper_supports_quality_assessment(status.helper_version)
            and not self._integration_busy
        )
        ready = base_ready and not self._queue_running
        period = (
            self.store.get_period(self._current_period_id)
            if self._current_period_id
            else None
        )
        gis_status = str(period.gis_status or "").upper() if period else ""
        has_remote = bool(period and period.estimation_guid)

        self.load_gis_button.setEnabled(bool(period) and ready)
        self.save_button.setEnabled(
            bool(period)
            and ready
            and (
                not has_remote
                or gis_status in {"DRAFT", "MODIFIED", "LOCAL"}
            )
        )
        self.publish_button.setEnabled(
            bool(period)
            and ready
            and (
                not has_remote
                or gis_status in {"DRAFT", "MODIFIED", "CANCELLED", "LOCAL"}
            )
        )
        self.publish_button.setText(
            "Разместить изменения месяца"
            if has_remote and gis_status == "MODIFIED"
            else "Разместить месяц"
        )
        self.cancel_button.setEnabled(
            bool(period)
            and ready
            and has_remote
            and gis_status in {"DRAFT", "APPROVED", "MODIFIED"}
        )
        self.save_year_button.setEnabled(
            bool(self._current_work_list_id) and ready
        )
        self.publish_year_button.setEnabled(
            bool(self._current_work_list_id) and ready
        )
        self.continue_queue_button.setEnabled(
            base_ready and bool(self._queue_failed_period_id)
        )

    def _set_enabled(self, enabled: bool) -> None:
        self.table.setEnabled(enabled)
        self.include_all_button.setEnabled(enabled)
        self.exclude_all_button.setEnabled(enabled)
        self.month_acts_button.setEnabled(enabled)
        local_enabled = enabled and not self._queue_running
        self.form_month_button.setEnabled(local_enabled)
        self.form_year_button.setEnabled(
            bool(self._current_work_list_id) and not self._queue_running
        )
        self.auto_act_month_button.setEnabled(local_enabled)
        self.auto_act_year_button.setEnabled(
            bool(self._current_work_list_id) and not self._queue_running
        )
        self._refresh_gis_buttons()

    def _relayout_month_buttons(self, columns: int | None = None) -> None:
        if columns is not None:
            self._month_button_columns = max(1, int(columns))
        while self.month_buttons_layout.count():
            self.month_buttons_layout.takeAt(0)
        for index, month in enumerate(range(1, 13)):
            row, column = divmod(index, self._month_button_columns)
            self.month_buttons_layout.addWidget(self.month_buttons[month], row, column)
            self.month_buttons_layout.setColumnStretch(column, 1)

    def _resize_table(self) -> None:
        row_count = self.table.rowCount()
        visible = min(max(row_count, 1), 15)
        row_height = max(24, self.table.verticalHeader().defaultSectionSize())
        header_height = max(26, self.table.horizontalHeader().height())
        self.table.setFixedHeight(
            header_height + visible * row_height + self.table.frameWidth() * 2 + 4
        )
        self.table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if row_count > 15
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def set_view_mode(self, mode: str) -> None:
        columns = 12 if mode == "normal" else 6
        if columns != self._month_button_columns:
            self._relayout_month_buttons(columns)
        self._resize_table()

    def flush_pending_changes(self) -> None:
        """Cells are saved immediately; kept for the main-window hook."""
