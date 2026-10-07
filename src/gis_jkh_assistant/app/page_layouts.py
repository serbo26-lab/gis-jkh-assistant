"""Shared page composition; execution and persistence stay in page controllers."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QScrollArea, QTableWidget, QVBoxLayout, QWidget,
)

from .widgets import SafeComboBox, ScopeSwitcher
from .workflow_widgets import (
    ActionMenuButton, ContextBar, DangerZone, OperationStatus, PageHeader,
    SectionCard, WorkflowPanel,
)

MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)


def page_root(page, title: str, subtitle: str) -> QVBoxLayout:
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    page.page_scroll = QScrollArea()
    page.page_scroll.setObjectName("PageScroll")
    page.page_scroll.setWidgetResizable(True)
    page.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
    page.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page.page_content = QWidget()
    page.page_content.setObjectName("PageContent")
    page.page_scroll.setWidget(page.page_content)
    outer.addWidget(page.page_scroll)
    page.page_layout = QVBoxLayout(page.page_content)
    page.page_layout.setContentsMargins(18, 14, 18, 18)
    page.page_layout.setSpacing(10)
    header = PageHeader(title, subtitle)
    page.save_status = header.status
    page.page_layout.addWidget(header)
    return page.page_layout


def context_for(page, *, downstream: bool = True) -> ContextBar:
    context = ContextBar()
    page.organization_combo = SafeComboBox()
    page.house_combo = SafeComboBox()
    context.add_field("Управляющая организация", page.organization_combo)
    context.add_field("Дом", page.house_combo)
    if downstream:
        page.work_list_combo = SafeComboBox()
        page.work_list_combo.setPlaceholderText("Выберите размещённый перечень")
        page.year_combo = SafeComboBox()
        context.add_field("Размещённый перечень", page.work_list_combo)
        context.add_field("Год", page.year_combo)
    page.context_card = context
    return context


def command(page, name: str, text: str, handler=None) -> QPushButton:
    button = QPushButton(text, page)
    button.hide()
    setattr(page, name, button)
    if handler is not None:
        button.clicked.connect(handler)
    return button


class NameColumnSizer(QObject):
    """Keep work names readable; only a genuinely wide table scrolls horizontally."""
    def __init__(self, widget, column):
        super().__init__(widget)
        self.widget = widget
        self.column = column
        widget.viewport().installEventFilter(self)
        widget.horizontalHeader().sectionResized.connect(self._section_resized)

    def _section_resized(self, column, _old, _new):
        if column != self.column:
            self.resize_name()

    def resize_name(self):
        other = sum(self.widget.columnWidth(i) for i in range(self.widget.columnCount()) if i != self.column)
        self.widget.setColumnWidth(self.column, max(320, self.widget.viewport().width() - other))

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Resize:
            self.resize_name()
        return super().eventFilter(watched, event)


def table(columns, *, name_column: int = 0, table_class=QTableWidget) -> QTableWidget:
    widget = table_class(0, len(columns))
    widget.setHorizontalHeaderLabels(columns)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().setVisible(False)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    widget.horizontalHeader().setMinimumSectionSize(40)
    for column in range(len(columns)):
        widget.horizontalHeader().setSectionResizeMode(
            column, QHeaderView.ResizeMode.Interactive if column == name_column
            else QHeaderView.ResizeMode.ResizeToContents,
        )
    widget.name_column_sizer = NameColumnSizer(widget, name_column)
    widget.name_column_sizer.resize_name()
    return widget


def connect_context(page):
    page.organization_combo.currentIndexChanged.connect(page._organization_changed)
    page.house_combo.currentIndexChanged.connect(page._house_changed)
    page.work_list_combo.currentIndexChanged.connect(page._work_list_changed)
    page.year_combo.currentIndexChanged.connect(page._year_changed)


def build_planning(page, table_class):
    root = page_root(page, "Планирование", "Размещённый перечень → план на год → ГИС ЖКХ")
    root.addWidget(context_for(page))
    page.workflow = WorkflowPanel(("Перечень размещён", "Заполнить план", "Проверить", "Разместить"))
    root.addWidget(page.workflow)
    command(page, "distribute_button", "Автораспределить", page._distribute)
    command(page, "clear_button", "Очистить план за год", page._clear_year)
    command(page, "load_gis_button", "Загрузить из ГИС", page._load_from_gis)
    command(page, "publish_button", "Проверить и разместить", page._publish_to_gis)
    page.workflow.register("fill", page.distribute_button)
    page.workflow.register("publish", page.publish_button)
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(3))
    sync = QHBoxLayout()
    sync.addWidget(page.load_gis_button)
    page.load_gis_button.show()
    sync.addStretch(1)
    root.addLayout(sync)
    page.operation_status = OperationStatus()
    page.plan_status_strip = page.operation_status
    page.plan_status = page.operation_status.label
    root.addWidget(page.operation_status)
    page.empty_hint = QLabel("Выберите размещённый перечень. Если его ещё нет, начните в разделе «Перечни работ и услуг».")
    page.empty_hint.setWordWrap(True)
    page.empty_hint.setObjectName("Muted")
    root.addWidget(page.empty_hint)
    card = SectionCard("План по месяцам")
    card.actions.addWidget(page.distribute_button)
    page.distribute_button.show()
    danger = DangerZone("Очистка")
    danger.add_command(page.clear_button)
    card.actions.addWidget(danger)
    hint = QLabel("Количество сохраняется сразу. Двойной щелчок — конкретные даты; Del — очистить выделенные ячейки.")
    hint.setWordWrap(True)
    hint.setObjectName("Muted")
    card.body.addWidget(hint)
    page.table = table(["Работа / услуга", "В перечне", "В плане", "Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"], table_class=table_class)
    page.table.setMinimumHeight(280)
    page.table.setEditTriggers(QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.AnyKeyPressed)
    card.body.addWidget(page.table, 1)
    root.addWidget(card, 1)
    connect_context(page)
    page.table.itemChanged.connect(page._cell_changed)
    page.table.cellDoubleClicked.connect(page._open_dates)
    page.table.delete_requested.connect(page._clear_selected_cells)
    page._set_plan_enabled(False)


def build_monthly(page, kind: str):
    quality = kind == "quality"
    root = page_root(page, "Оценка качества" if quality else "Фиксация работ и услуг",
                     "Фиксация → оценка → акты → проект → размещение" if quality
                     else "План → фактические показатели → размещение → оценка качества")
    root.addWidget(context_for(page))
    months = SectionCard()
    from PySide6.QtWidgets import QGridLayout
    page.month_buttons_layout = QGridLayout()
    page.month_buttons_layout.setSpacing(4)
    page.month_buttons = {}
    for month, name in enumerate(MONTHS, 1):
        button = QPushButton(f"{name}\nПусто")
        button.setObjectName("MonthButton")
        button.setCheckable(True)
        button.setMinimumWidth(0)
        button.clicked.connect(lambda _checked=False, value=month: page._select_month(value))
        page.month_buttons[month] = button
    months.body.addLayout(page.month_buttons_layout)
    page._relayout_month_buttons()
    scope_row = QHBoxLayout()
    page.scope_switcher = ScopeSwitcher("month", "Выбранный месяц", "period", "Доступный период")
    scope_row.addWidget(page.scope_switcher)
    page.scope_hint = QLabel()
    page.scope_hint.setObjectName("Muted")
    page.scope_hint.setWordWrap(True)
    scope_row.addWidget(page.scope_hint, 1)
    months.body.addLayout(scope_row)
    root.addWidget(months)
    steps = ("Сформировать", "Акты", "Проект в ГИС", "Разместить") if quality else ("План готов", "Заполнить факт", "Проверить", "Разместить")
    page.workflow = WorkflowPanel(steps)
    root.addWidget(page.workflow)
    page.month_status = QLabel()
    page.month_status.setObjectName("Muted")
    page.month_status.setWordWrap(True)
    page.continue_queue_button = QPushButton("Продолжить очередь")
    page.continue_queue_button.hide()
    page.continue_queue_button.clicked.connect(page._continue_queue)
    page.operation_status = OperationStatus()
    page.queue_status_strip = page.operation_status
    page.queue_progress = page.operation_status.progress
    page.queue_label = page.operation_status.label
    page.operation_status.add_action(page.continue_queue_button)
    page.operation_status.hide()
    if quality:
        _quality_commands(page)
    else:
        _fixation_commands(page)
    network = QHBoxLayout()
    load = page.load_gis_button if quality else page.load_month_button
    load.setText("Загрузить из ГИС")
    load.show()
    network.addWidget(load)
    page.network_menu = ActionMenuButton("Другие действия")
    for name in (("save_button", "publish_button") if quality else ("publish_month_button",)):
        page.network_menu.add_command(getattr(page, name))
    network.addWidget(page.network_menu)
    network.addStretch(1)
    if quality:
        page.remote_danger = DangerZone()
        page.remote_danger.add_command(page.cancel_button, when=lambda: page.cancel_button.isEnabled())
        network.addWidget(page.remote_danger)
    root.addLayout(network)
    root.addWidget(page.operation_status)
    root.addWidget(page.month_status)
    page.empty_hint = QLabel("Выберите размещённый перечень и месяц для работы.")
    page.empty_hint.setObjectName("Muted")
    page.empty_hint.setWordWrap(True)
    root.addWidget(page.empty_hint)
    if quality:
        _quality_data(page, root)
    else:
        _fixation_data(page, root)
    connect_context(page)
    page.scope_switcher.scopeChanged.connect(page._scope_changed)
    page.table.itemChanged.connect(page._table_item_changed)
    page._set_enabled(False)


def _quality_commands(page):
    for name, text, handler in (
        ("form_month_button", "Сформировать оценку", page._form_scope_from_fixation),
        ("auto_act_month_button", "Сформировать акты", page._auto_generate_scope_act),
        ("clear_values_button", "Очистить поля перерасчёта", page._clear_scope_values),
        ("load_gis_button", "Загрузить из ГИС", page._load_scope_from_gis),
        ("save_button", "Сохранить проект", page._save_scope),
        ("publish_button", "Разместить", page._publish_scope),
        ("cancel_button", "Аннулировать оценку в ГИС", page._cancel),
        ("include_all_button", "Включить все", lambda: page._set_all_included(True)),
        ("exclude_all_button", "Снять все", lambda: page._set_all_included(False)),
        ("month_acts_button", "Акты месяца…", page._open_month_acts),
    ):
        command(page, name, text, handler)
    for key, name in (("form", "form_month_button"), ("acts", "auto_act_month_button"),
                      ("save", "save_button"), ("publish", "publish_button"),
                      ("resume", "continue_queue_button")):
        page.workflow.register(key, getattr(page, name))
    page.workflow.register("review_acts", page.month_acts_button)
    page.workflow.register("next", lambda: None)


def _fixation_commands(page):
    for name, text, handler in (
        ("fill_month_button", "Заполнить по плану", page._fill_scope_from_plan),
        ("clear_month_button", "Очистить факт", page._clear_scope),
        ("load_month_button", "Загрузить из ГИС", page._load_scope_from_gis),
        ("publish_month_button", "Разместить", page._publish_scope),
        ("add_unplanned_button", "Добавить работу", page._request_add_unplanned),
    ):
        command(page, name, text, handler)
    for key, name in (("fill", "fill_month_button"), ("publish", "publish_month_button"),
                      ("resume", "continue_queue_button")):
        page.workflow.register(key, getattr(page, name))
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(4))


def _quality_data(page, root):
    card = SectionCard("Работы в оценке качества")
    page.preparation_menu = ActionMenuButton("Подготовка")
    page.preparation_menu.add_command(page.form_month_button)
    page.preparation_menu.add_command(page.auto_act_month_button)
    page.preparation_menu.menu_model.addSeparator()
    page.preparation_menu.add_command(page.include_all_button)
    page.preparation_menu.add_command(page.exclude_all_button)
    card.actions.addWidget(page.preparation_menu)
    card.actions.addWidget(page.month_acts_button)
    page.month_acts_button.show()
    danger = DangerZone("Очистка")
    danger.add_command(page.clear_values_button)
    card.actions.addWidget(danger)
    page.month_acts_summary = QLabel("Акты месяца: —")
    page.month_acts_summary.setObjectName("Muted")
    card.body.addWidget(page.month_acts_summary)
    note = QLabel("Для обычного акта оставьте поля перерасчёта пустыми. Проект можно сохранить без актов; акты проверяются при размещении.")
    note.setWordWrap(True)
    note.setObjectName("Muted")
    card.body.addWidget(note)
    page.table = table(["В оценку", "Работа / услуга", "Стоимость в\nежемесячной плате", "Дней ненадлежащего\nкачества", "Дней в\nмесяце", "Уменьшение\nплаты", "Акты"], name_column=1)
    card.body.addWidget(page.table, 1)
    root.addWidget(card, 1)


def _fixation_data(page, root):
    card = SectionCard("Фактические показатели месяца")
    card.actions.addWidget(page.fill_month_button)
    page.fill_month_button.show()
    danger = DangerZone("Очистка")
    danger.add_command(page.clear_month_button)
    card.actions.addWidget(danger)
    page.table = table(["Работа / услуга", "Цена план", "Цена факт", "Объем план", "Объем факт", "Кол-во план", "Кол-во факт", "Стоимость план", "Стоимость факт", "Фото", "Статус"])
    card.body.addWidget(page.table)
    root.addWidget(card)
    unplanned = SectionCard("Внеплановые работы")
    unplanned.actions.addWidget(page.add_unplanned_button)
    page.add_unplanned_button.show()
    page.unplanned_table = table(["Работа / услуга", "Вид работы / услуги", "Ед. изм.", "Цена", "Объем", "Количество", "Стоимость", "Комментарий", "Фото", ""])
    unplanned.body.addWidget(page.unplanned_table)
    root.addWidget(unplanned)
    page.unplanned_table.itemChanged.connect(page._unplanned_item_changed)
    root.addStretch(1)
