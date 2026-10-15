"""Theme-aware SVG icons adapted from the supplied GIS JKH icon concept."""
from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .styles import THEMES, tokens, color as theme_color

_PATHS = {
    "fees": '<path d="M5 4h14v17H5Z M8 8h8M8 12h5M8 16h8"/><path d="M9 2v4M15 2v4"/>',
    "houses": '<path d="M3 10 12 3l9 7v10H3Z"/><path d="M9 20v-8h6v8"/>',
    "lists": '<path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    "planning": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M8 14h2M14 14h2M8 18h2"/>',
    "fixation": '<path d="M14 3H5v18h14V8ZM14 3v5h5M8 12h7M8 16l2 2 5-5"/>',
    "quality": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z"/>',
    "sync": '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    "upload": '<path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5"/>',
    "act": '<path d="M14 3H5v18h14V8ZM14 3v5h5"/><circle cx="12" cy="14" r="3"/>',
    "add": '<path d="M12 4v16M4 12h16"/>',
    "next": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "danger": '<path d="m12 3 10 18H2ZM12 9v5M12 17v1"/>',
    "files": '<path d="M3 6h7l2 3h9v12H3Z"/>',
    "organization": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h2m4 0h2M8 11h2m4 0h2M10 21v-6h4v6"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "actions": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_up": '<path d="m6 15 6-6 6 6"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "edit": '<path d="m15 4 5 5-11 11-6 1 1-6ZM13 6l5 5"/>',
}



def _organization_svg():
    # Supplied light/dark SVGs; keep their fills, strokes and dark gradient.
    dark = tokens() is THEMES["dark"]
    accent = "url(#d_uo)" if dark else "#0066CC"
    definitions = ('<defs><linearGradient id="d_uo" x1="0%" y1="0%" x2="100%" y2="100%">'
                   '<stop offset="0%" stop-color="#38BDF8"/><stop offset="100%" stop-color="#818CF8"/>'
                   '</linearGradient></defs>') if dark else ""
    side_fill, side_stroke = ("#1E2333", "#475569") if dark else ("#F0F4F8", "#64748B")
    window = "#F8FAFC" if dark else "#0066CC"
    bottom_window = "#38BDF8" if dark else "#1F2937"
    side_window = "#94A3B8" if dark else "#64748B"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">{definitions}'
            f'<path d="M14 9h4.5a1.5 1.5 0 0 1 1.5 1.5V21h-6V9z" fill="{side_fill}" stroke="{side_stroke}" stroke-width="1.6" stroke-linejoin="round"/>'
            f'<rect x="4" y="3" width="10" height="18" rx="2" fill="{accent}" fill-opacity="{0.22 if dark else 0.12}" stroke="{accent}" stroke-width="1.8"/>'
            f'<path d="M7 7h4M7 11h4" stroke="{window}" stroke-width="1.8" stroke-linecap="round"/>'
            f'<path d="M7 15h4" stroke="{bottom_window}" stroke-width="1.8" stroke-linecap="round"/>'
            f'<path d="M16.5 13h1M16.5 17h1" stroke="{side_window}" stroke-width="1.6" stroke-linecap="round"/>'
            f'<path d="M2 21h20" stroke="{accent}" stroke-width="1.8" stroke-linecap="round"/></svg>')

def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    color = color or theme_color("muted_text")
    body = _PATHS.get(name, _PATHS["more"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="1.7" '
           f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    if name == "organizations":
        svg = _organization_svg()
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def set_button_icon(button, name: str, semantic: str = "muted_text", size: int = 20) -> None:
    """Remember SVG intent so changing the theme or text scale redraws it."""
    from PySide6.QtWidgets import QApplication
    button.setProperty("iconName", name)
    button.setProperty("iconSemantic", semantic)
    button.setProperty("iconBaseSize", size)
    app = QApplication.instance()
    scale = float(app.property("uiScale") or 1.0) if app else 1.0
    pixels = round(size * scale)
    button.setIcon(icon(name, theme_color(semantic), pixels))
    button.setIconSize(QSize(pixels, pixels))


def refresh_button_icons(app) -> None:
    for widget in app.allWidgets():
        name = widget.property("iconName")
        if name:
            set_button_icon(widget, name, widget.property("iconSemantic") or "muted_text",
                            int(widget.property("iconBaseSize") or 20))
