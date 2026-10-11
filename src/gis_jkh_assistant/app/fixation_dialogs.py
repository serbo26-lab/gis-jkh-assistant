"""Dialogs used by work fixation UI."""

from __future__ import annotations

import mimetypes
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.work_fixation import calculate_fact_total_cost
from .widgets import SafeComboBox
from .workflow_widgets import style_danger_button
from .error_messages import context_text, error_text


_ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".bmp", ".gif"}
_MAX_PHOTO_SIZE = 50 * 1024 * 1024


class FixationPhotoDialog(QDialog):
    def __init__(
        self,
        repository: Repository,
        *,
        fixation_item_id: str | None = None,
        unplanned_item_id: str | None = None,
        work_name: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.fixation_item_id = fixation_item_id
        self.unplanned_item_id = unplanned_item_id
        self.setWindowTitle("Фотографические изображения")
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        details = [context_text(parent)] if parent is not None else []
        if work_name:
            details.append("Работа / услуга: " + work_name)
        self.context_label = QLabel("\n".join(value for value in details if value))
        self.context_label.setWordWrap(True)
        self.context_label.setObjectName("Muted")
        self.context_label.setVisible(bool(self.context_label.text()))
        layout.addWidget(self.context_label)
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
        remove_button = style_danger_button(QPushButton("Удалить выбранное фото"))
        remove_button.setEnabled(False)
        self.list.currentItemChanged.connect(lambda *_: remove_button.setEnabled(self.list.currentItem() is not None))
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
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            try:
                size = path.stat().st_size
                if size > _MAX_PHOTO_SIZE:
                    QMessageBox.warning(self, "Файл слишком большой", f"{path.name}: размер превышает 50 МБ. Выберите файл меньшего размера.")
                    continue
                self.repository.add_work_fixation_photo(
                    fixation_item_id=self.fixation_item_id,
                    unplanned_item_id=self.unplanned_item_id,
                    file_name=path.name,
                    description=path.name,
                    content_type=content_type,
                    content=path.read_bytes(),
                )
            except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                QMessageBox.warning(self, "Не удалось добавить фото", error_text(
                    exc, context=path.name, effect="Это фото не добавлено. Ранее добавленные фотографии сохранены.",
                ))
        self._reload()

    def _remove(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        photo_id = item.data(Qt.ItemDataRole.UserRole)
        if photo_id:
            context = context_text(self.parentWidget()) if self.parentWidget() else ""
            answer = QMessageBox.question(
                self, "Удалить локальное фото?",
                f"{item.text()}\n{context}\n\nУдаляется выбранное фото из локальной фиксации. "
                "Сведения в ГИС ЖКХ этим действием не изменяются. Продолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.repository.delete_work_fixation_photo(str(photo_id))
            except (sqlite3.DatabaseError, OSError, ValueError) as exc:
                QMessageBox.warning(self, "Не удалось удалить фото", error_text(
                    exc, context=item.text(), effect="Фото остаётся в списке. Проверьте локальные данные перед повтором.",
                ))
                return
            self._reload()


class AddUnplannedWorkDialog(QDialog):
    def __init__(self, options: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.options = options
        self._photo_paths: list[Path] = []
        self._syncing_fact_fields = False
        self.setWindowTitle("Добавление внеплановой работы")
        self.resize(700, 500)

        outer = QVBoxLayout(self)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_content = QWidget()
        root = QVBoxLayout(self.form_content)
        root.setContentsMargins(0, 0, 0, 0)
        self.form_scroll.setWidget(self.form_content)
        outer.addWidget(self.form_scroll, 1)
        form = QFormLayout()

        self.work_combo = SafeComboBox()
        self.work_combo.setMinimumWidth(520)
        for option in options:
            label = option.get("name") or option.get("code") or "Без наименования"
            self.work_combo.addItem(label, option)
        form.addRow("Работа / услуга *", self.work_combo)

        self.work_type_label = QLabel("—")
        form.addRow("Вид работы / услуги", self.work_type_label)

        self.unit_label = QLabel("—")
        form.addRow("Единица измерения", self.unit_label)

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

        photo_header = QHBoxLayout()
        photo_header.addWidget(QLabel("Фотографические изображения"))
        self.add_photo_button = QPushButton("Добавить фото")
        self.remove_photo_button = style_danger_button(QPushButton("Удалить выбранное фото"))
        photo_header.addStretch(1)
        photo_header.addWidget(self.add_photo_button)
        photo_header.addWidget(self.remove_photo_button)
        root.addLayout(photo_header)

        self.photo_list = QListWidget()
        self.photo_list.setMaximumHeight(120)
        root.addWidget(self.photo_list)

        hint = QLabel(
            "Как в ГИС ЖКХ: либо заполните цену + объем + количество — стоимость "
            "рассчитается автоматически и будет заблокирована, либо укажите "
            "стоимость + количество — тогда цена и объем будут заблокированы. "
            "Чтобы сменить вариант, очистите заполненные поля текущего варианта."
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
        outer.addWidget(buttons)

        self.work_combo.currentIndexChanged.connect(self._update_work_type)
        self.price_edit.textChanged.connect(self._sync_fact_fields)
        self.amount_edit.textChanged.connect(self._sync_fact_fields)
        self.count_edit.textChanged.connect(self._sync_fact_fields)
        self.cost_edit.textChanged.connect(self._sync_fact_fields)
        self.add_photo_button.clicked.connect(self._add_photos)
        self.remove_photo_button.clicked.connect(self._remove_photo)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self._update_work_type()
        self._sync_fact_fields()
        self.ensurePolished()
        # Preserve the natural field spacing; scroll instead of compressing it.
        self.form_content.setMinimumHeight(root.sizeHint().height())
        margins = outer.contentsMargins()
        self.setMinimumWidth(root.minimumSize().width() + margins.left() + margins.right()
                             + self.form_scroll.verticalScrollBar().sizeHint().width())
        preferred_height = self.form_content.minimumHeight() + buttons.sizeHint().height() + 34
        screen = self.screen()
        available_height = screen.availableGeometry().height() - 48 if screen else 720
        if parent is not None:
            available_height = min(available_height, parent.window().height() - 48)
        self.resize(max(700, self.minimumWidth()), min(preferred_height, available_height))

    def _sync_fact_fields(self, *_args) -> None:
        if self._syncing_fact_fields:
            return
        self._syncing_fact_fields = True
        try:
            price = self.price_edit.text().strip()
            amount = self.amount_edit.text().strip()
            count = self.count_edit.text().strip()
            cost = self.cost_edit.text().strip()

            detailed_mode = bool(price or amount)
            total_mode = bool(cost) and not detailed_mode

            self.price_edit.setEnabled(not total_mode)
            self.amount_edit.setEnabled(not total_mode)
            self.cost_edit.setEnabled(not detailed_mode)

            if detailed_mode:
                calculated = calculate_fact_total_cost(price, amount, count)
                self.cost_edit.setText(calculated or "")
        finally:
            self._syncing_fact_fields = False

    def _add_photos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите фото",
            "",
            "Изображения (*.jpg *.jpeg *.bmp *.gif)",
        )
        for raw in paths:
            path = Path(raw)
            if path in self._photo_paths:
                continue
            if len(self._photo_paths) >= 1000:
                QMessageBox.warning(
                    self,
                    "Слишком много фото",
                    "К одной внеплановой работе можно прикрепить не более 1000 фото.",
                )
                break
            if path.suffix.lower() not in _ALLOWED_PHOTO_EXTENSIONS:
                QMessageBox.warning(
                    self,
                    "Неподдерживаемый файл",
                    f"{path.name}: допустимы только jpg, jpeg, bmp и gif.",
                )
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                QMessageBox.warning(self, "Не удалось прочитать файл", str(exc))
                continue
            if size > _MAX_PHOTO_SIZE:
                QMessageBox.warning(
                    self,
                    "Файл слишком большой",
                    f"{path.name}: размер превышает 50 МБ.",
                )
                continue
            if size <= 0:
                QMessageBox.warning(
                    self,
                    "Пустой файл",
                    f"{path.name}: файл пуст.",
                )
                continue
            self._photo_paths.append(path)
        self._reload_photo_paths()

    def _remove_photo(self) -> None:
        row = self.photo_list.currentRow()
        if row < 0:
            return
        self._photo_paths.pop(row)
        self._reload_photo_paths()

    def _reload_photo_paths(self) -> None:
        self.photo_list.clear()
        for path in self._photo_paths:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            self.photo_list.addItem(f"{path.name} • {size / 1024:.1f} КБ")

    def photo_paths(self) -> list[Path]:
        return list(self._photo_paths)

    def _update_work_type(self) -> None:
        option = self.work_combo.currentData() or {}
        self.work_type_label.setText(option.get("workTypeName") or "—")
        self.unit_label.setText(option.get("unitName") or "—")

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

        price_text = self.price_edit.text().strip()
        amount_text = self.amount_edit.text().strip()
        if price_text or amount_text:
            if not price_text or not amount_text or not count_text:
                QMessageBox.warning(
                    self,
                    "Заполните расчетные поля",
                    "Для варианта «цена × объем × количество» нужно заполнить "
                    "все три поля.",
                )
                return
            calculated = calculate_fact_total_cost(
                price_text,
                amount_text,
                count_text,
            )
            if calculated is None:
                QMessageBox.warning(
                    self,
                    "Проверьте расчетные поля",
                    "Цена, объем и количество должны быть неотрицательными числами, "
                    "а количество — целым числом.",
                )
                return
            self.cost_edit.setText(calculated)

        if not self.cost_edit.text().strip():
            QMessageBox.warning(
                self,
                "Укажите стоимость",
                "Для внеплановой работы требуется фактическая стоимость. "
                "Укажите ее вручную либо заполните цену, объем и количество "
                "для автоматического расчета.",
            )
            return

        try:
            cost = self._decimal(self.cost_edit.text().strip())
            if cost < 0:
                raise InvalidOperation
        except InvalidOperation:
            QMessageBox.warning(
                self,
                "Проверьте стоимость",
                "Стоимость должна быть неотрицательным числом.",
            )
            return

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
            "work_type_name": option.get("workTypeName"),
            "unit_name": option.get("unitName"),
            "fact_price": clean(self.price_edit),
            "fact_amount": clean(self.amount_edit),
            "fact_count": int(count_text) if count_text else None,
            "fact_total_cost": clean(self.cost_edit),
            "comment": self.comment_edit.toPlainText().strip() or None,
        }
