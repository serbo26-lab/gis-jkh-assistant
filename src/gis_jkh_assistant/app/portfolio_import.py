"""Read-only GIS import across an organization's existing work-list periods.

Only the three existing load commands are issued. Local content is never
replaced by this initial-fill workflow; individual pages retain that option.
"""

from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.work_lists.gis_fixation_sync import apply_remote_work_fixation, local_fixation_has_data
from gis_jkh_assistant.work_lists.gis_plan_sync import apply_remote_work_plan, local_plan_has_data
from gis_jkh_assistant.work_lists.gis_quality_sync import apply_remote_quality_assessment, local_quality_has_data
from gis_jkh_assistant.work_planning import period_months

from .gis_integration_worker import GisIntegrationWorker, connect_result

KINDS = {
    "plan": ("планирования", "load_work_plan", 90.0),
    "fixation": ("фиксации", "load_work_fixation", 120.0),
    "quality": ("оценок качества", "load_quality_assessment", 150.0),
}
STATES = {
    "pending": "Будет проверен", "loaded": "Загружено", "missing": "Не найдено",
    "protected": "Пропуск: локальные данные", "unplaced": "Не размещено в ГИС",
    "error": "Ошибка", "stopped": "Остановлено",
}


@dataclass
class ImportJob:
    kind: str
    house_id: str
    address: str
    work_list_id: str
    work_list_guid: str
    list_period: str
    year: int
    month: int | None
    fias: str | None
    state: str = "pending"
    detail: str = ""

    @property
    def period(self):
        return f"{self.month:02d}.{self.year}" if self.month else str(self.year)

    def payload(self):
        result = {"workListGuid": self.work_list_guid, "year": self.year}
        if self.month is not None:
            result.update(month=self.month, fiasHouseCode=self.fias)
        return result


def eligible_lists(repository, organization_id):
    return [
        (house, work_list)
        for house in repository.list_houses(organization_id)
        for work_list in repository.list_work_lists(house.id)
        if work_list.gis_work_list_guid
        and str(work_list.gis_status or "").upper() == "APPROVED"
    ]


def protected_reason(repository, store, job):
    """Inspect without creating periods or modifying their plan snapshots."""
    if job.kind == "plan":
        period = repository.get_work_plan_for_list_year(job.work_list_id, job.year)
        content = period and (
            local_plan_has_data(repository, period.id)
            or any(item.work_list_item_id is None for item in repository.list_work_plan_items(period.id))
        )
    elif job.kind == "fixation":
        period = repository.get_work_fixation_period_for_month(
            job.work_list_id, year=job.year, month=job.month,
        )
        content = period and (
            local_fixation_has_data(repository, period.id)
            or bool(period.managed_unplanned_guids_json)
            or any(item.work_list_item_id is None or repository.list_work_fixation_photos(fixation_item_id=item.id)
                   for item in repository.list_work_fixation_items(period.id))
        )
    else:
        period = store.get_period_for_month(job.work_list_id, year=job.year, month=job.month)
        content = period and (
            local_quality_has_data(store, period.id)
            or store.list_period_acts(period.id, include_deleted=True)
            or any(item.work_list_item_id is None for item in store.list_items(period.id))
        )
    if content or (period and str(period.gis_status or "").upper() in {"LOCAL", "MODIFIED", "ERROR", "CANCELLED", "DRAFT"}):
        return "Уже есть локальные данные или правки. Для замены используйте загрузку в соответствующем разделе."
    return ""


def prepare_jobs(repository, store, organization_id, kind):
    if kind not in KINDS:
        raise ValueError("Неизвестный раздел импорта")
    jobs = []
    for house, work_list in eligible_lists(repository, organization_id):
        points = ([(year, None) for year in range(work_list.start_year, work_list.end_year + 1)]
                  if kind == "plan" else period_months(work_list))
        for year, month in points:
            job = ImportJob(kind, house.id, house.address, work_list.id, work_list.gis_work_list_guid,
                            work_list.period_label, year, month, house.fias_house_guid or house.gis_house_guid)
            reason = protected_reason(repository, store, job)
            if reason:
                job.state, job.detail = "protected", reason
            elif month is not None and not job.fias:
                job.state, job.detail = "error", "У дома нет идентификатора ФИАС. В разделе «Дома» нажмите «Загрузить дома»."
            jobs.append(job)
    return jobs


