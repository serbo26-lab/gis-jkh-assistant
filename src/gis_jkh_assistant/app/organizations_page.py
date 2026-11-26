"""Organization controls moved from the houses registry, using existing editors."""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .act_requisites_dialog import ActRequisitesDialog
from .dialogs import OrganizationDialog, OrganizationForm
from .icons import set_button_icon
from .organization_sync import load_organization_from_gis, organization_import_reason
from .workflow_widgets import OperationStatus, SectionCard, WorkflowPanel, style_danger_button


class OrganizationsPage(QWidget):
    def __init__(self, repository, houses_page, integration_gateway=None):
        super().__init__()
        self.repository = repository
        self.houses_page = houses_page
        self.integration_gateway = integration_gateway
        self._bulk_operation_name = None
        self._organization_sync_worker = None
        self._current_organization_id = None
        self._saved_values = None
        self.requisites = None
        self.organization_combo = houses_page.organization_combo
        self.organization_combo.currentIndexChanged.disconnect(houses_page._organization_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        self.page_content = content
        body = QVBoxLayout(content)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(8)
        title = QLabel("Управляющая организация")
        title.setObjectName("Title")
        body.addWidget(title)
        actions = QHBoxLayout()
        self.load_organization_button = QPushButton("Добавить УО из ГИС ЖКХ…")
        set_button_icon(self.load_organization_button, "download")
        self.load_organization_button.clicked.connect(lambda: load_organization_from_gis(self))
        self.add_org_button = QPushButton("Добавить УО вручную…")
        set_button_icon(self.add_org_button, "add")
        self.add_org_button.clicked.connect(self._add_organization)
        actions.addWidget(self.load_organization_button)
        actions.addWidget(self.add_org_button)
        actions.addStretch(1)
        body.addLayout(actions)
        selector = QHBoxLayout()
        selector.addWidget(QLabel("УО"))
        selector.addWidget(self.organization_combo, 1)
        self.organization_combo.show()
        houses_page.organization_context.layout().itemAt(0).widget().setText("УО")
        body.addLayout(selector)
        self.workflow = WorkflowPanel(("Добавить УО", "Проверить реквизиты", "Дома"))
        self.workflow.register("organization", self.add_org_button)
        self.workflow.register("organization_load", self.load_organization_button)
        self.workflow.register("next", lambda: self.workflow.navigateRequested.emit(0))
        self.workflow.direct_keys = {"organization", "organization_load"}
        body.insertWidget(1, self.workflow)
        QWidget.setTabOrder(self.workflow.primary, self.workflow.help_button)
        QWidget.setTabOrder(self.workflow.help_button, self.load_organization_button)
        self.operation_status = OperationStatus()
        self.bulk_operation_status = self.operation_status.label
        self.operation_status.hide()
        body.addWidget(self.operation_status)
        self.empty_label = QLabel("Добавьте организацию из ГИС ЖКХ или вручную. Затем перейдите в раздел «Дома».")
        self.empty_label.setWordWrap(True)
        body.addWidget(self.empty_label)
        self.basic = SectionCard("Основные сведения")
        self.form = OrganizationForm()
        self.form.comment_edit.setMaximumHeight(60)
        self.basic.body.addWidget(self.form)
        self.save_button = QPushButton("Сохранить реквизиты УО")
        set_button_icon(self.save_button, "save")
        self.save_button.clicked.connect(self.flush_pending_changes)
        self.cancel_button = QPushButton("Отменить правки")
        self.cancel_button.clicked.connect(self._cancel_basic_edit)
        save_actions = QHBoxLayout()
        save_actions.addWidget(self.save_button)
        save_actions.addWidget(self.cancel_button)
        save_actions.addStretch(1)
        self.basic.body.addLayout(save_actions)
        body.addWidget(self.basic)
        self.people = SectionCard("Руководитель и подписанты актов")
        body.addWidget(self.people)
        self.delete_org_button = QPushButton("Удалить УО и её данные…")
        style_danger_button(self.delete_org_button)
        set_button_icon(self.delete_org_button, "trash")
        self.delete_org_button.clicked.connect(self._delete_organization)
        body.addWidget(self.delete_org_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addStretch(1)
        self.page_scroll = QScrollArea()
        self.page_scroll.setObjectName("PageScroll")
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setWidget(content)
        root.addWidget(self.page_scroll)
        self.organization_combo.currentIndexChanged.connect(self._organization_changed)
        self.refresh_current()

    def _bulk_worker_running(self):
        chief = getattr(self.requisites, "_worker", None)
        return any(worker is not None and worker.isRunning()
                   for worker in (self._organization_sync_worker, chief))

    def refresh_availability(self):
        busy = bool(self._bulk_operation_name is not None or self._bulk_worker_running()
                    or getattr(self, "_workflow_busy", False))
        houses_busy = bool(self.houses_page._bulk_operation_name or self.houses_page._bulk_worker_running()
                           or getattr(self.houses_page, "_workflow_busy", False))
        switch_busy = getattr(self.window(), "organization_switch_busy", lambda: False)()
        self.organization_combo.setEnabled(not busy and not houses_busy and not switch_busy)
        self.add_org_button.setEnabled(not busy and not houses_busy and not switch_busy)
        reason = organization_import_reason(self)
        self.load_organization_button.setEnabled(not reason and not busy and not houses_busy and not switch_busy)
        self.load_organization_button.setToolTip(reason or "Получить реквизиты из открытого кабинета и проверить перед добавлением.")
        available = bool(self._current_organization_id) and not busy and not houses_busy
        self.basic.setEnabled(available)
        self.delete_org_button.setEnabled(available)
        if self._current_organization_id:
            self.workflow.show_step(2, "Проверьте реквизиты УО и перейдите к домам",
                "Руководители и параметры подписи ниже используются при подготовке актов. Их можно заполнить позже.",
                "next", "Перейти к домам", reason="Дождитесь завершения операции." if busy else "")
        else:
            from_gis = not reason
            self.workflow.show_step(0, "Добавьте управляющую организацию",
                "Получите сведения из открытого кабинета ГИС ЖКХ или добавьте УО вручную.",
                "organization_load" if from_gis else "organization",
                "Добавить УО из ГИС ЖКХ" if from_gis else "Добавить УО вручную",
                reason="Дождитесь завершения операции." if busy else "")

    def _refresh_workflow(self):
        self.refresh_availability()

    def refresh_current(self):
        selected = self.organization_combo.currentData()
        organization = self.repository.get_organization(selected) if selected else None
        changed = selected != self._current_organization_id
        if not changed and self._saved_values is not None and self.form.values() != self._saved_values:
            return  # Never replace an unsaved edit with a timer/context refresh.
        if self._bulk_worker_running():
            return
        self._current_organization_id = selected
        self.form.set_organization(organization)
        self._saved_values = self.form.values()
        for widget in (self.basic, self.people, self.delete_org_button):
            widget.setVisible(organization is not None)
        self.empty_label.setVisible(organization is None)
        if changed or (organization and self.requisites is None):
            if self.requisites is not None:
                self.people.body.removeWidget(self.requisites)
                self.requisites.hide()
                self.requisites.deleteLater()
                self.requisites = None
            if organization:
                self.requisites = ActRequisitesDialog(
                    self.repository, organization_id=selected, page=self, embedded=True)
                self.people.body.addWidget(self.requisites)
                self.requisites.show()
        elif self.requisites is not None:
            self.requisites._refresh()
        self.refresh_availability()

    def flush_pending_changes(self):
        if (self._bulk_operation_name is not None or self._bulk_worker_running()
                or getattr(self, "_workflow_busy", False)):
            return False
        if not self._current_organization_id or self.form.values() == self._saved_values:
            return True
        if not self.form.validate():
            return False
        try:
            self.repository.update_organization(self._current_organization_id, **self.form.values())
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "УО уже существует", "Организация с таким ОГРН уже добавлена.")
            return False
        except sqlite3.Error as exc:
            QMessageBox.warning(self, "Не удалось сохранить УО", str(exc))
            return False
        self._saved_values = self.form.values()
        index = self.organization_combo.findData(self._current_organization_id)
        if index >= 0:
            self.organization_combo.setItemText(index, self._saved_values["name"])
        self.houses_page._reload_card_org_combo()
        self.houses_page._refresh_workflow()
        self._set_bulk_status("Реквизиты УО сохранены.")
        return True

    def _cancel_basic_edit(self):
        # The former edit dialog's Cancel action remains available inline.
        organization = self.repository.get_organization(self._current_organization_id)
        self.form.set_organization(organization)
        self._saved_values = self.form.values()
        self._set_bulk_status("Правки реквизитов отменены.")

    def _flush_autosave(self):
        guard = getattr(self.window(), "prepare_organization_change", None)
        if guard is not None and not guard():
            return False
        return self.flush_pending_changes() and self.houses_page.flush_pending_changes()

    def _organization_changed(self):
        selected_id = self.organization_combo.currentData()
        # The selector is shared with HousesPage. Flush in the OLD context:
        # a house autosave can refresh its registry, which must not select the
        # new organization's house before every page has accepted the switch.
        blocked = self.organization_combo.blockSignals(True)
        self.organization_combo.setCurrentIndex(self.organization_combo.findData(self._current_organization_id))
        self.organization_combo.blockSignals(blocked)
        if self._flush_autosave() is False:
            return
        blocked = self.organization_combo.blockSignals(True)
        self.organization_combo.setCurrentIndex(self.organization_combo.findData(selected_id))
        self.organization_combo.blockSignals(blocked)
        self.houses_page._organization_changed()
        self.refresh_current()

    def refresh_organizations(self, select_id=None):
        self.houses_page.refresh_organizations(select_id=select_id)
        self.refresh_current()

    def _add_organization(self):
        if not self._flush_autosave():
            return
        dialog = OrganizationDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            organization = self.repository.create_organization(**dialog.values())
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "УО уже существует", "Организация с таким ОГРН уже добавлена.")
            return
        self.refresh_organizations(select_id=organization.id)
        self.organization_combo.currentIndexChanged.emit(self.organization_combo.currentIndex())

    def _delete_organization(self):
        if not self._flush_autosave():
            return
        guard = getattr(self.window(), "can_delete_local_data", None)
        if guard is not None and not guard():
            return
        organization = self.repository.get_organization(self._current_organization_id)
        if organization is None:
            return
        answer = QMessageBox.question(self, "Удалить управляющую организацию?",
            f"Будет удалена УО «{organization.name}» и все ее дома из локальной базы. Продолжить?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.delete_organization(organization.id)
        self.houses_page._current_house_id = None
        self.refresh_organizations()
        self.organization_combo.currentIndexChanged.emit(self.organization_combo.currentIndex())

    def _set_bulk_status(self, text):
        self.operation_status.set_text(text)
        self.operation_status.show()

    def _begin_bulk_operation(self, name, text):
        if self._bulk_operation_name is not None or self._bulk_worker_running():
            return False
        self._bulk_operation_name = name
        self._set_bulk_status(text)
        self.refresh_availability()
        return True

    def _finish_bulk_operation(self, text):
        self._bulk_operation_name = None
        self._set_bulk_status(text)
        self.refresh_availability()
