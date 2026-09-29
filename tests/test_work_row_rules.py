from gis_jkh_assistant.app.work_row_rules import (
    WorkRowMode,
    editable_columns_for_mode,
    infer_work_row_mode,
)


def test_empty_row_keeps_both_input_paths_available() -> None:
    mode = infer_work_row_mode(price=None, volume=None, cost=None)
    assert mode == WorkRowMode.EMPTY
    assert editable_columns_for_mode(mode) == frozenset({0, 1, 2, 3, 4})


def test_price_or_volume_selects_components_mode() -> None:
    mode = infer_work_row_mode(price="10", volume=None, cost=None)
    assert mode == WorkRowMode.COMPONENTS
    assert editable_columns_for_mode(mode) == frozenset({0, 1, 2, 3})

    mode = infer_work_row_mode(price=None, volume="2.5", cost=None)
    assert mode == WorkRowMode.COMPONENTS


def test_cost_selects_quantity_plus_cost_mode() -> None:
    mode = infer_work_row_mode(price=None, volume=None, cost="1500.00")
    assert mode == WorkRowMode.TOTAL
    assert editable_columns_for_mode(mode) == frozenset({0, 3, 4})


def test_quantity_alone_does_not_choose_a_mode() -> None:
    # Quantity belongs to both valid GIS schemes and therefore must not lock fields.
    mode = infer_work_row_mode(price="", volume="", cost="")
    assert mode == WorkRowMode.EMPTY
