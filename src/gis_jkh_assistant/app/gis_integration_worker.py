"""Qt worker for long-running GIS integration commands."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from gis_jkh_assistant.gis_integration import IntegrationError, LocalIntegrationGateway


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
        self.payload = payload
        self.timeout = float(timeout)

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
