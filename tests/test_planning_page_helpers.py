from gis_jkh_assistant.app.planning_page import _helper_supports_work_plans


def test_planning_requires_helper_015_or_newer() -> None:
    assert not _helper_supports_work_plans(None)
    assert not _helper_supports_work_plans("0.1.4")
    assert _helper_supports_work_plans("0.1.5")
    assert _helper_supports_work_plans("0.2.0")
