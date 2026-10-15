"""The user-supplied SVG set is intact, packaged and applied consistently."""
import hashlib
import gc
import json
from pathlib import Path
import tomllib

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton
from shiboken6 import isValid

import test_ui_workflow as workflows
from gis_jkh_assistant.app import icons
from gis_jkh_assistant.app.workflow_widgets import OperationStatus

qt_app = workflows.qt_app
desktop = workflows.desktop


def test_original_assets_are_complete_valid_and_packaged(qt_app):
    expected = json.loads(Path(__file__).with_name("supplied_icons_sha256.json").read_text())
    assert len(expected) == 66
    assert {str(path.relative_to(icons.ASSET_ROOT)).replace("\\", "/")
            for path in icons.ASSET_ROOT.glob("*/*.svg")} == set(expected)
    for name, digest in expected.items():
        path = icons.ASSET_ROOT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
        assert QSvgRenderer(str(path)).isValid(), name
        assert not QIcon(str(path)).pixmap(18, 18).isNull(), name
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert set(config["tool"]["setuptools"]["package-data"]["gis_jkh_assistant.app"]) == {
        "assets/icons/light/*.svg", "assets/icons/dark/*.svg",
        "assets/branding/*.svg", "assets/branding/*.ico",
    }
    assert "--collect-data gis_jkh_assistant.app" in (
        root / ".github/workflows/build-windows.yml").read_text(encoding="utf-8")


def test_action_contracts_theme_switch_and_native_controls(desktop):
    window = desktop.window
    bindings = {
        window.organizations_page: {
            "load_organization_button": "download-cloud", "add_org_button": "plus",
            "save_button": "save", "delete_org_button": "trash",
        },
        window.houses_page: {
            **dict.fromkeys(("sync_houses_button", "sync_all_management_button", "sync_all_work_lists_button",
                             "sync_all_plans_button", "sync_all_fixations_button", "sync_all_quality_button"), "download-cloud"),
            **dict.fromkeys(("export_house_data_button", "import_house_data_button", "import_button"), "excel"),
            "add_house_button": "plus", "add_tariff_button": "plus", "edit_tariff_button": "edit",
            "delete_tariff_button": "trash", "delete_house_button": "trash", "sync_management_button": "sync",
        },
        window.work_lists_page: {
            "creation_toggle": "plus", "add_works_button": "plus", "directory_button": "services",
            "templates_button": "file-template", "replace_from_template_button": "file-template",
            "load_export_template_button": "file-template", "load_gis_lists_button": "download-cloud",
            "files_actions_button": "excel", "remove_works_button": "trash", "delete_button": "trash",
            "clear_works_button": "eraser", "cancel_gis_button": "ban", "save_as_template_button": "save",
            "lazy_fill_button": "wand", "project_save_button": "upload-cloud", "approve_gis_button": "upload-cloud",
            "sync_gis_button": "sync", "create_from_current_button": "copy",
        },
        window.planning_page: {
            "distribute_button": "wand", "dates_button": "calendar", "clear_button": "eraser",
            "load_gis_button": "download-cloud", "publish_button": "upload-cloud",
        },
        window.fixation_page: {
            "load_month_button": "download-cloud", "load_period_button": "download-cloud",
            "fill_month_button": "wand", "fill_period_button": "wand", "add_unplanned_button": "plus",
            "publish_month_button": "upload-cloud", "publish_period_button": "upload-cloud",
            "clear_month_button": "eraser", "clear_period_button": "eraser",
        },
        window.quality_page: {
            "form_month_button": "wand", "form_period_button": "wand", "auto_act_month_button": "wand",
            "auto_act_period_button": "wand", "load_gis_button": "download-cloud", "load_period_button": "download-cloud",
            "save_button": "save", "save_period_button": "save", "publish_button": "upload-cloud",
            "publish_period_button": "upload-cloud", "clear_values_button": "eraser", "clear_period_values_button": "eraser",
            "cancel_button": "ban", "month_acts_button": "act", "selected_act_button": "act",
            "upload_acts_button": "plus", "include_all_button": "check-all", "exclude_all_button": "check-all",
        },
        window.management_fees_page: {
            "load_button": "download-cloud", "new_button": "file-template", "delete_button": "trash",
            "refresh_protocols_button": "sync", "save_button": "upload-cloud", "approve_button": "upload-cloud",
        },
        window.settings_page: {"helper_setup_button": "browser-helper", "light_radio": "sun", "dark_radio": "moon"},
    }
    before = {getattr(page, name): (getattr(page, name).text(), getattr(page, name).isEnabled(),
                                  getattr(page, name).objectName())
              for page, contract in bindings.items() for name in contract}
    for theme in ("dark", "light") * 3:
        window._theme_changed(theme)
        workflows._settle(desktop.app)
        gc.collect()
        assert isValid(window.work_lists_page.manual_template_action)
        for page, contract in bindings.items():
            for attribute, name in contract.items():
                button = getattr(page, attribute)
                assert button.property("iconName") == name, (type(page).__name__, attribute)
                assert icons.asset_path(name).parent.name == theme
                assert button.icon().pixmap(button.iconSize()).toImage() == icons.icon(
                    name, size=button.iconSize().width()).pixmap(button.iconSize()).toImage()
        for button, state in before.items():
            assert (button.text(), button.isEnabled(), button.objectName()) == state
        navigation_icons = ("building", "home", "services", "calendar", "tasks", "quality", "housing-fee", "settings")
        for row, name in enumerate(navigation_icons):
            assert window.navigation.item(row).icon().pixmap(20).toImage() == icons.icon(name).pixmap(20).toImage()
        for page, column, name in ((window.fixation_page, 9, "camera"), (window.quality_page, 6, "act")):
            button = page.table.cellWidget(0, column)
            assert button.property("iconName") == name
            assert button.icon().pixmap(16).toImage() == icons.icon(name, size=16).pixmap(16).toImage()
        # Search decoration and context-menu actions also refresh with the theme.
        search = next(a for a in window.houses_page.search_edit.actions() if a.property("iconName") == "search")
        assert search.icon().pixmap(16).toImage() == icons.icon("search", size=16).pixmap(16).toImage()
        for action, name in zip(window.planning_page.table.cell_actions.actions(), ("calendar", "eraser")):
            assert action.icon().pixmap(18).toImage() == icons.icon(name, size=18).pixmap(18).toImage()
        for action, button, _when in window.work_lists_page.files_actions_button.entries:
            assert action.icon().pixmap(18).toImage() == button.icon().pixmap(18).toImage()
    assert window.settings_page.shared_radio.icon().isNull()
    assert window.settings_page.next_step_panel_checkbox.icon().isNull()
    for spin in (window.work_lists_page.start_year, window.work_lists_page.end_year):
        assert spin._plus_button.text() == "+" and spin._plus_button.icon().isNull()
        assert spin._minus_button.icon().isNull()