def apply_job(repository, store, job, remote):
    """Recheck local changes when the response arrives; apply one atomic unit."""
    if not isinstance(remote, dict) or not isinstance(remote.get("found"), bool):
        raise ValueError("Некорректный ответ ГИС: отсутствует признак found.")
    if not remote["found"]:
        job.state, job.detail = "missing", "В ГИС сведения за этот период не найдены. Локальные данные не изменены."
        return
    for field, expected in (("workListGuid", job.work_list_guid), ("year", job.year), ("month", job.month)):
        if remote.get(field) not in (None, "", 0) and str(remote[field]) != str(expected):
            raise ValueError("Ответ ГИС относится к другому перечню или периоду.")
    placed_states = {"APPROVED"} if job.kind == "quality" else {"APPROVED", "SAVED"}
    if str(remote.get("gisStatus") or "").upper() not in placed_states:
        job.state, job.detail = "unplaced", "Пропущен проект, отменённое или неизвестное состояние; импортируются только размещённые сведения."
        return
    with repository.connection.atomic():
        work_list = repository.get_work_list(job.work_list_id)
        house = repository.get_house(job.house_id)
        if (not work_list or not house or work_list.house_id != job.house_id
                or work_list.gis_work_list_guid != job.work_list_guid
                or str(work_list.gis_status or "").upper() != "APPROVED"
                or (job.month is not None and (house.fias_house_guid or house.gis_house_guid) != job.fias)
                or (job.month is None and not work_list.start_year <= job.year <= work_list.end_year)
                or (job.month is not None and (job.year, job.month) not in period_months(work_list))):
            raise ValueError("Перечень или его период изменился во время загрузки. Результат не применён.")
        reason = protected_reason(repository, store, job)
        if reason:
            job.state, job.detail = "protected", reason
            return
        arguments = dict(work_list_id=job.work_list_id, year=job.year, remote=remote)
        if job.kind == "plan":
            apply_remote_work_plan(repository, **arguments)
        elif job.kind == "fixation":
            apply_remote_work_fixation(repository, month=job.month, **arguments)
        else:
            apply_remote_quality_assessment(store, month=job.month, **arguments)
    job.state, job.detail = "loaded", "Размещённые сведения сохранены локально."


