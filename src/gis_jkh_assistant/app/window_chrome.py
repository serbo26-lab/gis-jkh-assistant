"""Theme the native Windows caption without replacing its standard controls.

Windows 11 supports caption/text colors through DwmSetWindowAttribute:
https://learn.microsoft.com/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
Other systems, older DWM implementations and offscreen Qt safely keep their
platform decoration. Window dragging, resizing, system menus and accessibility
continue to be provided by the OS.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtGui import QColor, QGuiApplication

from .styles import tokens

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _colorref(value: str) -> int:
    color = QColor(value)
    return color.red() | (color.green() << 8) | (color.blue() << 16)


def caption_attributes(theme: str) -> tuple[tuple[int, int], ...]:
    values = tokens(theme)
    return (
        (DWMWA_USE_IMMERSIVE_DARK_MODE, int(theme == "dark")),
        (DWMWA_CAPTION_COLOR, _colorref(values["app_background"])),
        (DWMWA_TEXT_COLOR, _colorref(values["text"])),
    )


def _dwm_set_attribute():
    function = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
    function.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
    function.restype = ctypes.c_long
    return function


def apply_window_theme(window, theme: str) -> bool:
    """Return whether any caption attribute was accepted by this native window."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return False
    try:
        set_attribute = _dwm_set_attribute()
        window_id = int(window.winId())
        accepted = False
        for attribute, raw_value in caption_attributes(theme):
            value = ctypes.c_uint32(raw_value)
            result = set_attribute(
                window_id, attribute, ctypes.byref(value), ctypes.sizeof(value),
            )
            accepted = result == 0 or accepted
        return accepted
    except (AttributeError, OSError, TypeError, ValueError):
        return False
