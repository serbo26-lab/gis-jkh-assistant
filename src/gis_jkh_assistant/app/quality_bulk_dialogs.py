"""Bulk act management for monthly quality assessment."""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
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

from gis_jkh_assistant.acceptance_act_pdf import build_acceptance_act_pdf
from gis_jkh_assistant.database.quality_assessment_store import (
    QualityAssessmentAct,
    QualityAssessmentItem,
    QualityAssessmentStore,
)

from .quality_dialogs import AddQualityActDialog


class SelectActWorksDialog(QDialog):
    def __init__(
        self,
        *,
        items: list[QualityAssessmentItem],
        selected_ids: set[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.items = items
        self.setWindowTitle("Применить акт к работам")
        self.resize(720, 520)

        root = QVBoxLayout(self)
        note = QLabel(
            "Отметьте работы и услуги, к которым относится этот акт. "
            "Один документ можно использовать сразу для нескольких строк."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        actions = QHBoxLayout()
        included = QPushButton("Все включённые в оценку")
        select_all = QPushButton("Выбрать все")
        clear = QPushButton("Снять все")
        actions.addWidget(included)
        actions.addWidget(select_all)
        actions.addWidget(clear)
        actions.addStretch(1)
        root.addLayout(actions)

        self.list = QListWidget()
        selected_ids = selected_ids or {
            item.id for item in items if item.included
        }
        for item in items:
            row = QListWidgetItem(item.work_name)
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                Qt.CheckState.Checked
                if item.id in selected_ids
                else Qt.CheckState.Unchecked
            )
            self.list.addItem(row)
        root.addWidget(self.list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Применить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)

        included.clicked.connect(self._select_included)
        select_all.clicked.connect(
            lambda: self._set_checks({item.id for item in self.items})
        )
        clear.clicked.connect(lambda: self._set_checks(set()))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

    def _set_checks(self, item_ids: set[str]) -> None:
        for index in range(self.list.count()):
            row = self.list.item(index)
            row.setCheckState(
                Qt.CheckState.Checked
                if row.data(Qt.ItemDataRole.UserRole) in item_ids
                else Qt.CheckState.Unchecked
            )

    def _select_included(self) -> None:
        self._set_checks({item.id for item in self.items if item.included})

    def _accept(self) -> None:
        if not self.selected_item_ids():
            QMessageBox.warning(
                self,
                "Не выбраны работы",
                "Акт нужно применить хотя бы к одной работе или услуге.",
            )
            return
        self.accept()

    def selected_item_ids(self) -> list[str]:
        result: list[str] = []
        for index in range(self.list.count()):
            row = self.list.item(index)
            if row.checkState() == Qt.CheckState.Checked:
                result.append(str(row.data(Qt.ItemDataRole.UserRole)))
        return result


class GenerateAcceptanceActDialog(QDialog):
    def __init__(
        self,
        *,
        items: list[QualityAssessmentItem],
        year: int,
        month: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.items = items
        self.year = int(year)
        self.month = int(month)
        self.setWindowTitle("Сформировать акт выполненных работ")
        self.resize(760, 650)

        first = QDate(self.year, self.month, 1)
        last = QDate(self.year, self.month, first.daysInMonth())

        root = QVBoxLayout(self)
        note = QLabel(
            "По умолчанию номер акта равен номеру месяца, дата акта — последний "
            "день месяца, а отчётный период покрывает месяц полностью. "
            "Значения можно изменить перед формированием."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        self.number_edit = QLineEdit(str(self.month))
        form.addRow("Номер акта *", self.number_edit)

        self.reg_date = QDateEdit(last)
        self.reg_date.setCalendarPopup(True)
        self.reg_date.setDisplayFormat("dd.MM.yyyy")
        self.reg_date.setMinimumDate(first)
        self.reg_date.setMaximumDate(last)
        form.addRow("Дата акта *", self.reg_date)

        self.period_from = QDateEdit(first)
        self.period_from.setCalendarPopup(True)
        self.period_from.setDisplayFormat("dd.MM.yyyy")
        self.period_from.setMinimumDate(first)
        self.period_from.setMaximumDate(last)

        self.period_to = QDateEdit(last)
        self.period_to.setCalendarPopup(True)
        self.period_to.setDisplayFormat("dd.MM.yyyy")
        self.period_to.setMinimumDate(first)
        self.period_to.setMaximumDate(last)

        period_row = QHBoxLayout()
        period_row.addWidget(self.period_from)
        period_row.addWidget(QLabel("—"))
        period_row.addWidget(self.period_to)
        form.addRow("Отчётный период *", period_row)
        root.addLayout(form)

        root.addWidget(QLabel("Работы и услуги, которые попадут в PDF и получат этот акт:"))

        quick = QHBoxLayout()
        included = QPushButton("Все включённые в оценку")
        select_all = QPushButton("Выбрать все")
        clear = QPushButton("Снять все")
        quick.addWidget(included)
        quick.addWidget(select_all)
        quick.addWidget(clear)
        quick.addStretch(1)
        root.addLayout(quick)

        self.list = QListWidget()
        included_ids = {item.id for item in items if item.included}
        for item in items:
            row = QListWidgetItem(item.work_name)
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                Qt.CheckState.Checked
                if item.id in included_ids
                else Qt.CheckState.Unchecked
            )
            self.list.addItem(row)
        root.addWidget(self.list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Сформировать PDF и применить"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)

        included.clicked.connect(
            lambda: self._set_checks({item.id for item in self.items if item.included})
        )
        select_all.clicked.connect(
            lambda: self._set_checks({item.id for item in self.items})
        )
        clear.clicked.connect(lambda: self._set_checks(set()))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

    def _set_checks(self, item_ids: set[str]) -> None:
        for index in range(self.list.count()):
            row = self.list.item(index)
            row.setCheckState(
                Qt.CheckState.Checked
                if row.data(Qt.ItemDataRole.UserRole) in item_ids
                else Qt.CheckState.Unchecked
            )

    def selected_item_ids(self) -> list[str]:
        result: list[str] = []
        for index in range(self.list.count()):
            row = self.list.item(index)
            if row.checkState() == Qt.CheckState.Checked:
                result.append(str(row.data(Qt.ItemDataRole.UserRole)))
        return result

    def _accept(self) -> None:
        if not self.number_edit.text().strip():
            QMessageBox.warning(self, "Укажите номер", "Номер акта обязателен.")
            return
        if self.period_from.date() > self.period_to.date():
            QMessageBox.warning(
                self,
                "Проверьте период",
                "Дата начала периода не может быть позже даты окончания.",
            )
            return
        if not self.selected_item_ids():
            QMessageBox.warning(
                self,
                "Не выбраны работы",
                "Выберите хотя бы одну работу или услугу.",
            )
            return
        self.accept()

    def values(self) -> dict:
        return {
            "number": self.number_edit.text().strip(),
            "reg_date": self.reg_date.date().toString("dd.MM.yyyy"),
            "period_from": self.period_from.date().toString("dd.MM.yyyy"),
            "period_to": self.period_to.date().toString("dd.MM.yyyy"),
            "item_ids": self.selected_item_ids(),
        }


class QualityMonthActsDialog(QDialog):
    def __init__(
        self,
        store: QualityAssessmentStore,
        *,
        period_id: str,
        organization_name: str,
        house_address: str,
        year: int,
        month: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.period_id = period_id
        self.organization_name = organization_name
        self.house_address = house_address
        self.year = int(year)
        self.month = int(month)
        self.setWindowTitle("Акты месяца")
        self.resize(880, 560)

        root = QVBoxLayout(self)
        note = QLabel(
            "Акт хранится в программе как один документ и может быть связан "
            "с несколькими работами. Это позволяет загрузить PDF один раз и "
            "применить его сразу ко всем нужным услугам."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.list = QListWidget()
        root.addWidget(self.list, 1)

        actions = QHBoxLayout()
        add = QPushButton("Добавить готовый акт")
        generate = QPushButton("Сформировать PDF")
        apply_to = QPushButton("Применить к…")
        apply_all = QPushButton("Ко всем включённым")
        save_copy = QPushButton("Сохранить копию PDF…")
        delete = QPushButton("Удалить акт")
        actions.addWidget(add)
        actions.addWidget(generate)
        actions.addWidget(apply_to)
        actions.addWidget(apply_all)
        actions.addWidget(save_copy)
        actions.addWidget(delete)
        root.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        root.addWidget(buttons)

        add.clicked.connect(self._add_ready)
        generate.clicked.connect(self._generate)
        apply_to.clicked.connect(self._edit_links)
        apply_all.clicked.connect(self._apply_all_included)
        save_copy.clicked.connect(self._save_copy)
        delete.clicked.connect(self._delete)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        self._reload()

    def _items(self) -> list[QualityAssessmentItem]:
        return self.store.list_items(self.period_id)

    def _current_act(self) -> QualityAssessmentAct | None:
        row = self.list.currentItem()
        if row is None:
            return None
        act_id = row.data(Qt.ItemDataRole.UserRole)
        return self.store.get_act(str(act_id)) if act_id else None

    def _reload(self, select_id: str | None = None) -> None:
        self.list.clear()
        for act in self.store.list_period_acts(self.period_id):
            act_name = (
                "Акт выполненных работ"
                if act.act_type == "ACCEPTANCE_ACT"
                else "Акт нарушения качества"
            )
            links = self.store.count_act_links(act.id)
            source = " • сформирован программой" if act.generated else ""
            remote = " • сохранён в ГИС" if act.remote_act_guid else ""
            text = (
                f"{act_name} • № {act.number} от {act.reg_date} • "
                f"{act.period_from}—{act.period_to} • {act.file_name} • "
                f"применён к {links} работам{source}{remote}"
            )
            row = QListWidgetItem(text)
            row.setData(Qt.ItemDataRole.UserRole, act.id)
            self.list.addItem(row)
            if select_id and act.id == select_id:
                self.list.setCurrentItem(row)

    def _choose_links(
        self,
        *,
        selected_ids: set[str] | None = None,
    ) -> list[str] | None:
        dialog = SelectActWorksDialog(
            items=self._items(),
            selected_ids=selected_ids,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.selected_item_ids()

    def _add_ready(self) -> None:
        items = self._items()
        if not items:
            return
        dialog = AddQualityActDialog(year=self.year, month=self.month, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        links = self._choose_links()
        if not links:
            return
        try:
            act = self.store.add_act(item_id=links[0], **dialog.values())
            self.store.set_act_links(act.id, links)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось добавить акт", str(exc))
            return
        self._reload(act.id)

    def _generate(self) -> None:
        items = self._items()
        if not items:
            return
        dialog = GenerateAcceptanceActDialog(
            items=items,
            year=self.year,
            month=self.month,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        selected = set(values["item_ids"])
        work_names = [item.work_name for item in items if item.id in selected]
        try:
            pdf = build_acceptance_act_pdf(
                organization_name=self.organization_name,
                house_address=self.house_address,
                act_number=values["number"],
                act_date=values["reg_date"],
                period_from=values["period_from"],
                period_to=values["period_to"],
                work_names=work_names,
            )
            file_name = (
                f"Акт_выполненных_работ_{self.month:02d}_{self.year}.pdf"
            )
            act = self.store.add_act(
                item_id=values["item_ids"][0],
                act_type="ACCEPTANCE_ACT",
                number=values["number"],
                reg_date=values["reg_date"],
                period_from=values["period_from"],
                period_to=values["period_to"],
                file_name=file_name,
                description=file_name,
                content_type="application/pdf",
                content=pdf,
                generated=True,
            )
            self.store.set_act_links(act.id, values["item_ids"])
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось сформировать акт", str(exc))
            return
        self._reload(act.id)

    def _edit_links(self) -> None:
        act = self._current_act()
        if act is None:
            return
        links = self._choose_links(
            selected_ids=set(self.store.act_link_item_ids(act.id))
        )
        if links is None:
            return
        try:
            self.store.set_act_links(act.id, links)
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось применить акт", str(exc))
            return
        self._reload(act.id)

    def _apply_all_included(self) -> None:
        act = self._current_act()
        if act is None:
            return
        targets = [item.id for item in self._items() if item.included]
        if not targets:
            QMessageBox.information(
                self,
                "Нет включённых работ",
                "Сначала включите нужные работы в оценку качества.",
            )
            return
        try:
            self.store.set_act_links(act.id, targets)
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось применить акт", str(exc))
            return
        self._reload(act.id)

    def _save_copy(self) -> None:
        act = self._current_act()
        if act is None or not act.content:
            QMessageBox.information(
                self,
                "Нет локального файла",
                "У выбранного акта нет локальной копии файла.",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить копию акта",
            act.file_name,
            "PDF (*.pdf);;Все файлы (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "wb") as target:
                target.write(act.content)
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось сохранить файл", str(exc))

    def _delete(self) -> None:
        act = self._current_act()
        if act is None:
            return
        links = self.store.count_act_links(act.id)
        answer = QMessageBox.question(
            self,
            "Удалить акт?",
            f"Акт сейчас применяется к {links} работам. Удалить его из всех связей?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_act(act.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Не удалось удалить акт", str(exc))
            return
        self._reload()
