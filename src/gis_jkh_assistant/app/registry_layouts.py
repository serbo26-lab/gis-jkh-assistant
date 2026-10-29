"""Registry selection, composition editing and house detail layout."""

from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .action_layout import ActionFlowLayout, PairedActionBar
from .page_layouts import command, command_menu, context_for, page_root, table, viewport_table, workspace_card
from .icons import bind_icons, set_action_icon, set_button_icon, set_search_icon
from .registry_workflow import check_work_list, refresh_work_lists, replace_from_template
from .widgets import SafeComboBox, SafeSpinBox, ensure_combo_chevron
from .workflow_widgets import (
    ActionMenuButton,
    OperationStatus,
    SectionCard,
    WorkflowPanel,
    style_danger_button,
)


class WorkListDetailsCard(SectionCard):
    """Keep the period visible without giving technical metadata the whole page."""

    def __init__(self):
        super().__init__("Период и параметры выбранного перечня", fit_content=True)
        self.period_grid = QGridLayout()
        self.summary_grid = QGridLayout()
        self.period_fields = []
        self.summary_fields = []
        self._columns = None
        self.body.addLayout(self.period_grid)
        self.body.addLayout(self.summary_grid)

    def add_field(self, label, control, *, period=False):
        field = QWidget()
        layout = QHBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        caption = QLabel(label)
        caption.setBuddy(control)
        caption.setObjectName("ContextCaption")
        layout.addWidget(caption)
        layout.addWidget(control, 1)
        (self.period_fields if period else self.summary_fields).append(field)
        self._columns = None
        self._fit_fields()

    def _fit_fields(self):
        scale = float(QApplication.instance().property("uiScale") or 1.0)
        columns = (2 if self.width() >= 580 * scale else 1,
                   3 if self.width() >= 800 * scale else 2)
        if columns == self._columns:
            return
        self._columns = columns
        for grid, fields, count in (
            (self.period_grid, self.period_fields, columns[0]),
            (self.summary_grid, self.summary_fields, columns[1]),
        ):
            while grid.count():
                grid.takeAt(0)
            for index, field in enumerate(fields):
                grid.addWidget(field, index // count, index % count)
            for column in range(3):
                grid.setColumnStretch(column, 1 if column < count else 0)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit_fields()

    def changeEvent(self, event):  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "_columns"):
            self._fit_fields()


def disclosure(title: str, body: QWidget, root, *, expanded: bool = False) -> QPushButton:
    toggle = QPushButton(f"Показать {title[:1].lower() + title[1:]}")
    set_button_icon(toggle, "chevron_down", size=20)
    toggle.setCheckable(True)
    toggle.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    def change(expanded):
        body.setVisible(expanded)
        toggle.setText(f"{'Скрыть' if expanded else 'Показать'} {title[:1].lower() + title[1:]}")
        set_button_icon(toggle, "chevron_up" if expanded else "chevron_down", size=20)
        toggle.setAccessibleDescription("Разворачивает или скрывает дополнительные поля")
        body.updateGeometry()
        if root.parentWidget():
            root.parentWidget().updateGeometry()

    toggle.toggled.connect(change)
    change(expanded)
    toggle.setChecked(expanded)
    root.addWidget(toggle)
    root.addWidget(body)
    return toggle


