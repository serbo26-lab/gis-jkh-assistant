"""Keep local year/month choices when dependent selectors are rebuilt."""

def remember_selection(page):
    work_list_id = page._current_work_list_id
    year = getattr(page, "_displayed_year", None)
    if not work_list_id or year is None:
        return
    memory = getattr(page, "_selection_memory", {})
    memory[work_list_id] = year
    memory[(work_list_id, year)] = getattr(page, "_current_month", None)
    page._selection_memory = memory


def rebuild_years(page, work_list_id):
    remember_selection(page)
    page._current_work_list_id = str(work_list_id) if work_list_id else None
    page._displayed_year = None
    work_list = page.repository.get_work_list(page._current_work_list_id) if work_list_id else None
    memory = getattr(page, "_selection_memory", {})
    page.year_combo.blockSignals(True)
    try:
        page.year_combo.clear()
        if work_list:
            for year in range(work_list.start_year, work_list.end_year + 1):
                page.year_combo.addItem(str(year), year)
            preferred = memory.get(work_list.id, work_list.base_year)
            index = page.year_combo.findData(preferred)
            page.year_combo.setCurrentIndex(max(index, 0))
        if hasattr(page, "_current_month"):
            page._current_month = memory.get((page._current_work_list_id, page.year_combo.currentData()))
    finally:
        page.year_combo.blockSignals(False)


def select_year(page):
    remember_selection(page)
    year = page.year_combo.currentData()
    if hasattr(page, "_current_month"):
        key = (page._current_work_list_id, year)
        page._current_month = getattr(page, "_selection_memory", {}).get(key, page._current_month)
    page._displayed_year = year
