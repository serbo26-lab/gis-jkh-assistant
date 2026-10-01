from gis_jkh_assistant.work_planning import helper_supports_work_plans


def test_planning_requires_helper_015_or_newer() -> None:
    assert not helper_supports_work_plans(None)
    assert not helper_supports_work_plans("0.1.4")
    assert helper_supports_work_plans("0.1.5")
    assert helper_supports_work_plans("0.2.0")
