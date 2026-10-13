"""Browser navigation invariants; no real browser, clipboard or GIS is used."""
from dataclasses import replace

import pytest

from gis_jkh_assistant.browser.extensions_navigation import ExtensionsNavigator
from gis_jkh_assistant.browser.helper_installation import BROWSERS


class Backend:
    def __init__(self):
        self.visible, self.focus, self.caption = {10}, 1, "about:blank — Browser"
        self.held = False
        self.launches, self.inputs, self.activations = [], [], []
        self.input_allowed = True

    def foreground(self):
        return self.focus

    def windows(self, executable):
        return self.visible

    def launch(self, executable):
        self.launches.append(executable)

    def activate(self, window):
        self.activations.append(window)
        self.focus = window

    def modifiers_pressed(self):
        return self.held

    def title(self, window):
        return self.caption

    def navigate(self, window, executable, address):
        if self.input_allowed:
            self.inputs.append((window, executable, address))
            self.caption = "Расширения — Browser"
        return self.input_allowed


@pytest.fixture
def session(tmp_path):
    def create(index=0):
        canonical = BROWSERS[index]
        exe = tmp_path / canonical.executable_name
        exe.write_bytes(b"not executed")
        browser = replace(canonical, executable=exe)
        backend, time = Backend(), [0.0]
        navigator = ExtensionsNavigator(browser, backend, clock=lambda: time[0])
        return navigator, backend, time, browser
    return create


@pytest.mark.parametrize("browser", range(3))
def test_only_new_verified_window_receives_one_internal_url(session, browser):
    nav, backend, _, selected = session(browser)
    assert nav.poll().state == "waiting"
    assert not backend.inputs  # Existing browser/GIS window is never used.
    backend.visible.add(20)
    assert nav.poll().state == "waiting"
    assert backend.activations == [20]
    nav.poll()
    assert backend.inputs == [(20, selected.executable, selected.extensions_url)]
    assert nav.poll().state == "opened"
    nav.poll()
    assert len(backend.inputs) == 1


def test_other_application_focus_cancels_before_typing(session):
    nav, backend, _, _ = session()
    backend.visible.add(20)
    backend.focus = 99
    assert nav.poll().state == "manual"
    assert not backend.inputs and not backend.activations


def test_existing_window_timeout_does_not_replace_its_tab(session):
    nav, backend, time, _ = session()
    time[0] = 11
    assert nav.poll().state == "manual"
    assert not backend.inputs


def test_profile_picker_never_receives_keys(session):
    nav, backend, time, _ = session()
    backend.visible.add(20)
    backend.focus, backend.caption = 20, "Выберите профиль — Chrome"
    nav.poll()
    assert not backend.inputs
    time[0] = 11
    assert nav.poll().state == "manual"


def test_multiple_new_windows_are_ambiguous(session):
    nav, backend, _, _ = session()
    backend.visible.update((20, 30))
    assert nav.poll().state == "manual"
    assert not backend.inputs


def test_held_keys_wait_and_cancel_stops_future_input(session):
    nav, backend, _, _ = session()
    backend.visible.add(20)
    backend.focus, backend.held = 20, True
    assert nav.poll().state == "waiting"
    assert not backend.inputs
    nav.cancel()
    backend.held = False
    assert nav.poll().state == "manual"
    assert not backend.inputs


def test_closed_target_does_not_switch_to_an_existing_window(session):
    nav, backend, _, _ = session()
    backend.visible.add(20)
    nav.poll()
    backend.visible.remove(20)
    assert nav.poll().state == "manual"
    assert not backend.inputs


def test_blocked_input_is_not_retried(session):
    nav, backend, _, _ = session()
    backend.visible.add(20)
    backend.focus, backend.input_allowed = 20, False
    assert nav.poll().state == "manual"
    backend.input_allowed = True
    nav.poll()
    assert not backend.inputs


def test_arbitrary_urls_and_wrong_executable_are_rejected_before_launch(session):
    _, backend, _, browser = session()
    backend.launches.clear()
    with pytest.raises(ValueError):
        ExtensionsNavigator(replace(browser, extensions_url="https://example.com"), backend)
    with pytest.raises(FileNotFoundError):
        ExtensionsNavigator(replace(browser, executable=browser.executable.parent), backend)
    assert not backend.launches
