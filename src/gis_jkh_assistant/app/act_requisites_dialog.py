"""Explicit editing of house representatives and organization act signatories."""
from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from reportlab.lib.utils import ImageReader

from gis_jkh_assistant.acceptance_act_details import (
    address_parts, display_date, iso_date, suggested_genitive, suggested_position_genitive,
)
from gis_jkh_assistant.database.act_requisites_store import ActRequisitesStore, normalized_person
from .gis_integration_worker import GisIntegrationWorker, connect_result


def load_act_image(path):
    file = Path(path)
    if file.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Изображение должно занимать не больше 5 МБ.")
    content = file.read_bytes()
    if not (content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff")):
        raise ValueError("Выберите изображение PNG или JPEG.")
    try:
        width, height = ImageReader(BytesIO(content)).getSize()
        if max(width, height) > 8000:
            raise ValueError("Сторона изображения не должна превышать 8000 пикселей.")
    except Exception as exc:
        raise ValueError("Не удалось прочитать изображение PNG / JPEG.") from exc
    return content, file.name


class ActPersonDialog(QDialog):
    def __init__(self, person=None, *, organization=False, parent=None):
        super().__init__(parent)
        person = person or {}
        self.organization = organization
        self.setWindowTitle("Подписант УО" if organization else "Представитель собственников")
        self.resize(660, 650)
        root = QVBoxLayout(self)
        hint = QLabel("Сроки относятся к полномочиям, а не к дате загрузки из ГИС. Окончание включается в период. Проверьте предложенное склонение ФИО." if organization else
                      "Укажите известные сведения. Без помещения блок о нём не печатается; без реквизитов основания фраза об основании не печатается. Даты ограничивают применение записи; пустые даты не задают ограничений. Запись сама по себе не подтверждает полномочия.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        self.edits = {}
        for field, label in (("full_name", "ФИО *"), ("name_genitive", "ФИО в родительном падеже"),
                             ("position", "Должность"), ("position_genitive", "Должность в родительном падеже"),
                             ("premises", "Квартира / помещение")):
            edit = QLineEdit(str(person.get(field) or ""))
            self.edits[field] = edit
            if (organization and field == "premises") or (not organization and field.startswith("position")):
                continue
            form.addRow(label, edit)
        self._position_source = self.edits["position"].text()
        if organization:
            genitive = self.edits["position_genitive"]
            genitive.setPlaceholderText("Например: генерального директора")
            if not genitive.text():
                genitive.setText(suggested_position_genitive(self._position_source))
            self.edits["position"].editingFinished.connect(self._position_changed)
        self.role = QComboBox()
        self.role.addItems(["Руководитель", "Уполномоченный сотрудник"] if organization else
                           ["Председатель совета МКД", "Уполномоченный собственник"])
        self.role.setCurrentText(person.get("role") or (self.role.itemText(0) if organization else "Уполномоченный собственник"))
        form.addRow("Статус", self.role)
        self.gender = QComboBox()
        self.gender.addItem("Уточните согласование…" if organization else "Уточните род для склонения…", "")
        self.gender.addItem("Действующего" if organization else "Мужской", "male")
        self.gender.addItem("Действующей" if organization else "Женский", "female")
        name = str(person.get("full_name", ""))
        inferred = "female" if name.endswith(("вна", "чна")) else "male" if name.endswith(("вич", "ьич")) else ""
        self.gender.setCurrentIndex(self.gender.findData(person.get("gender") or inferred))
        form.addRow("Согласование текста" if organization else "Род ФИО для склонения", self.gender)
        suggest = QPushButton("Предложить склонение ФИО")
        suggest.clicked.connect(lambda: self.edits["name_genitive"].setText(suggested_genitive(
            self.edits["full_name"].text(), self.gender.currentData())))
        self.edits["full_name"].editingFinished.connect(lambda: self.edits["name_genitive"].setText(
            self.edits["name_genitive"].text() or suggested_genitive(self.edits["full_name"].text(), self.gender.currentData())))
        form.addRow("", suggest)
        self.basis = QComboBox()
        self.basis.addItems(["Выберите основание…", "Устав", "Доверенность", "Иное"] if organization else
                            ["Не указано", "Решение ОСС", "Доверенность", "Иное"])
        self.basis.setCurrentText(person.get("basis_kind") or self.basis.itemText(0))
        form.addRow("Основание полномочий *" if organization else "Основание (если известно)", self.basis)
        for field, label in (("basis_number", "Номер основания"), ("basis_date", "Дата основания"),
                             ("basis_text", "Основание полным текстом"), ("valid_from", "Начало полномочий *"),
                             ("valid_to", "Окончание (включительно)")):
            value = person.get(field) or ""
            if field in {"basis_date", "valid_from", "valid_to"}:
                value = display_date(value)
            edit = QLineEdit(value)
            if field in {"basis_date", "valid_from", "valid_to"}:
                edit.setPlaceholderText("ДД.ММ.ГГГГ" if field != "valid_to" else "Пусто — срок не ограничен")
            self.edits[field] = edit
            if not organization and field == "valid_from":
                label = "Учитывать запись с (если известно)"
            form.addRow(label, edit)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _position_changed(self):
        edit = self.edits["position_genitive"]
        position = self.edits["position"].text()
        if not edit.text() or edit.text() == suggested_position_genitive(self._position_source):
            edit.setText(suggested_position_genitive(position))
        self._position_source = position

    def values(self):
        values = {key: edit.text().strip() for key, edit in self.edits.items()}
        for field in ("basis_date", "valid_from", "valid_to"):
            values[field] = iso_date(values[field])
        values.update(role=self.role.currentText(), gender=self.gender.currentData(),
                      basis_kind=self.basis.currentText() if self.basis.currentIndex() else "")
        return normalized_person(values, organization=self.organization)

    def _save(self):
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте реквизиты", str(exc))
            return
        self.accept()


class ActRequisitesDialog(QDialog):
    def __init__(self, repository, *, organization_id=None, house_id=None, page=None):
        super().__init__(page)
        self.repository, self.page = repository, page
        self.store = ActRequisitesStore(repository)
        self.owner = {"organization_id": organization_id} if organization_id else {"house_id": house_id}
        self.organization_id, self.house_id = organization_id, house_id
        self._worker = None
        self.setWindowTitle("Реквизиты актов УО" if organization_id else "Представители и реквизиты акта дома")
        self.resize(920, 640)
        root = QVBoxLayout(self)
        title = repository.get_organization(organization_id).name if organization_id else repository.get_house(house_id).address
        context = QLabel(title)
        context.setWordWrap(True)
        root.addWidget(context)
        self.address_edits = {}
        if house_id:
            parts = address_parts(title)
            parts.update({k: v for k, v in self.store.house_fields(house_id).items() if v})
            form = QFormLayout()
            for key, label in (("locality", "Населённый пункт"), ("house_number", "Номер дома с корпусом / строением"),
                               ("address_without_number", "Адрес без номера дома")):
                edit = QLineEdit(parts.get(key, ""))
                self.address_edits[key] = edit
                form.addRow(label, edit)
            root.addLayout(form)
            save_address = QPushButton("Сохранить адресные реквизиты акта")
            save_address.clicked.connect(self._save_address)
            root.addWidget(save_address)
        hint = QLabel("История полномочий: акт выбирает запись по своей дате. Если подходят несколько записей, подписанта нужно выбрать при подготовке. Старые акты не пересчитываются.")
        hint.setWordWrap(True)
        root.addWidget(hint)
        actions = QHBoxLayout()
        for text, callback in (("Добавить период полномочий…", lambda: self._edit()),
                               ("Изменить запись…", lambda: self._edit(self._current()))):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        if organization_id:
            self.load_button = QPushButton("Получить руководителя из ГИС…")
            self.load_button.clicked.connect(self._load_chief)
            actions.addWidget(self.load_button)
        actions.addStretch(1)
        root.addLayout(actions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ФИО / должность", "Начало", "Окончание включительно", "Основание"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda *_: self._edit(self._current()))
        root.addWidget(self.table, 1)
        if organization_id:
            images = QHBoxLayout()
            for label, callback in (("Подпись выбранного лица…", lambda: self._image("signature")),
                                    ("Печать УО…", lambda: self._image("stamp"))):
                button = QPushButton(label)
                button.clicked.connect(callback)
                images.addWidget(button)
            self.insert_images = QCheckBox("Вставлять загруженные изображения в новые акты")
            options = self.store.options(organization_id)
            self.insert_images.setChecked(bool(options["insert_images"]))
            self.insert_images.toggled.connect(lambda enabled: self._save_option(self.insert_images, "insert_images", enabled))
            images.addWidget(self.insert_images)
            root.addLayout(images)
            self.image_info = QLabel()
            self.image_info.setWordWrap(True)
            root.addWidget(self.image_info)
            self.table.currentCellChanged.connect(lambda *_: self._image_label())
            self.customer_signature_placeholder = QCheckBox("Использовать заглушку вместо подписи заказчика")
            self.customer_signature_placeholder.setChecked(bool(options["customer_signature_placeholder"]))
            self.customer_signature_placeholder.toggled.connect(lambda enabled: self._save_option(
                self.customer_signature_placeholder, "customer_signature_placeholder", enabled))
            root.addWidget(self.customer_signature_placeholder)
            placeholder_hint = QLabel("Для новых ручных и автоматических актов этой УО. Заменяет только строку подписи заказчика; ФИО остаётся в акте.")
            placeholder_hint.setWordWrap(True)
            root.addWidget(placeholder_hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh()

    def _refresh(self):
        self.people = self.store.list_people(**self.owner)
        self.table.setRowCount(len(self.people))
        for row, person in enumerate(self.people):
            for column, value in enumerate((person["full_name"] + (" — " + person["position"] if person["position"] else ""),
                                             display_date(person["valid_from"]) or "Не указано", display_date(person["valid_to"]) or "Без срока",
                                             person["basis_text"] or person["basis_kind"])):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
        self.table.resizeRowsToContents()
        if self.people:
            self.table.selectRow(0)
        if self.organization_id:
            self._image_label()

    def _current(self):
        row = self.table.currentRow()
        return self.people[row] if 0 <= row < len(self.people) else None

    def _edit(self, person=None, candidate=None):
        dialog = ActPersonDialog(candidate or person, organization=bool(self.organization_id), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.store.save_person(dialog.values(), person_id=person["id"] if person else None, **self.owner)
            self._refresh()
        except (ValueError, sqlite3.DatabaseError) as exc:
            QMessageBox.warning(self, "Не удалось сохранить", str(exc))

    def _save_address(self):
        try:
            self.store.save_house_fields(self.house_id, {k: v.text() for k, v in self.address_edits.items()})
            QMessageBox.information(self, "Реквизиты сохранены", "Эти значения будут использоваться в новых актах дома.")
        except sqlite3.DatabaseError as exc:
            QMessageBox.warning(self, "Не удалось сохранить", str(exc))

    def _image_label(self):
        person = self._current() or {}
        options = self.store.options(self.organization_id)
        self.image_info.setText("Подпись: " + (person.get("signature_name") or "не загружена") +
                                "; печать: " + (options.get("stamp_name") or "не загружена") +
                                ". Изображения не меняют статус подписания документа.")

    def _save_option(self, checkbox, field, enabled):
        try:
            self.store.save_options(self.organization_id, **{field: int(enabled)})
        except sqlite3.DatabaseError as exc:
            checkbox.blockSignals(True)
            checkbox.setChecked(not enabled)
            checkbox.blockSignals(False)
            QMessageBox.warning(self, "Не удалось сохранить настройку", str(exc))

    def _image(self, kind):
        person = self._current()
        if kind == "signature" and not person:
            QMessageBox.information(self, "Выберите подписанта", "Сначала добавьте и выберите запись подписанта.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Подпись" if kind == "signature" else "Печать УО", "", "Изображения (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            content, name = load_act_image(path)
            if kind == "signature":
                self.store.save_signature(person["id"], content, name)
            else:
                self.store.save_options(self.organization_id, stamp=content, stamp_name=name)
            self._refresh()
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            QMessageBox.warning(self, "Не удалось загрузить изображение", str(exc))

    def _load_chief(self):
        gateway = getattr(self.page, "integration_gateway", None)
        if gateway is None:
            return
        version = gateway.status().helper_version or ""
        try:
            parts = tuple(map(int, version.split(".")))
            supported = len(parts) == 3 and parts >= (0, 1, 32)
        except ValueError:
            supported = False
        if not supported:
            QMessageBox.information(self, "Обновите помощник", "Для загрузки реквизитов руководителя нужен помощник 0.1.32 или новее.")
            return
        worker = GisIntegrationWorker(gateway, "get_organization_act_signatory", {}, timeout=45, parent=self.page)
        self._worker = worker
        self.load_button.setEnabled(False)

        def received(result):
            organization = self.repository.get_organization(self.organization_id)
            if str(result.get("ogrn") or "") != organization.ogrn:
                raise ValueError("Ответ относится к другой УО.")
            candidates = result.get("candidates") or []
            if not candidates or any(not isinstance(p, dict) or not p.get("fullName") for p in candidates):
                raise ValueError("ГИС ЖКХ не вернул ФИО руководителя.")
            chosen = candidates[0]
            if len(candidates) > 1:
                labels = [p["fullName"] + " — " + (p.get("position") or "должность не указана") +
                          " (" + p.get("source", "ГИС") + ")" for p in candidates]
                text, accepted = QInputDialog.getItem(self, "В ГИС разные сведения о руководителе",
                    "Выберите подтверждённые вами сведения. История и даты полномочий из этих ответов неизвестны.",
                    labels, 0, False)
                if not accepted:
                    return
                chosen = candidates[labels.index(text)]
            candidate = {"full_name": chosen["fullName"], "position": chosen.get("position") or ""}
            QMessageBox.information(self, "Проверьте полученные сведения", "ФИО и должность получены из ГИС. Укажите основание и действительный срок полномочий перед сохранением.")
            self._edit(candidate=candidate)

        def finished():
            self._worker = None
            self.load_button.setEnabled(True)
        connect_result(worker, worker.succeeded, received)
        connect_result(worker, worker.failed, lambda error: QMessageBox.warning(self, "Не удалось получить руководителя", error))
        worker.finished.connect(finished)
        worker.start()

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            return
        super().reject()

    def closeEvent(self, event):  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


def open_act_requisites(page, *, organization=False):
    if page.flush_pending_changes() is False:
        return
    owner_id = page.organization_combo.currentData() if organization else page._current_house_id
    if not owner_id:
        QMessageBox.information(page, "Сначала выберите данные", "Выберите УО." if organization else "Выберите дом.")
        return
    dialog = ActRequisitesDialog(page.repository, page=page, **({"organization_id": owner_id} if organization else {"house_id": owner_id}))
    dialog.exec()
