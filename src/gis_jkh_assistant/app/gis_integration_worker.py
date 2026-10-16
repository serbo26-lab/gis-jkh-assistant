"""Qt worker for long-running GIS integration commands."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from gis_jkh_assistant.gis_integration import IntegrationError, LocalIntegrationGateway
from gis_jkh_assistant.gis_integration.organization_context import with_organization_context
from .error_messages import context_text, gis_error_text


def connect_result(worker, signal, callback):
    """Hold the repository/parents while a result handler opens a modal dialog."""
    def guarded(*args):
        parent = worker.parent()
        window = parent.window() if parent is not None else None
        if window is not None:
            window._result_callbacks = getattr(window, "_result_callbacks", 0) + 1
        try:
            callback(*args)
        except (sqlite3.DatabaseError, OSError, ValueError) as exc:
            worker.result_apply_failed = True
            detail = gis_error_text(
                exc, command_type=getattr(worker, "command_type", ""),
                context=context_text(parent) if parent else "", result_apply_failed=True,
            )
            strip = getattr(parent, "operation_status", None)
            if strip is not None:
                strip.outcome("Не удалось применить результат\n" + detail, tone="danger")
            if getattr(parent, "_queue_running", False):
                parent._queue_running = False
                ids = getattr(parent, "_queue_period_ids", [])
                index = getattr(parent, "_queue_index", 0)
                if index < len(ids):
                    parent._queue_failed_period_id = ids[index]
                button = getattr(parent, "continue_queue_button", None)
                if button is not None:
                    month, year = worker.payload.get("month"), worker.payload.get("year")
                    button.setText(f"Продолжить с {int(month):02d}.{year}" if month else "Продолжить очередь")
                    button.show()
            QMessageBox.critical(parent, "Не удалось обработать ответ ГИС ЖКХ", detail)
        finally:
            if window is not None:
                window._result_callbacks -= 1
    signal.connect(guarded)


class GisIntegrationWorker(QThread):
    succeeded = Signal(dict)
    failed = Signal(str)
    progressed = Signal(dict)

    def __init__(
        self,
        gateway: LocalIntegrationGateway,
        command_type: str,
        payload: dict,
        *,
        timeout: float = 120.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self.command_type = command_type
        combo = getattr(parent, "organization_combo", None)
        repository = getattr(parent, "repository", None)
        self.payload = (
            with_organization_context(repository, combo.currentData(), payload)
            if command_type != "get_current_organization" and combo is not None and repository is not None
            else dict(payload)
        )
        self.timeout = float(timeout)
        from .operation_presenter import observe_worker
        observe_worker(self, parent)

    def run(self) -> None:
        try:
            result = self.gateway.execute(
                self.command_type,
                self.payload,
                timeout=self.timeout,
                progress_callback=self.progressed.emit,
            )
        except IntegrationError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive GUI boundary
            self.failed.emit(str(exc))
            return

        self.succeeded.emit(result)
