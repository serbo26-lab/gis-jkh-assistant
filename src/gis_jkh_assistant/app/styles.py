"""One semantic design system shared by light and dark desktop themes."""
from __future__ import annotations

import re

from PySide6.QtGui import QColor, QPalette

THEMES = {
    "light": {
        "app_background": "#f3f5f8", "sidebar": "#eaf0f6", "surface": "#ffffff",
        "elevated_surface": "#f7f9fc", "border": "#dce3ec", "text": "#18283b",
        "muted_text": "#5e7086", "accent": "#1769c2", "accent_hover": "#1259a8",
        "success": "#167249", "warning": "#936000", "danger": "#b32c36",
        "selection": "#e5effb", "selection_text": "#124d90", "readonly": "#edf1f6",
        "success_surface": "#eaf6ef", "warning_surface": "#fff4dd",
        "danger_surface": "#fff0f1", "on_accent": "#ffffff",
    },
    "dark": {
        "app_background": "#101620", "sidebar": "#141d2b", "surface": "#1b2636",
        "elevated_surface": "#223044", "border": "#35455c", "text": "#ecf2fa",
        "muted_text": "#afbed1", "accent": "#438ee8", "accent_hover": "#337cd7",
        "success": "#6ed5a4", "warning": "#f3c477", "danger": "#ff9099",
        "selection": "#263f60", "selection_text": "#dcecff", "readonly": "#263246",
        "success_surface": "#223e37", "warning_surface": "#443925",
        "danger_surface": "#452b37", "on_accent": "#ffffff",
    },
}
_theme = "light"


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
QFrame#WorkflowPanel, QFrame#SidebarStatusCard {
    background: @surface; border: 1px solid @border; border-radius: 10px;
}
QFrame#WorkflowPanel { border-left: 3px solid @accent; }
QLabel#Title { font-size: 19pt; font-weight: 600; }
QLabel#SectionTitle { font-size: 11pt; font-weight: 600; }
QLabel#Muted, QLabel#SaveStatus, QLabel#ContextCaption, QLabel#ToolbarGroupLabel { color: @muted_text; }
QLabel#ContextCaption, QLabel#SaveStatus { font-size: 9pt; }
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
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: @surface; border: 1px solid @border; border-radius: 6px;
    padding: 5px 8px; min-height: 22px;
    selection-background-color: @selection; selection-color: @selection_text;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: @accent; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { width: 24px; border: none; border-left: 1px solid @border; }
QComboBox QAbstractItemView { background: @surface; color: @text; selection-background-color: @selection; selection-color: @selection_text; }
QSpinBox { min-height: 25px; }
QToolButton#SpinPlusButton, QToolButton#SpinMinusButton {
    background: @elevated_surface; border: none; border-left: 1px solid @border;
    padding: 0; font-size: 11pt;
}
QPushButton, QToolButton { background: @elevated_surface; border: 1px solid @border; border-radius: 6px; padding: 6px 10px; min-height: 22px; }
QPushButton:hover, QToolButton:hover { background: @selection; border-color: @accent; }
QPushButton:pressed { background: @readonly; }
QPushButton#PrimaryButton { color: @on_accent; background: @accent; border-color: @accent; font-weight: 600; padding: 8px 12px; }
QPushButton#PrimaryButton:hover { background: @accent_hover; }
QPushButton:disabled, QToolButton:disabled { color: @muted_text; background: @readonly; border-color: @border; }
QPushButton#DangerButton { color: @danger; }
QPushButton#DangerButton:hover { background: @danger_surface; border-color: @danger; }
QPushButton#MonthButton { padding: 6px 5px; min-height: 35px; font-size: 9pt; }
QPushButton#MonthButton:checked { background: @selection; border: 2px solid @accent; color: @selection_text; }
QPushButton#MonthButton[tone="success"] { color: @success; }
QPushButton#MonthButton[tone="warning"] { color: @warning; }
QPushButton#MonthButton[tone="danger"] { color: @danger; }
QPushButton#MonthButton:disabled { color: @muted_text; background: @readonly; }
QFrame#ScopeSwitcher { background: @readonly; border: 1px solid @border; border-radius: 7px; }
QPushButton#ScopeSwitcherButton { background: transparent; border: none; padding: 5px 9px; color: @muted_text; }
QPushButton#ScopeSwitcherButton:checked { background: @surface; color: @selection_text; border: 1px solid @border; font-weight: 600; }
QListWidget { background: @surface; border: 1px solid @border; border-radius: 7px; outline: 0; padding: 4px; }
QListWidget::item { border-radius: 6px; padding: 9px 8px; margin: 2px; }
QListWidget::item:selected { background: @selection; color: @selection_text; }
QListWidget::item:hover:!selected { background: @elevated_surface; }
QListWidget#Navigation { background: transparent; border: none; padding: 0; }
QListWidget#Navigation::item { padding: 12px 8px; color: @muted_text; }
QListWidget#Navigation::item:selected { background: @surface; color: @text; font-weight: 600; border-left: 3px solid @accent; }
QTableWidget { background: @surface; alternate-background-color: @elevated_surface; border: 1px solid @border; gridline-color: @border; selection-background-color: @selection; selection-color: @selection_text; }
QHeaderView::section { background: @elevated_surface; border: none; border-right: 1px solid @border; border-bottom: 1px solid @border; padding: 6px; font-weight: 600; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 1px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 1px; }
QScrollBar::handle { background: @border; border-radius: 4px; min-width: 24px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: @border; width: 1px; }
QProgressBar { background: @readonly; border: 1px solid @border; border-radius: 5px; min-height: 14px; text-align: center; }
QProgressBar::chunk { background: @accent; border-radius: 4px; }
QMenu { background: @surface; color: @text; border: 1px solid @border; padding: 5px; }
QMenu::item { padding: 7px 20px 7px 10px; border-radius: 4px; }
QMenu::item:selected { background: @selection; color: @selection_text; }
QMenu::item:disabled { color: @muted_text; }
QMenu::separator { height: 1px; background: @border; margin: 5px; }
QToolTip { background: @surface; color: @text; border: 1px solid @border; padding: 6px; }
QRadioButton, QCheckBox { spacing: 8px; background: transparent; }
"""


def stylesheet(theme: str = "light") -> str:
    palette = tokens(theme)
    return re.sub(r"@([a-z_]+)", lambda match: palette[match[1]], _SHEET)


def apply_theme(app, theme: str) -> None:
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
    app.setStyleSheet(stylesheet(_theme))


APP_STYLESHEET = stylesheet("light")
COMPACT_WINDOW_STYLESHEET = """
QWidget { font-size: 9pt; }
QLabel#Title { font-size: 17pt; }
QPushButton { padding: 5px 8px; }
QLabel#SectionTitle { font-size: 10pt; }
"""
NARROW_WINDOW_STYLESHEET = COMPACT_WINDOW_STYLESHEET
