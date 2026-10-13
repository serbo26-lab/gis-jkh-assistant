"""Qt wizard regressions using isolated data and local status only."""

from dataclasses import replace
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest

from gis_jkh_assistant.app import helper_setup_dialog as setup
from gis_jkh_assistant.browser.helper_installation import BROWSERS, read_bundle
from gis_jkh_assistant.gis_integration import IntegrationStatus

import test_ui_workflow as workflows

desktop = workflows.desktop
qt_app = workflows.qt_app
_settle = workflows._settle


@pytest.fixture
def wizard(qt_app, tmp_path):
    state = SimpleNamespace(status=IntegrationStatus(True, False, False), reads=0)

    def status():
        state.reads += 1
        return state.status

    gateway = SimpleNamespace(status=status, execute=lambda *a, **k: pytest.fail("No GIS commands"))
    dialog = setup.HelperSetupDialog(gateway, root=tmp_path / "helper", browsers=BROWSERS)
    dialog.show()
    _settle(qt_app)
    yield SimpleNamespace(app=qt_app, dialog=dialog, state=state, root=tmp_path / "helper")
    dialog.close()
    dialog.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _settle(qt_app)


def select_and_prepare(wizard, index=0):
    dialog = wizard.dialog
    dialog.browser_combo.setCurrentIndex(index)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    _settle(wizard.app)
    assert dialog._step == 1
    return dialog


@pytest.mark.parametrize("index", range(3))
def test_three_steps_prepare_copy_and_check_without_launching_or_gis(wizard, monkeypatch, index):
    monkeypatch.setattr(setup, "open_extensions_page", lambda *_: pytest.fail("Launch must be explicit"))
    assert not wizard.dialog.next.isEnabled(), "Several browsers require an explicit choice"
    dialog = select_and_prepare(wizard, index)
    path = Path(dialog.path_edit.text())
    assert path.is_dir()
    assert dialog.extensions_url.text() == BROWSERS[index].extensions_url
    assert not dialog.open_browser.isEnabled(), "Missing browser still allows manual installation"
    assert dialog.yandex_notice.isVisible() == (index == 2)
    QTest.mouseClick(dialog.copy_path, Qt.MouseButton.LeftButton)
    assert wizard.app.clipboard().text() == str(path)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    _settle(wizard.app)
    assert dialog._step == 2
    assert wizard.state.reads > 0
    assert "ожидается" in dialog.connection_status.text()
    wizard.state.status = IntegrationStatus(True, True, True, helper_version="0.1.30")
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert "подключён" in dialog.connection_status.text()
    assert "УО проверьте" in dialog.connection_status.text()
    assert dialog._step == 2, "A connected helper does not prove installation in the selected browser"
    QTest.mouseClick(dialog.back, Qt.MouseButton.LeftButton)
    assert dialog._step == 1
    assert Path(dialog.path_edit.text()) == path
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert path.is_dir()
    assert not dialog.timer.isActive()


def test_connection_waiting_timeout_and_gateway_exception_are_recoverable(wizard):
    dialog = select_and_prepare(wizard)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    dialog._waiting_since = time.monotonic() - 31
    dialog._refresh_connection()
    assert "пока не обнаружено" in dialog.connection_status.text()
    dialog.gateway.status = lambda: (_ for _ in ()).throw(OSError("isolated status failure"))
    dialog._refresh_connection()
    assert "не завершена" in dialog.connection_status.text()
    assert dialog.close_button.isEnabled()


def test_preparation_failure_keeps_step_and_allows_retry(wizard, monkeypatch):
    original = setup.prepare_helper
    monkeypatch.setattr(setup, "prepare_helper", lambda *a: (_ for _ in ()).throw(PermissionError("denied")))
    dialog = wizard.dialog
    dialog.browser_combo.setCurrentIndex(0)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    _settle(wizard.app)
    assert dialog._step == 0
    assert dialog.prepared_path is None
    assert dialog.errors[0].isVisible()
    assert dialog.next.isEnabled()
    assert "прежние версии сохранены" in dialog.errors[0].text()
    monkeypatch.setattr(setup, "prepare_helper", original)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 1
    assert not dialog.errors[0].isVisible()


