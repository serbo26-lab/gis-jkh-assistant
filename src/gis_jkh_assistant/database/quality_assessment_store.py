"""SQLite storage for the quality-assessment workflow."""

from __future__ import annotations

import calendar
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import WorkListStatus
from .repository import Repository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class QualityAssessmentPeriod:
    id: str
    work_list_id: str
    year: int
    month: int
    reporting_period_guid: str | None = None
    estimation_guid: str | None = None
    gis_status: str | None = None
    gis_last_sync_at: str | None = None
    gis_last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class QualityAssessmentItem:
    id: str
    quality_period_id: str
    work_list_item_id: str | None
    position: int
    work_code: str | None
    work_name: str
    work_guid: str | None
    included: bool
    cost: str | None
    days_count: int | None
    days_per_month: int
    cost_reduction: str | None
    gis_estimation_item_guid: str | None = None


@dataclass(frozen=True, slots=True)
class QualityAssessmentAct:
    id: str
    quality_item_id: str
    act_type: str
    number: str
    reg_date: str
    period_from: str
    period_to: str
    file_name: str
    description: str | None
    content_type: str
    file_size: int
    content: bytes | None = None
    remote_content_guid: str | None = None
    remote_act_guid: str | None = None
    deleted: bool = False
    link_deleted: bool = False
    generated: bool = False
    created_at: str | None = None


