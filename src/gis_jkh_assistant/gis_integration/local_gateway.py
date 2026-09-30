"""Loopback-only message gateway used by the optional GIS helper."""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


_CLIENT_HEADER = "X-GIS-JKH-Helper"
_CLIENT_VALUE = "desktop-helper"


class IntegrationError(RuntimeError):
    """Raised when a GIS integration command cannot be completed."""


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    running: bool
    helper_connected: bool
    gis_page_ready: bool
    page_url: str | None = None
    helper_version: str | None = None
    last_seen_age: float | None = None
    error: str | None = None


class _GatewayState:
    def __init__(self) -> None:
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self.results: dict[str, queue.Queue[dict[str, Any]]] = {}
        self.lock = threading.Lock()
        self.last_seen: float | None = None
        self.gis_page_ready = False
        self.page_url: str | None = None
        self.helper_version: str | None = None

    def update_hello(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.last_seen = time.monotonic()
            self.gis_page_ready = bool(payload.get("gisPageReady"))
            self.page_url = _optional_text(payload.get("pageUrl"))
            self.helper_version = _optional_text(payload.get("helperVersion"))

    def status(self, running: bool, error: str | None) -> IntegrationStatus:
        with self.lock:
            age = None if self.last_seen is None else max(0.0, time.monotonic() - self.last_seen)
            connected = age is not None and age < 8.0
            return IntegrationStatus(
                running=running,
                helper_connected=connected,
                gis_page_ready=connected and self.gis_page_ready,
                page_url=self.page_url,
                helper_version=self.helper_version,
                last_seen_age=age,
                error=error,
            )


class LocalIntegrationGateway:
    """Exchange short-lived commands over localhost without persisting web data."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = int(port)
        self._state = _GatewayState()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def start(self) -> None:
        if self._server is not None:
            return

        state = self._state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def _allowed(self) -> bool:
                return self.headers.get(_CLIENT_HEADER) == _CLIENT_VALUE

            def _headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    f"Content-Type, {_CLIENT_HEADER}",
                )
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self._headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                value = json.loads(raw.decode("utf-8"))
                return value if isinstance(value, dict) else {}

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(HTTPStatus.NO_CONTENT)
                self._headers()
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                if not self._allowed():
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "helper header missing"})
                    return

                try:
                    payload = self._read_json()
                except (ValueError, UnicodeDecodeError):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                    return

                path = urlparse(self.path).path
                if path == "/api/integration/hello":
                    state.update_hello(payload)
                    self._send_json(HTTPStatus.OK, {"ok": True})
                    return

                if path == "/api/integration/result":
                    command_id = _optional_text(payload.get("id"))
                    if not command_id:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "id required"})
                        return
                    with state.lock:
                        target = state.results.get(command_id)
                    if target is not None:
                        try:
                            target.put_nowait(payload)
                        except queue.Full:
                            pass
                    self._send_json(HTTPStatus.OK, {"ok": True})
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

            def do_GET(self) -> None:  # noqa: N802
                if not self._allowed():
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "helper header missing"})
                    return

                if urlparse(self.path).path != "/api/integration/next":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return

                try:
                    command = state.commands.get(timeout=15.0)
                except queue.Empty:
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self._headers()
                    self.end_headers()
                    return
                self._send_json(HTTPStatus.OK, command)

        try:
            server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            self._error = str(exc)
            return

        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="gis-jkh-local-integration",
            daemon=True,
        )
        self._thread.start()
        self._error = None

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def status(self) -> IntegrationStatus:
        return self._state.status(self._server is not None, self._error)

    def execute(
        self,
        command_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        if self._server is None:
            detail = f": {self._error}" if self._error else ""
            raise IntegrationError(f"Локальный шлюз интеграции не запущен{detail}.")

        status = self.status()
        if not status.helper_connected:
            raise IntegrationError(
                "Помощник браузера не подключен. Откройте ГИС ЖКХ в браузере "
                "и проверьте установку расширения."
            )
        if not status.gis_page_ready:
            raise IntegrationError(
                "Откройте личный кабинет ГИС ЖКХ в браузере и обновите страницу."
            )

        command_id = str(uuid.uuid4())
        result_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

        with self._state.lock:
            self._state.results[command_id] = result_queue
        self._state.commands.put(
            {"id": command_id, "type": str(command_type), "payload": payload or {}}
        )

        try:
            response = result_queue.get(timeout=float(timeout))
        except queue.Empty as exc:
            raise IntegrationError(
                "Помощник браузера не ответил вовремя. Проверьте открытую вкладку ГИС ЖКХ."
            ) from exc
        finally:
            with self._state.lock:
                self._state.results.pop(command_id, None)

        if not bool(response.get("ok")):
            raise IntegrationError(
                _optional_text(response.get("error"))
                or "Неизвестная ошибка интеграции с ГИС ЖКХ."
            )

        result = response.get("result")
        return result if isinstance(result, dict) else {}


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
