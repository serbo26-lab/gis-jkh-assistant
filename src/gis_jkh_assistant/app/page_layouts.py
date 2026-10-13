"""Shared page composition; execution and persistence stay in page controllers."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, QTimer, Qt
from PySide6.QtGui import QFont, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
    QScrollArea, QTableWidget, QVBoxLayout, QWidget,
)

from .widgets import SafeComboBox, ScopeSwitcher
from .action_layout import ActionFlowLayout
from .icons import set_button_icon
from .workflow_widgets import (
    ActionMenuButton, CommandGroup, ContextBar, OperationStatus, PageHeader, SectionCard, WorkflowPanel,
    WorkspaceCard, WorkspaceSplitter,
)

MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)


def month_button_text(month, state, compact=False):
    if not compact:
        return f"{MONTHS[month - 1]}\n{state}"
    names = ("Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек")
    states = {"не загружено": "не загр", "вне периода": "—", "проект в ГИС": "проект",
              "размещено": "в ГИС", "аннулировано": "отмен.", "не найдено": "нет",
              "локально": "лок.", "изменено": "измен."}
    return f"{names[month - 1]}\n{states.get(state, state)}"


class MonthSelectorCard(SectionCard):
    """Choose rows from real month-label widths, including larger UI text."""

    def __init__(self, page):
        super().__init__()
        self.body.setContentsMargins(6, 2, 6, 2)
        self.body.setSpacing(2)
        self.page = page
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit)

    def _queue_fit(self):
        if not self._fit_timer.isActive():
            self._fit_timer.start(0)

    def _fit(self):
        if not hasattr(self.page, "month_buttons"):
            return
        margins = self.body.contentsMargins()
        available = self.width() - margins.left() - margins.right()
        minimum = max(button.minimumSizeHint().width() for button in self.page.month_buttons.values())
        spacing = self.page.month_buttons_layout.spacing()
        columns = next((count for count in (12, 6, 4, 3, 2, 1)
                        if count * minimum + (count - 1) * spacing <= available), 1)
        if columns != self.page._month_button_columns:
            for column in range(12):
                self.page.month_buttons_layout.setColumnStretch(column, 0)
            self.page._relayout_month_buttons(columns)
        rows = (12 + columns - 1) // columns
        button_height = max(button.minimumSizeHint().height() for button in self.page.month_buttons.values())
        height = rows * button_height + (rows - 1) * spacing + margins.top() + margins.bottom() + self.body.spacing()
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._queue_fit()

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.LayoutRequest):
            if hasattr(self, "_fit_timer"):
                self._queue_fit()
        return result


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
    page.page_layout.setContentsMargins(10, 6, 10, 6)
    page.page_layout.setSpacing(4)
    header = PageHeader(title, subtitle)
    page.page_header = header
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
        page.work_list_combo = SafeComboBox(page)
        page.work_list_combo.hide()
        page.work_list_combo.setPlaceholderText("Выберите размещённый перечень")
        page.year_combo = SafeComboBox()
        context.add_field("Год", page.year_combo)
    page.context_card = context
    return context


def command(page, name: str, text: str, handler=None) -> QPushButton:
    if not hasattr(page, "command_host"):
        page.command_host = QWidget(page)
        page.command_host.hide()
    button = QPushButton(text, page.command_host)
    if name.startswith(("clear_", "cancel_", "delete_", "remove_", "replace_")):
        button.setObjectName("DangerButton")
        set_button_icon(button, "danger", "danger")
    button.hide()
    setattr(page, name, button)
    if handler is not None:
        button.clicked.connect(handler)
    return button


def command_menu(text, buttons, *, name="actions"):
    menu = ActionMenuButton(text, name)
    for button in buttons:
        menu.add_command(button)
    return menu


def viewport_table(widget):
    """The main table uses available space; secondary tables stay content-sized."""
    widget.setProperty("viewportManaged", True)
    widget.setMinimumHeight(160)
    widget.setMaximumHeight(16777215)
    widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    return widget


def workspace_card(title):
    card = WorkspaceCard()
    card.setProperty("mainWorkspace", True)
    card.setAccessibleName(title)
    return card


class NameColumnSizer(QObject):
    """Keep work names readable; only a genuinely wide table scrolls horizontally."""
    def __init__(self, widget, column):
        super().__init__(widget)
        self.widget = widget
        self.column = column
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.resize_name)
        widget.viewport().installEventFilter(self)
        widget.horizontalHeader().sectionResized.connect(self._section_resized)

    def _section_resized(self, column, _old, _new):
        if column != self.column:
            self.queue()

    def queue(self):
        if not self.timer.isActive():
            self.timer.start(0)

    def resize_name(self):
        other = sum(self.widget.columnWidth(i) for i in range(self.widget.columnCount())
                    if i != self.column and not self.widget.isColumnHidden(i))
        width = max(320, self.widget.viewport().width() - other)
        if self.widget.columnWidth(self.column) != width:
            self.widget.setColumnWidth(self.column, width)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Resize:
            self.queue()
        return super().eventFilter(watched, event)


class ReadableHeader(QHeaderView):
    """Qt's contents hint can omit styled multiline header padding after scale."""
    def sectionSizeFromContents(self, index):  # noqa: N802 - Qt API
        size = super().sectionSizeFromContents(index)
        item = self.parent().horizontalHeaderItem(index)
        if item is None:
            return size
        from PySide6.QtWidgets import QApplication
        scale = float(QApplication.instance().property("uiScale") or 1.0)
        font = QFont(self.font())
        font.setPointSizeF(9 * scale)
        font.setBold(True)
        metrics = QFontMetrics(font)
        lines = item.text().splitlines() or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines) + round(18 * scale)
        height = metrics.height() * len(lines) + round(10 * scale)
        return QSize(max(size.width(), width), max(size.height(), height))


