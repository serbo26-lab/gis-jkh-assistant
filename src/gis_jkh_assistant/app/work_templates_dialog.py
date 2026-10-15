"""Dialog for reusable user work-list templates."""

from __future__ import annotations

from .error_messages import context_text, error_text

import shutil
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from gis_jkh_assistant.database.models import TemplateSource
from gis_jkh_assistant.database.paths import template_files_dir
from gis_jkh_assistant.database.repository import Repository
from gis_jkh_assistant.gis_files.work_lists import WorkTemplateImportError, parse_work_template
from .action_layout import ActionFlowLayout
from .workflow_widgets import style_danger_button


class WorkTemplatesDialog(QDialog):
    def __init__(
        self, repository: Repository, organization_id: str, parent=None,
        *, save_current=None, current_ready: bool = False,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.organization_id = organization_id
        self._save_current = save_current
        self.setWindowTitle("Шаблоны перечней")
        self.resize(760, 520)

        layout = QVBoxLayout(self)

        title = QLabel("Шаблоны пользователя")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        hint = QLabel(
            'Здесь хранятся пользовательские шаблоны состава перечня. Чтобы сохранить готовый состав, нажмите «Сохранить текущий перечень как шаблон…». При создании перечня работы шаблона проверяются по справочнику выбранной УО в ГИС.'
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setWordWrap(True)
        layout.addWidget(self.list_widget, 1)
        self.empty_guidance = QLabel()
        self.empty_guidance.setObjectName("Muted")
        self.empty_guidance.setWordWrap(True)
        layout.addWidget(self.empty_guidance)

        buttons = ActionFlowLayout()
        from .icons import bind_icons
        self.import_button = QPushButton("Импортировать состав из Excel")
        self.import_button.setObjectName("PrimaryButton")
        self.save_current_button = QPushButton("Сохранить текущий перечень как шаблон…")
        self.save_current_button.setEnabled(current_ready and callable(save_current))
        self.save_current_button.setToolTip(
            "Доступно, если выбран локальный перечень с работами этой УО."
        )
        self.rename_button = QPushButton("Переименовать")
        self.delete_button = style_danger_button(QPushButton("Удалить шаблон"))
        self.close_button = QPushButton("Закрыть")
        bind_icons(self, import_button="excel", save_current_button="save",
                   rename_button="edit", delete_button="trash")

        self.import_button.clicked.connect(self._import_excel)
        self.save_current_button.clicked.connect(self._save_from_current)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button.clicked.connect(self._delete)
        self.close_button.clicked.connect(self.accept)
        self.list_widget.currentItemChanged.connect(self._update_actions)

        for button in (
            self.import_button,
            self.save_current_button,
            self.rename_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.refresh()

    def _save_from_current(self) -> None:
        if not self.save_current_button.isEnabled() or self._save_current is None:
            return
        self._save_current()
        self.refresh()

    def _update_actions(self, *_args):
        empty = self.list_widget.count() == 0
        self.empty_guidance.setText(
            "Шаблонов пока нет. Импортируйте состав из Excel либо "
            "сохраните состав выбранного локального перечня."
            if self.save_current_button.isEnabled() else
            "Шаблонов пока нет. Импортируйте состав из Excel. "
            "Чтобы создать шаблон из перечня, выберите в разделе «Перечни» "
            "локальный перечень с работами этой УО."
        )
        self.empty_guidance.setVisible(empty)
        self.save_current_button.setVisible(empty)
        selected = bool(self._selected_template_id())
        self.rename_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)
        self.list_widget.setToolTip("Шаблонов пока нет. Импортируйте состав из Excel или сохраните состав перечня как шаблон."
                                    if not self.list_widget.count() else "Выберите шаблон")

    def refresh(self, select_id: str | None = None) -> None:
        self.list_widget.clear()
        templates = self.repository.list_work_templates(self.organization_id)
        for template in templates:
            legacy_basic = (
                template.name.strip().casefold() == "базовый"
                and template.source in {TemplateSource.IMPORT, TemplateSource.BUILTIN}
            )
            if template.source == TemplateSource.BUILTIN or legacy_basic:
                continue
            count = len(self.repository.list_work_template_items(template.id))
            source = "Excel" if template.source == TemplateSource.IMPORT else "Вручную"
            text = f"{template.name}\n{count} строк • {source}"
            if template.file_version:
                text += f" • версия файла {template.file_version}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            self.list_widget.addItem(item)
            if template.id == select_id:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.count() and not self.list_widget.currentItem():
            self.list_widget.setCurrentRow(0)
        self._update_actions()

    def _selected_template_id(self) -> str | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _import_excel(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Импорт шаблона перечня",
            "",
            "Excel (*.xlsx *.xlsm)",
        )
        if not filename:
            return

        try:
            imported = parse_work_template(Path(filename))
        except (WorkTemplateImportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "Не удалось импортировать шаблон", error_text(exc, context=context_text(self)))
            return

        default_name = Path(filename).stem
        name, ok = QInputDialog.getText(
            self,
            "Название шаблона",
            "Название:",
            text=default_name,
        )
        if not ok or not name.strip():
            return

        try:
            template = self.repository.create_work_template(
                organization_id=self.organization_id,
                name=name.strip(),
                items=imported.items,
                source=TemplateSource.IMPORT,
                source_file_name=Path(filename).name,
                source_house_guid=imported.house_guid,
                file_type=imported.file_type,
                file_version=imported.version,
            )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Название уже используется",
                "У этой УО уже есть шаблон с таким названием.",
            )
            return

        try:
            suffix = Path(filename).suffix.lower() or ".xlsx"
            stored_path = template_files_dir() / f"{template.id}{suffix}"
            shutil.copy2(filename, stored_path)
            self.repository.set_work_template_source_path(template.id, str(stored_path))
        except OSError as exc:
            self.repository.delete_work_template(template.id)
            QMessageBox.critical(
                self,
                "Не удалось сохранить исходный шаблон",
                error_text(exc, context=context_text(self)),
            )
            return

        period = ""
        if imported.start_year and imported.start_month and imported.end_year and imported.end_month:
            period = (
                f"\nПериод исходного файла: {imported.start_month:02d}.{imported.start_year} — "
                f"{imported.end_month:02d}.{imported.end_year}"
            )
        QMessageBox.information(
            self,
            "Шаблон импортирован",
            f"Импортировано строк: {len(imported.items)}{period}",
        )
        self.refresh(select_id=template.id)

    def _rename(self) -> None:
        template_id = self._selected_template_id()
        if not template_id:
            return
        template = self.repository.get_work_template(template_id)
        if not template:
            return
        name, ok = QInputDialog.getText(
            self,
            "Переименовать шаблон",
            "Название:",
            text=template.name,
        )
        if not ok or not name.strip() or name.strip() == template.name:
            return
        try:
            self.repository.rename_work_template(template_id, name.strip())
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Название уже используется",
                "У этой УО уже есть шаблон с таким названием.",
            )
            return
        self.refresh(select_id=template_id)

    def _delete(self) -> None:
        template_id = self._selected_template_id()
        if not template_id:
            return
        template = self.repository.get_work_template(template_id)
        if not template:
            return
        answer = QMessageBox.question(
            self,
            "Удалить шаблон?",
            f"Удалить шаблон «{template.name}»? Уже созданные перечни не изменятся.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        source_path = Path(template.source_file_path) if template.source_file_path else None
        self.repository.delete_work_template(template_id)
        if source_path and source_path.exists():
            try:
                source_path.unlink()
            except OSError:
                pass
        self.refresh()
