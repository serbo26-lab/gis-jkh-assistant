"""Theme-aware SVG icons adapted from the supplied GIS JKH icon concept."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
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
}


def icon(name: str, color: str = "#54708e", size: int = 20) -> QIcon:
    body = _PATHS.get(name, _PATHS["more"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="1.7" '
           f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
