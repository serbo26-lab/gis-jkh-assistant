"""Targeted IA-01–IA-11 regressions; synthetic data and a socket-free gateway."""
import sqlite3
import threading
import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from test_ui_workflow import desktop as _desktop, qt_app as _qt_app, _select_page, _settle, ControlledWorker
from gis_jkh_assistant.app import fixation_page, quality_page
from gis_jkh_assistant.app.quality_bulk_dialogs import QualityMonthActsDialog
from gis_jkh_assistant.app.work_templates_dialog import WorkTemplatesDialog
from gis_jkh_assistant.app.queue_controls import preview
from gis_jkh_assistant.work_fixation import build_fixation_command_payload

desktop = _desktop
qt_app = _qt_app


@pytest.fixture
def messages(monkeypatch):
    values = []
    for kind in ('warning', 'information', 'critical', 'question'):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, **k: values.append(a[2]) or QMessageBox.Yes)
    return values


@pytest.mark.parametrize('value', ['abc', '-1', 'NaN', 'Infinity', '1e999'])
def test_bad_price_keeps_previous_cost_and_does_not_become_ready(desktop, messages, value):
    p = _select_page(desktop, 3)
    before = desktop.repository.list_work_fixation_items(p._current_period_id)[0]
    p.table.item(0, 1).setText(value)
    after = desktop.repository.list_work_fixation_items(p._current_period_id)[0]
    assert (after.fact_price, after.fact_total_cost) == (before.fact_price, before.fact_total_cost)
    assert 'Прежние значения сохранены' in messages[-1]
    invalid = replace(before, fact_price=value)
    with pytest.raises(ValueError, match='Цена факт'):
        build_fixation_command_payload(fias_house_code='fixture', work_list_guid='fixture',
                                       year=2026, month=1, items=[invalid])


def test_historical_bad_number_remains_visible_but_cannot_be_sent(desktop):
    p = _select_page(desktop, 3)
    repo = desktop.repository
    item = repo.list_work_fixation_items(p._current_period_id)[0]
    with repo.connection:
        repo.connection.execute('UPDATE work_fixation_items SET fact_price=? WHERE id=?', ('abc', item.id))
    p._load_table()
    p._refresh_gis_buttons()
    assert p.table.item(0, 1).text() == 'abc'
    assert p.table.item(0, 10).text() == 'Ошибка данных'
    assert not p.publish_month_button.isEnabled()


def test_mouse_navigation_rolls_back_current_and_selected_item(desktop, messages, monkeypatch):
    w = desktop.window
    p = _select_page(desktop, 0)
    monkeypatch.setattr(p, 'flush_pending_changes', lambda: False)
    target = w.navigation.visualItemRect(w.navigation.item(5)).center()
    QTest.mouseClick(w.navigation.viewport(), Qt.MouseButton.LeftButton, pos=target)
    QTest.qWait(20)
    assert w.pages.currentIndex() == w.navigation.currentRow() == 0
    assert w.navigation.selectedItems() == [w.navigation.item(0)]
    monkeypatch.setattr(p, 'flush_pending_changes', lambda: True)


