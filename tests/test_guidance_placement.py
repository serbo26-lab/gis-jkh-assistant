"""Guidance stays above section controls, outside the resizable work area."""

import test_ui_workflow as workflows
from PySide6.QtCore import QPoint

from gis_jkh_assistant.app.workflow_widgets import WorkflowPanel

desktop, qt_app = workflows.desktop, workflows.qt_app


def test_guidance_follows_title_in_every_existing_working_section(desktop):
    window = desktop.window
    pages = (
        window.organizations_page, window.houses_page, window.work_lists_page,
        window.planning_page, window.fixation_page, window.quality_page,
    )
    for width in (1366, 1024):
        window.resize(width, 768)
        for page in pages:
            window.navigation.setCurrentRow(window.pages.indexOf(page))
            workflows._settle(desktop.app)
            panel = page.workflow
            body = page.page_content.layout()
            assert panel.parentWidget() is page.page_content
            assert body.indexOf(panel) == 1, (type(page).__name__, width)
            assert panel.isVisibleTo(page)
            assert panel.geometry().top() > body.itemAt(0).geometry().bottom()
            assert panel.help_button.parentWidget() is panel
            assert panel.help_button.isVisibleTo(panel)
            for name in ("context_card", "workspace_splitter", "months_card"):
                control = getattr(page, name, None)
                if control is not None and control.isVisibleTo(page):
                    assert panel.geometry().bottom() < control.mapTo(page.page_content, QPoint()).y()

    toggle = window.settings_page.next_step_panel_checkbox
    toggle.setChecked(False)
    assert all(page.workflow.isHidden() for page in pages)
    toggle.setChecked(True)
    assert all(not page.workflow.isHidden() for page in pages)
    assert all(page.page_content.layout().indexOf(page.workflow) == 1 for page in pages)
    assert window.management_fees_page.findChildren(WorkflowPanel) == []
