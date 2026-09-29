"""Windows 11-inspired application stylesheet."""

APP_STYLESHEET = """
QWidget {
    font-family: "Segoe UI";
    font-size: 10pt;
    color: #1f1f1f;
}
QMainWindow, QDialog {
    background: #f3f3f3;
}
QStackedWidget {
    background: transparent;
}
QLabel {
    background: transparent;
}
QFrame#Card, QScrollArea#Card {
    background: #ffffff;
    border: 1px solid #e1e1e1;
    border-radius: 8px;
}
QLabel#Title {
    font-size: 18pt;
    font-weight: 600;
}
QLabel#SectionTitle {
    font-size: 11pt;
    font-weight: 600;
}
QLabel#Muted {
    color: #666666;
}
QLabel#SaveStatus {
    color: #5f5f5f;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    background: #ffffff;
    border: 1px solid #cfcfcf;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 22px;
    selection-background-color: #cfe8ff;
    selection-color: #111111;
}
QSpinBox {
    background: #ffffff;
    border: 1px solid #cfcfcf;
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 26px;
    selection-background-color: #cfe8ff;
    selection-color: #111111;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover {
    border-color: #b8b8b8;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #0067c0;
}
QToolButton#SpinPlusButton, QToolButton#SpinMinusButton {
    background: #fbfbfb;
    border: none;
    border-left: 1px solid #dedede;
    border-radius: 0px;
    padding: 0px;
    font-size: 11pt;
    font-weight: 500;
}
QToolButton#SpinPlusButton:hover, QToolButton#SpinMinusButton:hover {
    background: #f1f1f1;
}
QToolButton#SpinPlusButton:pressed, QToolButton#SpinMinusButton:pressed {
    background: #e8e8e8;
}
QPushButton {
    background: #fbfbfb;
    border: 1px solid #d0d0d0;
    border-radius: 6px;
    padding: 6px 12px;
    min-height: 22px;
}
QPushButton:hover {
    background: #f3f3f3;
    border-color: #c2c2c2;
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
    background: #075fa8;
    border-color: #075fa8;
}
QPushButton#PrimaryButton:pressed {
    background: #005a9e;
}
QPushButton#DangerButton {
    color: #b42318;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #e1e1e1;
    border-radius: 7px;
    outline: none;
    padding: 4px;
}
QListWidget::item {
    border-radius: 5px;
    padding: 8px;
    margin: 1px;
}
QListWidget::item:selected {
    background: #e7f2fb;
    color: #1f1f1f;
}
QListWidget::item:hover:!selected {
    background: #f5f5f5;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #fafafa;
    border: 1px solid #e1e1e1;
    gridline-color: #e2e2e2;
    selection-background-color: #0f78d1;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #f8f8f8;
    border: none;
    border-right: 1px solid #e1e1e1;
    border-bottom: 1px solid #e1e1e1;
    padding: 5px 6px;
    font-weight: 500;
}
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #c9c9c9;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: #aaaaaa;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QSplitter::handle {
    background: #e5e5e5;
    width: 1px;
}
"""

COMPACT_WINDOW_STYLESHEET = """
QWidget {
    font-size: 9pt;
}
QLabel#Title {
    font-size: 16pt;
}
QLabel#SectionTitle {
    font-size: 10pt;
}
QPushButton {
    padding: 5px 9px;
    min-height: 20px;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    padding: 4px 6px;
    min-height: 20px;
}
QSpinBox {
    padding: 3px 6px;
    min-height: 24px;
}
QListWidget::item {
    padding: 6px;
}
"""
