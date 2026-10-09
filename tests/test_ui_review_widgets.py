"""Behavioral regressions from the user's screenshots of shared controls."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QWidget  # noqa: E402

from gis_jkh_assistant.app import main_window  # noqa: E402
from gis_jkh_assistant.app.fixation_dialogs import AddUnplannedWorkDialog  # noqa: E402
from gis_jkh_assistant.app.quality_dialogs import AddQualityActDialog  # noqa: E402
from gis_jkh_assistant.app.styles import apply_theme  # noqa: E402
from gis_jkh_assistant.app.widgets import SafeComboBox, StatusStrip  # noqa: E402
from gis_jkh_assistant.database import Repository  # noqa: E402
from gis_jkh_assistant.gis_integration import IntegrationStatus  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    if os.name == "nt":
        if "Segoe UI" not in QFontDatabase.families():
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        application.setFont(QFont("Segoe UI", 10))
    yield application
    apply_theme(application, "light")


def settle(app):
    for _ in range(3):
        app.processEvents()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_long_combo_popup_stays_below_top_field_and_inside_own_window(app, theme, scale):
    apply_theme(app, theme, scale)
    window = QWidget()
    window.setGeometry(30, 30, 640, 580)
    combo = SafeComboBox(window)
    combo.setGeometry(18, 25, 290, 50)
    for index in range(47):
        combo.addItem(f"355041, край Ставропольский, г Ставрополь, пр-д Братский, д. {index + 1}")
    combo.setCurrentIndex(35)
    window.show()
    settle(app)
    combo.showPopup()
    settle(app)
    popup = combo.view().window()
    bounds = window.rect().translated(window.mapToGlobal(QPoint(0, 0)))
    if window.screen():
        bounds = bounds.intersected(window.screen().availableGeometry())
    assert bounds.contains(popup.geometry()), (bounds, popup.geometry())
    assert popup.y() >= combo.mapToGlobal(combo.rect().bottomLeft()).y()
    assert combo.view().verticalScrollBar().maximum() > 0
    assert combo.view().verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert combo.view().visualRect(combo.model().index(combo.currentIndex(), 0)).intersects(combo.view().viewport().rect())
    combo.hidePopup()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_reopened_combo_popup_recovers_height_after_short_window(app):
    apply_theme(app, "light")
    window = QWidget()
    window.setGeometry(25, 25, 600, 210)
    combo = SafeComboBox(window)
    combo.setGeometry(10, 15, 300, 36)
    combo.addItems([f"Дом {index}" for index in range(47)])
    window.show()
    settle(app)
    combo.showPopup()
    settle(app)
    short_height = combo.view().window().height()
    combo.hidePopup()
    window.resize(600, 660)
    settle(app)
    combo.showPopup()
    settle(app)
    assert combo.view().window().height() > short_height
    combo.hidePopup()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_popup_uses_space_above_low_field_without_leaving_window(app):
    apply_theme(app, "dark", 1.5)
    window = QWidget()
    window.setGeometry(20, 20, 600, 580)
    combo = SafeComboBox(window)
    combo.setGeometry(16, 510, 320, 50)
    combo.addItems([f"Дом {index}" for index in range(47)])
    window.show()
    settle(app)
    combo.showPopup()
    settle(app)
    popup = combo.view().window()
    bounds = window.rect().translated(window.mapToGlobal(QPoint(0, 0)))
    assert bounds.contains(popup.geometry()), (bounds, popup.geometry())
    assert popup.geometry().bottom() <= combo.mapToGlobal(QPoint(0, 0)).y()
    assert combo.view().verticalScrollBar().maximum() > 0
    combo.hidePopup()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_loading_activity_spans_status_frame_below_its_text(app):
    apply_theme(app, "dark", 1.25)
    strip = StatusStrip("Получено перечней: 270")
    strip.resize(1050, 75)
    strip.show_progress(0)
    strip.show()
    settle(app)
    assert strip.progress.width() >= strip.width() - 30
    assert strip.progress.y() >= strip.label.geometry().bottom()
    assert strip.progress.height() <= 6
    strip.close()
    strip.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_shared_selection_is_grouped_in_a_card_like_connection(app, tmp_path, monkeypatch):
    class Gateway:
        def start(self):
            pass

        def stop(self):
            pass

        def status(self):
            return IntegrationStatus(running=False, helper_connected=False, gis_page_ready=False,
                                     helper_version=None)

    apply_theme(app, "light")
    monkeypatch.setattr(main_window, "LocalIntegrationGateway", Gateway)
    repository = Repository(tmp_path / "review-sidebar.db")
    window = main_window.MainWindow(repository)
    window.integration_status_timer.stop()
    window.resize(1366, 768)
    window.show()
    settle(app)
    assert window.context_mode_label.parentWidget() is window.context_card
    assert window.context_card.objectName() == window.integration_card.objectName()
    assert window.context_card.isVisibleTo(window)
    assert any("выбор" in label.text().lower() for label in window.context_card.findChildren(QLabel))
    assert window.context_card.geometry().bottom() < window.integration_card.geometry().top()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    repository.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_upload_act_fields_do_not_overlap_at_large_scale(app, theme):
    apply_theme(app, theme, 1.5)
    dialog = AddQualityActDialog(year=2026, month=7)
    dialog.resize(620, 330)
    dialog.show()
    settle(app)
    fields = (dialog.type_combo, dialog.number_edit, dialog.reg_date,
              dialog.period_from, dialog.file_edit)
    for first, second in zip(fields, fields[1:]):
        assert first.geometry().bottom() < second.geometry().top()
    note = next(label for label in dialog.findChildren(QLabel)
                if label.text().startswith("Файл до 50 МБ"))
    assert dialog.file_edit.geometry().bottom() < note.geometry().top()
    dialog.close()
    dialog.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_unplanned_form_scrolls_without_overlapping_or_hiding_save(app, theme):
    apply_theme(app, theme, 1.5)
    parent = QWidget()
    parent.resize(1366, 768)
    dialog = AddUnplannedWorkDialog([{"name": "Уборка и содержание общего имущества"}], parent)
    dialog.show()
    settle(app)
    fields = (dialog.price_edit, dialog.amount_edit, dialog.count_edit,
              dialog.cost_edit, dialog.comment_edit)
    for first, second in zip(fields, fields[1:]):
        assert first.geometry().bottom() < second.geometry().top()
    assert dialog.height() < parent.height()
    assert dialog.form_scroll.verticalScrollBar().maximum() > 0
    assert dialog.form_content.width() <= dialog.form_scroll.viewport().width()
    dialog.form_scroll.ensureWidgetVisible(dialog.remove_photo_button)
    settle(app)
    visible = dialog.remove_photo_button.rect().translated(
        dialog.remove_photo_button.mapTo(dialog.form_scroll.viewport(), QPoint(0, 0))
    )
    assert dialog.form_scroll.viewport().rect().contains(visible)
    footer = dialog.findChild(QDialogButtonBox)
    assert dialog.rect().contains(footer.geometry())
    assert footer.button(QDialogButtonBox.StandardButton.Save).isVisibleTo(dialog)
    dialog.close()
    dialog.deleteLater()
    parent.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
