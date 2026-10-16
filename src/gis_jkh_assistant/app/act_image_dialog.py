"""Select one mark from a scan and preview its physical placement in an act."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from gis_jkh_assistant.act_images import (
    SIGNATURE_HEIGHT_MM, SIGNATURE_LINE_MM, SIGNATURE_WIDTH_MM, decode_act_image,
    fitted_size, image_png, load_act_image, normalize_image_settings, oriented_image,
    prepare_act_image, signature_placement,
)
from .icons import set_button_icon


def _qimage(image):
    return QImage.fromData(image_png(image))


class ImageRegion(QWidget):
    cropChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 260)
        self.setAccessibleName("Область подписи или печати на исходном изображении")
        self.picture = QImage()
        self.crop = [0, 0, 1, 1]
        self.anchor = None

    def sizeHint(self):
        return QSize(520, 430)

    def set_source(self, image, crop):
        thumbnail = image.copy()
        thumbnail.thumbnail((1400, 1400))
        self.picture = _qimage(thumbnail)
        self.crop = list(crop)
        self.update()

    def image_rect(self):
        if self.picture.isNull():
            return QRectF()
        bounds = self.rect().adjusted(8, 8, -8, -8)
        factor = min(bounds.width() / self.picture.width(), bounds.height() / self.picture.height())
        width, height = self.picture.width() * factor, self.picture.height() * factor
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#e5e7eb"))
        bounds = self.image_rect()
        if bounds.isEmpty():
            return
        painter.drawImage(bounds, self.picture)
        painter.fillRect(bounds, QColor(0, 0, 0, 85))
        left, top, right, bottom = self.crop
        selected = QRectF(bounds.left() + left * bounds.width(), bounds.top() + top * bounds.height(),
                          (right - left) * bounds.width(), (bottom - top) * bounds.height())
        painter.save()
        painter.setClipRect(selected)
        painter.drawImage(bounds, self.picture)
        painter.restore()
        painter.setPen(QPen(QColor("#0066cc"), 2, Qt.PenStyle.DashLine))
        painter.drawRect(selected)

    def _point(self, position):
        bounds = self.image_rect()
        return QPointF(max(0, min(1, (position.x() - bounds.left()) / bounds.width())),
                       max(0, min(1, (position.y() - bounds.top()) / bounds.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self.anchor = self._point(event.position())
            self.previous_crop = list(self.crop)

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            point = self._point(event.position())
            self.crop = [min(self.anchor.x(), point.x()), min(self.anchor.y(), point.y()),
                         max(self.anchor.x(), point.x()), max(self.anchor.y(), point.y())]
            self.update()

    def mouseReleaseEvent(self, event):
        if self.anchor is not None and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            self.anchor = None
            if self.crop[2] - self.crop[0] > .002 and self.crop[3] - self.crop[1] > .002:
                self.cropChanged.emit(self.crop)
            else:
                self.crop = self.previous_crop
                self.update()


class PlacementPreview(QWidget):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.picture = QImage()
        self.settings = normalize_image_settings(kind=kind)
        self.setMinimumSize(260, 190)
        self.setAccessibleName("Предпросмотр изображения в поле акта")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("white"))
        factor = min((self.width() - 24) / 60, (self.height() - 28) / 54)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.setPen(QPen(QColor("#e5e7eb"), 1))
        for position in range(-30, 31, 5):
            painter.drawLine(QPointF(position * factor, -27 * factor), QPointF(position * factor, 27 * factor))
        for position in range(-25, 26, 5):
            painter.drawLine(QPointF(-30 * factor, position * factor), QPointF(30 * factor, position * factor))
        if self.kind == "signature":
            origin = QPointF(-SIGNATURE_WIDTH_MM * factor / 2, SIGNATURE_HEIGHT_MM * factor / 2)
            painter.setPen(QPen(QColor("#b0b0b0"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(origin.x(), -origin.y(), SIGNATURE_WIDTH_MM * factor, SIGNATURE_HEIGHT_MM * factor))
            if not self.picture.isNull():
                x, y, width, height = signature_placement((self.picture.width(), self.picture.height()), self.settings)
                painter.drawImage(QRectF(origin.x() + x * factor, origin.y() - (y + height) * factor,
                                         width * factor, height * factor), self.picture)
            painter.setPen(QPen(QColor("black"), .7))
            painter.drawLine(QPointF(origin.x(), origin.y() - SIGNATURE_LINE_MM * factor),
                             QPointF(-origin.x(), origin.y() - SIGNATURE_LINE_MM * factor))
        elif not self.picture.isNull():
            size = self.settings["size_mm"]
            width, height = fitted_size((self.picture.width(), self.picture.height()), size, size)
            painter.drawImage(QRectF(-width * factor / 2, -height * factor / 2, width * factor, height * factor), self.picture)


class ActImageDialog(QDialog):
    def __init__(self, kind, content, name, settings=None, parent=None):
        super().__init__(parent)
        self.kind, self.content, self.name = kind, content, name
        self.settings = normalize_image_settings(settings, kind=kind)
        self.source = decode_act_image(content)
        self.setWindowTitle("Подпись — область и размер" if kind == "signature" else "Печать УО — область и размер")
        self.resize(940, 650)
        root = QVBoxLayout(self)
        hint = QLabel("Обведите мышью одну подпись или печать. Не включайте соседние отметки и линии сканированного бланка. Исходный файл сохранится целиком.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.region = ImageRegion()
        self.region.cropChanged.connect(self._crop_changed)
        left.addWidget(self.region, 1)
        actions = QHBoxLayout()
        for text, icon, callback in (("Выбрать файл…", "download-cloud", self._choose_file),
                                     ("Повернуть на 90°", "sync", self._rotate),
                                     ("Весь лист", "search", self._whole)):
            button = QPushButton(text)
            set_button_icon(button, icon)
            button.clicked.connect(callback)
            actions.addWidget(button)
        left.addLayout(actions)
        self.file_label = QLabel()
        self.file_label.setWordWrap(True)
        left.addWidget(self.file_label)
        body.addLayout(left, 3)
        right = QVBoxLayout()
        right.addWidget(QLabel("Размещение в акте · сетка 5 мм"))
        self.preview = PlacementPreview(kind)
        right.addWidget(self.preview, 1)
        self.size_label = QLabel()
        self.size_label.setWordWrap(True)
        right.addWidget(self.size_label)
        form = QFormLayout()
        self.fields = {}
        entries = (("size_mm", "Размер по большей стороне", 10, 50),) if kind == "stamp" else (
            ("width_mm", "Максимальная ширина", 5, 38),
            ("height_mm", "Максимальная высота", 3, 13),
            ("offset_mm", "Смещение вверх от линии", -5, 5))
        for key, label, lower, upper in entries:
            edit = QDoubleSpinBox()
            edit.setRange(lower, upper)
            edit.setDecimals(1)
            edit.setSingleStep(.5)
            edit.setSuffix(" мм")
            edit.setValue(self.settings[key])
            edit.valueChanged.connect(self._placement_changed)
            self.fields[key] = edit
            form.addRow(label, edit)
        right.addLayout(form)
        self.remove_background = QCheckBox("Убрать белый фон скана")
        self.remove_background.setChecked(self.settings["remove_background"])
        self.remove_background.toggled.connect(self._refresh_image)
        right.addWidget(self.remove_background)
        note = QLabel("Пропорции сохраняются. Белые поля обрезаются внутри выбранной области. Размер в акте не зависит от разрешения сканера." +
                      (" Подпись накладывается на линию; положение уточните смещением." if kind == "signature" else " Для круглой печати начальный размер — 40 мм."))
        note.setWordWrap(True)
        right.addWidget(note)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        right.addWidget(self.error_label)
        body.addLayout(right, 2)
        root.addLayout(body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Сохранить для новых актов")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._show_source()
        self._refresh_image()

    def _show_source(self):
        self.region.set_source(oriented_image(self.source, self.settings["rotation"]), self.settings["crop"])
        self.file_label.setText(self.name)

    def _crop_changed(self, crop):
        self.settings["crop"] = list(crop)
        self._refresh_image()

    def _whole(self):
        self.settings["crop"] = [0, 0, 1, 1]
        self._show_source()
        self._refresh_image()

    def _rotate(self):
        left, top, right, bottom = self.settings["crop"]
        self.settings["crop"] = [1 - bottom, left, 1 - top, right]
        self.settings["rotation"] = (self.settings["rotation"] + 90) % 360
        self._show_source()
        self._refresh_image()

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выбрать изображение", "", "Изображения (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            content, name = load_act_image(path)
            source = decode_act_image(content)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Не удалось открыть изображение", str(exc))
            return
        self.content, self.name, self.source = content, name, source
        self.settings.update(crop=[0, 0, 1, 1], rotation=0)
        self._show_source()
        self._refresh_image()

    def _refresh_image(self):
        self.settings["remove_background"] = self.remove_background.isChecked()
        try:
            image = prepare_act_image(self.content, self.settings, kind=self.kind)
            self.prepared_size = image.size
            image.thumbnail((1000, 1000))
            self.preview.picture = _qimage(image)
            self.error_label.clear()
            self.save_button.setEnabled(True)
        except ValueError as exc:
            self.prepared_size = None
            self.preview.picture = QImage()
            self.error_label.setText(str(exc))
            self.save_button.setEnabled(False)
        self._placement_changed()

    def _placement_changed(self):
        self.settings.update({key: edit.value() for key, edit in self.fields.items()})
        self.preview.settings = dict(self.settings)
        if self.prepared_size:
            if self.kind == "stamp":
                size = self.settings["size_mm"]
                width, height = fitted_size(self.prepared_size, size, size)
            else:
                _, _, width, height = signature_placement(self.prepared_size, self.settings)
            self.size_label.setText(f"Изображение в акте: {width:.1f} × {height:.1f} мм")
        else:
            self.size_label.clear()
        self.preview.update()
