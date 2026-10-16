"""Sequential fee exchange; uncertain writes are reconciled, never auto-retried."""
from collections import Counter
import sqlite3

from PySide6.QtCore import QObject, QTimer, Signal

from gis_jkh_assistant.fee_bulk import record_signature, transfer_reason
from gis_jkh_assistant.gis_integration import IntegrationError
from gis_jkh_assistant.management_fees import document_from_remote, fingerprint

from .gis_integration_worker import GisIntegrationWorker, connect_result

LABELS = {"ready": "Готов", "waiting": "В очереди", "running": "Выполняется",
          "done": "Выполнено", "error": "Ошибка", "uncertain": "Проверьте результат в ГИС",
          "stopped": "Не выполнено", "blocked": "Пропущено", "review": "Загружено частично"}
COMMANDS = {"load": "load_management_fees", "save": "save_management_fee_project",
            "approve": "approve_management_fee", "save_approve": "save_and_approve_management_fee"}


class FeeQueue(QObject):
    changed = Signal()
    settled = Signal()

    def __init__(self, page):
        super().__init__(page)
        self.page, self.jobs, self.worker = page, [], None
        self.running = self.stop_requested = False
        self._advance = QTimer(self)
        self._advance.setSingleShot(True)
        self._advance.timeout.connect(self._next)

    def start(self, jobs):
        if self.running or getattr(self.page.window(), "_close_requested", False) or getattr(self.page.window(), "_closing", False):
            return False
        selected = [job for job in jobs if job.state == "ready"]
        if not selected:
            return False
        self.jobs = selected
        return self._begin()

    def resume(self):
        if self.running or not any(job.state == "stopped" for job in self.jobs):
            return False
        for job in self.jobs:
            if job.state == "stopped":
                job.state = "ready"
        return self._begin()

    def _begin(self):
        self.running, self.stop_requested = True, False
        self.page._queue_running = True
        for job in self.jobs:
            if job.state == "ready":
                job.state, job.detail = "waiting", ""
        self._notify()
        self._advance.start(0)
        return True

    def request_stop(self):
        self.stop_requested = True
        self._notify()

    def summary(self):
        counts = Counter(job.state for job in self.jobs)
        return (f"Выполнено: {counts['done']} из {len(self.jobs)}; частично: {counts['review']}; ошибок: {counts['error']}; "
                f"результат не установлен: {counts['uncertain'] + sum(job.unresolved for job in self.jobs)}; "
                f"не выполнено: {counts['waiting'] + counts['stopped']}.")

    def _notify(self):
        self.page._refresh_integration_status()
        self.changed.emit()

    def _next(self):
        if not self.running or self.worker is not None:
            return
        page = self.page
        if self.stop_requested or getattr(page.window(), "_close_requested", False):
            self._finish()
            return
        job = next((job for job in self.jobs if job.state == "waiting"), None)
        if job is None:
            self._finish()
            return
        worker = None
        try:
            house = page.repository.get_house(job.house_id)
            if (not house or house.organization_id != job.organization_id
                    or page.organization_combo.currentData() != job.organization_id):
                raise ValueError("УО или дом изменились. Подготовьте очередь заново.")
            if not house.fias_house_guid or house.fias_house_guid != job.fias:
                raise ValueError("Код ФИАС дома изменился или отсутствует. Подготовьте очередь заново.")
            payload = {"fiasHouseGuid": house.fias_house_guid}
            if job.action != "load":
                record = page.store.get(job.record_id)
                if record is None:
                    raise ValueError("Проект удалён. Подготовьте очередь заново.")
                reason = transfer_reason(page.repository, record, job.organization_id, job.action)
                if reason:
                    raise ValueError(reason)
                if record_signature(record) != job.signature:
                    raise ValueError("Проект изменился после проверки. Подготовьте очередь заново.")
                remote = record["remote"] or {}
                payload.update(document=record["document"], contract=record["document"]["contract"],
                               remoteGuid=record["remote_guid"], lastUpdate=remote.get("lastUpdate"))
                # The marker is committed before dispatch and survives restart.
                page.store.mark_pending(job.record_id)
            try:
                worker = GisIntegrationWorker(page.integration_gateway, COMMANDS[job.action], payload,
                                              timeout=180, parent=page)
            except Exception:
                # No worker exists, so no request could have been dispatched.
                if job.action != "load":
                    with page.repository.connection:
                        page.repository.connection.execute(
                            "UPDATE management_fee_records SET pending=0 WHERE id=?", (job.record_id,))
                raise
        except (ValueError, sqlite3.Error, RuntimeError, IntegrationError) as exc:
            job.state, job.detail = "error", str(exc)
            self.stop_requested = True
            self._notify()
            self._advance.start(0)
            return
        self.worker = worker
        job.state, job.detail = "running", "Запрос выполняется."
        state = {"received": False, "finished": False, "processing": False, "released": False}

        def release():
            if state["released"] or not state["finished"] or state["processing"]:
                return
            state["released"] = True
            if not state["received"]:
                failed("Запрос завершился без результата.")
            self.worker = None
            worker.deleteLater()
            self._advance.start(0)

        def succeeded(result):
            state["received"], state["processing"] = True, True
            try:
                if job.action == "load":
                    if any(item.get("fiasHouseGuid") != house.fias_house_guid for item in result["contracts"]):
                        raise ValueError("ГИС вернула договор другого дома.")
                    page.store.import_remote(job.house_id, result)
                    job.detail = f"Загружено договоров: {len(result['contracts'])}; сведений: {len(result['records'])}."
                    job.unresolved = sum(bool(row["pending"]) for row in page.store.list(job.house_id))
                    if job.unresolved:
                        job.detail += f" Отправок с непроверенным результатом: {job.unresolved}. Проверьте их в личном кабинете."
                else:
                    expected = "DRAFT" if job.action == "save" else "APPROVED"
                    unchanged_published = job.action == "save" and result["record"].get("status") == "APPROVED"
                    if result["record"].get("status") != expected and not unchanged_published:
                        raise ValueError("ГИС вернула другое состояние. Загрузите сведения и проверьте результат.")
                    actual = document_from_remote(result["record"], result["contract"])
                    if fingerprint(actual) != fingerprint(job.document):
                        raise ValueError("Ответ ГИС не совпадает с проверенным проектом.")
                    page.store.apply_remote(job.record_id, result["record"], result["contract"])
                    job.detail = ("Изменений нет: проверенные данные уже размещены в ГИС." if unchanged_published else
                                  "Проект сохранён в ГИС." if job.action == "save" else "ГИС подтвердила размещение.")
                job.state = "review" if job.action == "load" and result.get("contractErrors") else "done"
                if job.state == "review":
                    job.detail += " Не прочитаны договоры: " + "; ".join(
                        f"№ {item.get('number', 'без номера')}: {item.get('message', 'ошибка')}"
                        for item in result["contractErrors"])
            except (ValueError, sqlite3.Error, KeyError, TypeError) as exc:
                job.state = "error" if job.action == "load" else "uncertain"
                job.detail = str(exc)
                self.stop_requested = True
            finally:
                state["processing"] = False
                self._notify()
                release()

        def failed(message):
            state["received"] = True
            job.state = "error" if job.action == "load" else "uncertain"
            job.detail = str(message)
            if job.action != "load":
                job.detail += " Загрузите сведения из ГИС; автоматический повтор отправки запрещён."
            self.stop_requested = True
            self._notify()

        def finished():
            state["finished"] = True
            # Result handlers and the main-window observer settle first.
            QTimer.singleShot(0, release)

        connect_result(worker, worker.succeeded, succeeded)
        connect_result(worker, worker.failed, failed)
        worker.finished.connect(finished)
        self._notify()
        try:
            worker.start()
        except RuntimeError as exc:
            failed(str(exc))
            worker.finished.emit()

    def _finish(self):
        for job in self.jobs:
            if job.state == "waiting":
                job.state = "stopped"
        self.running = self.page._queue_running = False
        self.page._refresh_table(self.page._record_id)
        counts = Counter(job.state for job in self.jobs)
        self.page.operation_status.outcome(self.summary(), tone="warning" if any(
            counts[key] for key in ("error", "uncertain", "stopped", "review")) or any(job.unresolved for job in self.jobs) else "success")
        self._notify()
        self.settled.emit()
