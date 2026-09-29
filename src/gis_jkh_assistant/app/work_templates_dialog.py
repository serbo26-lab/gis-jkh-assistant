"""Dialog for reusable user work-list templates."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
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


class WorkTemplatesDialog(QDialog):
    def __init__(self, repository: Repository, organization_id: str, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.organization_id = organization_id
        self.setWindowTitle("Шаблоны перечней")
        self.resize(760, 520)

        layout = QVBoxLayout(self)

        title = QLabel("Шаблоны пользователя")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        hint = QLabel(
            "Шаблон хранится в рамках выбранной УО. Его можно импортировать из XLSX ГИС ЖКХ, "
            "переименовать и затем использовать для разных домов."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setWordWrap(True)
        layout.addWidget(self.list_widget, 1)

        buttons = QHBoxLayout()
        self.import_button = QPushButton("Импорт из Excel")
        self.import_button.setObjectName("PrimaryButton")
        self.empty_button = QPushButton("Пустой шаблон")
        self.rename_button = QPushButton("Переименовать")
        self.delete_button = QPushButton("Удалить")
        self.close_button = QPushButton("Закрыть")

        self.import_button.clicked.connect(self._import_excel)
        self.empty_button.clicked.connect(self._create_empty)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button.clicked.connect(self._delete)
        self.close_button.clicked.connect(self.accept)

        for button in (
            self.import_button,
            self.empty_button,
            self.rename_button,
            self.delete_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.refresh()

    def refresh(self, select_id: str | None = None) -> None:
        self.list_widget.clear()
        templates = self.repository.list_work_templates(self.organization_id)
        for template in templates:
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
            QMessageBox.critical(self, "Не удалось импортировать шаблон", str(exc))
            return

        existing = self.repository.list_work_templates(self.organization_id)
        default_name = "Базовый" if not existing else Path(filename).stem
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
                str(exc),
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

    def _create_empty(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый шаблон", "Название:")
        if not ok or not name.strip():
            return
        try:
            template = self.repository.create_work_template(
                organization_id=self.organization_id,
                name=name.strip(),
                source=TemplateSource.MANUAL,
            )
        except sqlite3.IntegrityError:
            QMessageBox.warning(
                self,
                "Название уже используется",
                "У этой УО уже есть шаблон с таким названием.",
            )
            return
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
