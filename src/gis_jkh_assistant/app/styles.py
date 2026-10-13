"""One semantic design system shared by light and dark desktop themes."""
from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtGui import QColor, QPalette

THEMES = {
    "light": {
        "app_background": "#f3f5f8", "sidebar": "#eaf0f6", "surface": "#ffffff",
        "elevated_surface": "#f5f7fa", "border": "#c9d3df", "text": "#18283b",
        "muted_text": "#52647a", "accent": "#155aa8", "accent_hover": "#104c90",
        "success": "#167249", "warning": "#936000", "danger": "#b32c36",
        "selection": "#e5effb", "selection_text": "#124d90", "readonly": "#edf1f6",
        "success_surface": "#eaf6ef", "warning_surface": "#fff4dd",
        "danger_surface": "#fff0f1", "on_accent": "#ffffff",
    },
    "dark": {
        "app_background": "#111923", "sidebar": "#15202d", "surface": "#1b2735",
        "elevated_surface": "#223143", "border": "#41536a", "text": "#ecf2fa",
        "muted_text": "#b4c2d5", "accent": "#9bc7ff", "accent_hover": "#b2d4ff",
        "success": "#6ed5a4", "warning": "#f3c477", "danger": "#ff9099",
        "selection": "#263f60", "selection_text": "#dcecff", "readonly": "#263246",
        "success_surface": "#223e37", "warning_surface": "#443925",
        "danger_surface": "#452b37", "on_accent": "#10243b",
    },
}
_theme = "light"
_indicator_cache = None


def indicator_images(theme: str) -> dict[str, str]:
    """Vector indicators cached in OS temp; no external assets or build rules."""
    global _indicator_cache
    if _indicator_cache is None:
        _indicator_cache = TemporaryDirectory(prefix="gis-jkh-ui-")
    values = tokens(theme)
    ring, accent, surface, on_accent = (values[key] for key in ("muted_text", "accent", "surface", "on_accent"))
    bodies = {
        "radio_unchecked": f'<circle cx="12" cy="12" r="9" fill="{surface}" stroke="{ring}" stroke-width="2"/>',
        "radio_checked": f'<circle cx="12" cy="12" r="9" fill="{surface}" stroke="{accent}" stroke-width="2"/><circle cx="12" cy="12" r="5" fill="{accent}"/>',
        "check_unchecked": f'<rect x="3" y="3" width="18" height="18" rx="3" fill="{surface}" stroke="{ring}" stroke-width="2"/>',
        "check_checked": f'<rect x="3" y="3" width="18" height="18" rx="3" fill="{accent}" stroke="{accent}" stroke-width="2"/><path d="m7 12 3 3 7-7" fill="none" stroke="{on_accent}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>',
    }
    images = {}
    for key, body in bodies.items():
        path = Path(_indicator_cache.name) / f"{theme}-{key}.svg"
        if not path.exists():
            path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>', encoding="utf-8")
        images[key] = f'"{path.as_posix()}"'
    return images


def tokens(theme: str | None = None) -> dict[str, str]:
    return THEMES.get(theme or _theme, THEMES["light"])


def color(name: str) -> str:
    return tokens()[name]


