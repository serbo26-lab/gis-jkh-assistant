"""Explicit month and range commands using the existing operation handlers."""
from PySide6.QtWidgets import QLabel

from .action_layout import ActionFlowLayout
from .widgets import SafeComboBox
from .workflow_widgets import CommandGroup, DangerZone


def period_months(page, active):
    if not hasattr(page, "period_from"):
        return tuple(active)
    first = int(page.period_from.currentData() or 1)
    last = int(page.period_to.currentData() or 12)
    return tuple(month for month in active if first <= month <= last)


def command_row(card, caption, buttons):
    row = ActionFlowLayout()
    label = QLabel(caption)
    label.setObjectName("ToolbarGroupLabel")
    row.addWidget(label)
    for button in buttons:
        button.setProperty("compactCommand", True)
        row.addWidget(button)
        button.show()
    card.body.addLayout(row)
    return label


def build_commands(page, card, *, quality):
    current = ((page.load_gis_button, page.form_month_button, page.save_button, page.publish_button)
               if quality else (page.load_month_button, page.fill_month_button, page.publish_month_button))
    current[0].setText("Загрузить месяц из ГИС")
    current[1].setText("Сформировать оценку месяца" if quality else "Заполнить месяц по плану")
    if quality:
        page.save_button.setText("Сохранить проект месяца")
    current[-1].setText("Разместить месяц")
    page.month_commands_group = CommandGroup("Действия с выбранным месяцем")
    page.current_commands_label = QLabel()
    page.current_commands_label.setObjectName("ToolbarGroupLabel")
    page.month_commands_group.actions.addWidget(page.current_commands_label)
    clear_buttons = ((page.clear_values_button, page.cancel_button) if quality else (page.clear_month_button,))
    for button in (*current, *clear_buttons):
        button.setProperty("compactCommand", True)
        page.month_commands_group.actions.addWidget(button)
        button.show()
    card.body.addWidget(page.month_commands_group)
    # Compatibility handles for the presenter, never an additional visible menu.
    page.month_danger = DangerZone("Другие действия…")
    for button in clear_buttons:
        page.month_danger.add_command(button)
    page.month_danger.setParent(page.command_host)
    page.month_danger.hide()

    from .page_layouts import MONTHS
    page.period_from, page.period_to = SafeComboBox(), SafeComboBox()
    for combo in (page.period_from, page.period_to):
        for month, name in enumerate(MONTHS, 1):
            combo.addItem(name, month)
        combo.setMinimumContentsLength(6)
        combo.setMaximumWidth(150)
    page.period_to.setCurrentIndex(11)
    page.period_from.setAccessibleName("Первый месяц массовой операции")
    page.period_to.setAccessibleName("Последний месяц массовой операции")
    page.period_from.currentIndexChanged.connect(lambda: page._refresh_year_status())
    page.period_to.currentIndexChanged.connect(lambda: page._refresh_year_status())
    period = ((page.load_period_button, page.form_period_button, page.save_period_button, page.publish_period_button)
              if quality else (page.load_period_button, page.fill_period_button, page.publish_period_button))
    period[0].setText("Загрузить период из ГИС")
    period[1].setText("Сформировать период" if quality else "Заполнить период")
    if quality:
        page.save_period_button.setText("Сохранить проекты периода")
    period[-1].setText("Разместить период в ГИС")
    page.period_commands_group = CommandGroup("Действия с выбранным периодом")
    page.period_commands_label = QLabel()
    page.period_commands_label.setObjectName("ToolbarGroupLabel")
    page.period_commands_group.actions.addWidget(page.period_commands_label)
    clear_period = page.clear_period_values_button if quality else page.clear_period_button
    for widget in (page.period_from, QLabel("—"), page.period_to, *period, clear_period):
        widget.setProperty("compactCommand", True)
        page.period_commands_group.actions.addWidget(widget)
        widget.show()
    card.body.addWidget(page.period_commands_group)
    page.period_danger = DangerZone("Очистка периода…")
    page.period_danger.add_command(clear_period)
    page.period_danger.setParent(page.command_host)
    page.period_danger.hide()
    page.period_commands_label.setToolTip("Диапазон операций не меняет открытый месяц таблицы")


def refresh_commands(page, active, *, locked):
    if not hasattr(page, "current_commands_label"):
        return
    from .page_layouts import MONTHS
    year = page.year_combo.currentData()
    month = page._current_month
    page.current_commands_label.setText(
        f"Месяц: {MONTHS[month - 1]} {year}" if month and year else "Месяц не выбран"
    )
    selected = period_months(page, active)
    page.period_commands_label.setText(f"Период {year or '—'} • {len(selected)} мес.")
    page.period_commands_label.setToolTip(
        "Будут обработаны: " + ", ".join(f"{m:02d}.{year}" for m in selected)
        if selected else "Нет месяцев в выбранном диапазоне"
    )
    page.period_from.setEnabled(not locked)
    page.period_to.setEnabled(not locked)
    for name in ("load_period_button", "fill_period_button", "form_period_button",
                 "save_period_button", "publish_period_button", "auto_act_period_button",
                 "clear_period_button", "clear_period_values_button"):
        button = getattr(page, name, None)
        if button:
            button.setEnabled(button.isEnabled() and bool(selected))
    page.month_danger.sync()
    page.period_danger.sync()


def operation_months(page, work_list, year):
    from gis_jkh_assistant.work_planning import active_months_for_work_list
    return period_months(page, active_months_for_work_list(work_list, year))