def table(columns, *, name_column: int = 0, table_class=QTableWidget, header_class=ReadableHeader) -> QTableWidget:
    widget = table_class(0, len(columns))
    widget.setHorizontalHeader(header_class(Qt.Orientation.Horizontal, widget))
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
    widget.row_metrics_observer = TableHeightObserver(widget, lambda: resize_table_rows(widget, 4) if widget.property("viewportManaged") else fit_table_rows(widget))
    widget.model().rowsInserted.connect(widget.row_metrics_observer.queue)
    widget.model().modelReset.connect(widget.row_metrics_observer.queue)
    widget.model().dataChanged.connect(widget.row_metrics_observer.queue)
    return widget


def fit_table_rows(widget):
    """Keep text and embedded photo/act controls within full row rectangles."""
    from PySide6.QtWidgets import QApplication
    scale = float(QApplication.instance().property("uiScale") or 1.0)
    default = max(round(28 * scale), widget.fontMetrics().height() + round(10 * scale))
    if widget.verticalHeader().defaultSectionSize() != default:
        widget.verticalHeader().setDefaultSectionSize(default)
    control_widths = {}
    for row in range(widget.rowCount()):
        height = default
        for column in range(widget.columnCount()):
            cell = widget.cellWidget(row, column)
            if cell is not None:
                cell.ensurePolished()
                height = max(height, cell.minimumSizeHint().height() + round(4 * scale))
                control_widths[column] = max(control_widths.get(column, 0), cell.minimumSizeHint().width() + round(6 * scale))
        if widget.rowHeight(row) != height:
            widget.setRowHeight(row, height)
    header = widget.horizontalHeader()
    if isinstance(header, ReadableHeader):
        measured = getattr(widget, "_measured_header_columns", set())
        for column in range(widget.columnCount()):
            if header.sectionResizeMode(column) == QHeaderView.ResizeMode.ResizeToContents:
                measured.add(column)
            if column in measured:
                # Qt retains the old ResizeToContents width across stylesheet
                # font changes. Measure both text and embedded controls once,
                # so queued geometry updates converge to the same width.
                width = max(header.sectionSizeFromContents(column).width(), widget.sizeHintForColumn(column), control_widths.get(column, 0))
                if header.sectionResizeMode(column) != QHeaderView.ResizeMode.Interactive:
                    header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
                if widget.columnWidth(column) != width:
                    widget.setColumnWidth(column, width)
        widget._measured_header_columns = measured
    for column, width in control_widths.items():
        # ResizeToContents is deferred for a collapsed secondary table. Keep
        # its photo/delete controls readable even during a busy scale change.
        if widget.horizontalHeader().sectionResizeMode(column) != QHeaderView.ResizeMode.Interactive:
            widget.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        if widget.columnWidth(column) < width:
            widget.setColumnWidth(column, width)


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
    command(page, "load_gis_button", "Загрузить план года из ГИС", page._load_from_gis)
    command(page, "publish_button", "Разместить план года", page._publish_to_gis)
    page.workflow.register("fill", page.distribute_button)
    page.workflow.register("publish", page.publish_button)
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(3))
    page.workflow.register("load", page.load_gis_button)
    page.workflow.direct_keys = {"fill", "publish", "load"}
    page.operation_status = OperationStatus()
    page.operation_status.hide()
    page.plan_status_strip = page.operation_status
    page.plan_status = QLabel()
    page.plan_status.setObjectName("Muted")
    page.plan_status.setWordWrap(True)
    root.addWidget(page.plan_status)
    root.addWidget(page.operation_status)
    page.empty_hint = QLabel("Выберите размещённый перечень. Если его ещё нет, начните в разделе «Перечни работ и услуг».")
    page.empty_hint.setWordWrap(True)
    page.empty_hint.setObjectName("Muted")
    root.addWidget(page.empty_hint)
    card = workspace_card("План по месяцам")
    page.plan_actions = command_menu("Операции с планом", (page.load_gis_button, page.publish_button, page.clear_button), name="planning")
    page.plan_actions.setParent(page.command_host)
    page.plan_actions.hide()
    page.dates_button = QPushButton("Даты выбранной ячейки…")
    page.dates_button.setToolTip("Открыть конкретные даты выбранного месяца: Alt+D или двойной щелчок")
    for button in (page.distribute_button, page.dates_button, page.clear_button):
        card.actions.addWidget(button)
        button.show()
    _action_row(card, (page.load_gis_button, page.publish_button))
    hint = QLabel("Количество сохраняется сразу. Alt+D или двойной щелчок — даты; Del — очистить выделенные ячейки.")
    hint.setWordWrap(True)
    hint.setObjectName("Muted")
    page.table = table(["Работа / услуга", "В перечне", "В плане", "Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"], table_class=table_class)
    viewport_table(page.table)
    from .frozen_name import FrozenName
    page.table.frozen_name = FrozenName(page.table)
    page.table.setEditTriggers(QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.AnyKeyPressed)
    card.body.addWidget(page.table, 1)
    card.body.addWidget(hint)
    root.addWidget(card, 1)
    connect_context(page)
    page.table.itemChanged.connect(page._cell_changed)
    page.table.cellDoubleClicked.connect(page._open_dates)
    page.table.delete_requested.connect(page._clear_selected_cells)
    def dates_available():
        cell = page.table.currentItem()
        page.dates_button.setEnabled(bool(page.table.isEnabled() and page._current_plan_id and cell and cell.column() >= 3 and cell.flags() & Qt.ItemFlag.ItemIsEditable))
    def open_dates():
        dates_available()
        if page.dates_button.isEnabled():
            page._open_dates(page.table.currentRow(), page.table.currentColumn())
    page.table.currentCellChanged.connect(lambda *_: dates_available())
    page.dates_button.clicked.connect(open_dates)
    page.dates_shortcut = QShortcut(QKeySequence("Alt+D"), page)
    page.dates_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    page.dates_shortcut.activated.connect(open_dates)
    page.table.cell_actions = QMenu(page.table)
    dates_action = page.table.cell_actions.addAction("Даты выбранной ячейки… (Alt+D)")
    dates_action.triggered.connect(open_dates)
    clear_action = page.table.cell_actions.addAction("Очистить выделенные ячейки (Del)")
    clear_action.triggered.connect(page._clear_selected_cells)
    def show_cell_menu(position=None):
        dates_available()
        dates_action.setEnabled(page.dates_button.isEnabled())
        clear_action.setEnabled(bool(page.table.isEnabled() and any(item.column() >= 3 and item.flags() & Qt.ItemFlag.ItemIsEditable for item in page.table.selectedItems())))
        point = position if position is not None else page.table.visualItemRect(page.table.currentItem()).center()
        page.table.cell_actions.popup(page.table.viewport().mapToGlobal(point))
    page.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    page.table.customContextMenuRequested.connect(show_cell_menu)
    page.table.context_menu_requested.connect(show_cell_menu)
    dates_available()
    from .work_list_selector import install_selector
    install_selector(page, root, page.work_list_combo)
    page._set_plan_enabled(False)