def build_work_lists(page):
    root = page_root(
        page,
        "Перечни работ и услуг",
        "Выберите перечень дома или создайте новый, затем подготовьте его к размещению",
    )
    context = context_for(page, downstream=False)
    page.work_lists = QListWidget(page)
    page.work_lists.hide()
    page.registry_combo = SafeComboBox()
    page.registry_combo.setPlaceholderText("Выберите существующий перечень")
    page.list_count = QLabel("0")
    command(page, "delete_button", "Удалить локальный перечень", page._delete_work_list)
    page.registry_combo.setParent(page.command_host)
    page.registry_combo.hide()
    page.creation_toggle = QPushButton("Новый перечень…")
    set_button_icon(page.creation_toggle, "plus")
    page.creation_toggle.setCheckable(True)

    for field in context.fields:
        field.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
    root.addWidget(context)
    page.context_status = QLabel()
    page.context_status.setObjectName("StatusBadge")
    page.context_status.setWordWrap(True)
    root.addWidget(page.context_status)
    creation = SectionCard("Создать отдельный перечень")
    creation_form = QFormLayout()
    creation_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    page.year_spin = SafeSpinBox()
    page.year_spin.setRange(2015, 2100)
    page.year_spin.setValue(datetime.now().year)
    page.year_spin.setMinimumWidth(130)
    page.template_combo = SafeComboBox()
    creation_form.addRow("Год нового перечня", page.year_spin)
    creation_form.addRow("Начальный состав", page.template_combo)
    creation.body.addLayout(creation_form)
    command(page, "create_button", "Создать перечень", page._create_work_list)
    command(
        page, "create_from_current_button", "Копировать выбранный", page._clone_current_work_list
    )
    command(page, "templates_button", "Шаблоны пользователя…", page._manage_templates)
    for button in (page.create_from_current_button, page.create_button):
        creation.actions.addWidget(button)
        button.show()
    creation.hide()
    page.creation_toggle.toggled.connect(creation.setVisible)
    root.addWidget(creation)
    page.workflow = WorkflowPanel(("Состав", "Проверить", "Проект в ГИС", "Разместить"))
    root.addWidget(page.workflow)
    for name, text, handler in (
        ("load_gis_lists_button", "Загрузить перечни дома", page._load_house_work_lists_from_gis),
        (
            "load_export_template_button",
            "Получить официальный шаблон",
            page._download_export_template_from_gis,
        ),
        (
            "load_export_template_file_button",
            "Выбрать XLSX вручную…",
            page._load_export_template_file,
        ),
        ("export_button", "Сформировать XLSX", page._export_current_work_list),
        ("upload_to_gis_button", "Отправить XLSX в ГИС", page._upload_current_work_list_to_gis),
        ("sync_gis_button", "Проверить статус", page._sync_current_work_list_from_gis),
        (
            "cancel_gis_button",
            "Отменить размещённый перечень",
            page._cancel_current_work_list_in_gis,
        ),
        (
            "save_editable_gis_button",
            "Сохранить изменения проекта",
            page._save_editable_work_list_to_gis,
        ),
        ("approve_gis_button", "Разместить", page._approve_current_work_list_in_gis),
        ("directory_button", "Справочник работ…", page._refresh_directory),
        ("project_save_button", "Сохранить проект в ГИС", page._save_or_prepare_project),
        ("add_works_button", "Добавить работу…", page._add_works_from_directory),
        ("remove_works_button", "Удалить выбранные строки", page._remove_selected_works),
        ("clear_works_button", "Очистить состав", page._clear_all_works),
        (
            "replace_from_template_button",
            "Заменить состав по шаблону…",
            lambda: replace_from_template(page),
        ),
        ("save_as_template_button", "Сохранить как шаблон…", page._save_current_as_template),
        ("lazy_fill_button", "Распределить сумму…", page._open_lazy_distribution),
    ):
        command(page, name, text, handler)
    page.workflow.direct_keys = {"new", "create", "add", "prepare", "save", "sync", "publish"}
    page.workflow.register("new", lambda: page.creation_toggle.setChecked(True))
    page.workflow.register("create", page.create_button)
    page.workflow.register("check", lambda: check_work_list(page))
    page.workflow.register("add", page.add_works_button)
    page.workflow.register("prepare", lambda: prepare_project(page))
    page.workflow.register("save", page.save_editable_gis_button)
    page.workflow.register("sync", page.sync_gis_button)
    page.workflow.register("publish", page.approve_gis_button)
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(2))
    # Retain legacy handles used by the presenter; all execution stays in commands.
    page.project_actions = command_menu("Проект в ГИС…", (page.save_editable_gis_button, page.approve_gis_button), name="upload")
    page.project_actions.setParent(page.command_host)
    page.gis_actions = command_menu("Обмен с ГИС ЖКХ", (page.load_gis_lists_button, page.sync_gis_button, page.save_editable_gis_button, page.approve_gis_button, page.cancel_gis_button), name="sync")
    action, button, _when = page.gis_actions.entries[-1]
    def cancellation_visible():
        selected = page.repository.get_work_list(page._current_work_list_id) if page._current_work_list_id else None
        return bool(selected and str(selected.gis_status or "").upper() == "APPROVED")
    page.gis_actions.entries[-1] = (action, button, cancellation_visible)
    page.network_hint = QLabel()
    page.network_hint.setObjectName("Muted")
    page.network_hint.setWordWrap(True)
    page.files_card = QWidget(page.command_host)
    page.files_actions_button = ActionMenuButton("Файлы Excel", "files")
    page.manual_template_action = page.files_actions_button.add_command(page.load_export_template_file_button)
    page.export_xlsx_action = page.files_actions_button.add_command(page.export_button)
    page.files_actions_button.menu_model.addSeparator()
    page.download_template_action = page.gis_actions.add_command(page.load_export_template_button)
    page.files_actions_button.add_command(page.upload_to_gis_button)
    page.xlsx_network_actions = command_menu("Обмен XLSX…", (page.load_export_template_button, page.upload_to_gis_button))
    page.xlsx_network_actions.setParent(page.command_host)
    page.xlsx_toggle = QPushButton(page.command_host)
    page.operation_status = OperationStatus()
    page.operation_status.hide()
    root.addWidget(page.operation_status)
    details = WorkListDetailsCard()
    page.selected_label = QLabel("Выбранный перечень: —", page)
    page.selected_label.hide()
    for prefix, label in (("start", "Период с"), ("end", "Период по")):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        month = SafeComboBox()
        month.addItems(
            [
                f"{i:02d} — {m}"
                for i, m in enumerate(
                    (
                        "Январь",
                        "Февраль",
                        "Март",
                        "Апрель",
                        "Май",
                        "Июнь",
                        "Июль",
                        "Август",
                        "Сентябрь",
                        "Октябрь",
                        "Ноябрь",
                        "Декабрь",
                    ),
                    1,
                )
            ]
        )
        year = SafeSpinBox()
        year.setRange(2015, 2100)
        year.setMinimumWidth(130)
        setattr(page, prefix + "_month", month)
        setattr(page, prefix + "_year", year)
        row.addWidget(month, 1)
        row.addWidget(year)
        details.add_field(label, widget, period=True)
        month.currentIndexChanged.connect(page._queue_period_save)
        year.valueChanged.connect(page._queue_period_save)
    for name, label in (
        ("template_label", "Шаблон"),
        ("status_label", "Статус"),
        ("planned_total_label", "Планируемая сумма"),
    ):
        widget = QLabel("—")
        widget.setWordWrap(True)
        setattr(page, name, widget)
        details.add_field(label, widget)
    technical = QWidget()
    technical_layout = QVBoxLayout(technical)
    technical_layout.setContentsMargins(0, 0, 0, 0)
    technical_form = QFormLayout()
    technical_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    page.gis_state_label = QLabel("—")
    page.gis_state_label.setWordWrap(True)
    technical_form.addRow("Сведения ГИС", page.gis_state_label)
    technical_layout.addLayout(technical_form)
    page.export_template_status = QLabel()
    page.export_template_status.setObjectName("Muted")
    page.export_template_status.setWordWrap(True)
    technical_layout.addWidget(page.export_template_status)
    page.details_more_toggle = disclosure(
        "сведения ГИС и файл экспорта", technical, details.body
    )
    page.details_card = details
    page.details_toggle = disclosure(
        "параметры выбранного перечня", details, root, expanded=False
    )
    page.templates_card = QWidget(page.command_host)
    page.items_card = workspace_card("Состав работ и услуг")
    page.composition_actions = ActionMenuButton("Состав и шаблоны", "lists")
    page.composition_actions.menu_model.addSection("Шаблоны")
    for button in (page.templates_button, page.replace_from_template_button,
                   page.save_as_template_button):
        page.composition_actions.add_command(button)
    page.composition_actions.menu_model.addSeparator()
    page.composition_actions.menu_model.addSection("Выбранные строки перечня")
    for button in (page.remove_works_button, page.clear_works_button, page.delete_button):
        page.composition_actions.add_command(button)
    page.composition_actions.setParent(page.command_host)
    page.composition_actions.hide()
    page.gis_actions.hide()
    page.gis_actions.setParent(page.command_host)
    page.registry_danger = ActionMenuButton("Опасные действия…", "danger")
    page.registry_danger.add_command(page.cancel_gis_button, when=cancellation_visible)
    page.registry_danger.setParent(page.command_host)
    page.registry_danger.hide()
    page.preparation_bar = PairedActionBar(
        (page.creation_toggle, page.directory_button, page.load_export_template_button,
         page.load_gis_lists_button, page.files_actions_button),
        (page.delete_button, page.cancel_gis_button),
    )
    page.preparation_bar.groups[0].setAccessibleName("Подготовка перечня")
    page.preparation_bar.groups[1].setAccessibleName("Удаление и отмена выбранного перечня")
    root.insertWidget(root.indexOf(page.context_status) + 1, page.preparation_bar)
    page.editing_bar = PairedActionBar(
        (page.add_works_button, page.remove_works_button, page.replace_from_template_button,
         page.clear_works_button, page.templates_button, page.save_as_template_button,
         page.lazy_fill_button),
        (page.project_save_button, page.approve_gis_button, page.sync_gis_button),
    )
    page.editing_bar.groups[0].setAccessibleName("Редактирование состава перечня")
    page.editing_bar.groups[1].setAccessibleName("Размещение перечня в ГИС")
    page.items_card.body.addWidget(page.editing_bar)
    page.remove_works_button.setText("Удалить строку")
    page.replace_from_template_button.setText("Заменить состав по шаблону…")
    page.lazy_fill_button.setText("Распределение суммы…")
    page.add_works_button.setProperty("workflowAlternate", True)
    root.removeWidget(page.details_toggle)
    root.removeWidget(details)
    page.details_toggle.setText("Параметры перечня")
    page.lazy_fill_button.setToolTip("Распределить сумму между работами и услугами выбранного перечня")
    page.details_toggle.hide()
    details.show()
    hint = QLabel("Локальные изменения сохраняются автоматически.")
    page.items_hint = hint
    hint.setWordWrap(True)
    hint.setObjectName("Muted")
    page.items_table = viewport_table(table(["Работа / услуга", "Цена", "Объем", "Кол-во", "Стоимость"]))
    page.items_card.body.addWidget(page.items_table, 1)
    page.items_card.body.addWidget(hint)
    root.addWidget(page.network_hint)
    root.addWidget(page.items_card, 1)
    # Only the editable period is above the composition; technical details stay optional.
    root.insertWidget(root.indexOf(page.workflow), details)
    page.organization_combo.currentIndexChanged.connect(page._organization_changed)
    page.house_combo.currentIndexChanged.connect(page._house_changed)
    page.work_lists.currentItemChanged.connect(page._work_list_changed)
    page.registry_combo.currentIndexChanged.connect(
        lambda index: page.work_lists.setCurrentRow(index)
    )
    page.items_table.itemChanged.connect(page._item_changed)
    page.creation_toggle.toggled.connect(lambda: refresh_work_lists(page))
    page._view_mode = "normal"
    page._set_editor_enabled(False)
    from .work_list_selector import install_selector
    install_selector(page, root, page.work_lists)
    # Creation belongs to preparation beside the selected registry, not the title.


