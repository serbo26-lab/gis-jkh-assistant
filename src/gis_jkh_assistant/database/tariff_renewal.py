"""Finite tariff years; recurring prices are expanded only to a requested date."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone


def anniversary(start: date, years: int = 1) -> date:
    """A leap-day year continues on March 1 in a non-leap year."""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return date(start.year + years, 3, 1)


def year_end(start: date) -> date:
    return anniversary(start) - timedelta(days=1) if start.year < 9999 else date.max


def initialize_periods(repository):
    connection = repository.connection
    repository._ensure_column("house_tariffs", "auto_renew", "INTEGER NOT NULL DEFAULT 0")
    repository._ensure_column("house_tariffs", "renewal_source_id", "TEXT")
    if not connection.execute("SELECT 1 FROM house_tariffs WHERE manual_end_date IS NULL LIMIT 1").fetchone():
        return
    rows = connection.execute("SELECT * FROM house_tariffs ORDER BY house_id, start_year, start_month, start_day").fetchall()
    for index, row in enumerate(rows):
        finish = row["manual_end_date"]
        if finish is not None:
            continue
        following = rows[index + 1] if index + 1 < len(rows) else None
        if following and following["house_id"] == row["house_id"]:
            finish = (date(following["start_year"], following["start_month"], following["start_day"]) - timedelta(days=1)).isoformat()
        else:
            finish = year_end(date(row["start_year"], row["start_month"], row["start_day"])).isoformat()
        connection.execute("UPDATE house_tariffs SET manual_end_date=?, auto_renew=? WHERE id=?",
                           (finish, 1, row["id"]))


def _continuations(root, limit, through):
    finish = root.effective_to_date
    if not root.auto_renew or finish is None or finish == date.max:
        return
    anchor = finish + timedelta(days=1)
    offset = 0
    while anchor.year + offset <= 9999:
        start = anniversary(anchor, offset)
        if start > through or start >= limit:
            break
        end = (anniversary(anchor, offset + 1) - timedelta(days=1)
               if anchor.year + offset < 9999 else date.max)
        end = min(end, limit - timedelta(days=1)) if limit < date.max else end
        yield replace(
            root, id=f"renewal:{root.id}:{start.isoformat()}",
            start_year=start.year, start_month=start.month, start_day=start.day,
            end_year=end.year, end_month=end.month, end_day=end.day,
            manual_end_date=end.isoformat(), renewal_source_id=root.id,
        )
        offset += 1


def projected_tariffs(repository, house_id, through):
    stored = repository.list_house_tariffs(house_id)
    roots = [item for item in stored if not item.renewal_source_id]
    result = {item.effective_from_date: item for item in stored}
    for index, root in enumerate(roots):
        limit = roots[index + 1].effective_from_date if index + 1 < len(roots) else date.max
        for item in _continuations(root, limit, through):
            result.setdefault(item.effective_from_date, item)
    return sorted(result.values(), key=lambda item: item.effective_from_date)


def ensure_periods(repository, house_id, through):
    with repository.connection.atomic():
        existing = {item.id for item in repository.list_house_tariffs(house_id)}
        missing = [item for item in projected_tariffs(repository, house_id, through) if item.id not in existing]
        if not missing:
            return
        for item in missing:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            repository.connection.execute(
                """INSERT INTO house_tariffs (
                    id, house_id, amount, start_year, start_month, start_day, start_precision,
                    end_year, end_month, end_day, manual_end_date, auto_renew, renewal_source_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item.id, item.house_id, item.amount, item.start_year, item.start_month,
                 item.start_day, item.start_precision, item.end_year, item.end_month, item.end_day,
                 item.manual_end_date, 1, item.renewal_source_id, timestamp, timestamp),
            )
        repository._recalculate_house_tariff_periods(house_id)


def discard_future_renewals(repository, house_id, start, *, exclude_id=None):
    """An explicit change replaces automatic continuations up to the next explicit change."""
    rows = repository.list_house_tariffs(house_id)
    following = next((t.effective_from_date for t in rows if t.id != exclude_id
                      and not t.renewal_source_id and t.effective_from_date > start), date.max)
    for item in rows:
        if (item.id != exclude_id and item.renewal_source_id
                and start <= item.effective_from_date < following):
            repository.connection.execute("DELETE FROM house_tariffs WHERE id=?", (item.id,))
