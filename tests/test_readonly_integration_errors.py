"""Read failures keep local data and never imply that a remote write occurred.

Formatter/dispatcher checks use in-memory doubles. Page checks use only the
isolated offscreen workflow fixture and controlled workers; no GIS commands run.
"""

import importlib
import sqlite3
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from gis_jkh_assistant.app.error_messages import READ_ONLY_GIS_COMMANDS, gis_error_text
from gis_jkh_assistant.app.gis_integration_worker import connect_result
from gis_jkh_assistant.app.management_fees_page import ManagementFeesPage

import test_ui_workflow as workflows

desktop = workflows.desktop
qt_app = workflows.qt_app


def assert_read_failure(message):
    assert "локальные данные остаются в программе" in message
    assert "Изменения в ГИС ЖКХ не отправлялись" in message
    assert "повторите загрузку" in message
    assert "перед повторной отправкой" not in message.lower()
    assert "могли сохраниться изменения" not in message


@pytest.mark.parametrize("command", sorted(READ_ONLY_GIS_COMMANDS))
@pytest.mark.parametrize("error", [
    "Помощник браузера не ответил вовремя. Проверьте открытую вкладку ГИС ЖКХ.",
    TimeoutError("timed out"),
])
def test_read_timeout_preserves_local_data_without_remote_write_warning(command, error):
    message = gis_error_text(error, command_type=command, context="Тестовая УО • Дом 1")
    assert_read_failure(message)
    assert "Тестовая УО • Дом 1" in message
    assert "Технические сведения:" in message
    assert "прочитать или записать файл" not in message


@pytest.mark.parametrize("command", [
    "save_work_plan", "save_work_fixation", "save_quality_assessment", "approve_quality_assessment",
    "upload_import_work_list", "save_management_fee_project", "save_and_approve_management_fee",
    "download_work_list_export_template", "future_unknown_command", "",
])
def test_writes_export_jobs_and_unknown_commands_keep_uncertain_outcome_warning(command):
    message = gis_error_text("timeout", command_type=command)
    assert "в ГИС перед повторной отправкой" in message
    assert "могли сохраниться изменения" in message
    assert "Изменения в ГИС ЖКХ не отправлялись" not in message


class CallbackSignal:
    def connect(self, callback):
        self.callback = callback

    def emit(self, value):
        self.callback(value)


@pytest.mark.parametrize("command,read", [("list_managed_houses", True), ("save_work_plan", False), ("", False)])
def test_shared_result_guard_distinguishes_partial_local_apply_from_remote_write(monkeypatch, command, read):
    window = SimpleNamespace(_result_callbacks=0)
    parent = SimpleNamespace(window=lambda: window)
    worker = SimpleNamespace(parent=lambda: parent, command_type=command, payload={})
    signal = CallbackSignal()
    messages = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[2]))

    def fail_apply(_result):
        raise sqlite3.OperationalError("database is locked")

    connect_result(worker, signal, fail_apply)
    signal.emit({"items": []})
    assert worker.result_apply_failed
    assert window._result_callbacks == 0
    assert len(messages) == 1
    message = messages[0]
    if read:
        assert "Некоторые записи могли сохраниться локально" in message
        assert "Изменения в ГИС ЖКХ не отправлялись" in message
        assert "Проверьте локальные данные" in message
        assert "перед повторной отправкой" not in message.lower()
        assert "отменен" not in message.lower() and "отменён" not in message.lower()
    else:
        assert "изменения в ГИС не отменяются автоматически" in message
        assert "Перед повторной отправкой" in message


@pytest.mark.parametrize("command,read", [("load_management_fees", True), ("load_fee_protocols", True),
                                         ("save_management_fee_project", False)])
def test_fee_read_error_does_not_inherit_warning_from_a_pending_old_send(monkeypatch, command, read):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    page = SimpleNamespace(
        _worker=SimpleNamespace(command_type=command),
        _record=lambda: {"pending": True},
        operation_status=SimpleNamespace(outcome=lambda *args, **kwargs: None),
    )
    ManagementFeesPage._failed(page, "timeout")
    if read:
        assert_read_failure(messages[0])
        assert "при этой загрузке" in messages[0]
        assert "Результат прежней отправки ещё не установлен" in messages[0]
        assert "Повторная отправка заблокирована" in messages[0]
    else:
        assert "Изменения в форме сохранены" in messages[0]
        assert "перед повторной отправкой" in messages[0]


