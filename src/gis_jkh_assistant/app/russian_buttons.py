"""Localize Qt standard buttons without depending on optional translation assets."""
from PySide6.QtCore import QTranslator


class RussianButtons(QTranslator):
    def translate(self, context, sourceText, disambiguation=None, n=-1):  # noqa: N802
        if context in {"QPlatformTheme", "QMessageBox", "QDialogButtonBox"}:
            return {"Yes": "Да", "&Yes": "Да", "No": "Нет", "&No": "Нет",
                    "Cancel": "Отмена", "&Cancel": "Отмена", "Close": "Закрыть",
                    "Save": "Сохранить", "Open": "Открыть", "Discard": "Не сохранять",
                    "Apply": "Применить", "Reset": "Сбросить", "Retry": "Повторить",
                    "Abort": "Прервать", "Ignore": "Пропустить", "Help": "Справка",
                    "Yes to All": "Да для всех", "No to All": "Нет для всех"}.get(sourceText, "")
        return ""


def install(app):
    if not hasattr(app, "_russian_buttons"):
        app._russian_buttons = RussianButtons(app)
        app.installTranslator(app._russian_buttons)
