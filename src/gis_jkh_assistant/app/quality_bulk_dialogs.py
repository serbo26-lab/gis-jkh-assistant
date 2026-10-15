"""Bulk act management for monthly quality assessment."""

from __future__ import annotations
from dataclasses import asdict
import sqlite3

from .error_messages import context_text, error_text
from .icons import set_button_icon

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
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
from gis_jkh_assistant.acceptance_act_details import resolve_act_details, iso_date, has_quality_issues
from gis_jkh_assistant.database.act_requisites_store import ActRequisitesStore


class SelectActWorksDialog(QDialog):
    def __init__(
        self,
        *,
        items: list[QualityAssessmentItem],
        selected_ids: set[str] | None = None,
        act_label: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.items = items
        self.setWindowTitle("Выбрать работы для акта")
        self.resize(720, 520)

        root = QVBoxLayout(self)
        self.act_context = QLabel(act_label or "Выбор работ для акта")
        self.act_context.setObjectName("SectionTitle")
        self.act_context.setWordWrap(True)
        root.addWidget(self.act_context)
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
        for button in (included, select_all, clear):
            set_button_icon(button, "check-all")
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
        self.selection_count = QLabel()
        self.selection_count.setObjectName("Muted")
        root.addWidget(self.selection_count)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Применить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.list.itemChanged.connect(self._refresh_selection_count)
        self._refresh_selection_count()

        included.clicked.connect(self._select_included)
        select_all.clicked.connect(
            lambda: self._set_checks({item.id for item in self.items})
        )
        clear.clicked.connect(lambda: self._set_checks(set()))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

    def _refresh_selection_count(self, *_args) -> None:
        count = len(self.selected_item_ids())
        self.selection_count.setText(f"Выбрано: {count} из {self.list.count()}")
        self.apply_button.setEnabled(count > 0)

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
        repository=None, house_id=None, preview_callback=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.items = items
        self.year = int(year)
        self.month = int(month)
        self.locked_ids = set(locked_ids or set())
        self.repository, self.house_id = repository, house_id
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
        self.reg_date.setMinimumDate(QDate(1900, 1, 1))
        self.reg_date.setMaximumDate(QDate(2999, 12, 31))
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

        if repository is not None and house_id:
            parties = QFormLayout()
            self.executor_combo, self.customer_combo = QComboBox(), QComboBox()
            parties.addRow("Подписант УО на дату акта", self.executor_combo)
            parties.addRow("Представитель собственников", self.customer_combo)
            root.addLayout(parties)
            requisites_actions = QHBoxLayout()
            from .act_requisites_dialog import ActRequisitesDialog
            def edit_party(organization):
                house = repository.get_house(house_id)
                owner = {"organization_id": house.organization_id} if organization else {"house_id": house_id}
                # The data page owns network workers, keeping the repository alive.
                page = self.parent().parent() if self.parent() is not None else None
                dialog = ActRequisitesDialog(repository, page=page, **owner)
                dialog.exec()
                self._refresh_parties()
            for title, organization in (("Реквизиты УО…", True), ("Представитель и адрес дома…", False)):
                button = QPushButton(title)
                button.clicked.connect(lambda _=False, organization=organization: edit_party(organization))
                requisites_actions.addWidget(button)
            requisites_actions.addStretch(1)
            root.addLayout(requisites_actions)
            self.party_status = QLabel()
            self.party_status.setWordWrap(True)
            root.addWidget(self.party_status)
            self.reg_date.dateChanged.connect(self._refresh_parties)
            self.executor_combo.currentIndexChanged.connect(self._party_status)
            self.customer_combo.currentIndexChanged.connect(self._party_status)
            self._refresh_parties()

        root.addWidget(QLabel("Работы и услуги, которые попадут в PDF и получат этот акт:"))

        quick = QHBoxLayout()
        included = QPushButton("Все включённые в оценку")
        select_all = QPushButton("Выбрать все")
        clear = QPushButton("Снять все")
        for button in (included, select_all, clear):
            set_button_icon(button, "check-all")
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
                    'Работа пропущена: к ней уже прикреплён ваш акт или акт из ГИС.'
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
            "Создать PDF и связать с выбранными работами"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        root.addWidget(buttons)
        if preview_callback is not None:
            preview = buttons.addButton("Предпросмотр PDF…", QDialogButtonBox.ButtonRole.ActionRole)
            preview.clicked.connect(lambda: preview_callback(self.values()))

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

    def _refresh_parties(self, *_):
        store = ActRequisitesStore(self.repository)
        house = self.repository.get_house(self.house_id)
        point = self.reg_date.date().toString("yyyy-MM-dd")
        for combo, owner in ((self.executor_combo, {"organization_id": house.organization_id}),
                             (self.customer_combo, {"house_id": house.id})):
            selected = combo.currentData()
            people = store.people_on_date(point, **owner)
            combo.blockSignals(True)
            combo.clear()
            empty_label = "Не указан — в PDF будут подстановки" if owner.get("house_id") else "Нет записи на эту дату"
            combo.addItem("Выберите…" if people else empty_label, None)
            for person in people:
                validity = " — с " + person["valid_from"] if person["valid_from"] else " — начало периода не указано"
                combo.addItem(person["full_name"] + validity, person["id"])
            combo.setCurrentIndex(combo.findData(selected) if selected and combo.findData(selected) >= 0 else 1 if len(people) == 1 else 0)
            combo.blockSignals(False)
        self._party_status()

    def _details(self):
        return resolve_act_details(self.repository, self.house_id, self.reg_date.date().toString("yyyy-MM-dd"),
            executor_id=self.executor_combo.currentData(), customer_id=self.customer_combo.currentData())

    def _party_status(self, *_):
        if self.repository is not None:
            details = self._details()
            messages = details["issues"] + details["customer_notices"]
            self.party_status.setText(" ".join(messages) if messages else
                                     "Стороны и договор заполнены. Проверьте ФИО и реквизиты в предпросмотре.")

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
        if self.repository is not None:
            issues = self._details()["issues"]
            if issues:
                QMessageBox.warning(self, "Заполните реквизиты", "\n".join(issues) +
                    "\nЧерновик доступен в предпросмотре; для прикрепления акта заполните реквизиты.")
                return
        self.accept()

    def values(self) -> dict:
        return {
            "number": self.number_edit.text().strip(),
            "reg_date": self.reg_date.date().toString("dd.MM.yyyy"),
            "period_from": self.period_from.date().toString("dd.MM.yyyy"),
            "period_to": self.period_to.date().toString("dd.MM.yyyy"),
            "item_ids": self.selected_item_ids(),
            "executor_id": self.executor_combo.currentData() if self.repository is not None else None,
            "customer_id": self.customer_combo.currentData() if self.repository is not None else None,
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
            'Добавьте документ один раз, затем выберите все работы, к которым он относится. Для каждой работы отдельная копия файла не нужна.'
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.list, 1)

        self.empty_hint = QLabel("Актов пока нет. Добавьте готовый акт или сформируйте PDF.")
        self.empty_hint.setWordWrap(True)
        root.addWidget(self.empty_hint)

        from .action_layout import ActionFlowLayout
        from .workflow_widgets import style_danger_button

        actions = ActionFlowLayout()
        add = QPushButton("Добавить готовый акт")
        generate = QPushButton("Сформировать PDF с проверкой данных")
        self.generate_button = generate
        self.preparation_hint = QLabel()
        self.preparation_hint.setWordWrap(True)
        self.preparation_hint.hide()
        root.addWidget(self.preparation_hint)
        apply_to = QPushButton("Выбрать работы для акта…")
        apply_all = QPushButton("Применить к N включённым работам")
        save_copy = QPushButton("Сохранить копию файла…")
        delete = style_danger_button(QPushButton("Удалить акт"))
        for button, name in ((add, "plus"), (generate, "wand"), (apply_to, "check-all"),
                             (apply_all, "check-all"), (save_copy, "copy"), (delete, "trash")):
            set_button_icon(button, name)
        self.selection_buttons = (apply_to, apply_all, delete)
        self.save_copy_button = save_copy
        self.list.currentItemChanged.connect(self._update_actions)
        actions.addWidget(add)
        actions.addWidget(generate)
        root.addWidget(QLabel("Связи выбранного документа с работами"))
        actions_links = ActionFlowLayout()
        actions_links.addWidget(apply_to)
        actions_links.addWidget(apply_all)
        actions_links.addWidget(save_copy)
        danger_actions = ActionFlowLayout()
        danger_actions.addWidget(delete)
        root.addLayout(actions)
        root.addLayout(actions_links)
        root.addLayout(danger_actions)

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

    def _update_actions(self, *_args):
        act = self._current_act()
        for button in self.selection_buttons:
            button.setEnabled(act is not None)
        self.save_copy_button.setEnabled(bool(act and act.content))
        self.save_copy_button.setToolTip(act.file_name if act else "Сначала выберите акт")
        self.empty_hint.setVisible(self.list.count() == 0)
        count = sum(item.included for item in self._items())
        self.selection_buttons[1].setText(f"Применить к {count} включённым работам")

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
            source = "Сформирован программой" if act.generated else "Добавлен / импортирован"
            remote = "Сохранён в ГИС" if act.remote_act_guid else "Нет подтверждения ГИС"
            local = "Файл есть" if act.content else "Нет локального файла"
            text = (
                f"{act_name} • № {act.number} от {act.reg_date}\n"
                f"{source} • {remote} • {local} • Работ: {links}\n"
                f"Период: {act.period_from}—{act.period_to}"
            )
            row = QListWidgetItem(text)
            row.setToolTip(f"{text}\n{act.file_name} • {act.file_size / 1024:.1f} КБ")
            row.setData(Qt.ItemDataRole.UserRole, act.id)
            self.list.addItem(row)
            if select_id and act.id == select_id:
                self.list.setCurrentItem(row)
        self._update_actions()

    def _choose_links(
        self,
        *,
        selected_ids: set[str] | None = None,
        act_label: str = "",
    ) -> list[str] | None:
        dialog = SelectActWorksDialog(
            items=self._items(),
            selected_ids=selected_ids,
            act_label=act_label,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.selected_item_ids()

    def _add_ready(self) -> None:
        items = self._items()
        if not items:
            return
        dialog = AddQualityActDialog(
            year=self.year, month=self.month, house_address=self.house_address, parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        links = self._choose_links(
            act_label=(
                f"Акт № {dialog.number_edit.text().strip()} от "
                f"{dialog.reg_date.date().toString('dd.MM.yyyy')}"
            )
        )
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
        from gis_jkh_assistant.quality_automation import _acceptance_work_rows
        rows, _missing = _acceptance_work_rows(self.store, self.period_id, [item.id for item in selected_items])
        period = self.store.get_period(self.period_id)
        fixation = self.store.repository.get_work_fixation_period_for_month(period.work_list_id, year=self.year, month=self.month)
        return rows, [row.name for row in rows if row.actual_cost in (None, "")], fixation is not None

    def _generate(self, *, units_ready=False) -> None:
        page = self.parent()
        prepare = getattr(page, "_sync_work_units_for_act", None)
        if not units_ready and prepare is not None:
            if prepare(lambda: self._generate(units_ready=True) if self.isVisible() else None):
                self.generate_button.setEnabled(False)
                self.preparation_hint.setText("Уточняются единицы измерения… Можно закрыть окно: PDF не будет сформирован после закрытия.")
                self.preparation_hint.show()
                worker = page._integration_worker
                if worker is not None:
                    def finished():
                        self.generate_button.setEnabled(True)
                        self.preparation_hint.setText(page.operation_status.label.text())
                    worker.finished.connect(finished)
                return
        self.preparation_hint.hide()
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
            repository=self.store.repository,
            house_id=self.store.repository.get_work_list(self.store.get_period(self.period_id).work_list_id).house_id,
            preview_callback=self._preview_generated_act,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        selected = set(values["item_ids"])
        selected_items = [item for item in items if item.id in selected]
        work_rows, missing_costs, _has_fixation = self._act_work_rows(
            selected_items
        )

        selected_quality = [item for item in selected_items if has_quality_issues(item)]
        if selected_quality:
            QMessageBox.warning(self, "Проверьте принимаемые работы",
                "У выбранных работ есть дни ненадлежащего качества или перерасчёт. "
                "Акт содержит подтверждение надлежащего качества и отсутствия претензий. "
                "Уточните выбранные работы; акт о нарушении качества добавляется отдельным документом.")
            return

        if missing_costs:
            QMessageBox.warning(self, "Не заполнена стоимость",
                "У выбранных работ не заполнена фактическая стоимость. "
                "Уточните её в фиксации. Черновик с пустыми значениями можно посмотреть до сохранения акта.")
            return

        try:
            details = self._generated_details(values)
            if details["issues"]:
                raise ValueError(" ".join(details["issues"]))
            if iso_date(values["period_from"]) != f"{self.year}-{self.month:02d}-01" or iso_date(values["period_to"]) != f"{self.year}-{self.month:02d}-{QDate(self.year,self.month,1).daysInMonth():02d}":
                if QMessageBox.question(self, "Период короче месяца",
                    "Будут использованы месячные показатели выбранных работ без пропорционального пересчёта. "
                    "Они соответствуют указанному периоду выполнения?") != QMessageBox.StandardButton.Yes:
                    return
            details.update(work_rows=[asdict(row) for row in work_rows], **{k:v for k,v in values.items() if k != "item_ids"})
            pdf = build_acceptance_act_pdf(
                organization_name=self.organization_name,
                house_address=self.house_address,
                act_number=values["number"],
                act_date=values["reg_date"],
                period_from=values["period_from"],
                period_to=values["period_to"],
                work_rows=work_rows,
                details=details,
            )
            file_name = (
                f"Акт_выполненных_работ_{self.month:02d}_{self.year}.pdf"
            )
            with self.store.connection.atomic():
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
                ActRequisitesStore(self.store.repository).save_snapshot(act.id, details)
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            QMessageBox.warning(self, "Не удалось сформировать акт", error_text(exc, context=context_text(self)))
            return
        self._reload(act.id)

    def _generated_details(self, values):
        period = self.store.get_period(self.period_id)
        work_list = self.store.repository.get_work_list(period.work_list_id)
        return resolve_act_details(self.store.repository, work_list.house_id, values["reg_date"],
                                   executor_id=values.get("executor_id"), customer_id=values.get("customer_id"))

    def _preview_generated_act(self, values):
        from .pdf_preview_dialog import PdfPreviewDialog
        try:
            selected = set(values["item_ids"])
            rows, missing, _ = self._act_work_rows([item for item in self.store.list_items(self.period_id) if item.id in selected])
            details = self._generated_details(values)
            if missing:
                details["issues"].append("Не заполнена фактическая стоимость некоторых работ.")
            if any(has_quality_issues(item) for item in self.store.list_items(self.period_id) if item.id in selected):
                details["issues"].append("У выбранных работ есть замечания по качеству. Подтверждение надлежащего качества требует проверки.")
            pdf = build_acceptance_act_pdf(organization_name=self.organization_name,
                house_address=self.house_address, act_number=values["number"], act_date=values["reg_date"],
                period_from=values["period_from"], period_to=values["period_to"], work_rows=rows, details=details)
            PdfPreviewDialog(pdf, note="Предпросмотр не прикрепляет акт и ничего не отправляет в ГИС.", parent=self).exec()
        except (ValueError, RuntimeError, OSError) as exc:
            QMessageBox.warning(self, "Не удалось подготовить предпросмотр", str(exc))

    def _edit_links(self) -> None:
        act = self._current_act()
        if act is None:
            return
        links = self._choose_links(
            selected_ids=set(self.store.act_link_item_ids(act.id)),
            act_label=f"Акт № {act.number} от {act.reg_date}",
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
