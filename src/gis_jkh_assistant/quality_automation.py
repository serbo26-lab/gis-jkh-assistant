"""Automation helpers for quality assessment periods and acceptance acts."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from typing import Any

from gis_jkh_assistant.acceptance_act_pdf import (
    AcceptanceActWorkRow,
    build_acceptance_act_pdf,
)
from gis_jkh_assistant.database.quality_assessment_store import (
    QualityAssessmentStore,
)


@dataclass(frozen=True, slots=True)
class QualityFillResult:
    included: int
    excluded: int
    protected: int


@dataclass(frozen=True, slots=True)
class QualityActGenerationResult:
    created: int
    linked_items: int
    protected_items: int
    missing_costs: int


def _has_fixation_fact(item: Any) -> bool:
    return any(
        value not in (None, "")
        for value in (
            getattr(item, "fact_price", None),
            getattr(item, "fact_amount", None),
            getattr(item, "fact_count", None),
            getattr(item, "fact_total_cost", None),
        )
    )


def _manual_act_item_ids(
    store: QualityAssessmentStore,
    period_id: str,
) -> set[str]:
    """Rows with user/imported acts are protected from automatic act generation."""

    rows = store.connection.execute(
        """
        SELECT DISTINCT l.quality_item_id
        FROM quality_assessment_act_links l
        JOIN quality_assessment_acts a ON a.id = l.act_id
        JOIN quality_assessment_items i ON i.id = l.quality_item_id
        WHERE i.quality_period_id = ?
          AND a.deleted = 0
          AND l.deleted = 0
          AND a.generated = 0
        """,
        (period_id,),
    ).fetchall()
    return {str(row["quality_item_id"]) for row in rows}


def fill_quality_period_from_fixation(
    store: QualityAssessmentStore,
    period_id: str,
) -> QualityFillResult:
    """Build a quality assessment from the fixation facts of the same month.

    Rows that already have a user/imported act are intentionally left untouched.
    Other rows are included only when fixation contains actual values. For normal
    completed work the quality-specific fields stay empty: cost, days of improper
    quality and reduction are filled only for quality-violation/recalculation cases.
    """

    period = store.get_period(period_id)
    if period is None:
        raise ValueError("Период оценки качества не найден.")

    fixation = store.repository.get_work_fixation_period_for_month(
        period.work_list_id,
        year=period.year,
        month=period.month,
    )
    fixation_items = (
        store.repository.list_work_fixation_items(fixation.id)
        if fixation is not None
        else []
    )

    by_list_item_id = {
        item.work_list_item_id: item
        for item in fixation_items
        if item.work_list_item_id
    }
    by_guid = {
        str(item.work_guid): item
        for item in fixation_items
        if item.work_guid
    }
    by_code = {
        str(item.work_code): item
        for item in fixation_items
        if item.work_code
    }

    protected = _manual_act_item_ids(store, period_id)
    included = 0
    excluded = 0
    updates: list[tuple[int, str | None, int | None, str | None, str]] = []

    for item in store.list_items(period_id):
        if item.id in protected:
            continue
        fixation_item = (
            by_list_item_id.get(item.work_list_item_id)
            or (by_guid.get(str(item.work_guid)) if item.work_guid else None)
            or (by_code.get(str(item.work_code)) if item.work_code else None)
        )
        if fixation_item is not None and _has_fixation_fact(fixation_item):
            updates.append((1, None, None, None, item.id))
            included += 1
        else:
            updates.append((0, None, None, None, item.id))
            excluded += 1

    with store.connection:
        store.connection.executemany(
            """
            UPDATE quality_assessment_items
            SET included = ?, cost = ?, days_count = ?, cost_reduction = ?
            WHERE id = ?
            """,
            updates,
        )
        store._touch_period(period_id)

    return QualityFillResult(
        included=included,
        excluded=excluded,
        protected=len(protected),
    )


def _acceptance_work_rows(
    store: QualityAssessmentStore,
    period_id: str,
    item_ids: list[str],
) -> tuple[list[AcceptanceActWorkRow], int]:
    period = store.get_period(period_id)
    if period is None:
        raise ValueError("Период оценки качества не найден.")

    fixation = store.repository.get_work_fixation_period_for_month(
        period.work_list_id,
        year=period.year,
        month=period.month,
    )
    fixation_items = (
        store.repository.list_work_fixation_items(fixation.id)
        if fixation is not None
        else []
    )
    list_items = store.repository.list_work_list_items(period.work_list_id)

    fixation_by_list_id = {
        item.work_list_item_id: item
        for item in fixation_items
        if item.work_list_item_id
    }
    fixation_by_guid = {
        str(item.work_guid): item
        for item in fixation_items
        if item.work_guid
    }
    fixation_by_code = {
        str(item.work_code): item
        for item in fixation_items
        if item.work_code
    }
    list_by_id = {item.id: item for item in list_items}
    items_by_id = {item.id: item for item in store.list_items(period_id)}

    rows: list[AcceptanceActWorkRow] = []
    missing_costs = 0
    for item_id in item_ids:
        item = items_by_id[item_id]
        fixation_item = (
            fixation_by_list_id.get(item.work_list_item_id)
            or (
                fixation_by_guid.get(str(item.work_guid))
                if item.work_guid
                else None
            )
            or (
                fixation_by_code.get(str(item.work_code))
                if item.work_code
                else None
            )
        )
        list_item = (
            list_by_id.get(item.work_list_item_id)
            if item.work_list_item_id
            else None
        )
        actual_cost = (
            getattr(fixation_item, "fact_total_cost", None)
            if fixation_item is not None
            else None
        )
        if actual_cost in (None, ""):
            missing_costs += 1
        rows.append(
            AcceptanceActWorkRow(
                name=item.work_name,
                unit_name=(
                    getattr(fixation_item, "unit_name", None)
                    or getattr(list_item, "unit_name", None)
                ),
                periodicity=(
                    getattr(fixation_item, "fact_count", None)
                    if fixation_item is not None
                    else None
                ),
                actual_cost=actual_cost,
            )
        )
    return rows, missing_costs


def generate_acceptance_act_for_period(
    store: QualityAssessmentStore,
    *,
    period_id: str,
    organization_name: str,
    house_address: str,
) -> QualityActGenerationResult:
    """Create one standard monthly act without touching user/imported acts."""

    period = store.get_period(period_id)
    if period is None:
        raise ValueError("Период оценки качества не найден.")

    items = store.list_items(period_id)
    protected = _manual_act_item_ids(store, period_id)
    target_ids = [
        item.id
        for item in items
        if item.included and item.id not in protected
    ]
    if not target_ids:
        return QualityActGenerationResult(
            created=0,
            linked_items=0,
            protected_items=len(protected),
            missing_costs=0,
        )

    # Replace only program-generated acceptance acts. Manual/imported acts remain
    # untouched and their linked rows are excluded from the new generated act.
    for act in store.list_period_acts(period_id):
        if act.generated and act.act_type == "ACCEPTANCE_ACT":
            store.delete_act(act.id)

    work_rows, missing_costs = _acceptance_work_rows(
        store,
        period_id,
        target_ids,
    )
    last_day = calendar.monthrange(period.year, period.month)[1]
    period_from = f"01.{period.month:02d}.{period.year}"
    period_to = f"{last_day:02d}.{period.month:02d}.{period.year}"
    number = str(period.month)

    pdf = build_acceptance_act_pdf(
        organization_name=organization_name,
        house_address=house_address,
        act_number=number,
        act_date=period_to,
        period_from=period_from,
        period_to=period_to,
        work_rows=work_rows,
    )
    file_name = f"Акт_выполненных_работ_{period.month:02d}_{period.year}.pdf"
    act = store.add_act(
        item_id=target_ids[0],
        act_type="ACCEPTANCE_ACT",
        number=number,
        reg_date=period_to,
        period_from=period_from,
        period_to=period_to,
        file_name=file_name,
        description=file_name,
        content_type="application/pdf",
        content=pdf,
        generated=True,
    )
    store.set_act_links(act.id, target_ids)
    return QualityActGenerationResult(
        created=1,
        linked_items=len(target_ids),
        protected_items=len(protected),
        missing_costs=missing_costs,
    )