@pytest.mark.parametrize("page_name,module_name,method,command", [
    ("houses_page", "houses_page", "_sync_houses_from_gis", "list_managed_houses"),
    ("houses_page", "houses_page", "_sync_all_work_lists_from_gis", "list_work_lists"),
    ("houses_page", "houses_page", "_sync_management_from_gis", "get_house_management_info"),
    ("work_lists_page", "work_lists_page", "_load_house_work_lists_from_gis", "list_work_lists"),
    ("planning_page", "planning_page", "_load_from_gis", "load_work_plan"),
    ("fixation_page", "fixation_page", "_load_selected_month_from_gis", "load_work_fixation"),
    ("quality_page", "quality_page", "_load_from_gis", "load_quality_assessment"),
])
def test_page_read_timeout_uses_read_recovery_and_keeps_existing_content(
    desktop, monkeypatch, page_name, module_name, method, command,
):
    module = importlib.import_module("gis_jkh_assistant.app." + module_name)
    workers, messages = [], []

    def create_worker(*args, **kwargs):
        worker = workflows.ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(module, "GisIntegrationWorker", create_worker)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[2]))
    page = getattr(desktop.window, page_name)
    before = desktop.repository.list_work_list_items(desktop.work_list.id)
    getattr(page, method)()
    assert len(workers) == 1
    worker = workers[0]
    assert worker.command_type == command
    worker.failed.emit("Помощник браузера не ответил вовремя.")
    worker.finish()
    assert len(messages) == 1
    assert_read_failure(messages[0])
    assert desktop.repository.list_work_list_items(desktop.work_list.id) == before


@pytest.mark.parametrize("command,read", [("sync_work_list", True), ("save_editable_work_list", False)])
def test_shared_work_list_dispatcher_classifies_the_actual_command(desktop, monkeypatch, command, read):
    module = importlib.import_module("gis_jkh_assistant.app.work_lists_page")
    workers, messages = [], []

    def create_worker(*args, **kwargs):
        worker = workflows.ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(module, "GisIntegrationWorker", create_worker)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[2]))
    page = desktop.window.work_lists_page
    page._run_integration_command(
        work_list_id=desktop.work_list.id, command_type=command, payload={},
        success_callback=lambda *_args: None, busy_text="Проверка",
    )
    workers[0].failed.emit("timeout")
    workers[0].finish()
    if read:
        assert_read_failure(messages[0])
    else:
        assert "могли сохраниться изменения" in messages[0]


@pytest.mark.parametrize("page_name", ["fixation_page", "quality_page"])
@pytest.mark.parametrize("apply_failure", [False, True])
def test_read_queue_keeps_prior_periods_and_distinguishes_local_apply_failure(
    desktop, monkeypatch, page_name, apply_failure,
):
    page = getattr(desktop.window, page_name)
    module = importlib.import_module("gis_jkh_assistant.app." + page_name)
    workers = []

    def create_worker(*args, **kwargs):
        worker = workflows.ControlledWorker(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(module, "GisIntegrationWorker", create_worker)
    if page_name == "quality_page":
        periods = [page.store.get_or_create_period(work_list_id=desktop.work_list.id, year=2026, month=m)
                   for m in (1, 2)]
        def prior_items():
            return page.store.list_items(periods[0].id)
        page._queue_command = "load_quality_assessment"
        send = page._send_queue_period
        apply_name = "apply_remote_quality_assessment"
    else:
        periods = [desktop.repository.get_or_create_work_fixation_period(
            work_list_id=desktop.work_list.id, year=2026, month=m,
        ) for m in (1, 2)]
        def prior_items():
            return desktop.repository.list_work_fixation_items(periods[0].id)
        page._queue_mode = "load"
        send = page._send_load_period
        apply_name = "apply_remote_work_fixation"
    page._queue_period_ids = [period.id for period in periods]
    page._queue_index = page._queue_successes = 1
    page._queue_failed_period_id = None
    page._queue_running = True
    before = prior_items()
    send(periods[1].id)
    assert len(workers) == 1
    worker = workers[0]
    if apply_failure:
        def fail_apply(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(module, apply_name, fail_apply)
        worker.succeeded.emit({"found": True})
    else:
        worker.failed.emit("timeout")
    worker.finish()
    message = page.queue_label.text()
    assert "Ранее загруженные месяцы сохранены" in message
    assert "Успешные месяцы повторно не отправляются" not in message
    assert "Изменения в ГИС ЖКХ не отправлялись" in message
    assert "перед повторной отправкой" not in message.lower()
    if apply_failure:
        assert "Некоторые записи могли сохраниться локально" in message
    else:
        assert_read_failure(message)
    assert page._queue_index == page._queue_successes == 1
    assert page._queue_failed_period_id == periods[1].id
    assert prior_items() == before
