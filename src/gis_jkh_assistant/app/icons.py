"""Original supplied SVGs, selected by theme and shared action meaning."""
from pathlib import Path
from weakref import WeakSet

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit
from shiboken6 import isValid

from .styles import THEMES, tokens, color as theme_color

ASSET_ROOT = Path(__file__).parent / "assets" / "icons"
_icon_actions = WeakSet()
ALIASES = {
    "organizations": "building", "organization": "building", "houses": "home",
    "fees": "housing-fee", "lists": "services", "planning": "calendar", "fixation": "tasks",
    "download": "download-cloud", "upload": "upload-cloud", "add": "plus", "files": "excel",
}
# Existing navigation/disclosure glyphs have no counterpart in the supplied set.
_GLYPHS = {
    "next": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "danger": '<path d="m12 3 10 18H2ZM12 9v5M12 17v1"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "actions": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_up": '<path d="m6 15 6-6 6 6"/>',
}


def asset_path(name: str) -> Path:
    theme = "dark" if tokens() is THEMES["dark"] else "light"
    if name == "app-logo":
        return ASSET_ROOT.parent / "branding" / f"logo-{theme}.svg"
    return ASSET_ROOT / theme / f"{ALIASES.get(name, name)}.svg"


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    path = asset_path(name)
    if path.is_file():
        # The supplied dark files use HTML's lowercase "lineargradient".
        # Standalone SVG is case-sensitive: normalize only that XML tag when
        # reading it, preserving the original file, geometry and all colors.
        svg = path.read_bytes().replace(b"<lineargradient", b"<linearGradient").replace(
            b"</lineargradient", b"</linearGradient")
    else:
        body = _GLYPHS.get(name, _GLYPHS["more"])
        color = color or theme_color("muted_text")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
               f'fill="none" stroke="{color}" stroke-width="1.7" '
               f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()
    # Render the SVG at twice its logical size; QIcon provides the native
    # disabled/active modes. No bitmap assets or system-icon substitutions.
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg)).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def _remember(target, name, semantic, size):
    target.setProperty("iconName", ALIASES.get(name, name))
    target.setProperty("iconSemantic", semantic)
    target.setProperty("iconBaseSize", size)


def _pixels(size):
    app = QApplication.instance()
    return round(size * (float(app.property("uiScale") or 1.0) if app else 1.0))


def set_button_icon(button, name: str, semantic: str = "muted_text", size: int = 18) -> None:
    _remember(button, name, semantic, size)
    pixels = _pixels(size)
    button.setIcon(icon(name, theme_color(semantic), pixels))
    button.setIconSize(QSize(pixels, pixels))


def set_action_icon(action, name: str, size: int = 18, semantic: str = "muted_text") -> None:
    _icon_actions.add(action)
    _remember(action, name, semantic, size)
    action.setIcon(icon(name, theme_color(semantic), _pixels(size)))


def set_label_icon(label, name: str, size: int = 16) -> None:
    _remember(label, name, "muted_text", size)
    pixels = _pixels(size)
    label.setPixmap(icon(name).pixmap(QSize(pixels, pixels), label.devicePixelRatioF()))
    label.setFixedSize(pixels, pixels)


def set_search_icon(edit):
    action = QAction(edit)
    set_action_icon(action, "search", 16)
    edit.addAction(action, QLineEdit.ActionPosition.LeadingPosition)
    action.triggered.connect(lambda _checked=False: edit.setFocus())


def bind_icons(owner, **bindings):
    for attribute, name in bindings.items():
        set_button_icon(getattr(owner, attribute), name)


def refresh_button_icons(app) -> None:
    for widget in app.allWidgets():
        name = widget.property("iconName")
        if name:
            size = int(widget.property("iconBaseSize") or 18)
            if isinstance(widget, QLabel):
                set_label_icon(widget, name, size)
            else:
                set_button_icon(widget, name, widget.property("iconSemantic") or "muted_text", size)
    # Visit only actions explicitly bound by this module. Do not create fresh
    # wrappers for internal Qt actions or retain menus after their owner closes.
    for action in list(_icon_actions):
        if not isValid(action):
            _icon_actions.discard(action)
            continue
        if name := action.property("iconName"):
            set_action_icon(action, name, int(action.property("iconBaseSize") or 18),
                            action.property("iconSemantic") or "muted_text")