def test_corrupt_resource_can_be_retried_without_false_prepared_message(wizard, monkeypatch):
    dialog = wizard.dialog
    dialog.bundle = None
    monkeypatch.setattr(setup, "read_bundle", lambda: (_ for _ in ()).throw(ValueError("broken archive")))
    dialog.browser_combo.setCurrentIndex(0)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 0
    assert dialog.prepared_path is None
    assert dialog.next.isEnabled()
    assert dialog.errors[0].isVisible()
    assert not wizard.root.exists()
    monkeypatch.setattr(setup, "read_bundle", read_bundle)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 1


def test_browser_file_selection_cancel_wrong_file_and_success(wizard, tmp_path, monkeypatch):
    dialog = wizard.dialog
    dialog.browser_combo.setCurrentIndex(0)
    monkeypatch.setattr(setup.QFileDialog, "getOpenFileName", lambda *a: ("", ""))
    dialog._choose_browser()
    assert dialog._browser().executable is None
    wrong = tmp_path / "wrong.exe"
    wrong.write_bytes(b"never run")
    monkeypatch.setattr(setup.QFileDialog, "getOpenFileName", lambda *a: (str(wrong), ""))
    dialog._choose_browser()
    assert dialog.errors[0].isVisible()
    assert dialog._browser().executable is None
    correct = tmp_path / "chrome.exe"
    correct.write_bytes(b"never run")
    monkeypatch.setattr(setup.QFileDialog, "getOpenFileName", lambda *a: (str(correct), ""))
    dialog._choose_browser()
    assert dialog._browser().executable == correct
    assert not dialog.errors[0].isVisible()
    assert dialog.open_browser.isEnabled()


def test_browser_and_folder_launch_errors_preserve_prepared_files(wizard, tmp_path, monkeypatch):
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"never run")
    wizard.dialog.browsers[0] = replace(BROWSERS[0], executable=executable)
    dialog = select_and_prepare(wizard)
    monkeypatch.setattr(setup, "open_extensions_page", lambda *a: (_ for _ in ()).throw(FileNotFoundError("deleted executable")))
    QTest.mouseClick(dialog.open_browser, Qt.MouseButton.LeftButton)
    assert dialog.errors[1].isVisible()
    assert "Ctrl+L" in dialog.errors[1].text()
    monkeypatch.setattr(setup, "reveal_helper_folder", lambda *_: False)
    monkeypatch.setattr(setup.QDesktopServices, "openUrl", lambda *_: False)
    QTest.mouseClick(dialog.open_folder, Qt.MouseButton.LeftButton)
    assert "проводнике" in dialog.errors[1].text()
    assert dialog.prepared_path.is_dir()
    assert dialog._step == 1


@pytest.mark.parametrize("status, fragments", [
    (IntegrationStatus(False, False, False, error="port busy"), ("не запущен", "port busy")),
    (IntegrationStatus(True, False, False), ("ожидается", "расширение включено")),
    (IntegrationStatus(True, True, False), ("не определена", "Нужна версия")),
    (IntegrationStatus(True, True, True, helper_version="0.1.27"), ("Нужна версия 0.1.30", "УО проверьте")),
    (IntegrationStatus(True, True, False, helper_version="0.1.30"), ("подключён", "Вкладка ГИС не найдена")),
    (IntegrationStatus(True, True, True, helper_version="0.1.31"), ("подключён", "Версия подготовленных файлов")),
])
def test_status_reports_local_observations_not_browser_or_authentication(status, fragments):
    message = setup.connection_message(status, "0.1.30")
    assert all(fragment in message for fragment in fragments)
    assert "установка завершена" not in message.lower()
    assert "авторизация успешна" not in message.lower()


def test_settings_button_opens_single_dialog_and_reopens_status_timer(desktop, tmp_path, monkeypatch):
    original = setup.prepare_helper_near_program
    monkeypatch.setattr(setup, "prepare_helper_near_program",
                        lambda bundle, _directory=None: original(bundle, tmp_path / "private-helper"))
    window = desktop.window
    window.navigation.setCurrentRow(5)
    _settle(desktop.app)
    before_context = window._normalized_context(window._shared_context)
    QTest.mouseClick(window.settings_page.helper_setup_button, Qt.MouseButton.LeftButton)
    _settle(desktop.app)
    dialog = window._helper_setup_dialog
    assert dialog.isVisible()
    assert dialog.gateway is desktop.gateway
    dialog.browser_combo.setCurrentIndex(0)
    QTest.keyClick(dialog.next, Qt.Key.Key_Return)
    _settle(desktop.app)
    assert dialog._step == 1
    assert dialog.prepared_path == tmp_path / "private-helper" / "browser-extension"
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.timer.isActive()
    window.settings_page.helper_setup_button.click()
    _settle(desktop.app)
    assert window._helper_setup_dialog is dialog
    assert dialog.timer.isActive()
    assert window.navigation.count() == 6
    assert window._normalized_context(window._shared_context) == before_context
    dialog.close()


