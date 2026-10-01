"""Dialogs used by work fixation UI."""

from __future__ import annotations

import mimetypes
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
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
    QTextEdit,
    QVBoxLayout,
)

from gis_jkh_assistant.database import Repository


_ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".bmp", ".gif"}
_MAX_PHOTO_SIZE = 50 * 1024 * 1024


class FixationPhotoDialog(QDialog):
    def __init__(
        self,
        repository: Repository,
        *,
        fixation_item_id: str | None = None,
        unplanned_item_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.fixation_item_id = fixation_item_id
        self.unplanned_item_id = unplanned_item_id
        self.setWindowTitle("Фотографические изображения")
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        note = QLabel(
            "Допустимые форматы: jpg, jpeg, bmp, gif. Размер одного файла — до 50 МБ."
        )
        note.setWordWrap(True)
        note.setObjectName("Muted")
        layout.addWidget(note)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        actions = QHBoxLayout()
        add_button = QPushButton("Добавить фото")
        remove_button = QPushButton("Удалить выбранное")
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        layout.addWidget(buttons)

        add_button.clicked.connect(self._add)
        remove_button.clicked.connect(self._remove)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        self._reload()

    def _photos(self):
        return self.repository.list_work_fixation_photos(
            fixation_item_id=self.fixation_item_id,
            unplanned_item_id=self.unplanned_item_id,
        )

    def _reload(self) -> None:
        self.list.clear()
        for photo in self._photos():
            text = f"{photo.file_name} • {photo.file_size / 1024:.1f} КБ"
            if photo.remote_content_guid:
                text += " • загружено в ГИС"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, photo.id)
            self.list.addItem(item)

    def _add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите фото",
            "",
            "Изображения (*.jpg *.jpeg *.bmp *.gif)",
        )
        for raw in paths:
            path = Path(raw)
            if path.suffix.lower() not in _ALLOWED_PHOTO_EXTENSIONS:
                QMessageBox.warning(
                    self,
                    "Неподдерживаемый файл",
                    f"{path.name}: допустимы только jpg, jpeg, bmp и gif.",
                )
                continue
            size = path.stat().st_size
            if size > _MAX_PHOTO_SIZE:
                QMessageBox.warning(
                    self,
                    "Файл слишком большой",
                    f"{path.name}: размер превышает 50 МБ.",
                )
                continue
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                self.repository.add_work_fixation_photo(
                    fixation_item_id=self.fixation_item_id,
                    unplanned_item_id=self.unplanned_item_id,
                    file_name=path.name,
                    description=path.name,
                    content_type=content_type,
                    content=path.read_bytes(),
                )
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Не удалось добавить фото", str(exc))
        self._reload()

    def _remove(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        photo_id = item.data(Qt.ItemDataRole.UserRole)
        if photo_id:
            self.repository.delete_work_fixation_photo(str(photo_id))
            self._reload()


class AddUnplannedWorkDialog(QDialog):
    def __init__(self, options: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.options = options
        self.setWindowTitle("Добавление внеплановой работы")
        self.resize(700, 500)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.work_combo = QComboBox()
        self.work_combo.setMinimumWidth(520)
        for option in options:
            label = option.get("name") or option.get("code") or "Без наименования"
            unit = option.get("unitName")
            if unit:
                label += f" • {unit}"
            self.work_combo.addItem(label, option)
        form.addRow("Работа / услуга *", self.work_combo)

        self.work_type_label = QLabel("—")
        form.addRow("Вид работы / услуги", self.work_type_label)

        self.price_edit = QLineEdit()
        self.amount_edit = QLineEdit()
        self.count_edit = QLineEdit()
        self.cost_edit = QLineEdit()
        self.comment_edit = QTextEdit()
        self.comment_edit.setMaximumHeight(90)

        form.addRow("Цена", self.price_edit)
        form.addRow("Объем", self.amount_edit)
        form.addRow("Количество", self.count_edit)
        form.addRow("Стоимость", self.cost_edit)
        form.addRow("Комментарий", self.comment_edit)
        root.addLayout(form)

        hint = QLabel(
            "Стоимость можно оставить пустой — если цена, объем и количество заполнены, "
            "она будет рассчитана автоматически."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Добавить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)

        self.work_combo.currentIndexChanged.connect(self._update_work_type)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self._update_work_type()

    def _update_work_type(self) -> None:
        option = self.work_combo.currentData() or {}
        self.work_type_label.setText(option.get("workTypeName") or "—")

    @staticmethod
    def _decimal(text: str) -> Decimal:
        normalized = text.replace("\u00a0", "").replace(" ", "").replace(",", ".")
        return Decimal(normalized)

    def _accept(self) -> None:
        if self.work_combo.currentIndex() < 0:
            QMessageBox.warning(self, "Выберите работу", "Не выбрана внеплановая работа.")
            return

        count_text = self.count_edit.text().strip()
        if count_text:
            try:
                count = int(count_text)
                if count < 0:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Проверьте количество",
                    "Количество должно быть целым числом не меньше нуля.",
                )
                return

        if not self.cost_edit.text().strip():
            try:
                price = self._decimal(self.price_edit.text().strip())
                amount = self._decimal(self.amount_edit.text().strip())
                count = Decimal(int(count_text))
            except (InvalidOperation, ValueError):
                pass
            else:
                value = (price * amount * count).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
                self.cost_edit.setText(format(value, "f"))

        self.accept()

    def values(self) -> dict:
        option = dict(self.work_combo.currentData() or {})
        def clean(widget: QLineEdit) -> str | None:
            text = widget.text().strip()
            return text or None
        count_text = self.count_edit.text().strip()
        return {
            "work_code": str(option.get("code") or ""),
            "work_name": str(option.get("name") or ""),
            "work_guid": str(option.get("guid") or ""),
            "work_type_code": option.get("workTypeCode"),
            "work_type_guid": option.get("workTypeGuid"),
            "unit_name": option.get("unitName"),
            "fact_price": clean(self.price_edit),
            "fact_amount": clean(self.amount_edit),
            "fact_count": int(count_text) if count_text else None,
            "fact_total_cost": clean(self.cost_edit),
            "comment": self.comment_edit.toPlainText().strip() or None,
        }