def mirror_registry(page):
    combo = page.registry_combo
    rows = [
        (
            page.work_lists.item(i).text().replace("\n", " · "),
            page.work_lists.item(i).data(Qt.ItemDataRole.UserRole),
        )
        for i in range(page.work_lists.count())
    ]
    existing = [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]
    combo.blockSignals(True)
    if rows != existing:
        combo.clear()
        for label, key in rows:
            combo.addItem(label, key)
    combo.setCurrentIndex(page.work_lists.currentRow())
    combo.blockSignals(False)


def prepare_project(page):
    """Acquire the existing official export basis, then use the existing import path."""
    selected = page._current_work_list_id
    if page.flush_pending_changes() is False:
        return
    if page._integration_busy:
        return
    if page._export_template_path() is not None:
        page._upload_current_work_list_to_gis()
        return
    page._preparing_project = True
    page.operation_status.set_text("Получение файла-шаблона ГИС для подготовки перечня.")
    page.operation_status.show()
    page._download_export_template_from_gis()
    worker = page._integration_worker
    if worker is not None:
        obtained = False

        def succeeded(_result):
            nonlocal obtained
            obtained = page._export_template_path() is not None

        def ready():
            page._preparing_project = False
            def upload_if_open():
                if not getattr(page.window(), "_close_requested", False) and not getattr(page.window(), "_closing", False):
                    page._upload_current_work_list_to_gis()
            if obtained and selected == page._current_work_list_id and not getattr(page.window(), "_close_requested", False):
                QTimer.singleShot(0, upload_if_open)

        worker.succeeded.connect(succeeded)
        worker.finished.connect(ready)
    else:
        page._preparing_project = False


