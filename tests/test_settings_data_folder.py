"""Opening the data folder targets the active profile without modifying it."""

from pathlib import Path

import pytest

import test_ui_workflow as workflows
from gis_jkh_assistant.app import settings_page
from gis_jkh_assistant.database import Repository

qt_app = workflows.qt_app


@pytest.mark.parametrize("opened", [True, False])
def test_data_folder_uses_active_database_and_reports_launch_failure(
    qt_app, tmp_path, monkeypatch, opened,
):
    database = tmp_path / "Другой профиль с пробелами" / "data.db"
    repository = Repository(database)
    repository.set_setting("backup-test", "сохранено")
    urls, warnings = [], []

    def open_url(url):
        urls.append(url)
        return opened

    monkeypatch.setattr(settings_page.QDesktopServices, "openUrl", open_url)
    monkeypatch.setattr(
        settings_page.QMessageBox, "warning", lambda *args: warnings.append(args[2]),
    )
    page = settings_page.SettingsPage(repository)
    try:
        page.open_data_folder_button.click()
        assert len(urls) == 1
        assert urls[0].isLocalFile()
        assert Path(urls[0].toLocalFile()) == database.parent
        assert repository.get_setting("backup-test") == "сохранено"
        if opened:
            assert not warnings
        else:
            assert len(warnings) == 1
            assert str(database.parent) in warnings[0]
            assert "закройте программу" in warnings[0]
    finally:
        page.close()
        repository.close()