_SHEET = """
QWidget { font-family: "Segoe UI", "DejaVu Sans"; font-size: 10pt; color: @text; }
QMainWindow, QDialog, QWidget#PageContent { background: @app_background; }
QWidget#Sidebar { background: @sidebar; border-right: 1px solid @border; }
QStackedWidget, QLabel, QScrollArea#PageScroll { background: transparent; }
QScrollArea#PageScroll { border: none; }
QFrame#Card, QScrollArea#Card, QFrame#ContextBar, QFrame#ActionToolbar,
QFrame#WorkflowPanel, QFrame#SidebarStatusCard, QFrame#CommandGroup {
    background: @surface; border: 1px solid @border; border-radius: 5px;
}
QFrame#HouseSection { background: transparent; border: none; border-bottom: 1px solid @border; }
QFrame#ContextBar { background: transparent; border: none; }
QFrame#SidebarStatusCard { background: transparent; border: none; }
QFrame#WorkflowPanel { border-left: 3px solid @accent; }
QLabel#Title { font-size: 16pt; font-weight: 600; }
QLabel#SectionTitle { font-size: 10pt; font-weight: 600; }
QLabel#Muted, QLabel#SaveStatus, QLabel#ContextCaption, QLabel#ToolbarGroupLabel { color: @muted_text; font-size: 9pt; }
QLabel#ContextCaption, QLabel#SaveStatus { font-size: 9pt; }
QLabel#SaveStatus[saveError="true"] { color: @danger; }
QLabel#BlockingReason { color: @warning; font-size: 9pt; }
QLabel#WorkflowStep { color: @muted_text; padding: 5px 7px; border-radius: 5px; font-size: 9pt; }
QLabel#WorkflowStep[stage="current"] { background: @selection; color: @selection_text; font-weight: 600; }
QLabel#WorkflowStep[stage="done"] { color: @success; }
QFrame#StatusStrip { background: @elevated_surface; border: 1px solid @border; border-radius: 7px; }
QFrame#StatusStrip[tone="danger"] { background: @danger_surface; }
QLabel#StatusStripText { color: @muted_text; font-size: 9pt; }
QLabel#SidebarStatusTitle { font-size: 9pt; font-weight: 600; }
QLabel#SidebarStatusText { font-size: 9pt; }
QLabel#StatusBadge { background: @selection; color: @selection_text; border-radius: 5px; padding: 5px 8px; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateEdit {
    background: @surface; border: 2px solid @border; border-radius: 4px;
    padding: 2px 6px; min-height: 20px;
    selection-background-color: @selection; selection-color: @selection_text;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus { border-color: @accent; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { width: 24px; border: none; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView { background: @surface; color: @text; selection-background-color: @selection; selection-color: @selection_text; }
QSpinBox { min-height: 25px; }
QSpinBox QLineEdit { border: none; padding: 0; min-height: 0; background: transparent; }
QToolButton#SpinPlusButton, QToolButton#SpinMinusButton {
    background: @elevated_surface; border: none; border-left: 1px solid @border;
    padding: 0; font-size: 11pt;
}
QPushButton, QToolButton { background: @elevated_surface; border: 2px solid @border; border-radius: 4px; padding: 2px 8px; min-height: 20px; }
QPushButton:focus, QToolButton:focus { border-color: @accent; }
QPushButton#DisclosureButton { border-color: transparent; background: transparent; color: @muted_text; font-size: 9pt; }
QPushButton#DisclosureButton:focus { border-color: @accent; }
QPushButton:hover, QToolButton:hover { background: @selection; border-color: @accent; }
QPushButton:pressed { background: @readonly; }
QPushButton#PrimaryButton { color: @on_accent; background: @accent; border-color: @accent; font-weight: 600; padding: 2px 10px; }
QPushButton#PrimaryButton:focus { border-color: @text; }
QPushButton#PrimaryButton:hover { background: @accent_hover; }
QPushButton#PrimaryButton:disabled { color: @muted_text; background: @readonly; border-color: @border; }
QPushButton:disabled, QToolButton:disabled { color: @muted_text; background: @readonly; border-color: @border; }
QPushButton#DangerButton { color: @danger; border-color: @danger; }
QPushButton#DangerButton:hover { background: @danger_surface; border-color: @danger; }
QPushButton#MonthButton { padding: 2px 3px; min-height: 28px; font-size: 9pt; }
QPushButton[compactCommand="true"], QComboBox[compactCommand="true"] { font-size: 9pt; min-height: 18px; padding: 1px 5px; }
QLabel#ToolbarGroupLabel { font-size: 9pt; }
QComboBox[compactCommand="true"] { padding-right: 26px; }
QTableWidget[compactTable="true"] { font-size: 9pt; }
QTableWidget[compactTable="true"] QPushButton { font-size: 9pt; min-height: 18px; padding: 1px 5px; }
QPushButton#MonthButton:checked { background: @selection; border: 2px solid @accent; color: @selection_text; }
QPushButton#MonthButton[tone="success"] { color: @success; }
QPushButton#MonthButton[tone="warning"] { color: @warning; }
QPushButton#MonthButton[tone="danger"] { color: @danger; }
QPushButton#MonthButton:disabled { color: @muted_text; background: @readonly; }
QFrame#ScopeSwitcher { background: @readonly; border: 1px solid @border; border-radius: 7px; }
QPushButton#ScopeSwitcherButton { background: transparent; border: 2px solid transparent; padding: 2px 6px; min-height: 20px; color: @muted_text; }
QPushButton#ScopeSwitcherButton:checked { background: @surface; color: @selection_text; border-color: @border; font-weight: 600; }
QPushButton#ScopeSwitcherButton:focus { border-color: @accent; }
QListWidget { background: @surface; border: 1px solid @border; border-radius: 7px; outline: 0; padding: 4px; }
QListWidget::item { border-radius: 6px; padding: 9px 8px; margin: 2px; }
QListWidget::item:selected { background: @selection; color: @selection_text; }
QListWidget::item:hover:!selected { background: @elevated_surface; }
QListWidget#Navigation { background: transparent; border: none; padding: 0; }
QListWidget#Navigation::item { padding: 6px 6px; color: @muted_text; }
QListWidget#Navigation::item:selected { background: @surface; color: @text; font-weight: 600; border-left: 3px solid @accent; }
QTableWidget, QTableView#FrozenName { background: @surface; alternate-background-color: @elevated_surface; border: 1px solid @border; gridline-color: @border; selection-background-color: @selection; selection-color: @selection_text; }
QTableWidget:focus, QListWidget:focus { border-color: @accent; }
QListWidget#Navigation::item:focus { border: 2px solid @accent; }
QHeaderView::section { background: @elevated_surface; border: none; border-right: 1px solid @border; border-bottom: 1px solid @border; padding: 4px; font-size: 9pt; font-weight: 600; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 1px; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 1px; }
QScrollBar::handle { background: @border; border-radius: 4px; min-width: 24px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: @border; width: 1px; }
QProgressBar { background: @readonly; border: 1px solid @border; border-radius: 5px; min-height: 14px; text-align: center; }
QProgressBar::chunk { background: @accent; border-radius: 4px; }
QProgressBar#StatusStripProgress { border: none; border-radius: 2px; min-height: 3px; max-height: 3px; }
QProgressBar#StatusStripProgress::chunk { border-radius: 2px; }
QMenu { background: @surface; color: @text; border: 1px solid @border; padding: 5px; }
QMenu::item { padding: 7px 20px 7px 10px; border-radius: 4px; }
QMenu::item:selected { background: @selection; color: @selection_text; }
QMenu::item:disabled { color: @muted_text; }
QMenu::separator { height: 1px; background: @border; margin: 5px; }
QToolTip { background: @surface; color: @text; border: 1px solid @border; padding: 6px; }
QRadioButton, QCheckBox { spacing: 8px; background: transparent; }
QRadioButton:focus, QCheckBox:focus { background: @selection; color: @selection_text; }
QRadioButton::indicator, QCheckBox::indicator, QTableView::indicator { width: 18px; height: 18px; }
QRadioButton::indicator:unchecked { image: url(@radio_unchecked); }
QRadioButton::indicator:checked { image: url(@radio_checked); }
QCheckBox::indicator:unchecked, QTableView::indicator:unchecked { image: url(@check_unchecked); }
QCheckBox::indicator:checked, QTableView::indicator:checked { image: url(@check_checked); }
"""


