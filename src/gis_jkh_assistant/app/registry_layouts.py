"""Registry selection, composition editing and house detail layout."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .page_layouts import command, context_for, page_root, table
from .icons import set_button_icon
from .registry_workflow import check_work_list, refresh_work_lists, replace_from_template
from .widgets import SafeComboBox, SafeSpinBox, ensure_combo_chevron
from .workflow_widgets import (
    ActionMenuButton,
    DangerZone,
    OperationStatus,
    SectionCard,
    WorkflowPanel,
)


def disclosure(title: str, body: QWidget, root) -> QPushButton:
    toggle = QPushButton(f"Показать {title[:1].lower() + title[1:]}")
    set_button_icon(toggle, "chevron_down")
    toggle.setCheckable(True)
    toggle.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    def change(expanded):
        body.setVisible(expanded)
        toggle.setText(f"{'Скрыть' if expanded else 'Показать'} {title[:1].lower() + title[1:]}")
        set_button_icon(toggle, "chevron_up" if expanded else "chevron_down")
        toggle.setAccessibleDescription("Разворачивает или скрывает дополнительные поля")

    toggle.toggled.connect(change)
    body.hide()
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
    context.add_field("Существующий перечень", page.registry_combo)
    page.creation_toggle = QPushButton("Новый перечень…")
    page.creation_toggle.setCheckable(True)
    context.add_field("Создание", page.creation_toggle)
    root.addWidget(context)
    page.context_status = QLabel()
    page.context_status.setObjectName("StatusBadge")
    page.context_status.setWordWrap(True)
    root.addWidget(page.context_status)
    creation = SectionCard("Создать отдельный перечень")
    row = QHBoxLayout()
    page.year_spin = SafeSpinBox()
    page.year_spin.setRange(2015, 2100)
    page.year_spin.setValue(datetime.now().year)
    page.year_spin.setFixedWidth(130)
    page.template_combo = SafeComboBox()
    row.addWidget(QLabel("Год нового перечня"))
    row.addWidget(page.year_spin)
    row.addWidget(page.template_combo, 1)
    creation.body.addLayout(row)
    command(page, "create_button", "Создать перечень", page._create_work_list)
    command(
        page, "create_from_current_button", "Копировать выбранный", page._clone_current_work_list
    )
    command(page, "templates_button", "Шаблоны пользователя…", page._manage_templates)
    for button in (page.templates_button, page.create_from_current_button, page.create_button):
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
        ("delete_button", "Удалить локальный перечень", page._delete_work_list),
        ("add_works_button", "Добавить работу…", page._add_works_from_directory),
        ("remove_works_button", "Удалить выбранные строки", page._remove_selected_works),
        ("clear_works_button", "Очистить состав", page._clear_all_works),
        (
            "replace_from_template_button",
            "Заменить по шаблону",
            lambda: replace_from_template(page),
        ),
        ("save_as_template_button", "Сохранить как шаблон…", page._save_current_as_template),
        ("lazy_fill_button", "Распределить сумму…", page._open_lazy_distribution),
    ):
        command(page, name, text, handler)
    page.workflow.register("new", lambda: page.creation_toggle.setChecked(True))
    page.workflow.register("create", page.create_button)
    page.workflow.register("check", lambda: check_work_list(page))
    page.workflow.register("add", page.add_works_button)
    page.workflow.register("prepare", lambda: prepare_project(page))
    page.workflow.register("save", page.save_editable_gis_button)
    page.workflow.register("sync", page.sync_gis_button)
    page.workflow.register("publish", page.approve_gis_button)
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(2))
    network = SectionCard("Обмен выбранного дома и перечня с ГИС ЖКХ")
    page.load_gis_lists_button.show()
    network.actions.addWidget(page.load_gis_lists_button)
    page.sync_gis_button.show()
    network.actions.addWidget(page.sync_gis_button)
    page.project_actions = ActionMenuButton("Проект и размещение в ГИС", "upload")
    page.project_actions.add_command(
        page.save_editable_gis_button, when=lambda: page.save_editable_gis_button.isEnabled()
    )
    page.project_actions.add_command(
        page.approve_gis_button, when=lambda: page.approve_gis_button.isEnabled()
    )
    network.actions.addWidget(page.project_actions)
    page.remote_danger = DangerZone()
    page.remote_danger.add_command(
        page.cancel_gis_button, when=lambda: page.cancel_gis_button.isEnabled()
    )
    network.actions.addWidget(page.remote_danger)
    page.network_hint = QLabel()
    page.network_hint.setObjectName("Muted")
    page.network_hint.setWordWrap(True)
    network.body.addWidget(page.network_hint)
    root.addWidget(network)

    files = SectionCard("Файлы на компьютере и шаблоны пользователя")
    page.files_actions_button = ActionMenuButton("Локальные файлы XLSX", "files")
    page.manual_template_action = page.files_actions_button.add_command(
        page.load_export_template_file_button
    )
    page.export_xlsx_action = page.files_actions_button.add_command(page.export_button)
    files.actions.addWidget(page.files_actions_button)
    page.templates_button.show()
    files.actions.addWidget(page.templates_button)
    page.local_list_danger = DangerZone("Удалить локальный перечень")
    page.local_list_danger.add_command(page.delete_button)
    files.actions.addWidget(page.local_list_danger)
    root.addWidget(files)
    fallback = SectionCard("Резервный обмен с ГИС через XLSX")
    hint = QLabel("Используйте этот способ, если нужно обменяться техническим файлом. Обычный путь — кнопка следующего шага выше.")
    hint.setWordWrap(True)
    hint.setObjectName("Muted")
    fallback.body.addWidget(hint)
    page.xlsx_network_actions = ActionMenuButton("XLSX: получить из ГИС или отправить в ГИС", "sync")
    page.download_template_action = page.xlsx_network_actions.add_command(
        page.load_export_template_button
    )
    page.xlsx_network_actions.add_command(page.upload_to_gis_button)
    fallback.actions.addWidget(page.xlsx_network_actions)
    page.xlsx_toggle = disclosure("резервный обмен через XLSX с ГИС ЖКХ", fallback, root)
    page.operation_status = OperationStatus()
    page.operation_status.hide()
    root.addWidget(page.operation_status)
    details = SectionCard("Период и параметры выбранного перечня")
    page.selected_label = QLabel("Выбранный перечень: —")
    details.body.addWidget(page.selected_label)
    form = QFormLayout()
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
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
        year.setFixedWidth(130)
        setattr(page, prefix + "_month", month)
        setattr(page, prefix + "_year", year)
        row.addWidget(month, 1)
        row.addWidget(year)
        form.addRow(label, widget)
        month.currentIndexChanged.connect(page._queue_period_save)
        year.valueChanged.connect(page._queue_period_save)
    for name, label in (
        ("template_label", "Шаблон"),
        ("status_label", "Статус"),
        ("planned_total_label", "Планируемая сумма"),
        ("gis_state_label", "Сведения ГИС"),
    ):
        widget = QLabel("—")
        widget.setWordWrap(True)
        setattr(page, name, widget)
        form.addRow(label, widget)
    details.body.addLayout(form)
    page.export_template_status = QLabel()
    page.export_template_status.setObjectName("Muted")
    page.export_template_status.setWordWrap(True)
    details.body.addWidget(page.export_template_status)
    page.details_toggle = disclosure("параметры выбранного перечня", details, root)
    page.items_card = SectionCard("Состав работ и услуг")
    for button in (page.add_works_button, page.lazy_fill_button):
        button.show()
        page.items_card.actions.addWidget(button)
    composition = ActionMenuButton("Состав и шаблоны")
    page.composition_menu = composition
    composition.add_command(page.save_as_template_button)
    composition.menu_model.addSeparator()
    composition.add_command(page.replace_from_template_button)
    page.items_card.actions.addWidget(composition)
    danger = DangerZone("Очистка")
    page.composition_danger = danger
    danger.add_command(page.remove_works_button)
    danger.add_command(page.clear_works_button)
    page.items_card.actions.addWidget(danger)
    hint = QLabel(
        "В строке: цена + объём + количество либо количество + стоимость. Локальные изменения сохраняются автоматически."
    )
    page.items_hint = hint
    hint.setWordWrap(True)
    hint.setObjectName("Muted")
    page.items_card.body.addWidget(hint)
    page.items_table = table(["Работа / услуга", "Цена", "Объем", "Кол-во", "Стоимость"])
    page.items_table.setMinimumHeight(300)
    page.items_card.body.addWidget(page.items_table, 1)
    root.addWidget(page.items_card, 1)
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
    page._save_period()
    if page._integration_busy:
        return
    if page._export_template_path() is not None:
        page._upload_current_work_list_to_gis()
        return
    page._preparing_project = True
    page.operation_status.set_text("Подготовка проекта • получение основы для сохранения из ГИС")
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
            if obtained and selected == page._current_work_list_id:
                QTimer.singleShot(0, page._upload_current_work_list_to_gis)

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
    old_toolbar = page.organization_actions_button.parentWidget()
    root.removeWidget(old_toolbar)
    old_toolbar.hide()
    page.gis_actions_button.setObjectName("")
    page.gis_actions_button.setText("Загрузка из ГИС ЖКХ")
    page.organization_actions_button.setText("Управляющая организация")
    page.files_actions_button.setText("Файлы на компьютере")
    set_button_icon(page.files_actions_button, "files")
    page.add_house_button.setText("Добавить дом")
    page.add_org_button.setText("Добавить УО вручную")
    page.edit_org_button.setText("Изменить реквизиты УО")
    page.sync_houses_button.setText("Загрузить все дома УО")
    page.sync_all_management_button.setText("Загрузить управление всеми домами")
    page.sync_all_work_lists_button.setText("Загрузить перечни всех домов")
    set_button_icon(page.sync_houses_button, "houses")
    set_button_icon(page.sync_all_management_button, "organization")
    set_button_icon(page.sync_all_work_lists_button, "lists")
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
    page.organization_actions_button.menu().insertSeparator(page.organization_delete_action)
    page.organization_delete_action.setText("Удалить УО и её данные…")
    page.workflow = WorkflowPanel(("Реестр УО", "Выбрать дом", "Проверить карточку", "Перечни"))
    page.workflow.register("organization", page.add_org_button)
    page.load_organization_button = QPushButton("Добавить УО из ГИС")
    set_button_icon(page.load_organization_button, "download")
    def load_organization():
        from .organization_sync import load_organization_from_gis
        load_organization_from_gis(page)
    page.load_organization_button.clicked.connect(load_organization)
    page.workflow.register("organization_load", page.load_organization_button)
    page.workflow.register("load", page.sync_houses_button)
    page.workflow.register("reset_search", page.search_edit.clear)
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(1))
    root.insertWidget(2, page.workflow)
    page.organization_sync_card = SectionCard("Загрузка из ГИС ЖКХ — вся управляющая организация")
    hint = QLabel("Эти действия относятся ко всей выбранной УО, независимо от дома в карточке. Реквизиты УО берутся из открытого личного кабинета: перед добавлением их можно проверить.")
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    page.organization_sync_card.body.addWidget(hint)
    for button in (page.load_organization_button, page.sync_houses_button,
                   page.sync_all_management_button, page.sync_all_work_lists_button):
        page.organization_sync_card.actions.addWidget(button)
        button.show()
    root.insertWidget(3, page.organization_sync_card)
    page.manual_actions_card = SectionCard("Ручное добавление и файлы на компьютере")
    for button in (page.add_org_button, page.edit_org_button, page.add_house_button,
                   page.files_actions_button):
        page.manual_actions_card.actions.addWidget(button)
        button.show()
    page.organization_danger = DangerZone("Удаление УО")
    page.organization_danger.add_command(page.delete_org_button)
    page.manual_actions_card.actions.addWidget(page.organization_danger)
    root.insertWidget(4, page.manual_actions_card)
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
                    section = SectionCard("Тарифы")
                    page.card_layout.addWidget(section)
                    while layout.count():
                        child = layout.takeAt(0).widget()
                        if isinstance(child, QLabel):
                            child.hide()
                    page.add_tariff_button.setText("Добавить")
                    section.actions.addWidget(page.add_tariff_button)
                    section.actions.addWidget(page.edit_tariff_button)
                    tariff_danger = DangerZone("Удаление")
                    tariff_danger.add_command(page.delete_tariff_button)
                    page.delete_tariff_button.hide()
                    section.actions.addWidget(tariff_danger)
                    continue
                if isinstance(layout, QFormLayout):
                    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                section.body.addLayout(layout)
            else:
                page.card_layout.addItem(item)
    danger = SectionCard("Удаление дома")
    hint = QLabel(
        "Удаление из локальной базы затронет связанные перечни, планы, фиксацию и оценки этого дома."
    )
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    danger.body.addWidget(hint)
    danger.actions.addWidget(page.delete_house_button)
    page.delete_house_button.show()
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
    page.splitter.setMinimumHeight(340)
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(page.page_scroll)
