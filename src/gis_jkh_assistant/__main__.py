"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gis_jkh_assistant.app.main_window import MainWindow
from gis_jkh_assistant.app.styles import APP_STYLESHEET
from gis_jkh_assistant.database.paths import database_path
from gis_jkh_assistant.database.repository import Repository


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GIS ЖКХ Assistant")
    app.setStyleSheet(APP_STYLESHEET)

    repository = Repository(database_path())
    window = MainWindow(repository)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