def build_monthly(page, kind: str):
    quality = kind == "quality"
    root = page_root(page, "Оценка качества" if quality else "Фиксация работ и услуг",
                     "Работы, акты и оценка выбранного месяца" if quality else "Фактические показатели выбранного месяца")
    context = context_for(page)
    page.month_combo = SafeComboBox()
    for month, name in enumerate(MONTHS, 1):
        page.month_combo.addItem(name, month)
    page.month_combo.currentIndexChanged.connect(lambda: page._select_month(page.month_combo.currentData()))
    context.add_field("Месяц просмотра", page.month_combo)
    root.addWidget(context)
    months = MonthSelectorCard(page)
    page.months_card = months
    page.month_details_button = QPushButton("Все месяцы…", page)
    page.month_details_button.setCheckable(True)
    page.month_details_button.setAccessibleDescription("Показывает месяцы и их состояния; выбор месяца не меняет область команды периода")
    page.compact_month_row = QWidget(page)
    page.compact_month_row.hide()
    page.month_grid = QWidget()
    from PySide6.QtWidgets import QGridLayout
    page.month_buttons_layout = QGridLayout(page.month_grid)
    page.month_buttons_layout.setContentsMargins(0, 0, 0, 0)
    page.month_buttons_layout.setSpacing(4)
    page.month_buttons = {}
    page._month_button_columns = 6
    for month, name in enumerate(MONTHS, 1):
        button = QPushButton(f"{name}\n—")
        button.setObjectName("MonthButton")
        button.setCheckable(True)
        button.setMinimumWidth(0)
        button.clicked.connect(lambda _checked=False, value=month: page._select_month(value))
        page.month_buttons[month] = button
        page.month_buttons_layout.addWidget(button, (month - 1) // 6, (month - 1) % 6)
    months.body.addWidget(page.month_grid)
    page.month_details_button.setChecked(True)
    page.month_details_button.hide()
    page.month_details_button.toggled.connect(months.setVisible)
    root.addWidget(months)
    steps = ("Сформировать", "Акты", "Проект в ГИС", "Разместить") if quality else ("План готов", "Заполнить факт", "Проверить", "Разместить")
    page.workflow = WorkflowPanel(steps)
    root.addWidget(page.workflow)
    scope = QHBoxLayout()
    scope.setContentsMargins(0, 0, 0, 0)
    page.scope_switcher = ScopeSwitcher("month", "Месяц", "period", "Период")
    page.scope_switcher.setAccessibleName("Область основной команды: открытый месяц или доступный период")
    page.scope_switcher.setToolTip("Меняет только рекомендацию и её основную кнопку. Команды «Заполнение и обмен» отдельно указывают свою область; месяц таблицы не меняется.")
    scope.addWidget(page.scope_switcher)
    page.scope_hint = QLabel()
    page.scope_hint.setObjectName("Muted")
    page.scope_hint.setWordWrap(True)
    from PySide6.QtWidgets import QSizePolicy
    page.scope_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if not quality:
        scope.addWidget(page.scope_hint)
    page.scope_row = QWidget()
    page.scope_row.setLayout(scope)
    page.workflow.action_row.insertWidget(0, page.scope_row)
    page.workflow.compact_scope = page.scope_row
    if quality:
        page.workflow.place_help_below_actions()
        page.workflow.body.setContentsMargins(8, 2, 8, 2)
        page.workflow.body.setSpacing(2)
    page.workflow.title.hide()
    page.workflow.help_button.toggled.connect(page.workflow.title.setVisible)
    if quality:
        # Show the table month separately from the period-wide action targets.
        # Place the summary below command controls, not between their buttons.
        page.action_scope_hint = QLabel()
        page.action_scope_hint.setObjectName("Muted")
        page.action_scope_hint.setWordWrap(True)
        page.action_months_button = QPushButton("Показать состав месяцев…")
        page.action_months_button.setCheckable(True)
        page.action_months_button.setVisible(False)
        page.action_months_detail = QLabel()
        page.action_months_detail.setObjectName("Muted")
        page.action_months_detail.setWordWrap(True)
        page.action_months_detail.setVisible(False)
        page.action_months_button.toggled.connect(page.action_months_detail.setVisible)
        action_summary = QVBoxLayout()
        action_summary.setContentsMargins(0, 0, 0, 0)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(8)
        summary_row.addWidget(page.scope_hint, 1)
        summary_row.addWidget(page.action_scope_hint, 3)
        action_summary.addLayout(summary_row)
        action_summary.addWidget(page.action_months_button)
        action_summary.addWidget(page.action_months_detail)
        page.workflow.body.insertLayout(
            page.workflow.body.indexOf(page.workflow.help_panel), action_summary,
        )
    page.month_status = QLabel()
    page.month_status.setObjectName("Muted")
    page.month_status.setWordWrap(True)
    page.month_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    page.continue_queue_button = QPushButton("Продолжить очередь")
    page.continue_queue_button.hide()
    page.continue_queue_button.clicked.connect(page._continue_queue)
    page.operation_status = OperationStatus()
    page.queue_status_strip = page.operation_status
    page.queue_progress = page.operation_status.progress
    page.queue_label = page.operation_status.label
    page.operation_status.add_action(page.continue_queue_button)
    from .queue_controls import request_stop
    page.stop_queue_button = QPushButton("Остановить после месяца")
    page.stop_queue_button.setToolTip("Текущий запрос завершится; следующие месяцы не будут отправлены.")
    page.stop_queue_button.clicked.connect(lambda: request_stop(page))
    page.stop_queue_button.hide()
    page.operation_status.add_action(page.stop_queue_button)
    page.operation_status.hide()
    if quality:
        _quality_commands(page)
    else:
        _fixation_commands(page)
    root.addWidget(page.operation_status)
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
    chain = [page.organization_combo, page.house_combo, page.work_list_combo, page.year_combo,
             page.month_combo, *page.scope_switcher.buttons.values(), page.workflow.primary,
             page.workflow.help_button, page.month_details_button, *page.month_buttons.values()]
    chain += [page.month_acts_button, page.selection_actions, page.acts_actions, page.data_actions] if quality else [page.plan_reference_checkbox, page.data_actions]
    if quality:
        chain += [page.include_all_button, page.exclude_all_button]
    chain += [page.table]
    for before, after in zip(chain, chain[1:]):
        QWidget.setTabOrder(before, after)
    # Month/period commands are explicit; the old recommendation switch remains
    # available to controller compatibility, not as a second scope selector.
    page.scope_row.hide()
    page.workflow.title.show()
    page.workflow.help_button.toggled.disconnect(page.workflow.title.setVisible)
    page.workflow.help_button.toggled.connect(
        lambda expanded: page.workflow.set_recommendation_visible(
            expanded or not getattr(page, "_dense_monthly", False)
        )
    )
    page.workflow.direct_keys = {"fill", "form", "acts", "review_acts", "save", "publish", "load"}
    page.workflow.register("load", page.load_gis_button if quality else page.load_month_button)
    if quality:
        page.workflow.register("acts", page.month_acts_button)
        page.workflow.title.setParent(page.workflow._summary_widget)
        page.workflow._summary_widget.layout().insertWidget(0, page.workflow.title)
        page.workflow._summary_widget.show()
        page.scope_hint.hide()
        page.action_scope_hint.hide()
    from .work_list_selector import install_selector
    install_selector(page, root, page.work_list_combo)


def _action_row(card, buttons):
    actions = ActionFlowLayout()
    for button in buttons:
        actions.addWidget(button)
        button.show()
    card.body.addLayout(actions)


def _quality_commands(page):
    for name, text, handler in (
        ("form_month_button", "Сформировать оценку месяца", lambda: page._form_month_from_fixation()),
        ("form_period_button", "Сформировать оценки периода", lambda: page._form_year_from_fixation()),
        ("auto_act_month_button", "Автоакт месяца", lambda: page._auto_generate_month_act()),
        ("auto_act_period_button", "Автоакты периода", lambda: page._auto_generate_year_acts()),
        ("clear_values_button", "Очистить перерасчёт месяца", lambda: page._clear_month_values()),
        ("clear_period_values_button", "Очистить перерасчёт периода", lambda: page._clear_year_values()),
        ("load_gis_button", "Загрузить месяц из ГИС", lambda: page._load_from_gis()),
        ("load_period_button", "Загрузить период из ГИС", lambda: page._load_year_from_gis()),
        ("save_button", "Сохранить проект месяца", lambda: page._save_project()),
        ("save_period_button", "Сохранить проекты периода", lambda: page._save_year_projects()),
        ("publish_button", "Разместить месяц", lambda: page._publish()),
        ("publish_period_button", "Разместить период", lambda: page._publish_year()),
        ("cancel_button", "Аннулировать оценку месяца", page._cancel),
        ("include_all_button", "Включить все", lambda: page._set_all_included(True)),
        ("exclude_all_button", "Снять все", lambda: page._set_all_included(False)),
        ("month_acts_button", "Акты месяца…", page._open_month_acts),
        ("create_act_button", "Создать PDF акта месяца…", lambda: page._open_month_acts(start_generate=True)),
        ("upload_acts_button", "Загрузить свои акты…", lambda: page._open_month_acts(start_upload=True)),
    ):
        command(page, name, text, handler)
    for key, handler in (("form", page._form_scope_from_fixation), ("acts", page._auto_generate_scope_act),
                         ("save", page._save_scope), ("publish", page._publish_scope)):
        page.workflow.register(key, handler)
    page.workflow.register("resume", page.continue_queue_button)
    page.workflow.register("review_acts", page.month_acts_button)
    page.workflow.register("next", lambda: None)


def _fixation_commands(page):
    for name, text, handler in (
        ("fill_month_button", "Заполнить месяц по плану", lambda: page._fill_month_from_plan()),
        ("fill_period_button", "Заполнить период по плану", lambda: page._fill_year_from_plan()),
        ("clear_month_button", "Очистить факт месяца", lambda: page._clear_month()),
        ("clear_period_button", "Очистить факт периода", page._clear_period),
        ("load_month_button", "Загрузить месяц из ГИС", lambda: page._load_selected_month_from_gis()),
        ("load_period_button", "Загрузить период из ГИС", lambda: page._load_year_from_gis()),
        ("publish_month_button", "Разместить месяц", lambda: page._publish_selected_month()),
        ("publish_period_button", "Разместить период", lambda: page._publish_year()),
        ("add_unplanned_button", "Добавить внеплановую работу", page._request_add_unplanned),
    ):
        command(page, name, text, handler)
    page.workflow.register("fill", page._fill_scope_from_plan)
    page.workflow.register("publish", page._publish_scope)
    page.workflow.register("resume", page.continue_queue_button)
    page.workflow.register("next", lambda: page.workflow.navigateRequested.emit(4))


def _quality_data(page, root):
    card = workspace_card("Работы в оценке качества")
    page.data_card = card
    card.body.setContentsMargins(8, 3, 8, 3)
    page.selection_actions = command_menu(
        "Оценка: работы", (page.include_all_button, page.exclude_all_button)
    )
    page.acts_actions = command_menu(
        "Документы: акты",
        (page.auto_act_month_button, page.auto_act_period_button,
         page.create_act_button, page.upload_acts_button), name="act",
    )
    page.month_actions = command_menu("Открытый месяц", (page.form_month_button, page.load_gis_button, page.save_button, page.publish_button, page.clear_values_button, page.cancel_button), name="quality")
    page.period_actions = command_menu("Доступные месяцы года", (page.form_period_button, page.load_period_button, page.save_period_button, page.publish_period_button, page.clear_period_values_button), name="sync")
    _monthly_data_menu(page)
    page.data_actions.setText("Данные и обмен с ГИС")
    from .monthly_commands import build_commands
    for hidden in (page.data_actions, page.acts_actions, page.selection_actions):
        hidden.setParent(page.command_host)
        hidden.hide()
    page.month_acts_button.setText("Акты…")
    page.month_acts_button.setToolTip("Документы и связи месяца: добавить свой акт или подготовить PDF вручную")
    page.auto_act_month_button.setText("Сформировать автоматический акт за месяц")
    page.auto_act_period_button.setText("Сформировать автоматические акты за период")
    page.create_act_button.setText("Подготовить PDF вручную…")
    page.upload_acts_button.setText("Добавить готовый акт…")
    build_commands(page, card, quality=True)
    page.acts_commands_group = CommandGroup("Работа с актами")
    for button in (page.month_acts_button, page.upload_acts_button,
                   page.auto_act_month_button, page.auto_act_period_button):
        button.setProperty("compactCommand", True)
        page.acts_commands_group.actions.addWidget(button)
        button.show()
    page.month_acts_button.setMinimumWidth(page.month_acts_button.minimumSizeHint().width())
    card.body.addWidget(page.acts_commands_group)
    from .assessment_header import AssessmentHeader
    page.table = viewport_table(table(["В оценку", "Работа / услуга", "Плата / мес., руб.", "Дни нарушения", "Дней в месяце", "Перерасчёт, руб.", "Акты"], name_column=1, header_class=AssessmentHeader))
    page.table.horizontalHeader().bind_commands(page.include_all_button, page.exclude_all_button)
    for column, detail in ((2, "Стоимость работы в ежемесячной плате"),
                           (3, "Число дней предоставления работы или услуги ненадлежащего качества"),
                           (5, "Уменьшение платы по результатам оценки качества")):
        page.table.horizontalHeaderItem(column).setToolTip(detail)
    page.table.setProperty("preferredVisibleRows", 5)
    page.table.setProperty("compactTable", True)
    page.selected_act_button = QPushButton("Акт выбранной работы…")
    page.selected_act_button.setProperty("compactCommand", True)
    page.selected_act_button.setToolTip(
        "Открыть акты выбранной строки без горизонтальной прокрутки таблицы"
    )
    page.selected_act_button.setEnabled(False)
    page.selected_act_button.clicked.connect(page._open_selected_acts)
    page.acts_commands_group.actions.addWidget(page.selected_act_button)
    card.body.addWidget(page.table, 1)
    page.table.currentCellChanged.connect(
        lambda *_: page._update_selected_act_button()
    )
    page.month_acts_summary = QLabel("Акты месяца: —")
    page.month_acts_summary.setObjectName("Muted")
    page.month_acts_summary.setWordWrap(True)
    help_button = QPushButton("Об актах…")
    help_button.setProperty("compactCommand", True)
    help_button.setToolTip("Акты и перерасчёт: правила подготовки документов")
    help_button.setCheckable(True)
    page.acts_explanation = QLabel(
        "Обычный акт подтверждает выполнение: поля перерасчёта оставьте пустыми. Автоакт создаётся по фактическим показателям. "
        "PDF акта можно подготовить вручную; «Акты месяца» управляет документами и связями.\n"
        "Акт нарушения качества автоматически не формируется: загрузите свой документ и укажите дни ненадлежащего качества. "
        "Проект сохраняется без актов; для размещения акты обязательны."
    )
    page.acts_explanation.setWordWrap(True)
    page.acts_explanation.setObjectName("Muted")
    page.acts_explanation.hide()
    help_button.toggled.connect(page.acts_explanation.setVisible)
    footer = ActionFlowLayout()
    page.month_status.setProperty("flowFullText", True)
    page.month_acts_summary.setProperty("flowFullText", True)
    footer.addWidget(page.month_status)
    footer.addWidget(page.month_acts_summary)
    footer.addWidget(help_button)
    card.body.addLayout(footer)
    card.body.addWidget(page.acts_explanation)
    root.addWidget(card, 1)


def _fixation_data(page, root):
    card = workspace_card("Фактические показатели месяца")
    page.data_card = card
    page.month_actions = command_menu("Открытый месяц", (page.load_month_button, page.publish_month_button, page.clear_month_button), name="fixation")
    page.period_actions = command_menu("Доступные месяцы года", (page.fill_period_button, page.load_period_button, page.publish_period_button, page.clear_period_button), name="sync")
    _monthly_data_menu(page, direct_month_fill=True)
    page.plan_reference_checkbox = QCheckBox("Плановые значения рядом с фактом")
    page.plan_reference_checkbox.setToolTip("Плановые показатели рядом с фактическими для сравнения")
    card.actions.addWidget(page.plan_reference_checkbox)
    page.data_actions.setParent(page.command_host)
    page.data_actions.hide()
    from .monthly_commands import build_commands
    build_commands(page, card, quality=False)
    page.table = viewport_table(table(["Работа / услуга", "Цена факт", "Объём факт", "Кол-во факт", "Стоимость факт", "Цена план", "Объём план", "Кол-во план", "Стоимость план", "Фото", "Статус"]))
    page.table.setProperty("compactTable", True)
    page.plan_reference_checkbox.toggled.connect(page._show_plan_reference)
    page._show_plan_reference(False)
    card.body.addWidget(page.table, 1)
    note = QLabel("Факт сохраняется локально. Серые поля недоступны; стоимость по цене и объёму рассчитывается автоматически.")
    note.setWordWrap(True)
    note.setObjectName("Muted")
    page.table.setToolTip(note.text())
    card.body.addWidget(page.month_status)
    unplanned_row = QHBoxLayout()
    page.unplanned_toggle = QPushButton("Внеплановые работы: 0")
    page.unplanned_toggle.setFlat(True)
    page.unplanned_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    unplanned_row.addWidget(page.unplanned_toggle)
    unplanned_row.addWidget(page.add_unplanned_button)
    page.add_unplanned_button.show()
    unplanned_row.addStretch(1)
    page.unplanned_card = SectionCard(fit_content=True)
    page.unplanned_card.body.addLayout(unplanned_row)
    page.unplanned_table = table(["Работа / услуга", "Вид работы / услуги", "Ед. изм.", "Цена", "Объем", "Количество", "Стоимость", "Комментарий", "Фото", ""])
    page.unplanned_card.body.addWidget(page.unplanned_table)
    page.unplanned_table.setProperty("preferredVisibleRows", 1)
    viewport_table(page.unplanned_table)
    page.unplanned_splitter = WorkspaceSplitter()
    page.unplanned_splitter.setProperty("mainWorkspace", True)
    page.unplanned_splitter.setChildrenCollapsible(False)
    page.unplanned_splitter.addWidget(card)
    page.unplanned_splitter.addWidget(page.unplanned_card)
    page.unplanned_splitter.setStretchFactor(0, 4)
    page.unplanned_splitter.setStretchFactor(1, 1)
    page.unplanned_splitter.setSizes([420, 90])
    root.addWidget(page.unplanned_splitter, 1)
    def update_count(*_args):
        from shiboken6 import isValid
        if not isValid(page.unplanned_table):
            return
        count = page.unplanned_table.rowCount()
        page.unplanned_toggle.setText(f"Внеплановые работы: {count}")
        page.unplanned_toggle.setAccessibleDescription(f"{count} строк выбранного месяца. Внеплановые работы видны под основной таблицей.")
    page.unplanned_table.model().rowsInserted.connect(update_count)
    page.unplanned_table.model().rowsRemoved.connect(update_count)
    page.unplanned_table.model().modelReset.connect(update_count)
    page.unplanned_table.itemChanged.connect(page._unplanned_item_changed)


def _monthly_data_menu(page, *, direct_month_fill=False):
    page.data_actions = ActionMenuButton("Заполнение и обмен", "sync")
    page.data_actions.setToolTip("Выберите область явно: открытый месяц или доступные месяцы выбранного года. Переключатель рекомендаций эту область не меняет.")
    if direct_month_fill:
        # A direct first-level action for the selected month, never the whole period.
        # The original command button retains the existing confirmation and handler.
        page.data_actions.menu_model.addSection("Подготовка текущего месяца")
        page.month_fill_menu_action = page.data_actions.add_command(
            page.fill_month_button, text="Заполнить текущий месяц по плану…",
        )
        page.data_actions.menu_model.addSeparator()
        page.data_actions.menu_model.addSection("Обмен с ГИС и другие операции")
    for submenu in (page.month_actions, page.period_actions):
        submenu.setParent(page.command_host)
        submenu.hide()
        page.data_actions.add_submenu(submenu)


def compact_monthly(page, compact):
    page.context_card.set_compact(False)
    page.workflow.set_compact(True)
    page.scope_row.hide()
    page.months_card.show()
    if compact != getattr(page, "_dense_monthly", False):
        if compact:
            page.workflow.action_row.removeWidget(page.workflow.help_button)
            page.data_card.actions.addWidget(page.workflow.help_button)
        else:
            page.data_card.actions.removeWidget(page.workflow.help_button)
            page.workflow.action_row.addWidget(page.workflow.help_button)
        page.workflow.help_button.setVisible(page.workflow.guidance_enabled)
    page._dense_monthly = compact
    for month, button in page.month_buttons.items():
        state = button.property("monthState")
        if state:
            button.setText(month_button_text(month, state, compact))
    page.months_card._queue_fit()
    page.workflow.set_recommendation_visible(not compact or page.workflow.help_button.isChecked())
    if hasattr(page, "manual_documents_caption"):
        for widget in (page.manual_documents_caption, page.create_act_button, page.upload_acts_button):
            widget.setVisible(not compact)


def resize_table_rows(widget, max_rows):
    """Reserve full row rectangles and horizontal scrollbar at every scale."""
    fit_table_rows(widget)
    if widget.property("viewportManaged"):
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        visible_rows = int(widget.property("preferredVisibleRows") or 4)
        from PySide6.QtWidgets import QApplication
        scale = float(QApplication.instance().property("uiScale") or 1.0)
        if widget.property("compactTable") and scale >= 1.25:
            visible_rows = min(visible_rows, 2)
        row = max([widget.verticalHeader().defaultSectionSize()] + [widget.rowHeight(i) for i in range(min(visible_rows, widget.rowCount()))])
        extra = 0 if visible_rows >= 5 else 4
        extent = sum(widget.columnWidth(i) for i in range(widget.columnCount()) if not widget.isColumnHidden(i))
        scrollbar = widget.horizontalScrollBar().sizeHint().height() if extent > widget.viewport().width() else 0
        header_height = max(widget.horizontalHeader().height(), widget.horizontalHeader().sizeHint().height())
        widget.setMinimumHeight(header_height + row * visible_rows + scrollbar + widget.frameWidth() * 2 + extra)
        widget.setMaximumHeight(16777215)
        return
    widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if widget.rowCount() > max_rows else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    visible = min(max(widget.rowCount(), 1), max_rows)
    row_height = sum(max(widget.rowHeight(row), widget.verticalHeader().defaultSectionSize()) for row in range(min(visible, widget.rowCount())))
    if not widget.rowCount():
        row_height = widget.verticalHeader().defaultSectionSize()
    extent = sum(widget.columnWidth(i) for i in range(widget.columnCount()) if not widget.isColumnHidden(i))
    available = widget.contentsRect().width()
    if widget.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
        available -= widget.verticalScrollBar().sizeHint().width()
    scrollbar = widget.horizontalScrollBar().sizeHint().height() if extent > available else 0
    height = widget.horizontalHeader().height() + row_height + widget.frameWidth() * 2 + scrollbar + 4
    if widget.height() != height:
        widget.setFixedHeight(height)


class TableHeightObserver(QObject):
    def __init__(self, widget, callback):
        super().__init__(widget)
        self.widget = widget
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.callback = callback
        self._header_pending = False
        self._refreshing = False
        self.timer.timeout.connect(self._refresh)
        widget.installEventFilter(self)
        widget.horizontalHeader().installEventFilter(self)
        widget.horizontalScrollBar().rangeChanged.connect(self.queue)
        widget.horizontalHeader().sectionResized.connect(self.queue)
        widget.verticalHeader().sectionResized.connect(self.queue)

    def queue(self, *_args):
        if not self._refreshing and not self.timer.isActive():
            self.timer.start(0)

    def _refresh(self):
        self._refreshing = True
        try:
            if self._header_pending:
                self._header_pending = False
                header = self.widget.horizontalHeader()
                if header.count():
                    header.model().headerDataChanged.emit(Qt.Orientation.Horizontal, 0, header.count() - 1)
            self.callback()
        finally:
            self._refreshing = False

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
                self._header_pending = True
            self.queue()
        return super().eventFilter(watched, event)
