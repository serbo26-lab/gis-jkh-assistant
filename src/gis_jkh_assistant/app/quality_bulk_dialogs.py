"""Bulk act management for monthly quality assessment."""

from __future__ import annotations

from .error_messages import context_text, error_text

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

from gis_jkh_assistant.acceptance_act_pdf import (
    AcceptanceActWorkRow,
    build_acceptance_act_pdf,
)
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
        selected_ids: set[str] | None = None,
        locked_ids: set[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.items = items
        self.year = int(year)
        self.month = int(month)
        self.locked_ids = set(locked_ids or set())
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
        included_ids = (
            set(selected_ids)
            if selected_ids is not None
            else {item.id for item in items if item.included}
        )
        for item in items:
            locked = item.id in self.locked_ids
            label = (
                f"{item.work_name} — есть пользовательский/импортированный акт"
                if locked
                else item.work_name
            )
            row = QListWidgetItem(label)
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            if locked:
                row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                row.setToolTip(
                    "Строка исключена из автоматического акта: "
                    "к ней уже привязан пользовательский или импортированный акт."
                )
                row.setCheckState(Qt.CheckState.Unchecked)
            else:
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
            item_id = str(row.data(Qt.ItemDataRole.UserRole))
            if item_id in self.locked_ids:
                row.setCheckState(Qt.CheckState.Unchecked)
                continue
            row.setCheckState(
                Qt.CheckState.Checked
                if item_id in item_ids
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
        context = QLabel(f"{organization_name} • {house_address} • {month:02d}.{year}")
        context.setWordWrap(True)
        context.setObjectName("Muted")
        root.addWidget(context)
        note = QLabel(
            "Акт хранится в программе как один документ и может быть связан "
            "с несколькими работами. Это позволяет загрузить PDF один раз и "
            "применить его сразу ко всем нужным услугам."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.list = QListWidget()
        root.addWidget(self.list, 1)

        from .action_layout import ActionFlowLayout
        from .workflow_widgets import style_danger_button

        actions = ActionFlowLayout()
        add = QPushButton("Добавить готовый акт")
        generate = QPushButton("Сформировать PDF с проверкой данных")
        apply_to = QPushButton("Связать с выбранными работами…")
        apply_all = QPushButton("Связать со всеми включёнными")
        save_copy = QPushButton("Сохранить копию файла…")
        delete = style_danger_button(QPushButton("Удалить акт"))
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
            QMessageBox.warning(self, "Не удалось добавить акт", error_text(exc, context=context_text(self)))
            return
        self._reload(act.id)

    def _act_work_rows(
        self,
        selected_items: list[QualityAssessmentItem],
    ) -> tuple[list[AcceptanceActWorkRow], list[str], bool]:
        period = self.store.get_period(self.period_id)
        fixation_period = (
            self.store.repository.get_work_fixation_period_for_month(
                period.work_list_id,
                year=self.year,
                month=self.month,
            )
            if period is not None
            else None
        )
        fixation_items = (
            self.store.repository.list_work_fixation_items(fixation_period.id)
            if fixation_period is not None
            else []
        )

        by_list_item_id = {
            item.work_list_item_id: item
            for item in fixation_items
            if item.work_list_item_id
        }
        by_guid = {
            item.work_guid: item
            for item in fixation_items
            if item.work_guid
        }
        by_code = {
            item.work_code: item
            for item in fixation_items
            if item.work_code
        }

        list_items = (
            self.store.repository.list_work_list_items(period.work_list_id)
            if period is not None
            else []
        )
        list_item_by_id = {item.id: item for item in list_items}

        rows: list[AcceptanceActWorkRow] = []
        missing_cost_names: list[str] = []
        for item in selected_items:
            fixation = (
                by_list_item_id.get(item.work_list_item_id)
                or (by_guid.get(item.work_guid) if item.work_guid else None)
                or (by_code.get(item.work_code) if item.work_code else None)
            )
            list_item = (
                list_item_by_id.get(item.work_list_item_id)
                if item.work_list_item_id
                else None
            )
            actual_cost = fixation.fact_total_cost if fixation else None
            if not actual_cost:
                missing_cost_names.append(item.work_name)

            rows.append(
                AcceptanceActWorkRow(
                    name=item.work_name,
                    unit_name=(
                        getattr(fixation, "unit_name", None)
                        or getattr(list_item, "unit_name", None)
                    ),
                    periodicity=(
                        fixation.fact_count
                        if fixation is not None
                        else None
                    ),
                    actual_cost=actual_cost,
                )
            )

        return rows, missing_cost_names, fixation_period is not None

    def _generate(self) -> None:
        items = self._items()
        if not items:
            return
        protected = {
            item.id
            for item in items
            if any(not act.generated for act in self.store.list_acts(item.id))
        }
        selected_ids = {
            item.id
            for item in items
            if item.included and item.id not in protected
        }
        if not selected_ids:
            QMessageBox.information(
                self,
                "Нет строк для автоматического акта",
                "У всех включённых работ уже есть пользовательские или "
                "импортированные акты. Они не будут перезаписаны.",
            )
            return
        dialog = GenerateAcceptanceActDialog(
            items=items,
            year=self.year,
            month=self.month,
            selected_ids=selected_ids,
            locked_ids=protected,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        selected = set(values["item_ids"])
        selected_items = [item for item in items if item.id in selected]
        work_rows, missing_costs, has_fixation = self._act_work_rows(
            selected_items
        )

        if missing_costs:
            examples = "\n".join(f"• {name}" for name in missing_costs[:5])
            if len(missing_costs) > 5:
                examples += f"\n… ещё: {len(missing_costs) - 5}"
            prefix = (
                "За выбранный месяц фиксация работ ещё не создана. "
                if not has_fixation
                else ""
            )
            answer = QMessageBox.question(
                self,
                "Не заполнена фактическая стоимость",
                prefix
                + f"Для {len(missing_costs)} из {len(work_rows)} работ нет "
                "значения «Стоимость факт.» в разделе «Фиксация работ и услуг». "
                "В сформированном акте эти ячейки останутся пустыми.\n\n"
                + examples
                + "\n\nСформировать акт?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            pdf = build_acceptance_act_pdf(
                organization_name=self.organization_name,
                house_address=self.house_address,
                act_number=values["number"],
                act_date=values["reg_date"],
                period_from=values["period_from"],
                period_to=values["period_to"],
                work_rows=work_rows,
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
            QMessageBox.warning(self, "Не удалось сформировать акт", error_text(exc, context=context_text(self)))
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
            QMessageBox.warning(self, "Не удалось применить акт", error_text(exc, context=context_text(self)))
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
            QMessageBox.warning(self, "Не удалось применить акт", error_text(exc, context=context_text(self)))
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
            "Все файлы (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "wb") as target:
                target.write(act.content)
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось сохранить файл", error_text(exc, context=context_text(self)))

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
            QMessageBox.warning(self, "Не удалось удалить акт", error_text(exc, context=context_text(self)))
            return
        self._reload()
