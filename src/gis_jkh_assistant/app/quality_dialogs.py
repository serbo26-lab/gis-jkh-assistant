"""Dialogs for quality-assessment acts."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore


_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".rtf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".jpeg",
    ".jpg",
    ".tiff",
}
_MAX_FILE_SIZE = 50 * 1024 * 1024


class AddQualityActDialog(QDialog):
    def __init__(self, *, year: int, month: int, parent=None) -> None:
        super().__init__(parent)
        self.year = int(year)
        self.month = int(month)
        self._file_path: Path | None = None
        self.setWindowTitle("Добавление акта")
        self.resize(620, 330)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = QComboBox()
        self.type_combo.addItem("Акт выполненных работ", "ACCEPTANCE_ACT")
        self.type_combo.addItem("Акт нарушения качества", "QUALITY_VIOLATION_ACT")
        form.addRow("Тип акта *", self.type_combo)

        self.number_edit = QLineEdit()
        form.addRow("Номер *", self.number_edit)

        first = QDate(self.year, self.month, 1)
        last = QDate(self.year, self.month, first.daysInMonth())

        self.reg_date = QDateEdit(first)
        self.reg_date.setCalendarPopup(True)
        self.reg_date.setDisplayFormat("dd.MM.yyyy")
        self.reg_date.setMinimumDate(first)
        self.reg_date.setMaximumDate(last)
        form.addRow("Дата заключения *", self.reg_date)

        self.period_from = QDateEdit(first)
        self.period_from.setCalendarPopup(True)
        self.period_from.setDisplayFormat("dd.MM.yyyy")
        self.period_from.setMinimumDate(first)
        self.period_from.setMaximumDate(last)

        self.period_to = QDateEdit(first)
        self.period_to.setCalendarPopup(True)
        self.period_to.setDisplayFormat("dd.MM.yyyy")
        self.period_to.setMinimumDate(first)
        self.period_to.setMaximumDate(last)

        period_row = QHBoxLayout()
        period_row.addWidget(self.period_from)
        period_row.addWidget(QLabel("—"))
        period_row.addWidget(self.period_to)
        form.addRow("Отчётный период *", period_row)

        file_row = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        choose = QPushButton("Выбрать файл…")
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(choose)
        form.addRow("Акт *", file_row)

        root.addLayout(form)

        note = QLabel(
            "Файл до 50 МБ. Допустимы PDF, RTF, DOC, DOCX, XLS, XLSX, "
            "JPEG/JPG и TIFF. Период акта должен полностью входить в выбранный месяц."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Добавить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)

        choose.clicked.connect(self._choose_file)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

    def _choose_file(self) -> None:
        raw, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл акта",
            "",
            "Акты (*.pdf *.rtf *.doc *.docx *.xls *.xlsx *.jpeg *.jpg *.tiff)",
        )
        if not raw:
            return
        path = Path(raw)
        if path.suffix.lower() not in _ALLOWED_EXTENSIONS:
            QMessageBox.warning(self, "Неподдерживаемый файл", "Формат файла не поддерживается.")
            return
        try:
            size = path.stat().st_size
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось прочитать файл", str(exc))
            return
        if size <= 0:
            QMessageBox.warning(self, "Пустой файл", "Выбранный файл пуст.")
            return
        if size > _MAX_FILE_SIZE:
            QMessageBox.warning(self, "Файл слишком большой", "Размер файла превышает 50 МБ.")
            return
        self._file_path = path
        self.file_edit.setText(path.name)

    def _accept(self) -> None:
        if not self.number_edit.text().strip():
            QMessageBox.warning(self, "Укажите номер", "Номер акта обязателен.")
            return
        if self._file_path is None:
            QMessageBox.warning(self, "Добавьте файл", "Нужно выбрать файл акта.")
            return
        if self.period_from.date() > self.period_to.date():
            QMessageBox.warning(
                self,
                "Проверьте период",
                "Дата начала отчётного периода не может быть позже даты окончания.",
            )
            return
        self.accept()

    def values(self) -> dict:
        assert self._file_path is not None
        content_type = mimetypes.guess_type(self._file_path.name)[0] or "application/octet-stream"
        return {
            "act_type": str(self.type_combo.currentData()),
            "number": self.number_edit.text().strip(),
            "reg_date": self.reg_date.date().toString("dd.MM.yyyy"),
            "period_from": self.period_from.date().toString("dd.MM.yyyy"),
            "period_to": self.period_to.date().toString("dd.MM.yyyy"),
            "file_name": self._file_path.name,
            "description": self._file_path.name,
            "content_type": content_type,
            "content": self._file_path.read_bytes(),
        }


class QualityActsDialog(QDialog):
    def __init__(
        self,
        store: QualityAssessmentStore,
        *,
        item_id: str,
        year: int,
        month: int,
        editable: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.item_id = item_id
        self.year = int(year)
        self.month = int(month)
        self.editable = bool(editable)
        self.setWindowTitle("Акты")
        self.resize(720, 430)

        root = QVBoxLayout(self)
        self.list = QListWidget()
        root.addWidget(self.list, 1)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Добавить акт")
        self.delete_button = QPushButton("Отвязать от работы")
        self.add_button.setEnabled(self.editable)
        self.delete_button.setEnabled(self.editable)
        actions.addWidget(self.add_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        root.addLayout(actions)

        if not self.editable:
            note = QLabel(
                "Оценка уже сохранена в ГИС ЖКХ. Изменение и удаление размещённых "
                "актов включим после отдельной проверки сценария обновления."
            )
            note.setObjectName("Muted")
            note.setWordWrap(True)
            root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        root.addWidget(buttons)

        self.add_button.clicked.connect(self._add)
        self.delete_button.clicked.connect(self._delete)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for act in self.store.list_acts(self.item_id):
            act_name = (
                "Акт выполненных работ"
                if act.act_type == "ACCEPTANCE_ACT"
                else "Акт нарушения качества"
            )
            text = (
                f"{act_name} • № {act.number} • {act.reg_date} • "
                f"{act.period_from}—{act.period_to} • {act.file_name}"
            )
            if act.remote_content_guid or act.remote_act_guid:
                text += " • сохранён в ГИС"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, act.id)
            self.list.addItem(item)

    def _add(self) -> None:
        if not self.editable:
            return
        dialog = AddQualityActDialog(year=self.year, month=self.month, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            self.store.add_act(item_id=self.item_id, **dialog.values())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось добавить акт", str(exc))
            return
        self._reload()

    def _delete(self) -> None:
        if not self.editable:
            return
        row = self.list.currentItem()
        if row is None:
            return
        act_id = row.data(Qt.ItemDataRole.UserRole)
        if not act_id:
            return
        try:
            self.store.unlink_act(str(act_id), self.item_id)
        except ValueError as exc:
            QMessageBox.warning(self, "Нельзя удалить акт", str(exc))
            return
        self._reload()
