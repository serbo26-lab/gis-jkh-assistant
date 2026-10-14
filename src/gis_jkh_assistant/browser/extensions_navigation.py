"""Open the extensions page in a newly created, verified Windows browser window.

No profiles, policies or developer-mode preferences are edited. The Qt caller
polls the session; launching and waiting never sleep on the GUI thread.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time

from .helper_installation import BROWSERS, Browser


@dataclass(frozen=True)
class NavigationResult:
    state: str
    message: str = ""


class ExtensionsNavigator:
    def __init__(self, browser: Browser, backend, *, clock=time.monotonic):
        canonical = next((item for item in BROWSERS if item.key == browser.key), None)
        if canonical is None or browser.extensions_url != canonical.extensions_url:
            raise ValueError("Выберите Chrome, Edge или Яндекс Браузер из списка.")
        exe = browser.executable
        if (exe is None or not exe.is_absolute() or not exe.is_file()
                or exe.name.casefold() != canonical.executable_name.casefold()):
            raise FileNotFoundError(f"Укажите файл {canonical.executable_name}.")
        self.browser, self.backend, self.clock = browser, backend, clock
        self.origin = backend.foreground()
        self.previous = set(backend.windows(exe))
        self.deadline = clock() + 10
        self.target = None
        self.sent = False
        self.result = NavigationResult("waiting", "Открываем страницу расширений…")
        # A dedicated blank window keeps existing GIS tabs out of the macro.
        backend.launch(exe)

    def cancel(self):
        if self.result.state == "waiting":
            self.result = NavigationResult("manual", "Автоматический переход остановлен.")

    def _manual(self, reason):
        self.result = NavigationResult("manual", reason)
        return self.result

    def poll(self):
        if self.result.state != "waiting":
            return self.result
        if self.clock() >= self.deadline:
            return self._manual("Не удалось подтвердить открытие страницы. Откройте её по адресу ниже.")
        windows = set(self.backend.windows(self.browser.executable))
        foreground = self.backend.foreground()
        if self.target is None:
            new = windows - self.previous
            if len(new) > 1:
                return self._manual("Открылось несколько окон браузера. Выберите нужное и вставьте адрес ниже.")
            if not new:
                if foreground != self.origin and foreground not in windows:
                    return self._manual("Вы перешли в другое окно. Откройте расширения вручную по адресу ниже.")
                return self.result
            self.target = next(iter(new))
        if self.target not in windows:
            return self._manual("Окно браузера закрыто. Откройте браузер и вставьте адрес ниже.")
        if not self.sent:
            if foreground != self.target:
                if foreground != self.origin:
                    return self._manual("Вы перешли в другое окно. Автоматический ввод остановлен.")
                self.backend.activate(self.target)
                return self.result
            if self.backend.modifiers_pressed():
                return self.result
            title = self.backend.title(self.target).casefold()
            if not any(word in title for word in (
                "about:blank", "без названия", "безназвания", "безымян", "безымён", "без имени",
                "untitled", "пустая страница", "blank page", "новая вкладка", "new tab",
            )):
                # Profile pickers and sign-in windows share the Chromium class.
                return self.result
            # The backend rechecks foreground and process identity immediately
            # before inserting one serial input batch.
            if not self.backend.navigate(self.target, self.browser.executable, self.browser.extensions_url):
                return self._manual("Windows не разрешила автоматический ввод. Вставьте адрес ниже вручную.")
            self.sent = True
            return self.result
        title = self.backend.title(self.target).casefold()
        if any(word in title for word in ("расширения", "дополнения", "extensions", "add-ons")):
            self.result = NavigationResult("opened", "Страница расширений открыта.")
            return self.result
        if foreground != self.target:
            return self._manual("Адрес введён. Если страница не открылась, вставьте его вручную.")
        return self.result


def start_extensions_navigation(browser):
    if os.name != "nt":
        return None
    return ExtensionsNavigator(browser, WindowsBrowserBackend())


class WindowsBrowserBackend:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.user = c.WinDLL("user32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "GetForegroundWindow": ([], w.HWND),
            "SetForegroundWindow": ([w.HWND], w.BOOL),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, c.POINTER(w.DWORD)], w.DWORD),
            "GetClassNameW": ([w.HWND, w.LPWSTR, c.c_int], c.c_int),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, c.c_int], c.c_int),
            "GetAsyncKeyState": ([c.c_int], c.c_short),
            "EnumWindows": ([self.callback_type, w.LPARAM], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL

    def foreground(self):
        return self.user.GetForegroundWindow()

    def _matches(self, window, executable):
        c, w = self.c, self.w
        if not self.user.IsWindowVisible(window):
            return False
        name = c.create_unicode_buffer(256)
        self.user.GetClassNameW(window, name, len(name))
        if name.value != "Chrome_WidgetWin_1":
            return False
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(window, c.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            image, size = c.create_unicode_buffer(32768), w.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, image, c.byref(size)):
                return False
            return os.path.normcase(str(Path(image.value).resolve())) == os.path.normcase(str(executable.resolve()))
        finally:
            self.kernel.CloseHandle(handle)

    def windows(self, executable):
        found = []
        @self.callback_type
        def visit(window, _data):
            if self._matches(window, executable):
                found.append(window)
            return True
        self.user.EnumWindows(visit, 0)
        return found

    @staticmethod
    def launch(executable):
        subprocess.Popen([str(executable), "--new-window", "about:blank"], shell=False,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def activate(self, window):
        self.user.SetForegroundWindow(window)

    def modifiers_pressed(self):
        return any(self.user.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def title(self, window):
        buffer = self.c.create_unicode_buffer(1024)
        self.user.GetWindowTextW(window, buffer, len(buffer))
        return buffer.value

    def navigate(self, window, executable, address):
        c, w = self.c, self.w
        class Mouse(c.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                        ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", c.c_size_t)]
        class Keyboard(c.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", c.c_size_t)]
        class Hardware(c.Structure):
            _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]
        class Payload(c.Union):
            _fields_ = [("mi", Mouse), ("ki", Keyboard), ("hi", Hardware)]
        class Input(c.Structure):
            _fields_ = [("type", w.DWORD), ("payload", Payload)]
        def key(vk=0, scan=0, flags=0):
            return Input(1, Payload(ki=Keyboard(vk, scan, flags, 0, 0)))
        events = [key(0x11), key(0x4C), key(0x4C, flags=2), key(0x11, flags=2)]
        for char in address:
            events.extend((key(scan=ord(char), flags=4), key(scan=ord(char), flags=6)))
        events.extend((key(0x0D), key(0x0D, flags=2)))
        self.user.SendInput.argtypes = [w.UINT, c.POINTER(Input), c.c_int]
        self.user.SendInput.restype = w.UINT
        if (self.foreground() != window or not self._matches(window, executable)
                or self.modifiers_pressed()):
            return False
        inputs = (Input * len(events))(*events)
        count = self.user.SendInput(len(inputs), inputs, c.sizeof(Input))
        if count != len(inputs):
            # Never leave an injected Ctrl key pressed after partial input.
            release = (Input * 3)(key(0x11, flags=2), key(0x4C, flags=2), key(0x0D, flags=2))
            self.user.SendInput(len(release), release, c.sizeof(Input))
            return False
        return True
