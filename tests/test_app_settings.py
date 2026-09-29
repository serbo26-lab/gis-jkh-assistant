from gis_jkh_assistant.database import Repository


def test_application_settings_are_persistent(tmp_path) -> None:
    path = tmp_path / "app.db"

    repository = Repository(path)
    assert repository.get_setting("gis_export_template") is None
    repository.set_setting("gis_export_template", "template.xlsx")
    repository.close()

    repository = Repository(path)
    assert repository.get_setting("gis_export_template") == "template.xlsx"
    repository.set_setting("gis_export_template", None)
    assert repository.get_setting("gis_export_template") is None
    repository.close()
