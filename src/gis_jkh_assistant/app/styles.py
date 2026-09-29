"""Minimal Windows 11-inspired stylesheet."""

APP_STYLESHEET = """
QWidget {
    font-family: "Segoe UI";
    font-size: 10pt;
    color: #1f1f1f;
    background: #f6f6f6;
}
QMainWindow, QDialog {
    background: #f6f6f6;
}
QFrame#Card, QScrollArea#Card {
    background: #ffffff;
    border: 1px solid #e3e3e3;
    border-radius: 10px;
}
QLabel#Title {
    font-size: 18pt;
    font-weight: 600;
    background: transparent;
}
QLabel#SectionTitle {
    font-size: 11pt;
    font-weight: 600;
    background: transparent;
}
QLabel#Muted {
    color: #666666;
    background: transparent;
}
QLabel#SaveStatus {
    color: #5f5f5f;
    background: transparent;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: #ffffff;
    border: 1px solid #cfcfcf;
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 22px;
    selection-background-color: #cfe8ff;
    selection-color: #111111;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #0067c0;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #c9c9c9;
    border-radius: 7px;
    padding: 7px 13px;
    min-height: 22px;
}
QPushButton:hover {
    background: #f2f2f2;
}
QPushButton:pressed {
    background: #e9e9e9;
}
QPushButton#PrimaryButton {
    color: #ffffff;
    background: #0067c0;
    border-color: #0067c0;
}
QPushButton#PrimaryButton:hover {
    background: #075a9c;
}
QPushButton#DangerButton {
    color: #b42318;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #e3e3e3;
    border-radius: 8px;
    outline: none;
    padding: 4px;
}
QListWidget::item {
    border-radius: 6px;
    padding: 8px;
    margin: 1px;
}
QListWidget::item:selected {
    background: #e6f2ff;
    color: #1f1f1f;
}
QListWidget::item:hover:!selected {
    background: #f3f3f3;
}
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #c7c7c7;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QSplitter::handle {
    background: #e4e4e4;
    width: 1px;
}
"""
