"""Pure workflow decisions from existing domain state, without changing GIS rules."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MonthState:
    month: int
    status: str = "LOCAL"
    has_data: bool = False
    publish_error: str | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class NextStep:
    stage: int
    key: str | None
    title: str
    action: str
    detail: str
    done: frozenset[int] = frozenset()


def quality_step(months: list[MonthState]) -> NextStep:
    filled = [month for month in months if month.has_data]
    if not filled or len(filled) < len(months):
        return NextStep(0, "form", "Оценка ещё не сформирована", "Сформировать оценку",
                        f"Сформировано: {len(filled)} из {len(months)} мес. Включите работы по фактическим показателям; затем подготовьте акты или сохраните проект.")
    unsaved = [month for month in filled if month.status in {"LOCAL", "MODIFIED", "CANCELLED", "ERROR", ""}]
    if unsaved:
        return NextStep(2, "save", "Оценка сохранена локально", "Сохранить проект",
                        f"Готово к сохранению: {len(unsaved)} мес. Проект можно сохранить без актов; они потребуются при размещении.", frozenset({0, 1} if all(not month.publish_error for month in filled) else {0}))
    drafts = [month for month in filled if month.status == "DRAFT"]
    if drafts:
        errors = [month.publish_error for month in drafts if month.publish_error]
        if errors:
            return NextStep(1, "acts", "Проект в ГИС • подготовьте акты", "Подготовить акты",
                            errors[0], frozenset({0, 2}))
        return NextStep(3, "publish", "Проект готов к размещению", "Разместить",
                        f"Проверены работы и акты. К размещению: {len(drafts)} мес.", frozenset({0, 1, 2}))
    if all(month.status == "APPROVED" for month in filled):
        return NextStep(3, "next", "Оценка размещена в ГИС", "Следующий месяц",
                        f"Размещено: {len(filled)} из {len(months)} месяцев выбранной области.", frozenset({0, 1, 2, 3}))
    return NextStep(2, "save", "Проверьте состояние оценки", "Сохранить проект",
                    "Проверьте локальные данные и последний ответ ГИС.", frozenset({0}))


def fixation_step(months: list[MonthState]) -> NextStep:
    filled = [month for month in months if month.has_data]
    if not filled or len(filled) < len(months):
        return NextStep(1, "fill", "Факт ещё не заполнен", "Заполнить по плану",
                        f"Заполнено: {len(filled)} из {len(months)} мес. Перенесите план в факт, затем проверьте суммы и фотографии.", frozenset({0}))
    pending = [month for month in filled if month.status not in {"SAVED", "APPROVED"} or month.last_error]
    if pending:
        return NextStep(2, "publish", "Фактические показатели готовы к проверке", "Разместить",
                        f"Заполнено: {len(filled)} из {len(months)} мес. Будут обновлены все заполненные месяцы выбранной области, включая ранее размещённые.", frozenset({0, 1}))
    return NextStep(3, "next", "Фиксация размещена в ГИС", "К оценке качества",
                    f"Размещены все заполненные месяцы ({len(filled)}). Следующий этап — оценка качества.", frozenset({0, 1, 2, 3}))