class QualityAssessmentStore:
    """Quality-assessment tables kept beside the main Repository connection."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.connection: sqlite3.Connection = repository.connection
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS quality_assessment_periods (
                id TEXT PRIMARY KEY,
                work_list_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                reporting_period_guid TEXT,
                estimation_guid TEXT,
                gis_status TEXT,
                gis_last_sync_at TEXT,
                gis_last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (work_list_id) REFERENCES work_lists(id) ON DELETE CASCADE,
                UNIQUE (work_list_id, year, month),
                CHECK (month BETWEEN 1 AND 12)
            );

            CREATE INDEX IF NOT EXISTS ix_quality_periods_list
                ON quality_assessment_periods(work_list_id, year, month);

            CREATE TABLE IF NOT EXISTS quality_assessment_items (
                id TEXT PRIMARY KEY,
                quality_period_id TEXT NOT NULL,
                work_list_item_id TEXT,
                position INTEGER NOT NULL,
                work_code TEXT,
                work_name TEXT NOT NULL,
                work_guid TEXT,
                included INTEGER NOT NULL DEFAULT 0,
                cost TEXT,
                days_count INTEGER,
                days_per_month INTEGER NOT NULL,
                cost_reduction TEXT,
                gis_estimation_item_guid TEXT,
                FOREIGN KEY (quality_period_id)
                    REFERENCES quality_assessment_periods(id) ON DELETE CASCADE,
                FOREIGN KEY (work_list_item_id)
                    REFERENCES work_list_items(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS ix_quality_items_period
                ON quality_assessment_items(quality_period_id, position);

            CREATE TABLE IF NOT EXISTS quality_assessment_acts (
                id TEXT PRIMARY KEY,
                quality_item_id TEXT NOT NULL,
                act_type TEXT NOT NULL,
                number TEXT NOT NULL,
                reg_date TEXT NOT NULL,
                period_from TEXT NOT NULL,
                period_to TEXT NOT NULL,
                file_name TEXT NOT NULL,
                description TEXT,
                content_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                content BLOB,
                remote_content_guid TEXT,
                remote_act_guid TEXT,
                deleted INTEGER NOT NULL DEFAULT 0,
                generated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (quality_item_id)
                    REFERENCES quality_assessment_items(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_quality_acts_item
                ON quality_assessment_acts(quality_item_id, created_at);

            CREATE TABLE IF NOT EXISTS quality_assessment_act_links (
                act_id TEXT NOT NULL,
                quality_item_id TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (act_id, quality_item_id),
                FOREIGN KEY (act_id)
                    REFERENCES quality_assessment_acts(id) ON DELETE CASCADE,
                FOREIGN KEY (quality_item_id)
                    REFERENCES quality_assessment_items(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_quality_act_links_item
                ON quality_assessment_act_links(quality_item_id, deleted);
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(quality_assessment_acts)"
            ).fetchall()
        }
        if "deleted" not in columns:
            self.connection.execute(
                "ALTER TABLE quality_assessment_acts "
                "ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"
            )
        if "generated" not in columns:
            self.connection.execute(
                "ALTER TABLE quality_assessment_acts "
                "ADD COLUMN generated INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO quality_assessment_act_links
                (act_id, quality_item_id, deleted, created_at)
            SELECT id, quality_item_id, deleted, created_at
            FROM quality_assessment_acts
            """
        )
        self.connection.commit()

    @staticmethod
    def _period(row: sqlite3.Row) -> QualityAssessmentPeriod:
        return QualityAssessmentPeriod(**dict(row))

    @staticmethod
    def _item(row: sqlite3.Row) -> QualityAssessmentItem:
        data = dict(row)
        data["included"] = bool(data["included"])
        return QualityAssessmentItem(**data)

    @staticmethod
    def _act(row: sqlite3.Row) -> QualityAssessmentAct:
        data = dict(row)
        data["deleted"] = bool(data.get("deleted", 0))
        data["link_deleted"] = bool(data.get("link_deleted", 0))
        data["generated"] = bool(data.get("generated", 0))
        return QualityAssessmentAct(**data)

    def get_period(self, period_id: str) -> QualityAssessmentPeriod | None:
        row = self.connection.execute(
            "SELECT * FROM quality_assessment_periods WHERE id = ?",
            (period_id,),
        ).fetchone()
        return self._period(row) if row else None

    def get_period_for_month(
        self, work_list_id: str, *, year: int, month: int
    ) -> QualityAssessmentPeriod | None:
        row = self.connection.execute(
            """
            SELECT * FROM quality_assessment_periods
            WHERE work_list_id = ? AND year = ? AND month = ?
            """,
            (work_list_id, int(year), int(month)),
        ).fetchone()
        return self._period(row) if row else None

    def list_periods(self, work_list_id: str, *, year: int) -> list[QualityAssessmentPeriod]:
        rows = self.connection.execute(
            """
            SELECT * FROM quality_assessment_periods
            WHERE work_list_id = ? AND year = ?
            ORDER BY month
            """,
            (work_list_id, int(year)),
        ).fetchall()
        return [self._period(row) for row in rows]

    def get_or_create_period(
        self, *, work_list_id: str, year: int, month: int
    ) -> QualityAssessmentPeriod:
        work_list = self.repository.get_work_list(work_list_id)
        if work_list is None:
            raise ValueError("Перечень работ и услуг не найден.")
        if (
            work_list.status != WorkListStatus.PLACED
            and str(work_list.gis_status or "").upper() != "APPROVED"
        ):
            raise ValueError(
                "Оценка качества доступна только для размещенного перечня работ и услуг."
            )
        if not work_list.gis_work_list_guid:
            raise ValueError("У перечня отсутствует GUID ГИС ЖКХ.")

        year = int(year)
        month = int(month)
        if not 1 <= month <= 12:
            raise ValueError("Месяц должен быть от 1 до 12.")
        if not (
            (work_list.start_year, work_list.start_month)
            <= (year, month)
            <= (work_list.end_year, work_list.end_month)
        ):
            raise ValueError("Выбранный месяц не входит в период перечня.")

        existing = self.get_period_for_month(work_list_id, year=year, month=month)
        if existing is not None:
            return existing

        period_id = str(uuid.uuid4())
        timestamp = _now()
        month_days = calendar.monthrange(year, month)[1]
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO quality_assessment_periods
                    (id, work_list_id, year, month, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (period_id, work_list_id, year, month, timestamp, timestamp),
            )
            for item in self.repository.list_work_list_items(work_list_id):
                self.connection.execute(
                    """
                    INSERT INTO quality_assessment_items (
                        id, quality_period_id, work_list_item_id, position,
                        work_code, work_name, work_guid, included,
                        days_per_month
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        period_id,
                        item.id,
                        int(item.position),
                        _clean(item.work_code),
                        item.work_name,
                        _clean(item.work_guid),
                        month_days,
                    ),
                )
        result = self.get_period(period_id)
        assert result is not None
        return result

    def list_items(self, period_id: str) -> list[QualityAssessmentItem]:
        rows = self.connection.execute(
            """
            SELECT * FROM quality_assessment_items
            WHERE quality_period_id = ?
            ORDER BY position
            """,
            (period_id,),
        ).fetchall()
        return [self._item(row) for row in rows]

    def get_item(self, item_id: str) -> QualityAssessmentItem | None:
        row = self.connection.execute(
            "SELECT * FROM quality_assessment_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return self._item(row) if row else None

    def update_item(
        self,
        item_id: str,
        *,
        included: bool,
        cost: str | None,
        days_count: int | None,
        cost_reduction: str | None,
    ) -> None:
        item = self.get_item(item_id)
        if item is None:
            raise ValueError("Строка оценки качества не найдена.")
        if days_count is not None:
            days_count = int(days_count)
            if days_count < 0 or days_count > item.days_per_month:
                raise ValueError(
                    "Количество дней ненадлежащего качества должно быть от 0 "
                    f"до {item.days_per_month}."
                )
        with self.connection:
            self.connection.execute(
                """
                UPDATE quality_assessment_items
                SET included = ?, cost = ?, days_count = ?, cost_reduction = ?
                WHERE id = ?
                """,
                (
                    1 if included else 0,
                    _clean(cost),
                    days_count,
                    _clean(cost_reduction),
                    item_id,
                ),
            )
            self._touch_period(item.quality_period_id)

    def set_items_included(self, period_id: str, included: bool) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE quality_assessment_items
                SET included = ?
                WHERE quality_period_id = ?
                """,
                (1 if included else 0, period_id),
            )
            self._touch_period(period_id)

    def clear_quality_values(self, period_id: str) -> int:
        """Clear recalculation-only fields without touching inclusion or acts."""

        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE quality_assessment_items
                SET cost = NULL,
                    days_count = NULL,
                    cost_reduction = NULL
                WHERE quality_period_id = ?
                """,
                (period_id,),
            )
            self._touch_period(period_id)
        return int(cursor.rowcount or 0)

    def get_act(self, act_id: str) -> QualityAssessmentAct | None:
        row = self.connection.execute(
            """
            SELECT a.*, 0 AS link_deleted
            FROM quality_assessment_acts a
            WHERE a.id = ?
            """,
            (act_id,),
        ).fetchone()
        return self._act(row) if row else None

    def list_acts(
        self,
        item_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[QualityAssessmentAct]:
        where = "" if include_deleted else "AND a.deleted = 0 AND l.deleted = 0"
        rows = self.connection.execute(
            f"""
            SELECT a.*, l.deleted AS link_deleted
            FROM quality_assessment_acts a
            JOIN quality_assessment_act_links l ON l.act_id = a.id
            WHERE l.quality_item_id = ? {where}
            ORDER BY a.created_at, a.rowid
            """,
            (item_id,),
        ).fetchall()
        return [self._act(row) for row in rows]

    def list_period_acts(
        self,
        period_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[QualityAssessmentAct]:
        where = "" if include_deleted else "AND a.deleted = 0 AND l.deleted = 0"
        rows = self.connection.execute(
            f"""
            SELECT a.*, MIN(l.deleted) AS link_deleted
            FROM quality_assessment_acts a
            JOIN quality_assessment_act_links l ON l.act_id = a.id
            JOIN quality_assessment_items i ON i.id = l.quality_item_id
            WHERE i.quality_period_id = ? {where}
            GROUP BY a.id
            ORDER BY a.created_at, a.rowid
            """,
            (period_id,),
        ).fetchall()
        return [self._act(row) for row in rows]

    def act_link_item_ids(
        self,
        act_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[str]:
        where = "" if include_deleted else "AND deleted = 0"
        rows = self.connection.execute(
            f"""
            SELECT quality_item_id
            FROM quality_assessment_act_links
            WHERE act_id = ? {where}
            ORDER BY rowid
            """,
            (act_id,),
        ).fetchall()
        return [str(row["quality_item_id"]) for row in rows]

    def count_act_links(self, act_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM quality_assessment_act_links
            WHERE act_id = ? AND deleted = 0
            """,
            (act_id,),
        ).fetchone()
        return int(row["count"] if row else 0)

    def add_act(
        self,
        *,
        item_id: str,
        act_type: str,
        number: str,
        reg_date: str,
        period_from: str,
        period_to: str,
        file_name: str,
        description: str | None,
        content_type: str,
        content: bytes,
        generated: bool = False,
    ) -> QualityAssessmentAct:
        item = self.get_item(item_id)
        if item is None:
            raise ValueError("Строка оценки качества не найдена.")
        if not content:
            raise ValueError("Файл акта пуст.")
        act_id = str(uuid.uuid4())
        created_at = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO quality_assessment_acts (
                    id, quality_item_id, act_type, number, reg_date,
                    period_from, period_to, file_name, description,
                    content_type, file_size, content, generated, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    act_id,
                    item_id,
                    str(act_type),
                    str(number).strip(),
                    str(reg_date),
                    str(period_from),
                    str(period_to),
                    str(file_name),
                    _clean(description),
                    str(content_type),
                    len(content),
                    content,
                    1 if generated else 0,
                    created_at,
                ),
            )
            self.connection.execute(
                """
                INSERT INTO quality_assessment_act_links
                    (act_id, quality_item_id, deleted, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (act_id, item_id, created_at),
            )
            self._touch_period(item.quality_period_id)
        result = self.get_act(act_id)
        assert result is not None
        return result

    def set_act_links(self, act_id: str, item_ids: list[str]) -> None:
        act = self.get_act(act_id)
        if act is None:
            raise ValueError("Акт не найден.")

        owner = self.get_item(act.quality_item_id)
        if owner is None:
            raise ValueError("Не найден период акта.")

        target_ids = list(dict.fromkeys(str(item_id) for item_id in item_ids))
        for item_id in target_ids:
            item = self.get_item(item_id)
            if item is None or item.quality_period_id != owner.quality_period_id:
                raise ValueError("Акт можно применять только к работам одного месяца.")

        existing = {
            str(row["quality_item_id"]): bool(row["deleted"])
            for row in self.connection.execute(
                """
                SELECT quality_item_id, deleted
                FROM quality_assessment_act_links
                WHERE act_id = ?
                """,
                (act_id,),
            ).fetchall()
        }
        remote = bool(act.remote_act_guid or act.remote_content_guid)
        with self.connection:
            for item_id in target_ids:
                if item_id in existing:
                    self.connection.execute(
                        """
                        UPDATE quality_assessment_act_links
                        SET deleted = 0
                        WHERE act_id = ? AND quality_item_id = ?
                        """,
                        (act_id, item_id),
                    )
                else:
                    self.connection.execute(
                        """
                        INSERT INTO quality_assessment_act_links
                            (act_id, quality_item_id, deleted, created_at)
                        VALUES (?, ?, 0, ?)
                        """,
                        (act_id, item_id, _now()),
                    )

            for item_id in set(existing) - set(target_ids):
                if remote:
                    self.connection.execute(
                        """
                        UPDATE quality_assessment_act_links
                        SET deleted = 1
                        WHERE act_id = ? AND quality_item_id = ?
                        """,
                        (act_id, item_id),
                    )
                else:
                    self.connection.execute(
                        """
                        DELETE FROM quality_assessment_act_links
                        WHERE act_id = ? AND quality_item_id = ?
                        """,
                        (act_id, item_id),
                    )

            if not target_ids and not remote:
                self.connection.execute(
                    "DELETE FROM quality_assessment_acts WHERE id = ?",
                    (act_id,),
                )
            self._touch_period(owner.quality_period_id)

    def unlink_act(self, act_id: str, item_id: str) -> None:
        remaining = [
            linked_id
            for linked_id in self.act_link_item_ids(act_id)
            if linked_id != str(item_id)
        ]
        self.set_act_links(act_id, remaining)

    def delete_act(self, act_id: str) -> None:
        self.set_act_links(act_id, [])

    def _touch_period(self, period_id: str) -> None:
        period = self.get_period(period_id)
        status = str(period.gis_status or "").upper() if period else ""
        next_status = "MODIFIED" if status in {"DRAFT", "APPROVED"} else "LOCAL"
        self.connection.execute(
            """
            UPDATE quality_assessment_periods
            SET gis_status = ?, gis_last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (next_status, _now(), period_id),
        )

    def set_gis_state(
        self,
        period_id: str,
        *,
        reporting_period_guid: str | None = None,
        estimation_guid: str | None = None,
        gis_status: str | None = None,
        gis_last_error: str | None = None,
        item_guids: dict[str, str] | None = None,
        act_guids: dict[str, tuple[str | None, str | None]] | None = None,
    ) -> None:
        updates = {
            "gis_last_sync_at": _now(),
            "gis_last_error": _clean(gis_last_error),
            "updated_at": _now(),
        }
        if reporting_period_guid is not None:
            updates["reporting_period_guid"] = _clean(reporting_period_guid)
        if estimation_guid is not None:
            updates["estimation_guid"] = _clean(estimation_guid)
        if gis_status is not None:
            updates["gis_status"] = _clean(gis_status)

        with self.connection:
            clause = ", ".join(f"{key} = ?" for key in updates)
            values = list(updates.values()) + [period_id]
            self.connection.execute(
                f"UPDATE quality_assessment_periods SET {clause} WHERE id = ?",
                values,
            )
            for item_id, guid in (item_guids or {}).items():
                self.connection.execute(
                    """
                    UPDATE quality_assessment_items
                    SET gis_estimation_item_guid = ?
                    WHERE id = ? AND quality_period_id = ?
                    """,
                    (_clean(guid), item_id, period_id),
                )
            for act_id, pair in (act_guids or {}).items():
                content_guid, act_guid = pair
                self.connection.execute(
                    """
                    UPDATE quality_assessment_acts
                    SET remote_content_guid = ?, remote_act_guid = ?, deleted = 0
                    WHERE id = ?
                    """,
                    (_clean(content_guid), _clean(act_guid), act_id),
                )

            self.connection.execute(
                """
                DELETE FROM quality_assessment_act_links
                WHERE deleted = 1
                  AND quality_item_id IN (
                      SELECT id
                      FROM quality_assessment_items
                      WHERE quality_period_id = ?
                  )
                """,
                (period_id,),
            )
            self.connection.execute(
                """
                DELETE FROM quality_assessment_acts
                WHERE id NOT IN (
                    SELECT DISTINCT act_id
                    FROM quality_assessment_act_links
                )
                """
            )
            self.connection.execute(
                """
                DELETE FROM quality_assessment_acts
                WHERE deleted = 1
                  AND quality_item_id IN (
                      SELECT id
                      FROM quality_assessment_items
                      WHERE quality_period_id = ?
                  )
                """,
                (period_id,),
            )

    def mark_cancelled(self, period_id: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE quality_assessment_periods
                SET estimation_guid = NULL,
                    gis_status = 'CANCELLED',
                    gis_last_sync_at = ?,
                    gis_last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (_now(), _now(), period_id),
            )
            self.connection.execute(
                """
                UPDATE quality_assessment_items
                SET gis_estimation_item_guid = NULL
                WHERE quality_period_id = ?
                """,
                (period_id,),
            )
            self.connection.execute(
                """
                UPDATE quality_assessment_acts
                SET remote_act_guid = NULL,
                    deleted = 0
                WHERE id IN (
                    SELECT DISTINCT l.act_id
                    FROM quality_assessment_act_links l
                    JOIN quality_assessment_items i ON i.id = l.quality_item_id
                    WHERE i.quality_period_id = ?
                )
                """,
                (period_id,),
            )

    def set_error(self, period_id: str, message: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE quality_assessment_periods
                SET gis_last_sync_at = ?, gis_last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (_now(), _clean(message), _now(), period_id),
            )