class ImportReviewDialog(QDialog):
    def __init__(self, jobs, organization_name, *, preview=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Импорт из ГИС ЖКХ — проверка" if preview else "Импорт из ГИС ЖКХ — результаты")
        self.resize(1000, 600)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"УО: {organization_name}\nДомов: {len({j.house_id for j in jobs})}; "
            f"перечней: {len({j.work_list_id for j in jobs})}; периодов: {len(jobs)}."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        if preview:
            hint = QLabel(
                "Будут последовательно проверены все периоды указанных перечней. "
                "Импортируются только размещённые сведения. Существующие локальные данные, акты и фото "
                "пропускаются без перезаписи. Изменений в ГИС не производится. "
                "Акты и фотографии из ГИС загружаются в объёме существующего помощника: "
                "сведения и ссылки на вложения, без скачивания самих файлов."
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)
        table = QTableWidget(len(jobs), 5)
        self.table = table
        table.setHorizontalHeaderLabels(["Дом", "Перечень", "Период", "Результат", "Подробности"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(False)
        table.verticalHeader().hide()
        for row, job in enumerate(jobs):
            for column, value in enumerate((job.address, job.list_period, job.period, STATES[job.state], job.detail)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((245, 165, 80, 205, 300)):
            table.setColumnWidth(column, width)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel if preview else QDialogButtonBox.StandardButton.Close)
        if preview:
            start = buttons.addButton("Начать импорт", QDialogButtonBox.ButtonRole.AcceptRole)
            start.setEnabled(any(job.state == "pending" for job in jobs))
            buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class PortfolioImport(QObject):
    """Own the continuation timer; hold the page busy until the queue settles."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.jobs = []
        self.worker = None
        self.running = False
        self.stop_requested = False
        self.organization_id = None
        self.organization_name = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)
        self.stop_button = QPushButton("Остановить после текущего запроса")
        self.stop_button.clicked.connect(self.request_stop)
        self.retry_button = QPushButton("Повторить ошибки")
        self.retry_button.clicked.connect(lambda: self._resume({"error"}))
        self.continue_button = QPushButton("Продолжить импорт")
        self.continue_button.clicked.connect(lambda: self._resume({"stopped"}))
        self.results_button = QPushButton("Результаты импорта…")
        self.results_button.clicked.connect(self.show_results)
        for button in (self.stop_button, self.continue_button, self.retry_button, self.results_button):
            page.bulk_status_strip.add_action(button)
            button.hide()

    def _store(self):
        quality = getattr(self.page.window(), "quality_page", None)
        if quality is not None:
            return quality.store
        if not hasattr(self, "_standalone_store"):
            self._standalone_store = QualityAssessmentStore(self.page.repository)
        return self._standalone_store

    def _can_start(self):
        from .registry_workflow import _connection_reason
        page, window = self.page, self.page.window()
        if self.running or page._bulk_operation_name or page._bulk_worker_running():
            return False
        reason = _connection_reason(page)
        if reason:
            QMessageBox.information(page, "Импорт из ГИС ЖКХ", reason)
            return False
        data_pages = getattr(window, "_data_pages", lambda: (page,))()
        if any(getattr(window, "_page_is_busy", lambda p: False)(p) for p in data_pages):
            QMessageBox.information(page, "Выполняется операция", "Дождитесь завершения текущей операции перед массовым импортом.")
            return False
        return all(p.flush_pending_changes() is not False for p in data_pages)

    def start(self, kind):
        if not self._can_start():
            return
        page = self.page
        organization_id = page.organization_combo.currentData()
        if not organization_id:
            return
        try:
            jobs = prepare_jobs(page.repository, self._store(), organization_id, kind)
        except Exception as exc:
            QMessageBox.warning(page, "Не удалось подготовить импорт", str(exc))
            return
        if not jobs:
            QMessageBox.information(page, "Нет перечней для импорта", "Сначала загрузите размещённые перечни выбранной УО из ГИС ЖКХ.")
            return
        dialog = ImportReviewDialog(jobs, page.organization_combo.currentText(), preview=True, parent=page)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if not accepted or not self._can_start():
            return
        # A modal preview can process other UI events; never silently change scope.
        if page.organization_combo.currentData() != organization_id:
            QMessageBox.information(page, "Выбор УО изменился", "Повторите подготовку импорта для выбранной УО.")
            return
        self.jobs = jobs
        self.organization_id = organization_id
        self.organization_name = page.organization_combo.currentText()
        self._begin()

    def _begin(self):
        if not self.page._begin_bulk_operation("portfolio_import", "Подготовка импорта…"):
            return
        self.running, self.stop_requested = True, False
        self.stop_button.show()
        self.stop_button.setEnabled(True)
        for button in (self.continue_button, self.retry_button, self.results_button):
            button.hide()
        self._timer.start(0)

    def _resume(self, states):
        if not self._can_start():
            return
        if self.page.organization_combo.currentData() != self.organization_id:
            QMessageBox.information(self.page, "Другая УО", "Выберите УО предыдущего импорта перед его продолжением.")
            return
        for job in self.jobs:
            if job.state in states:
                job.state, job.detail = "pending", ""
        self._begin()

    def request_stop(self):
        self.stop_requested = True
        self.stop_button.setEnabled(False)
        self.page.bulk_operation_status.setText("Остановка после текущего запроса. Его результат будет сохранён.")

    def _next(self):
        if not self.running:
            return
        if self.stop_requested or getattr(self.page.window(), "_close_requested", False):
            for job in self.jobs:
                if job.state == "pending":
                    job.state = "stopped"
            self._finish()
            return
        job = next((j for j in self.jobs if j.state == "pending"), None)
        if job is None:
            self._finish()
            return
        try:
            reason = protected_reason(self.page.repository, self._store(), job)
            if reason:
                job.state, job.detail = "protected", reason
                self._timer.start(0)
                return
            if job.month is not None and not job.fias:
                raise ValueError("У дома нет идентификатора ФИАС. В разделе «Дома» нажмите «Загрузить дома».")
            if self.page.organization_combo.currentData() != self.organization_id:
                raise ValueError("Выбранная УО изменилась; запрос не отправлен.")
            _, command, timeout = KINDS[job.kind]
            worker = GisIntegrationWorker(self.page.integration_gateway, command, job.payload(), timeout=timeout, parent=self.page)
        except Exception as exc:
            job.state, job.detail = "error", str(exc)
            self._timer.start(0)
            return
        self.worker = worker

        def received(result):
            try:
                apply_job(self.page.repository, self._store(), job, result)
            except Exception as exc:
                job.state, job.detail = "error", str(exc)

        def failed(message):
            job.state, job.detail = "error", message

        def finished():
            if job.state == "pending":
                job.state, job.detail = "error", "Запрос завершился без результата."
            self.worker = None
            worker.deleteLater()
            self._timer.start(0)

        connect_result(worker, worker.succeeded, received)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        worker.progressed.connect(lambda _: self._progress(job))
        self._progress(job)
        worker.start()

    def _progress(self, job):
        completed = sum(j.state != "pending" for j in self.jobs)
        self.page.bulk_status_strip.show_progress(len(self.jobs), completed)
        self.page.bulk_operation_status.setText(
            f"Импорт {KINDS[job.kind][0]} • {completed}/{len(self.jobs)} • {job.address} • {job.period}"
        )

    def _finish(self):
        # The existing bulk status setter processes Qt events. Keep the window
        # from closing its repository until this final UI update returns.
        window = self.page.window()
        window._result_callbacks = getattr(window, "_result_callbacks", 0) + 1
        try:
            self._finish_ui()
        finally:
            window._result_callbacks -= 1

    def _finish_ui(self):
        self.running = False
        counts = Counter(job.state for job in self.jobs)
        text = (
            f"Импорт {'остановлен' if counts['stopped'] else 'завершён'}. "
            f"Загружено: {counts['loaded']}; не найдено: {counts['missing']}; "
            f"локальные данные пропущены: {counts['protected']}; не размещено: {counts['unplaced']}; "
            f"ошибки: {counts['error']}; не выполнено: {counts['stopped']}."
        )
        self.page._workflow_busy = False
        self.page._finish_bulk_operation(text)
        self.page.bulk_status_strip.outcome(text, tone="warning" if counts['error'] or counts['stopped'] else "success")
        self.stop_button.hide()
        self.retry_button.setVisible(bool(counts['error']))
        self.continue_button.setVisible(bool(counts['stopped']))
        self.results_button.show()

    def show_results(self):
        dialog = ImportReviewDialog(self.jobs, self.organization_name, parent=self.page)
        dialog.exec()
        dialog.deleteLater()