def stylesheet(theme: str = "light", scale: float = 1.0) -> str:
    palette = dict(tokens(theme))
    palette.update(indicator_images(theme))
    sheet = re.sub(r"@([a-z_]+)", lambda match: palette[match[1]], _SHEET)
    return re.sub(
        r"(\d+(?:\.\d+)?)(pt|px)",
        lambda match: (f"{round(float(match[1]) * scale)}px" if match[2] == "px"
                       else f"{float(match[1]) * scale:g}pt"), sheet,
    )


def apply_theme(app, theme: str, scale: float = 1.0) -> None:
    global _theme
    _theme = theme if theme in THEMES else "light"
    values = tokens()
    palette = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "app_background"),
        (QPalette.ColorRole.WindowText, "text"), (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Base, "surface"), (QPalette.ColorRole.AlternateBase, "elevated_surface"),
        (QPalette.ColorRole.Button, "elevated_surface"), (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.ToolTipBase, "surface"), (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.Highlight, "selection"), (QPalette.ColorRole.HighlightedText, "selection_text"),
        (QPalette.ColorRole.PlaceholderText, "muted_text"), (QPalette.ColorRole.Link, "accent"),
    ):
        palette.setColor(role, QColor(values[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(values["muted_text"]))
    app.setPalette(palette)
    app.setProperty("uiScale", scale)
    app.setStyleSheet(stylesheet(_theme, scale))
    from .icons import refresh_button_icons
    refresh_button_icons(app)
    from .widgets import refresh_combo_chevrons
    refresh_combo_chevrons(app)


APP_STYLESHEET = stylesheet("light")
COMPACT_WINDOW_STYLESHEET = """
QWidget { font-size: 9pt; }
QLabel#Title { font-size: 17pt; }
QPushButton { padding: 5px 8px; }
QLabel#SectionTitle { font-size: 10pt; }
"""
NARROW_WINDOW_STYLESHEET = COMPACT_WINDOW_STYLESHEET
