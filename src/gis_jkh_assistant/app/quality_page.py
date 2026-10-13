"""Quality assessment page for completed works and services."""

from __future__ import annotations

from .error_messages import context_text, error_text
from .queue_controls import preview as queue_preview

import sqlite3

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.gis_integration import LocalIntegrationGateway
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
from .monthly_commands import operation_months
from gis_jkh_assistant.work_planning import active_months_for_work_list

from .gis_integration_worker import GisIntegrationWorker, connect_result
from .page_layouts import build_monthly, compact_monthly, resize_table_rows, TableHeightObserver
from .workflow_presenter import refresh_monthly
from .quality_bulk_dialogs import QualityMonthActsDialog
from .quality_dialogs import QualityActsDialog


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
        self._queue_skipped = 0
        self._build_ui()
        self.refresh_organizations()

    def _build_ui(self) -> None:
        build_monthly(self, "quality")
        self._height_observer = TableHeightObserver(self.table, self._resize_table)

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
        from .selection_memory import rebuild_years
        rebuild_years(self, self.work_list_combo.currentData())
        self._current_period_id = None
        self._year_changed()

    def _year_changed(self) -> None:
        from .selection_memory import select_year
        select_year(self)
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
        except (sqlite3.DatabaseError, ValueError) as exc:
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
                name.setToolTip(item.work_name)
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
        self._update_selected_act_button()

    def _selected_act_item_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            return None
        cell = self.table.item(row, 0)
        return str(cell.data(_ITEM_ID_ROLE)) if cell and cell.data(_ITEM_ID_ROLE) else None

    def _update_selected_act_button(self) -> None:
        item_id = self._selected_act_item_id()
        self.selected_act_button.setEnabled(bool(
            item_id and self._current_period_id and self.table.isEnabled()
        ))
        row = self.table.currentRow()
        name = self.table.item(row, 1) if row >= 0 else None
        self.selected_act_button.setToolTip(
            f"Акты: {name.text()}" if name else "Выберите работу в таблице"
        )

    def _open_selected_acts(self) -> None:
        item_id = self._selected_act_item_id()
        if item_id and self.selected_act_button.isEnabled():
            self._open_acts(item_id)

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
        except (sqlite3.DatabaseError, ValueError) as exc:
            QMessageBox.warning(self, "Проверьте данные", error_text(exc, context=context_text(self), effect="Правка не подтверждена сохранением. Таблица будет заново прочитана из локальной базы.", recovery="Проверьте значение в строке, устраните причину и повторите ввод."))
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

    def _scope_is_period(self) -> bool:
        return self.scope_switcher.scope() == "period"

    def _scope_changed(self, _scope: str) -> None:
        self._set_enabled(bool(self._current_period_id))

    def _form_scope_from_fixation(self) -> None:
        if self._scope_is_period():
            self._form_year_from_fixation()
        else:
            self._form_month_from_fixation()

    def _auto_generate_scope_act(self) -> None:
        if self._scope_is_period():
            self._auto_generate_year_acts()
        else:
            self._auto_generate_month_act()

    def _clear_scope_values(self) -> None:
        if self._scope_is_period():
            self._clear_year_values()
        else:
            self._clear_month_values()

    def _load_scope_from_gis(self) -> None:
        if self._scope_is_period():
            self._load_year_from_gis()
        else:
            self._load_from_gis()

    def _save_scope(self) -> None:
        if self._scope_is_period():
            self._save_year_projects()
        else:
            self._save_project()

    def _publish_scope(self) -> None:
        if self._scope_is_period():
            self._publish_year()
        else:
            self._publish()

    def _clear_month_values(self) -> None:
        if not self._current_period_id:
            return
        answer = QMessageBox.question(
            self,
            "Очистить данные месяца?",
            "Будут очищены только поля перерасчёта: «Стоимость в ежемесячной "
            "плате», «Дней ненадлежащего качества» и «Уменьшение платы». "
            "Выбор работ и акты сохранятся. Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = self.store.clear_quality_values(self._current_period_id)
        self._load_table()
        self._refresh_period_status()
        self._refresh_year_status()
        QMessageBox.information(
            self,
            "Данные очищены",
            f"Очищено строк: {count}. Акты и включение работ не изменены.",
        )

    def _clear_year_values(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        year = int(self.year_combo.currentData())
        months = self._active_months()
        answer = QMessageBox.question(
            self,
            "Очистить данные доступных месяцев года?",
            f"Поля перерасчёта будут очищены во всех доступных месяцах "
            f"выбранного периода ({len(months)}). Акты и выбор работ сохранятся. "
            "Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        periods = {
            period.month: period
            for period in self.store.list_periods(
                self._current_work_list_id,
                year=year,
            )
        }
        rows = 0
        processed = 0
        for month in months:
            period = periods.get(month)
            if period is None:
                continue
            rows += self.store.clear_quality_values(period.id)
            processed += 1
        self._month_changed()
        self._refresh_year_status()
        QMessageBox.information(
            self,
            "Данные года очищены",
            f"Обработано месяцев: {processed}.\nОчищено строк: {rows}. "
            "Акты и включение работ не изменены.",
        )

    def _active_months(self) -> tuple[int, ...]:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return ()
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return ()
        return operation_months(self,
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
            "Поля перерасчёта останутся пустыми. Фактическая стоимость "
            "используется только при формировании PDF акта выполненных работ.\n\n"
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
            "Операция повторно формирует также ранее заполненные и размещённые месяцы. "
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

    def _auto_generate_month_act(self, *, units_ready=False) -> None:
        if not self._current_period_id:
            return
        if not units_ready and self._sync_work_units_for_act(lambda: self._auto_generate_month_act(units_ready=True)):
            return
        try:
            result = self._auto_generate_act_for_period(self._current_period_id)
        except (sqlite3.DatabaseError, OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "Не удалось сформировать акт",
                error_text(exc, context=context_text(self)),
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

    def _auto_generate_year_acts(self, *, units_ready=False) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        months = self._active_months()
        resume = getattr(self, "_auto_act_resume", None)
        resume_here = bool(resume and resume[:2] == (self._current_work_list_id, int(self.year_combo.currentData())))
        if resume_here:
            months = tuple(month for month in months if month >= resume[2])
        if not units_ready:
            answer = QMessageBox.question(
                self,
                "Сформировать акты за доступные месяцы года?",
                f"Будут обработаны сформированные оценки в доступных месяцах "
                f"выбранного года ({len(months)} мес. в периоде перечня). "
                "Пользовательские и импортированные акты не изменяются. "
                "Ранее сформированные программой акты будут заменены. Продолжить?"
                + (f"\nПродолжение с {_MONTHS[resume[2] - 1]}. Акты предыдущих успешно обработанных месяцев сохраняются."
                   if resume_here else ""),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if not units_ready and self._sync_work_units_for_act(lambda: self._auto_generate_year_acts(units_ready=True)):
            return
        year = int(self.year_combo.currentData())
        created = linked = protected = missing = 0
        processed = 0
        failure = None
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
            except (sqlite3.DatabaseError, OSError, RuntimeError, ValueError) as exc:
                failure = f"{_MONTHS[month - 1]} {year}: {exc}"
                self._auto_act_resume = (self._current_work_list_id, year, month)
                break
            processed += 1
            created += result.created
            linked += result.linked_items
            protected += result.protected_items
            missing += result.missing_costs

        if not failure:
            self._auto_act_resume = None

        self._month_changed()
        self._refresh_year_status()
        notify = QMessageBox.warning if failure else QMessageBox.information
        notify(
            self,
            "Акты сформированы частично" if failure else "Акты за год сформированы",
            f"Обработано месяцев: {processed}.\n"
            f"Сформировано актов: {created}.\n"
            f"Связей с работами: {linked}.\n"
            f"Защищено пользовательскими/импортированными актами: {protected}.\n"
            f"Строк без фактической стоимости: {missing}."
            + (f"\n\nОстановлено: {failure}\nУспешно созданные акты сохранены." if failure else ""),
        )

    def _sync_work_units_for_act(self, continuation=None) -> bool:
        """Prepare PDF metadata asynchronously; False means no lookup is needed."""
        if not self._current_work_list_id:
            return False
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return True
        status = self.integration_gateway.status()
        if not (status.running and status.helper_connected and status.gis_page_ready
                and helper_supports_quality_assessment(status.helper_version)):
            return False
        work_list_id = self._current_work_list_id
        owner_period_id = self._current_period_id
        items = [item for item in self.repository.list_work_list_items(work_list_id)
                 if not str(item.unit_name or "").strip() and (item.work_code or item.work_guid)]
        if not items:
            return False
        payload = {"items": [{"localItemId": i.id, "workCode": i.work_code,
                              "workGuid": i.work_guid} for i in items]}
        self._integration_busy = True
        self._refresh_gis_buttons()
        worker = GisIntegrationWorker(self.integration_gateway, "resolve_planned_work_units",
                                      payload, timeout=45.0, parent=self)
        self._integration_worker = worker
        ready = {"value": False}

        def succeeded(result):
            units = {str(row["localItemId"]): str(row["unitName"]).strip()
                     for row in (result.get("items") or [])
                     if row.get("localItemId") and str(row.get("unitName") or "").strip()}
            self.repository.update_work_list_unit_names(work_list_id, units)
            ready["value"] = True

        def failed(message):
            self.operation_status.outcome(
                "Не удалось уточнить единицы измерения. Локальные акты сохранены. "
                "Повторите подготовку PDF после восстановления связи.\n" + message, tone="danger")

        def finished():
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_gis_buttons()
            if (ready["value"] and not getattr(worker, "result_apply_failed", False)
                    and not getattr(self.window(), "_close_requested", False)):
                self.operation_status.outcome("Единицы измерения проверены.", tone="success")
                if (continuation is not None and self._current_work_list_id == work_list_id
                        and self._current_period_id == owner_period_id):
                    continuation()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        connect_result(worker, worker.finished, finished)
        worker.start()
        return True

    def _open_month_acts(self, *, start_upload: bool = False, start_generate: bool = False) -> None:
        if (
            not self._current_period_id
            or self._current_month is None
            or self.year_combo.currentData() is None
        ):
            return
        dialog = QualityMonthActsDialog(
            self.store,
            period_id=self._current_period_id,
            organization_name=self.organization_combo.currentText(),
            house_address=self.house_combo.currentText(),
            year=int(self.year_combo.currentData()),
            month=int(self._current_month),
            parent=self,
        )
        if start_upload:
            QTimer.singleShot(0, dialog._add_ready)
        elif start_generate:
            QTimer.singleShot(0, dialog._generate)
        dialog.exec()
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()

    def _refresh_act_summary(self) -> None:
        if not self._current_period_id:
            self.month_acts_summary.setText("Актов: —")
            return
        acts = self.store.list_period_acts(self._current_period_id)
        links = sum(self.store.count_act_links(act.id) for act in acts)
        self.month_acts_summary.setText(
            f"Актов: {len(acts)} • Связей: {links}"
        )
        self.month_acts_summary.setToolTip(f"Акты месяца: {len(acts)} • связей с работами: {links}")

    def _open_acts(self, item_id: str) -> None:
        if not self._current_period_id or self._current_month is None:
            return
        dialog = QualityActsDialog(
            self.store,
            item_id=item_id,
            year=int(self.year_combo.currentData()),
            month=int(self._current_month),
            editable=True,
            work_name=next(
                (item.work_name for item in self.store.list_items(self._current_period_id)
                 if item.id == item_id),
                "",
            ),
            parent=self,
        )
        dialog.exec()
        self._load_table()
        self._refresh_act_summary()
        self._refresh_period_status()
        self._refresh_year_status()

    def _validation_error(
        self,
        period_id: str,
        *,
        require_acts: bool,
    ) -> str | None:
        selected = [
            item for item in self.store.list_items(period_id) if item.included
        ]
        if not selected:
            return "Нет работ, включённых в оценку качества."
        if not require_acts:
            return None
        for item in selected:
            acts = self.store.list_acts(item.id)
            if not acts:
                return (
                    f"Для работы «{item.work_name}» нужно добавить "
                    "хотя бы один акт перед размещением."
                )
            if any(act.act_type == "QUALITY_VIOLATION_ACT" for act in acts):
                if item.days_count is None:
                    return (
                        f"Для работы «{item.work_name}» добавлен акт нарушения "
                        "качества, но не указано количество дней "
                        "ненадлежащего качества."
                    )
        return None

    def _validate_local(self, *, require_acts: bool) -> bool:
        if not self._current_period_id:
            return False
        error = self._validation_error(
            self._current_period_id,
            require_acts=require_acts,
        )
        if error:
            title = "Оценка не готова к размещению" if require_acts else "Проект не готов"
            QMessageBox.warning(self, title, error)
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

    def _apply_gis_result(self, period_id: str, result: dict, acknowledged_deletions=None) -> None:
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
            acknowledged_deletions=acknowledged_deletions,
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
                self.repository.set_setting(f"ui.remote_probe.quality.{period.id}", "missing")
                self.operation_status.outcome("Оценка качества в ГИС не найдена. Локальные данные сохранены.")
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
                self.operation_status.outcome("Загрузка оценки отменена. Локальные данные сохранены.")
                return

            try:
                applied = apply_remote_quality_assessment(
                    self.store,
                    work_list_id=self._current_work_list_id,
                    year=period.year,
                    month=period.month,
                    remote=result,
                )
            except (sqlite3.DatabaseError, ValueError) as exc:
                self.operation_status.outcome("Не удалось применить оценку. Изменения импорта отменены; повторите загрузку после устранения ошибки.", tone="danger")
                QMessageBox.critical(
                    self,
                    "Не удалось применить оценку качества",
                    error_text(exc, context=context_text(self)),
                )
                return

            self.repository.set_setting(f"ui.remote_probe.quality.{period.id}", "found")
            self._load_table()
            self._refresh_act_summary()
            self._refresh_period_status()
            self._refresh_year_status()
            self.operation_status.outcome(f"Оценка загружена: работ {applied['items']}, актов {applied['acts']}.", tone="success")
            QMessageBox.information(
                self,
                "Оценка качества загружена",
                f"Загружено работ: {applied['items']}.\n"
                f"Загружено актов: {applied['acts']}."
                + ("\nЛокальные документы и связи сохранены. Оценка содержит локальные изменения."
                   if applied.get("preserved_local") else ""),
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
                error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."),
            )

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_period_status()
            self._refresh_integration_status()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.progressed.connect(progressed)
        worker.finished.connect(finished)
        worker.start()

    def _load_year_from_gis(self) -> None:
        if not self._current_work_list_id or self.year_combo.currentData() is None:
            return
        if self._integration_worker is not None and self._integration_worker.isRunning():
            return

        months = self._active_months()
        if not months:
            return
        answer = QMessageBox.question(
            self,
            "Загрузить оценки качества за доступные месяцы года?",
            f"Будут последовательно проверены {len(months)} мес. выбранного "
            "периода. Найденные в ГИС ЖКХ оценки заменят локальные данные "
            "соответствующих месяцев. Месяцы без оценки будут пропущены. "
            "Продолжить?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        year = int(self.year_combo.currentData())
        periods = [
            self.store.get_or_create_period(
                work_list_id=self._current_work_list_id,
                year=year,
                month=month,
            )
            for month in months
        ]
        self._queue_period_ids = [period.id for period in periods]
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_skipped = 0
        self._queue_failed_period_id = None
        self._stop_queue_requested = False
        self._queue_command = "load_quality_assessment"
        self._queue_title = "Загрузка оценки качества из ГИС ЖКХ"
        self._queue_running = True
        self.queue_status_strip.setVisible(True)
        self.queue_progress.setVisible(True)
        self.queue_progress.setRange(0, len(self._queue_period_ids))
        self.queue_progress.setValue(0)
        self.continue_queue_button.setVisible(False)
        self.queue_label.setText("Очередь загрузки запущена…")
        self._start_next_queue_item()

    def _save_project(self) -> None:
        if not self._validate_local(require_acts=False):
            return
        self._run_gis("save_quality_assessment", "Проект оценки качества сохранён в ГИС ЖКХ.")

    def _publish(self) -> None:
        if not self._validate_local(require_acts=True):
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
        work_list = self.repository.get_work_list(self._current_work_list_id)
        if work_list is None:
            return []
        active_months = set(operation_months(self, work_list, year))
        result = []
        for period in self.store.list_periods(
            self._current_work_list_id,
            year=year,
        ):
            if period.month not in active_months:
                continue
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
                "Сохранить проекты оценки качества для сформированных "
                "месяцев доступного периода выбранного года?"
            ),
        )

    def _publish_year(self) -> None:
        self._start_year_queue(
            "approve_quality_assessment",
            title="Размещение оценки качества",
            confirmation=(
                "Разместить оценку качества для сформированных месяцев "
                "доступного периода выбранного года? Месяцы будут обработаны последовательно."
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

        require_acts = command == "approve_quality_assessment"
        errors = []
        for period in periods:
            error = self._validation_error(
                period.id,
                require_acts=require_acts,
            )
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
            "При ошибке очередь остановится на проблемном месяце."
            + "\n\n" + queue_preview(self, periods),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._queue_period_ids = [period.id for period in periods]
        self._queue_index = 0
        self._queue_successes = 0
        self._queue_skipped = 0
        self._queue_failed_period_id = None
        self._stop_queue_requested = False
        self._queue_command = command
        self._queue_title = title
        self._queue_running = True
        self.queue_status_strip.setVisible(True)
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
        self._stop_queue_requested = False
        self._queue_running = True
        self.continue_queue_button.setVisible(False)
        self.queue_label.setText("Продолжаем очередь…")
        self._start_next_queue_item()

    def _start_next_queue_item(self) -> None:
        from .queue_controls import stop_before_next
        if stop_before_next(self):
            return
        if getattr(self.window(), "_close_requested", False):
            self._queue_running = False
            self._integration_busy = False
            self.queue_label.setText(f"Очередь остановлена при закрытии. Обработано месяцев: {self._queue_successes}.")
            return
        if self._queue_index >= len(self._queue_period_ids):
            self._queue_running = False
            self._integration_busy = False
            self._refresh_gis_buttons()
            self.queue_progress.setValue(len(self._queue_period_ids))
            suffix = (
                f" • не найдено в ГИС ЖКХ: {self._queue_skipped}"
                if self._queue_command == "load_quality_assessment"
                else ""
            )
            self.queue_label.setText(
                f"Очередь завершена. Успешно обработано месяцев: "
                f"{self._queue_successes}{suffix}."
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

        if self._queue_command == "save_quality_assessment":
            action = "Сохраняется проект"
        elif self._queue_command == "load_quality_assessment":
            action = "Загружается из ГИС ЖКХ"
        else:
            action = "Размещается"
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

        period = self.store.get_period(period_id)
        if period is None:
            return

        if self._queue_command == "load_quality_assessment":
            work_list = self.repository.get_work_list(period.work_list_id)
            house = (
                self.repository.get_house(work_list.house_id)
                if work_list is not None
                else None
            )
            fias = (
                (house.fias_house_guid or house.gis_house_guid)
                if house is not None
                else None
            )
            if (
                work_list is None
                or not work_list.gis_work_list_guid
                or not fias
            ):
                payload = None
            else:
                payload = {
                    "fiasHouseCode": fias,
                    "workListGuid": work_list.gis_work_list_guid,
                    "year": period.year,
                    "month": period.month,
                }
        else:
            payload = self._payload_for_period(period_id, show_error=False)

        if payload is None:
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
        timeout = 150.0 if self._queue_command == "load_quality_assessment" else 180.0
        acknowledged_deletions = self.store.deletion_snapshot(payload)
        worker = GisIntegrationWorker(
            self.integration_gateway,
            self._queue_command,
            payload,
            timeout=timeout,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            command_succeeded["value"] = True
            if self._queue_command == "load_quality_assessment":
                if result.get("found"):
                    try:
                        apply_remote_quality_assessment(
                            self.store,
                            work_list_id=period.work_list_id,
                            year=period.year,
                            month=period.month,
                            remote=result,
                        )
                    except (sqlite3.DatabaseError, ValueError) as exc:
                        command_succeeded["value"] = False
                        failed(str(exc))
                        return
                    self.repository.set_setting(f"ui.remote_probe.quality.{period.id}", "found")
                    self._queue_successes += 1
                else:
                    self.repository.set_setting(f"ui.remote_probe.quality.{period.id}", "missing")
                    self._queue_skipped += 1
            else:
                self._apply_gis_result(period_id, result, acknowledged_deletions)
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
                f"{period.year}: {message}\nУспешно: {self._queue_successes} из {len(self._queue_period_ids)}. "
                "Успешные месяцы повторно не отправляются. Устраните причину и продолжите с проблемного месяца."
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
                self._integration_busy = True
                self._refresh_integration_status()
                QTimer.singleShot(250, self._start_next_queue_item)
            else:
                self._integration_busy = False
                self._refresh_integration_status()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
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
        acknowledged_deletions = self.store.deletion_snapshot(payload)
        worker = GisIntegrationWorker(
            self.integration_gateway,
            command,
            payload,
            timeout=180.0,
            parent=self,
        )
        self._integration_worker = worker

        def succeeded(result: dict) -> None:
            self._apply_gis_result(period_id, result, acknowledged_deletions)
            self._load_table()
            self._refresh_act_summary()
            self._refresh_period_status()
            self._refresh_year_status()
            QMessageBox.information(self, "ГИС ЖКХ", success_message)
            self.operation_status.outcome(success_message, tone="success")

        def failed(message: str) -> None:
            self.store.set_error(period_id, message)
            self._refresh_period_status()
            self._refresh_year_status()
            QMessageBox.critical(self, "Ошибка ГИС ЖКХ", error_text(message, context=context_text(self), effect="Обмен не завершён. Состояние сведений в ГИС может отличаться от локального; проверьте его перед повторной отправкой."))

        def finished() -> None:
            self._integration_worker = None
            self._integration_busy = False
            self._refresh_integration_status()

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
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
        refresh_monthly(self, "quality")

    def _refresh_integration_status(self) -> None:
        self._refresh_gis_buttons()

    def _refresh_gis_buttons(self) -> None:
        refresh_monthly(self, "quality")

    def _set_enabled(self, enabled: bool) -> None:
        self.table.setEnabled(enabled)
        self.include_all_button.setEnabled(enabled)
        self.exclude_all_button.setEnabled(enabled)
        self.month_acts_button.setEnabled(enabled)

        period_scope = self._scope_is_period()
        local_month_enabled = enabled and not self._queue_running
        local_period_enabled = (
            bool(self._current_work_list_id) and not self._queue_running
        )
        local_enabled = local_period_enabled if period_scope else local_month_enabled
        self.clear_values_button.setEnabled(local_enabled)
        self.form_month_button.setEnabled(local_enabled)
        self.auto_act_month_button.setEnabled(local_enabled)

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
        resize_table_rows(self.table, 15)

    def set_view_mode(self, mode: str) -> None:
        compact_monthly(self, mode != "normal")
        columns = 12 if mode == "normal" else 6
        if columns != self._month_button_columns:
            self._relayout_month_buttons(columns)
        self._resize_table()

    def flush_pending_changes(self) -> None:
        """Cells are saved immediately; kept for the main-window hook."""