def adapt_houses(page):
    root = page.layout()
    for widget in page.findChildren(QLabel):
        if widget.text().startswith("XLSX-операции для домов"):
            root.removeWidget(widget)
            widget.hide()
    # Existing commands and their legacy actions remain one execution path.
    # The visible surface puts organization-wide downloads in plain sight.
    old_toolbar = page.files_actions_button.parentWidget()
    root.removeWidget(old_toolbar)
    old_toolbar.hide()
    page.gis_actions_button.setObjectName("")
    page.gis_actions_button.setText("Загрузка из ГИС ЖКХ")
    page.files_actions_button.setText("Файлы Excel")
    set_button_icon(page.files_actions_button, "files")
    page.add_house_button.setText("Добавить дом")
    page.sync_houses_button.setText("Загрузить все дома УО")
    page.sync_all_management_button.setText("Загрузить управление всеми домами")
    page.sync_all_work_lists_button.setText("Загрузить перечни всех домов")
    bind_icons(page, sync_houses_button="download-cloud", sync_all_management_button="download-cloud",
               sync_all_work_lists_button="download-cloud", sync_all_plans_button="download-cloud",
               sync_all_fixations_button="download-cloud", sync_all_quality_button="download-cloud",
               sync_all_fees_button="download-cloud",
               add_house_button="plus", export_house_data_button="excel", import_house_data_button="excel",
               sync_management_button="sync", add_tariff_button="plus", edit_tariff_button="edit",
               retry_save_button="save")
    set_search_icon(page.search_edit)
    page.sync_management_button.setText("Обновить управление из ГИС")
    page.sync_management_button.setMinimumWidth(0)
    page.splitter.setSizes([300, 780])
    page.splitter.widget(0).setMinimumWidth(210)
    page.splitter.widget(1).setMinimumWidth(360)
    for combo in (page.organization_combo, page.card_org_combo):
        ensure_combo_chevron(combo)
        combo.setMinimumWidth(0)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    page.command_host = QWidget(page)
    page.command_host.hide()
    page.workflow = WorkflowPanel(("Реестр УО", "Выбрать дом", "Проверить карточку", "Перечни"))
    page.workflow.register("organization", lambda: page.workflow.navigateRequested.emit(-1))
    page.workflow.register("load", page.sync_houses_button)
    page.workflow.register("reset_search", page.search_edit.clear)
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(1))
    root.insertWidget(1, page.workflow)
    page.bulk_actions_button = command_menu("Обновить все дома УО", (page.sync_all_management_button, page.sync_all_work_lists_button), name="sync")
    page.bulk_actions_button.setToolTip(
        "Обновить сведения об управлении и перечнях для домов выбранной УО."
    )
    # Existing Excel operations keep their original handlers and file formats.
    files_menu = page.files_actions_button.menu()
    files_menu.actions()[0].setText("Экспорт Excel: шаблон площадей и тарифов…")
    files_menu.actions()[1].setText("Импорт Excel: площади и тарифы…")
    page.import_houses_action.setText("Импорт Excel: дома из XLSX…")
    for action in files_menu.actions():
        set_action_icon(action, "excel")
    for button in (page.sync_all_management_button, page.sync_all_work_lists_button):
        button.setParent(page.command_host)
    page.add_house_button.setText("Добавить дом вручную")
    page.import_button.setText("Импортировать дома из XLSX…")
    set_button_icon(page.import_button, "files")
    house_list_actions = QWidget()
    page.house_list_actions = house_list_actions
    house_actions = ActionFlowLayout(house_list_actions)
    for button in (page.add_house_button, page.import_button):
        button.setProperty("compactCommand", True)
        house_actions.addWidget(button)
        button.show()
    list_layout = page.house_list.parentWidget().layout()
    list_layout.addWidget(house_list_actions)
    page.bulk_actions_button.hide()
    page.bulk_actions_button.setParent(page.command_host)
    page.files_actions_button.hide()
    page.files_actions_button.setParent(page.command_host)
    page.house_download_actions = QWidget()
    flow = ActionFlowLayout(page.house_download_actions)
    label = QLabel("Импорт из ГИС ЖКХ:")
    label.setObjectName("ToolbarGroupLabel")
    flow.addWidget(label)
    for button in (page.sync_houses_button, page.sync_all_management_button, page.sync_all_work_lists_button,
                   *page.portfolio_import_buttons.values()):
        button.setProperty("compactCommand", True)
        flow.addWidget(button)
        button.show()
    root.insertWidget(3, page.house_download_actions)
    page.house_local_actions = QWidget()
    local_flow = ActionFlowLayout(page.house_local_actions)
    local_label = QLabel("Импорт локальный:")
    local_label.setObjectName("ToolbarGroupLabel")
    local_flow.addWidget(local_label)
    for button in (page.export_house_data_button, page.import_house_data_button):
        button.setProperty("compactCommand", True)
        local_flow.addWidget(button)
        button.show()
    root.insertWidget(4, page.house_local_actions)
    page.export_house_data_button.setText("Экспорт данных в Excel…")
    page.import_house_data_button.setText("Импорт данных из Excel…")
    from .act_requisites_dialog import open_act_requisites
    page.sync_houses_button.setText("Загрузить дома")
    page.sync_all_management_button.setText("Загрузить управление")
    page.sync_all_work_lists_button.setText("Загрузить перечни")
    page.workflow.direct_keys = {"load"}
    page.workflow.detail.setText("Управление, перечни и площади можно загрузить отдельно; эти шаги необязательны.")

    # Preserve existing autosave widgets and signals while moving deletion to the detail.
    page.delete_house_button.parentWidget().layout().removeWidget(page.delete_house_button)
    page.delete_house_button.hide()
    items = []
    while page.card_layout.count():
        items.append(page.card_layout.takeAt(0))
    page.house_detail_title = QLabel("Выбранный дом: —")
    page.house_detail_title.setObjectName("SectionTitle")
    page.house_detail_title.setWordWrap(True)
    page.card_layout.addWidget(page.house_detail_title)
    page.act_requisites_button = QPushButton("Представители собственников и реквизиты акта…")
    set_button_icon(page.act_requisites_button, "signature")
    page.act_requisites_button.clicked.connect(lambda: open_act_requisites(page))
    page.card_layout.addWidget(page.act_requisites_button)
    section = None
    for item in items:
        widget = item.widget()
        if isinstance(widget, QLabel) and widget.objectName() == "SectionTitle":
            if widget.text() == "Начисления":
                widget.hide()
                continue
            title = "Основные данные" if widget.text() == "Карточка дома" else widget.text()
            if title == "Помещения":
                title = "Площадь и помещения"
            section = SectionCard(title)
            section.setObjectName("HouseSection")
            if title in {"Идентификаторы", "Служебное"}:
                toggle = disclosure(title, section, page.card_layout)
                if title == "Идентификаторы":
                    page.house_ids_toggle = toggle
            else:
                page.card_layout.addWidget(section)
            widget.hide()
        elif section is not None:
            if widget:
                section.body.addWidget(widget)
            elif item.layout():
                layout = item.layout()
                if (
                    isinstance(layout, QHBoxLayout)
                    and layout.count()
                    and isinstance(layout.itemAt(0).widget(), QLabel)
                    and layout.itemAt(0).widget().text() == "Тарифы по периодам"
                ):
                    section = SectionCard("Тарифы", icon_name="tariffs")
                    section.setObjectName("HouseSection")
                    page.card_layout.addWidget(section)
                    while layout.count():
                        child = layout.takeAt(0).widget()
                        if isinstance(child, QLabel):
                            child.hide()
                    page.add_tariff_button.setText("Добавить")
                    section.actions.addWidget(page.add_tariff_button)
                    section.actions.addWidget(page.edit_tariff_button)
                    page.delete_tariff_button.setText("Удалить тариф")
                    style_danger_button(page.delete_tariff_button)
                    set_button_icon(page.delete_tariff_button, "trash")
                    section.actions.addWidget(page.delete_tariff_button)
                    continue
                if isinstance(layout, QFormLayout):
                    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                section.body.addLayout(layout)
            else:
                page.card_layout.addItem(item)
    danger = SectionCard("Удаление дома")
    danger.setObjectName("HouseSection")
    hint = QLabel(
        "Удаление из локальной базы затронет связанные перечни, планы, фиксацию и оценки этого дома."
    )
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    danger.body.addWidget(hint)
    danger.actions.addWidget(page.delete_house_button)
    style_danger_button(page.delete_house_button)
    set_button_icon(page.delete_house_button, "trash")
    page.card_layout.addWidget(danger)
    page.operation_status = page.bulk_status_strip
    page.bulk_status_strip.hide()
    page.card_layout.setContentsMargins(8, 8, 8, 8)
    # Large text uses vertical page scrolling; no command row is compressed.
    content = QWidget()
    content.setObjectName("PageContent")
    content.setLayout(root)
    page.page_content = content
    page.page_scroll = QScrollArea()
    page.page_scroll.setObjectName("PageScroll")
    page.page_scroll.setWidgetResizable(True)
    page.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page.page_scroll.setWidget(content)
    root.setContentsMargins(12, 10, 12, 10)
    root.setSpacing(6)
    page.splitter.setMinimumHeight(220)
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(page.page_scroll)
    QWidget.setTabOrder(page.workflow.primary, page.workflow.help_button)
    QWidget.setTabOrder(page.workflow.help_button, page.sync_houses_button)