@pytest.mark.parametrize("busy", ["worker", "callback", "page", "bulk"])
def test_settings_does_not_open_helper_during_active_exchange(desktop, monkeypatch, busy):
    window = desktop.window
    messages = []
    monkeypatch.setattr(workflows.QMessageBox, "information", lambda *a: messages.append(a[2]))
    if busy == "worker":
        window._active_workers.add(object())
    elif busy == "callback":
        window._result_callbacks = 1
    elif busy == "page":
        # Use an existing page busy marker, rather than creating a test-only one.
        window.fixation_page._workflow_busy = True
    else:
        window.houses_page._bulk_operation_name = "isolated fake bulk"
    try:
        window._open_helper_setup()
        assert getattr(window, "_helper_setup_dialog", None) is None
        assert len(messages) == 1
        assert "Дождитесь завершения обмена" in messages[0]
    finally:
        window._active_workers.clear()
        window._result_callbacks = 0
        window.fixation_page._workflow_busy = False
        window.houses_page._bulk_operation_name = None


@pytest.mark.parametrize("deleted", [False, True])
def test_reopen_revalidates_ready_files_without_automatic_writes(wizard, deleted):
    dialog = select_and_prepare(wizard)
    path = dialog.prepared_path
    dialog.close()
    if deleted:
        for name in ("manifest.json", "background.js"):
            (path / name).unlink()
        path.rmdir()
    else:
        (path / "background.js").write_bytes(b"changed; keep these contents")
    before = {str(p): p.read_bytes() for p in wizard.root.rglob("*") if p.is_file()}
    dialog.reopen()
    dialog.show()
    _settle(wizard.app)
    after = {str(p): p.read_bytes() for p in wizard.root.rglob("*") if p.is_file()}
    assert before == after
    assert dialog._step == 0
    assert dialog.prepared_path is None
    assert not dialog.prepared_label.text()
    assert not dialog.files_status.text()
    assert dialog.errors[0].isVisible()
    assert str(path) in dialog.errors[0].text()
    if deleted:
        QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
        assert dialog._step == 1, "Restoration requires the explicit prepare action"


def test_deleted_files_cannot_advance_to_connection_with_ready_claim(wizard):
    dialog = select_and_prepare(wizard)
    (dialog.prepared_path / "background.js").unlink()
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 0
    assert dialog.prepared_path is None
    assert not dialog.files_status.text()


def test_manual_recheck_revalidates_prepared_files(wizard):
    dialog = select_and_prepare(wizard)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 2
    file = dialog.prepared_path / "background.js"
    file.write_bytes(b"changed after the connection step was opened")
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 0
    assert dialog.prepared_path is None
    assert not dialog.files_status.text()
    assert file.read_bytes() == b"changed after the connection step was opened"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_minimum_dialog_keeps_all_footer_captions_readable(wizard, theme, scale):
    from gis_jkh_assistant.app.styles import apply_theme
    from gis_jkh_assistant.app.ui_scale import apply_layout_scale
    apply_theme(wizard.app, theme, scale)
    dialog = wizard.dialog
    apply_layout_scale(dialog, scale)
    dialog._fit_footer()
    dialog.resize(540, 360)
    _settle(wizard.app)
    dialog = select_and_prepare(wizard)
    _settle(wizard.app)
    for button in (dialog.back, dialog.next, dialog.close_button):
        assert button.width() >= button.sizeHint().width()
        assert dialog.rect().contains(button.geometry())
    apply_theme(wizard.app, "light")


def test_open_button_copies_internal_url_and_explains_manual_navigation(wizard, tmp_path, monkeypatch):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"test executable; no process will be started")
    wizard.dialog.browsers[0] = replace(BROWSERS[0], executable=exe)
    dialog = select_and_prepare(wizard)
    calls = []
    monkeypatch.setattr(setup, "open_extensions_page", lambda browser: calls.append(browser.key))
    QTest.mouseClick(dialog.open_browser, Qt.MouseButton.LeftButton)
    assert calls == ["chrome"]
    assert wizard.app.clipboard().text() == "chrome://extensions/"
    assert "Ctrl+L" in dialog.browser_steps.text()
    assert "Ctrl+V" in dialog.browser_steps.text()
    # The user sees the browser step before the folder path.
    assert dialog.open_browser.y() < dialog.path_edit.y()