def test_fact_undo_redo_restores_cost_preserves_photo_and_refuses_stale_edit(desktop, messages):
    p = _select_page(desktop, 3)
    repo = desktop.repository
    before = repo.list_work_fixation_items(p._current_period_id)[0]
    assert not any(button.isEnabled() for button in p.fact_history.buttons)
    photo = repo.add_work_fixation_photo(fixation_item_id=before.id, file_name='fixture.jpg',
                                       content_type='image/jpeg', content=b'fixture', description='fixture')
    p.table.item(0, 1).setText('7')
    changed = repo.list_work_fixation_items(p._current_period_id)[0]
    assert changed.fact_price == '7'
    assert p.fact_history.buttons[0].isEnabled()
    p.table.setFocus()
    QTest.keyClick(p.table, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    restored = repo.list_work_fixation_items(p._current_period_id)[0]
    assert (restored.fact_price, restored.fact_total_cost) == (before.fact_price, before.fact_total_cost)
    assert not p.fact_history.buttons[0].isEnabled() and p.fact_history.buttons[1].isEnabled()
    p.fact_history.apply(True)
    assert repo.list_work_fixation_items(p._current_period_id)[0].fact_price == '7'
    assert repo.list_work_fixation_photos(fixation_item_id=before.id)[0].id == photo.id
    repo.update_work_fixation_item(before.id, fact_price='9', fact_amount='1', fact_count=1, fact_total_cost='9')
    p.fact_history.apply()
    assert repo.list_work_fixation_items(p._current_period_id)[0].fact_price == '9'
    assert 'другой операцией' in messages[-1]


@pytest.mark.parametrize('index,module', [(3, fixation_page), (4, quality_page)])
def test_queue_stops_between_months_and_resumes_without_repeating(desktop, messages, monkeypatch, index, module):
    p = _select_page(desktop, index)
    repo = desktop.repository
    periods = []
    for month in (1, 2, 9):
        if index == 3:
            period = repo.get_or_create_work_fixation_period(work_list_id=desktop.work_list.id, year=2026, month=month)
            repo.fill_work_fixation_from_plan(period.id)
        else:
            period = p.store.get_or_create_period(work_list_id=desktop.work_list.id, year=2026, month=month)
        periods.append(period)
    sent = []
    def worker(*args, **kwargs):
        w = ControlledWorker(*args, **kwargs)
        sent.append(w)
        return w
    monkeypatch.setattr(module, 'GisIntegrationWorker', worker)
    if index == 3:
        p._publish_year()
    else:
        monkeypatch.setattr(p, '_periods_with_quality_for_year', lambda *a: periods)
        monkeypatch.setattr(p, '_validation_error', lambda *a, **k: None)
        monkeypatch.setattr(p, '_payload_for_period', lambda period_id, **k: {'month': next(x.month for x in periods if x.id == period_id), 'year': 2026})
        p._save_year_projects()
    assert sent
    p.stop_queue_button.click()
    sent[0].succeeded.emit({})
    sent[0].finish()
    QTest.qWait(300)
    assert len(sent) == 1
    assert not p._queue_running
    assert p._queue_index == 1
    assert p.continue_queue_button.isVisible()
    assert 'Январь 2026' in preview(p, periods) and 'Сентябрь 2026' in preview(p, periods)
    p._continue_queue()
    assert len(sent) == 2 and sent[1].payload['month'] == 2
    sent[1].failed.emit('isolated stop')
    sent[1].finish()
    _settle(desktop.app)


def test_plan_name_stays_fixed_and_row_heights_match(desktop):
    p = _select_page(desktop, 2)
    desktop.window._scale_changed(150)
    QTest.qWait(30)
    p.table.horizontalScrollBar().setValue(p.table.horizontalScrollBar().maximum())
    _settle(desktop.app)
    frozen = p.table.frozen_name.view
    assert frozen.isVisible()
    assert frozen.columnViewportPosition(0) == 0
    assert frozen.model().index(0, 0).data() == p.table.item(0, 0).text()
    assert frozen.rowHeight(0) == p.table.rowHeight(0)


def test_frozen_header_updates_after_qt_style_notifications(desktop):
    p = _select_page(desktop, 2)
    frozen = p.table.frozen_name
    before = frozen.view.columnWidth(0)
    p.table.setColumnWidth(0, before + 40)
    # Resizing a second view inside Qt's style/header signal is unsafe.
    assert frozen.view.columnWidth(0) == before
    assert frozen._sync_timer.isActive()
    QTest.qWait(10)
    assert frozen.view.columnWidth(0) == p.table.columnWidth(0)
    desktop.window.settings_page.dark_radio.click()
    QTest.qWait(10)
    assert frozen.view.horizontalHeader().height() == p.table.horizontalHeader().height()
    assert frozen.view.rowHeight(0) == p.table.rowHeight(0)


def test_compact_context_retains_visible_labels_and_table_space(desktop):
    p = _select_page(desktop, 4)
    desktop.window.resize(1366, 730)
    desktop.window._scale_changed(150)
    QTest.qWait(40)
    assert all(control.isVisible() for control in p.context_card.controls)
    assert p.context_card.height() < 145
    assert p.table.viewport().height() >= 5 * p.table.rowHeight(0)
    assert p.page_scroll.verticalScrollBar().maximum() == 0


def test_standard_confirmation_buttons_are_russian(desktop):
    box = QMessageBox(desktop.window)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
    assert box.button(QMessageBox.Yes).text().replace('&','') == 'Да'
    assert box.button(QMessageBox.No).text().replace('&','') == 'Нет'
    assert box.button(QMessageBox.Cancel).text().replace('&','') == 'Отмена'


def test_act_empty_state_and_wrapped_status_preserve_links(desktop):
    p = _select_page(desktop, 4)
    d = QualityMonthActsDialog(p.store, period_id=p._current_period_id,
                              organization_name='УО', house_address='Дом', year=2026, month=1, parent=p)
    assert not any(b.isEnabled() for b in d.selection_buttons)
    item = p.store.list_items(p._current_period_id)[0]
    act = p.store.add_act(item_id=item.id, act_type='ACCEPTANCE_ACT', number='1', reg_date='31.01.2026',
                         period_from='01.01.2026', period_to='31.01.2026', file_name='очень-длинный-файл'*30+'.pdf',
                         description='fixture', content_type='application/pdf', content=b'%PDF-fixture', generated=True)
    d._reload(act.id)
    assert all(b.isEnabled() for b in d.selection_buttons) and d.save_copy_button.isEnabled()
    assert '\nСформирован программой' in d.list.item(0).text()
    assert 'Файл есть' in d.list.item(0).text()
    assert act.file_name in d.list.item(0).toolTip()
    assert d.list.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert p.store.count_act_links(act.id) == 1
    d.close()
    templates = WorkTemplatesDialog(desktop.repository, desktop.organization.id, p)
    assert not templates.rename_button.isEnabled() and not templates.delete_button.isEnabled()
    templates.close()


def test_unit_lookup_is_async_and_applies_on_gui_thread(desktop, monkeypatch):
    p = _select_page(desktop, 4)
    repo = desktop.repository
    item = repo.list_work_list_items(desktop.work_list.id)[0]
    with repo.connection:
        repo.connection.execute('UPDATE work_list_items SET unit_name=NULL WHERE id=?', (item.id,))
    release = threading.Event()
    def slow(*a, **k):
        assert release.wait(2)
        return {'items': [{'localItemId': item.id, 'unitName': 'м²'}]}
    monkeypatch.setattr(desktop.gateway, 'execute', slow)
    ticks, done = [], []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    started = time.monotonic()
    assert p._sync_work_units_for_act(lambda: done.append(1))
    try:
        assert time.monotonic() - started < 0.2
        QTest.qWait(60)
        assert ticks and not done
    finally:
        release.set()
        for _ in range(100):
            QTest.qWait(10)
            if p._integration_worker is None:
                break
        timer.stop()
    assert done == [1]
    assert repo.list_work_list_items(desktop.work_list.id)[0].unit_name == 'м²'


def test_open_existing_acts_never_calls_network(desktop, monkeypatch):
    p = _select_page(desktop, 4)
    monkeypatch.setattr(p, '_sync_work_units_for_act', lambda *a: pytest.fail('Listing must be local'))
    monkeypatch.setattr(QualityMonthActsDialog, 'exec', lambda self: 0)
    p._open_month_acts()


def test_unit_lookup_failure_and_changed_context_do_not_generate_pdf(desktop, messages, monkeypatch):
    p = _select_page(desktop, 4)
    item = desktop.repository.list_work_list_items(desktop.work_list.id)[0]
    with desktop.repository.connection:
        desktop.repository.connection.execute('UPDATE work_list_items SET unit_name=NULL WHERE id=?', (item.id,))
    monkeypatch.setattr(quality_page, 'GisIntegrationWorker', ControlledWorker)
    done = []
    assert p._sync_work_units_for_act(lambda: done.append(1))
    worker = p._integration_worker
    worker.failed.emit('isolated timeout')
    worker.finish()
    assert not p._integration_busy and not done
    assert 'Локальные акты сохранены' in p.operation_status.label.text()
    assert p._sync_work_units_for_act(lambda: done.append(1))
    worker = p._integration_worker
    original = p._current_period_id
    p._current_period_id = None  # Simulate a programmatic context change during a pending response.
    worker.succeeded.emit({'items': [{'localItemId': item.id, 'unitName': 'м²'}]})
    worker.finish()
    p._current_period_id = original
    assert not done and not p._integration_busy


def test_closing_act_dialog_during_preparation_does_not_generate_pdf(desktop, monkeypatch):
    p = _select_page(desktop, 4)
    item = desktop.repository.list_work_list_items(desktop.work_list.id)[0]
    with desktop.repository.connection:
        desktop.repository.connection.execute('UPDATE work_list_items SET unit_name=NULL WHERE id=?', (item.id,))
    monkeypatch.setattr(quality_page, 'GisIntegrationWorker', ControlledWorker)
    d = QualityMonthActsDialog(p.store, period_id=p._current_period_id,
                              organization_name='УО', house_address='Дом', year=2026, month=1, parent=p)
    monkeypatch.setattr(d, '_items', lambda: pytest.fail('Closed dialog must not continue PDF generation'))
    d.show()
    d.generate_button.click()
    assert not d.generate_button.isEnabled() and d.preparation_hint.isVisible()
    worker = p._integration_worker
    d.close()
    worker.succeeded.emit({'items': [{'localItemId': item.id, 'unitName': 'м²'}]})
    worker.finish()
    assert not p._integration_busy


def test_undo_database_failure_keeps_history_for_retry(desktop, messages, monkeypatch):
    p = _select_page(desktop, 3)
    p.table.item(0, 1).setText('7')
    def fail(*a, **k):
        raise sqlite3.OperationalError('isolated lock')
    with monkeypatch.context() as patch:
        patch.setattr(desktop.repository, 'update_work_fixation_item', fail)
        p.fact_history.apply()
        assert p.fact_history.undo_entries
    p.fact_history.apply()
    assert not p.fact_history.undo_entries