def test_dark_svg_gradient_strokes_are_visible_without_modifying_sources(qt_app):
    from gis_jkh_assistant.app.styles import apply_theme
    apply_theme(qt_app, "dark")
    try:
        for name in ("plus", "building", "home", "upload-cloud", "check-all"):
            image = icons.icon(name, size=24).pixmap(24).toImage()
            vivid = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
                     if image.pixelColor(x, y).alpha() > 100 and image.pixelColor(x, y).blue() > 130]
            assert len(vivid) > 8, name  # An unresolved gradient previously left a dark outline only.
            assert b"lineargradient" in icons.asset_path(name).read_bytes()
    finally:
        apply_theme(qt_app, "light")


def test_vector_disabled_state_and_truthful_success_indicator(qt_app):
    button = QPushButton("Удалить")
    icons.set_button_icon(button, "trash")
    size = QSize(18, 18)
    assert button.icon().pixmap(size, QIcon.Mode.Normal).toImage() != button.icon().pixmap(
        size, QIcon.Mode.Disabled).toImage()
    status = OperationStatus()
    status.outcome("Ответ получен; данные ещё проверяются", tone="muted")
    assert status.state_icon.isHidden()
    status.outcome("Изменения сохранены", tone="success")
    assert not status.state_icon.isHidden()
    status.outcome("Не удалось сохранить", tone="danger")
    assert status.state_icon.isHidden()
    status.close()
    button.close()
    status.deleteLater()
    button.deleteLater()