def test_single_exe_fallback_prompts_visible_directory(wizard, tmp_path, monkeypatch):
    dialog = wizard.dialog
    # Simulate the ordinary distribution path, not the fixture's explicit root.
    dialog.root = None
    original = setup.prepare_helper_near_program
    output = tmp_path / "Documents"
    output.mkdir()
    attempts = []
    def prepare(bundle, directory=None):
        attempts.append(directory)
        if directory is None:
            raise PermissionError("program folder read-only")
        return original(bundle, directory)
    monkeypatch.setattr(setup, "prepare_helper_near_program", prepare)
    monkeypatch.setattr(setup.QFileDialog, "getExistingDirectory", lambda *a: str(output))
    dialog.browser_combo.setCurrentIndex(0)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 1
    assert attempts == [None, output]
    assert dialog.prepared_path == output / "browser-extension"
    assert dialog.selected_directory == output
    assert (dialog.prepared_path / "manifest.json").is_file()


def test_cancel_alternative_folder_keeps_error_visible(wizard, monkeypatch):
    dialog = wizard.dialog
    dialog.root = None
    monkeypatch.setattr(setup, "prepare_helper_near_program",
                        lambda *a: (_ for _ in ()).throw(PermissionError("read-only")))
    monkeypatch.setattr(setup.QFileDialog, "getExistingDirectory", lambda *a: "")
    dialog.browser_combo.setCurrentIndex(0)
    QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
    assert dialog._step == 0
    assert dialog.errors[0].isVisible()
    assert "доступное место" in dialog.errors[0].text()


@pytest.mark.parametrize("index, caption, advice", [
    (0, "рекомендуется", "сохранял"),
    (1, "поддерживается", "спросить"),
    (2, "не рекомендуется", "принудительно"),
])
def test_browser_recommendations_are_visible_before_installation(wizard, index, caption, advice):
    dialog = wizard.dialog
    dialog.browser_combo.setCurrentIndex(index)
    assert caption in dialog.browser_combo.itemText(index)
    assert advice in dialog.browser_advice.text()
    assert "найден" in dialog.browser_combo.itemText(index) or "не найден" in dialog.browser_combo.itemText(index)


@pytest.mark.parametrize("index", range(3))
def test_browser_shortcuts_explain_address_bar_paste_and_navigation(wizard, tmp_path, monkeypatch, index):
    dialog = wizard.dialog
    browser = BROWSERS[index]
    exe = tmp_path / browser.executable_name
    exe.write_bytes(b"test executable")
    dialog.browsers[index] = replace(browser, executable=exe)
    dialog.browser_combo.setCurrentIndex(index)
    assert dialog.open_browser.isEnabled()
    # PR #58 keeps one caption; browser-specific steps still explain the result.
    assert "Открыть браузер" == dialog.open_browser.text()
    assert "Копировать адрес расширений" == dialog.copy_url.text()
    instructions = dialog.browser_steps.text()
    assert "Ctrl+L" in instructions and "адресная строка" in instructions
    assert "Ctrl+V" in instructions and "вставится адрес" in instructions
    assert "Enter" in instructions and "откроет страницу" in instructions
    select_and_prepare(wizard, index)
    calls = []
    monkeypatch.setattr(setup, "open_extensions_page", lambda candidate: calls.append(candidate.key))
    QTest.mouseClick(dialog.open_browser, Qt.MouseButton.LeftButton)
    assert calls == [browser.key]
    assert wizard.app.clipboard().text() == browser.extensions_url
    if index == 0:
        assert "Chrome" in dialog.feedback.text()
    else:
        assert "Браузер запущен" in dialog.feedback.text()


def test_copy_address_explains_shortcuts_without_launching_browser(wizard, monkeypatch):
    dialog = select_and_prepare(wizard, 1)
    monkeypatch.setattr(setup, "open_extensions_page", lambda *_: pytest.fail("No launch when copying"))
    QTest.mouseClick(dialog.copy_url, Qt.MouseButton.LeftButton)
    assert wizard.app.clipboard().text() == "edge://extensions/"
    assert "адресная строка" in dialog.feedback.text()
    assert "вставить адрес" in dialog.feedback.text()
