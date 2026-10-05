"""SQLite repository used by the desktop application."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gis_jkh_assistant.work_fixation import proportional_month_cost

from .models import (
    House,
    HouseTariff,
    ManagementOrganization,
    RecordSource,
    TemplateSource,
    WorkList,
    WorkListItem,
    WorkListStatus,
    WorkFixationItem,
    WorkFixationPeriod,
    WorkFixationPhoto,
    WorkFixationUnplannedItem,
    WorkPlan,
    WorkPlanItem,
    WorkPlanMonth,
    WorkTemplate,
    WorkTemplateItem,
)


def _numeric_text_equal(left: str, right: str) -> bool:
    def as_decimal(value: str) -> Decimal:
        text = str(value or "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
        return Decimal(text)

    try:
        return as_decimal(left) == as_decimal(right)
    except InvalidOperation:
        return str(left or "").strip() == str(right or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    """Small SQLite data-access layer used by the autosaving UI."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS management_organizations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ogrn TEXT NOT NULL UNIQUE,
                kpp TEXT,
                comment TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS houses (
                id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                address TEXT NOT NULL,
                house_object_guid TEXT,
                gis_house_guid TEXT,
                fias_house_guid TEXT,
                gis_unique_house_number TEXT,
                cadastral_number TEXT,
                charge_area TEXT,
                residential_premises_count INTEGER NOT NULL DEFAULT 0,
                non_residential_premises_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'manual',
                comment TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (organization_id)
                    REFERENCES management_organizations(id)
                    ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS ux_house_object_guid
                ON houses(organization_id, house_object_guid)
                WHERE house_object_guid IS NOT NULL AND house_object_guid <> '';

            CREATE UNIQUE INDEX IF NOT EXISTS ux_house_gis_guid
                ON houses(organization_id, gis_house_guid)
                WHERE gis_house_guid IS NOT NULL AND gis_house_guid <> '';

            CREATE TABLE IF NOT EXISTS house_tariffs (
                id TEXT PRIMARY KEY,
                house_id TEXT NOT NULL,
                amount TEXT NOT NULL,
                start_year INTEGER NOT NULL,
                start_month INTEGER NOT NULL,
                start_day INTEGER NOT NULL DEFAULT 1,
                start_precision TEXT NOT NULL DEFAULT 'month',
                end_year INTEGER NOT NULL,
                end_month INTEGER NOT NULL,
                end_day INTEGER NOT NULL DEFAULT 31,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (house_id)
                    REFERENCES houses(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_house_tariffs_period
                ON house_tariffs(
                    house_id, start_year, start_month, start_day,
                    end_year, end_month, end_day
                );

            CREATE TABLE IF NOT EXISTS work_templates (
                id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                source_file_name TEXT,
                source_file_path TEXT,
                source_house_guid TEXT,
                file_type TEXT,
                file_version TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (organization_id)
                    REFERENCES management_organizations(id)
                    ON DELETE CASCADE,
                UNIQUE (organization_id, name)
            );

            CREATE TABLE IF NOT EXISTS work_template_items (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                work_code TEXT,
                work_name TEXT NOT NULL,
                work_guid TEXT,
                unit_name TEXT,
                price TEXT,
                volume TEXT,
                quantity TEXT,
                cost TEXT,
                allocation_percent TEXT,
                FOREIGN KEY (template_id)
                    REFERENCES work_templates(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_template_items
                ON work_template_items(template_id, position);

            CREATE TABLE IF NOT EXISTS work_lists (
                id TEXT PRIMARY KEY,
                house_id TEXT NOT NULL,
                template_id TEXT,
                base_year INTEGER NOT NULL,
                start_year INTEGER NOT NULL,
                start_month INTEGER NOT NULL,
                end_year INTEGER NOT NULL,
                end_month INTEGER NOT NULL,
                planned_total TEXT,
                planned_tariff TEXT,
                planned_area TEXT,
                planned_months TEXT,
                status TEXT NOT NULL DEFAULT 'local',
                gis_work_list_guid TEXT,
                gis_file_guid TEXT,
                gis_import_job_guid TEXT,
                gis_status TEXT,
                gis_last_sync_at TEXT,
                gis_last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (house_id)
                    REFERENCES houses(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (template_id)
                    REFERENCES work_templates(id)
                    ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS ix_work_lists_house_year
                ON work_lists(house_id, base_year, start_year, start_month);

            CREATE TABLE IF NOT EXISTS work_list_items (
                id TEXT PRIMARY KEY,
                work_list_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                work_code TEXT,
                work_name TEXT NOT NULL,
                work_guid TEXT,
                unit_name TEXT,
                price TEXT,
                volume TEXT,
                quantity TEXT,
                cost TEXT,
                allocation_percent TEXT,
                FOREIGN KEY (work_list_id)
                    REFERENCES work_lists(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_work_list_items
                ON work_list_items(work_list_id, position);

            CREATE TABLE IF NOT EXISTS work_plans (
                id TEXT PRIMARY KEY,
                work_list_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                gis_work_plan_guid TEXT,
                gis_status TEXT,
                gis_last_sync_at TEXT,
                gis_last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (work_list_id)
                    REFERENCES work_lists(id)
                    ON DELETE CASCADE,
                UNIQUE (work_list_id, year)
            );

            CREATE INDEX IF NOT EXISTS ix_work_plans_list_year
                ON work_plans(work_list_id, year);

            CREATE TABLE IF NOT EXISTS work_plan_items (
                id TEXT PRIMARY KEY,
                work_plan_id TEXT NOT NULL,
                work_list_item_id TEXT,
                position INTEGER NOT NULL,
                work_code TEXT,
                work_name TEXT NOT NULL,
                work_guid TEXT,
                list_quantity TEXT,
                FOREIGN KEY (work_plan_id)
                    REFERENCES work_plans(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (work_list_item_id)
                    REFERENCES work_list_items(id)
                    ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS ix_work_plan_items
                ON work_plan_items(work_plan_id, position);

            CREATE TABLE IF NOT EXISTS work_plan_months (
                work_plan_item_id TEXT NOT NULL,
                month INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                dates_json TEXT,
                PRIMARY KEY (work_plan_item_id, month),
                FOREIGN KEY (work_plan_item_id)
                    REFERENCES work_plan_items(id)
                    ON DELETE CASCADE,
                CHECK (month BETWEEN 1 AND 12),
                CHECK (count >= 0)
            );

            CREATE TABLE IF NOT EXISTS work_fixation_periods (
                id TEXT PRIMARY KEY,
                work_list_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                reporting_period_guid TEXT,
                gis_status TEXT,
                gis_last_sync_at TEXT,
                gis_last_error TEXT,
                managed_unplanned_guids_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (work_list_id)
                    REFERENCES work_lists(id)
                    ON DELETE CASCADE,
                UNIQUE (work_list_id, year, month),
                CHECK (month BETWEEN 1 AND 12)
            );

            CREATE INDEX IF NOT EXISTS ix_work_fixation_periods_list
                ON work_fixation_periods(work_list_id, year, month);

            CREATE TABLE IF NOT EXISTS work_fixation_items (
                id TEXT PRIMARY KEY,
                fixation_period_id TEXT NOT NULL,
                work_list_item_id TEXT,
                work_plan_item_id TEXT,
                position INTEGER NOT NULL,
                work_code TEXT,
                work_name TEXT NOT NULL,
                work_guid TEXT,
                unit_name TEXT,
                planned_price TEXT,
                planned_amount TEXT,
                planned_count INTEGER NOT NULL DEFAULT 0,
                planned_total_cost TEXT,
                fact_price TEXT,
                fact_amount TEXT,
                fact_count INTEGER,
                fact_total_cost TEXT,
                gis_fixed_work_guid TEXT,
                managed_photo_content_guids_json TEXT,
                FOREIGN KEY (fixation_period_id)
                    REFERENCES work_fixation_periods(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (work_list_item_id)
                    REFERENCES work_list_items(id)
                    ON DELETE SET NULL,
                FOREIGN KEY (work_plan_item_id)
                    REFERENCES work_plan_items(id)
                    ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS ix_work_fixation_items
                ON work_fixation_items(fixation_period_id, position);

            CREATE TABLE IF NOT EXISTS work_fixation_unplanned_items (
                id TEXT PRIMARY KEY,
                fixation_period_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                work_code TEXT NOT NULL,
                work_name TEXT NOT NULL,
                work_guid TEXT NOT NULL,
                work_type_code TEXT,
                work_type_guid TEXT,
                work_type_name TEXT,
                unit_name TEXT,
                fact_price TEXT,
                fact_amount TEXT,
                fact_count INTEGER,
                fact_total_cost TEXT,
                comment TEXT,
                gis_fixed_work_guid TEXT,
                managed_photo_content_guids_json TEXT,
                FOREIGN KEY (fixation_period_id)
                    REFERENCES work_fixation_periods(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_work_fixation_unplanned
                ON work_fixation_unplanned_items(fixation_period_id, position);

            CREATE TABLE IF NOT EXISTS work_fixation_photos (
                id TEXT PRIMARY KEY,
                fixation_period_id TEXT NOT NULL,
                fixation_item_id TEXT,
                unplanned_item_id TEXT,
                file_name TEXT NOT NULL,
                description TEXT,
                content_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                content BLOB,
                remote_content_guid TEXT,
                remote_photo_guid TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (fixation_period_id)
                    REFERENCES work_fixation_periods(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (fixation_item_id)
                    REFERENCES work_fixation_items(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (unplanned_item_id)
                    REFERENCES work_fixation_unplanned_items(id)
                    ON DELETE CASCADE,
                CHECK (
                    (fixation_item_id IS NOT NULL AND unplanned_item_id IS NULL)
                    OR
                    (fixation_item_id IS NULL AND unplanned_item_id IS NOT NULL)
                )
            );

            CREATE INDEX IF NOT EXISTS ix_work_fixation_photos_period
                ON work_fixation_photos(fixation_period_id);

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._ensure_column("houses", "charge_area", "TEXT")
        self._ensure_column("house_tariffs", "start_day", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column(
            "house_tariffs",
            "start_precision",
            "TEXT NOT NULL DEFAULT 'month'",
        )
        self._ensure_column("house_tariffs", "end_day", "INTEGER NOT NULL DEFAULT 31")
        self._ensure_column("work_templates", "source_file_path", "TEXT")
        self._ensure_column("work_template_items", "allocation_percent", "TEXT")
        self._ensure_column("work_list_items", "unit_name", "TEXT")
        self._ensure_column("work_fixation_items", "unit_name", "TEXT")
        self._ensure_column("work_lists", "planned_total", "TEXT")
        self._ensure_column("work_lists", "planned_tariff", "TEXT")
        self._ensure_column("work_lists", "planned_area", "TEXT")
        self._ensure_column("work_lists", "planned_months", "TEXT")
        self._ensure_column("work_lists", "gis_work_list_guid", "TEXT")
        self._ensure_column("work_lists", "gis_file_guid", "TEXT")
        self._ensure_column("work_lists", "gis_import_job_guid", "TEXT")
        self._ensure_column("work_lists", "gis_status", "TEXT")
        self._ensure_column("work_lists", "gis_last_sync_at", "TEXT")
        self._ensure_column("work_lists", "gis_last_error", "TEXT")
        self._ensure_column("work_list_items", "allocation_percent", "TEXT")
        self._ensure_column("work_fixation_periods", "managed_unplanned_guids_json", "TEXT")
        self._ensure_column("work_fixation_items", "managed_photo_content_guids_json", "TEXT")
        self._ensure_column("work_fixation_unplanned_items", "work_type_name", "TEXT")
        self.connection.commit()
        self._normalize_all_house_tariff_periods()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    @staticmethod
    def _org_from_row(row: sqlite3.Row) -> ManagementOrganization:
        return ManagementOrganization(**dict(row))

    @staticmethod
    def _house_from_row(row: sqlite3.Row) -> House:
        data = dict(row)
        data["source"] = RecordSource(data["source"])
        return House(**data)

    def get_setting(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row and row["value"] is not None else None

    def set_setting(self, key: str, value: str | None) -> None:
        with self.connection:
            if value is None:
                self.connection.execute(
                    "DELETE FROM app_settings WHERE key = ?",
                    (key,),
                )
            else:
                self.connection.execute(
                    """
                    INSERT INTO app_settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, str(value)),
                )

    def list_organizations(self) -> list[ManagementOrganization]:
        rows = self.connection.execute(
            "SELECT * FROM management_organizations ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [self._org_from_row(row) for row in rows]

    def get_organization(self, organization_id: str) -> ManagementOrganization | None:
        row = self.connection.execute(
            "SELECT * FROM management_organizations WHERE id = ?", (organization_id,)
        ).fetchone()
        return self._org_from_row(row) if row else None

    def get_organization_by_ogrn(self, ogrn: str) -> ManagementOrganization | None:
        row = self.connection.execute(
            "SELECT * FROM management_organizations WHERE ogrn = ?", (ogrn.strip(),)
        ).fetchone()
        return self._org_from_row(row) if row else None

    def create_organization(
        self,
        *,
        name: str,
        ogrn: str,
        kpp: str | None = None,
        comment: str | None = None,
    ) -> ManagementOrganization:
        org_id = str(uuid.uuid4())
        timestamp = _now()
        self.connection.execute(
            """
            INSERT INTO management_organizations
                (id, name, ogrn, kpp, comment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (org_id, name.strip(), ogrn.strip(), _clean(kpp), _clean(comment), timestamp, timestamp),
        )
        self.connection.commit()
        result = self.get_organization(org_id)
        assert result is not None
        return result

    def update_organization(self, organization_id: str, **fields: object) -> None:
        allowed = {"name", "ogrn", "kpp", "comment"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        clause = ", ".join(f"{key} = ?" for key in updates)
        values = [updates[key] for key in updates]
        values.append(organization_id)
        self.connection.execute(
            f"UPDATE management_organizations SET {clause} WHERE id = ?", values
        )
        self.connection.commit()

    def upsert_imported_organization(
        self, *, name: str, ogrn: str, kpp: str | None = None
    ) -> ManagementOrganization:
        existing = self.get_organization_by_ogrn(ogrn)
        if existing:
            self.update_organization(existing.id, name=name.strip(), kpp=_clean(kpp))
            result = self.get_organization(existing.id)
            assert result is not None
            return result
        return self.create_organization(name=name, ogrn=ogrn, kpp=kpp)

    def delete_organization(self, organization_id: str) -> None:
        self.connection.execute(
            "DELETE FROM management_organizations WHERE id = ?", (organization_id,)
        )
        self.connection.commit()

    def list_houses(self, organization_id: str, search: str = "") -> list[House]:
        if search.strip():
            rows = self.connection.execute(
                """
                SELECT * FROM houses
                WHERE organization_id = ? AND address LIKE ?
                ORDER BY address COLLATE NOCASE
                """,
                (organization_id, f"%{search.strip()}%"),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM houses WHERE organization_id = ? ORDER BY address COLLATE NOCASE",
                (organization_id,),
            ).fetchall()
        return [self._house_from_row(row) for row in rows]

    def get_house(self, house_id: str) -> House | None:
        row = self.connection.execute("SELECT * FROM houses WHERE id = ?", (house_id,)).fetchone()
        return self._house_from_row(row) if row else None

    def create_house(
        self,
        *,
        organization_id: str,
        address: str,
        house_object_guid: str | None = None,
        gis_house_guid: str | None = None,
        fias_house_guid: str | None = None,
        gis_unique_house_number: str | None = None,
        cadastral_number: str | None = None,
        charge_area: str | None = None,
        residential_premises_count: int = 0,
        non_residential_premises_count: int = 0,
        source: RecordSource = RecordSource.MANUAL,
        comment: str | None = None,
    ) -> House:
        house_id = str(uuid.uuid4())
        timestamp = _now()
        self.connection.execute(
            """
            INSERT INTO houses (
                id, organization_id, address, house_object_guid, gis_house_guid,
                fias_house_guid, gis_unique_house_number, cadastral_number, charge_area,
                residential_premises_count, non_residential_premises_count,
                source, comment, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                house_id,
                organization_id,
                address.strip(),
                _clean(house_object_guid),
                _clean(gis_house_guid),
                _clean(fias_house_guid),
                _clean(gis_unique_house_number),
                _clean(cadastral_number),
                _clean(charge_area),
                max(0, int(residential_premises_count)),
                max(0, int(non_residential_premises_count)),
                str(source),
                _clean(comment),
                timestamp,
                timestamp,
            ),
        )
        self.connection.commit()
        result = self.get_house(house_id)
        assert result is not None
        return result

    def update_house(self, house_id: str, **fields: object) -> None:
        allowed = {
            "organization_id",
            "address",
            "house_object_guid",
            "gis_house_guid",
            "fias_house_guid",
            "gis_unique_house_number",
            "cadastral_number",
            "charge_area",
            "residential_premises_count",
            "non_residential_premises_count",
            "comment",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        clause = ", ".join(f"{key} = ?" for key in updates)
        values = [updates[key] for key in updates]
        values.append(house_id)
        self.connection.execute(f"UPDATE houses SET {clause} WHERE id = ?", values)
        self.connection.commit()

    def _find_imported_house(
        self,
        organization_id: str,
        house_object_guid: str | None,
        gis_house_guid: str | None,
        gis_unique_house_number: str | None,
    ) -> House | None:
        probes: Iterable[tuple[str, str | None]] = (
            ("house_object_guid", house_object_guid),
            ("gis_house_guid", gis_house_guid),
            ("gis_unique_house_number", gis_unique_house_number),
        )
        for field, value in probes:
            if not value:
                continue
            row = self.connection.execute(
                f"SELECT * FROM houses WHERE organization_id = ? AND {field} = ? LIMIT 1",
                (organization_id, value),
            ).fetchone()
            if row:
                return self._house_from_row(row)
        return None

    def upsert_imported_house(self, organization_id: str, house: object) -> tuple[House, bool]:
        existing = self._find_imported_house(
            organization_id,
            getattr(house, "house_object_guid", None),
            getattr(house, "gis_house_guid", None),
            getattr(house, "gis_unique_house_number", None),
        )
        payload = dict(
            address=getattr(house, "address"),
            house_object_guid=getattr(house, "house_object_guid", None),
            gis_house_guid=getattr(house, "gis_house_guid", None),
            fias_house_guid=getattr(house, "fias_house_guid", None),
            gis_unique_house_number=getattr(house, "gis_unique_house_number", None),
            cadastral_number=getattr(house, "cadastral_number", None),
            residential_premises_count=getattr(house, "residential_premises_count", 0),
            non_residential_premises_count=getattr(house, "non_residential_premises_count", 0),
        )
        if existing:
            self.update_house(existing.id, **payload)
            refreshed = self.get_house(existing.id)
            assert refreshed is not None
            return refreshed, False
        created = self.create_house(
            organization_id=organization_id,
            source=RecordSource.IMPORT,
            **payload,
        )
        return created, True

    def delete_house(self, house_id: str) -> None:
        self.connection.execute("DELETE FROM houses WHERE id = ?", (house_id,))
        self.connection.commit()


    @staticmethod
    def _house_tariff_from_row(row: sqlite3.Row) -> HouseTariff:
        return HouseTariff(**dict(row))

    def list_house_tariffs(self, house_id: str) -> list[HouseTariff]:
        rows = self.connection.execute(
            """
            SELECT * FROM house_tariffs
            WHERE house_id = ?
            ORDER BY start_year, start_month, start_day, end_year, end_month, end_day, created_at
            """,
            (house_id,),
        ).fetchall()
        return [self._house_tariff_from_row(row) for row in rows]

    def get_house_tariff(self, tariff_id: str) -> HouseTariff | None:
        row = self.connection.execute(
            "SELECT * FROM house_tariffs WHERE id = ?",
            (tariff_id,),
        ).fetchone()
        return self._house_tariff_from_row(row) if row else None

    def create_house_tariff(
        self,
        *,
        house_id: str,
        amount: str,
        start_year: int,
        start_month: int,
        start_day: int = 1,
        start_precision: str = "month",
        end_year: int | None = None,
        end_month: int | None = None,
        end_day: int | None = None,
    ) -> HouseTariff:
        del end_year, end_month, end_day
        _validate_date_point(start_year, start_month, start_day)
        precision = _validate_date_precision(start_precision)
        self._assert_unique_tariff_start(
            house_id,
            start_year=start_year,
            start_month=start_month,
            start_day=start_day,
        )

        tariff_id = str(uuid.uuid4())
        timestamp = _now()
        self.connection.execute(
            """
            INSERT INTO house_tariffs (
                id, house_id, amount,
                start_year, start_month, start_day, start_precision,
                end_year, end_month, end_day,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tariff_id,
                house_id,
                _required_text(amount, "Тариф"),
                int(start_year),
                int(start_month),
                int(start_day),
                precision,
                9999,
                12,
                31,
                timestamp,
                timestamp,
            ),
        )
        self.connection.commit()
        self._recalculate_house_tariff_periods(house_id)
        result = self.get_house_tariff(tariff_id)
        assert result is not None
        return result

    def update_house_tariff(
        self,
        tariff_id: str,
        *,
        amount: str,
        start_year: int,
        start_month: int,
        start_day: int = 1,
        start_precision: str = "month",
        end_year: int | None = None,
        end_month: int | None = None,
        end_day: int | None = None,
    ) -> None:
        del end_year, end_month, end_day
        _validate_date_point(start_year, start_month, start_day)
        precision = _validate_date_precision(start_precision)

        existing = self.get_house_tariff(tariff_id)
        if existing is None:
            raise ValueError("Тариф не найден.")

        self._assert_unique_tariff_start(
            existing.house_id,
            start_year=start_year,
            start_month=start_month,
            start_day=start_day,
            exclude_id=tariff_id,
        )

        self.connection.execute(
            """
            UPDATE house_tariffs
            SET amount = ?,
                start_year = ?,
                start_month = ?,
                start_day = ?,
                start_precision = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                _required_text(amount, "Тариф"),
                int(start_year),
                int(start_month),
                int(start_day),
                precision,
                _now(),
                tariff_id,
            ),
        )
        self.connection.commit()
        self._recalculate_house_tariff_periods(existing.house_id)

    def upsert_house_tariff_change(
        self,
        *,
        house_id: str,
        amount: str,
        start_year: int,
        start_month: int,
        start_day: int = 1,
        start_precision: str = "month",
    ) -> tuple[HouseTariff, bool, bool]:
        """Create or update one tariff change point.

        Returns (tariff, created, changed). Existing equal values are left intact.
        """

        _validate_date_point(start_year, start_month, start_day)
        precision = _validate_date_precision(start_precision)
        existing = next(
            (
                item
                for item in self.list_house_tariffs(house_id)
                if (
                    item.start_year,
                    item.start_month,
                    item.start_day,
                )
                == (int(start_year), int(start_month), int(start_day))
            ),
            None,
        )
        cleaned_amount = _required_text(amount, "Тариф")
        if existing is None:
            created = self.create_house_tariff(
                house_id=house_id,
                amount=cleaned_amount,
                start_year=start_year,
                start_month=start_month,
                start_day=start_day,
                start_precision=precision,
            )
            return created, True, True

        changed = (
            existing.amount != cleaned_amount
            or existing.start_precision != precision
        )
        if changed:
            self.update_house_tariff(
                existing.id,
                amount=cleaned_amount,
                start_year=start_year,
                start_month=start_month,
                start_day=start_day,
                start_precision=precision,
            )
            refreshed = self.get_house_tariff(existing.id)
            assert refreshed is not None
            return refreshed, False, True
        return existing, False, False

    def delete_house_tariff(self, tariff_id: str) -> None:
        existing = self.get_house_tariff(tariff_id)
        self.connection.execute(
            "DELETE FROM house_tariffs WHERE id = ?",
            (tariff_id,),
        )
        self.connection.commit()
        if existing is not None:
            self._recalculate_house_tariff_periods(existing.house_id)

    def find_house_tariff_at_date(
        self,
        house_id: str,
        target_date: date,
    ) -> HouseTariff | None:
        """Return the tariff effective on one exact calendar date."""

        candidates = self.list_house_tariffs(house_id)
        active = [
            item
            for item in candidates
            if item.effective_from_date <= target_date
            and (
                item.effective_to_date is None
                or target_date <= item.effective_to_date
            )
        ]
        return active[-1] if active else None

    def find_house_tariff_covering_period(
        self,
        house_id: str,
        *,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
    ) -> HouseTariff | None:
        _validate_period(start_year, start_month, end_year, end_month)
        period_start = date(int(start_year), int(start_month), 1)
        period_end = date(
            int(end_year),
            int(end_month),
            _last_day_of_month(end_year, end_month),
        )

        candidates = self.list_house_tariffs(house_id)
        active = [
            item
            for item in candidates
            if item.effective_from_date <= period_start
        ]
        if not active:
            return None

        current = active[-1]
        later_changes = [
            item
            for item in candidates
            if period_start < item.effective_from_date <= period_end
        ]
        if any(
            not _numeric_text_equal(item.amount, current.amount)
            for item in later_changes
        ):
            return None
        return current

    def _assert_unique_tariff_start(
        self,
        house_id: str,
        *,
        start_year: int,
        start_month: int,
        start_day: int = 1,
        exclude_id: str | None = None,
    ) -> None:
        rows = self.list_house_tariffs(house_id)
        for item in rows:
            if exclude_id and item.id == exclude_id:
                continue
            if (
                item.start_year,
                item.start_month,
                item.start_day,
            ) == (int(start_year), int(start_month), int(start_day)):
                raise ValueError(
                    "Для этого дома уже указан тариф, начинающий действовать "
                    f"с {int(start_day):02d}.{int(start_month):02d}.{int(start_year)}."
                )

    def _normalize_all_house_tariff_periods(self) -> None:
        rows = self.connection.execute(
            "SELECT DISTINCT house_id FROM house_tariffs"
        ).fetchall()
        for row in rows:
            self._recalculate_house_tariff_periods(str(row["house_id"]))

    def _recalculate_house_tariff_periods(self, house_id: str) -> None:
        rows = self.connection.execute(
            """
            SELECT id, start_year, start_month, start_day
            FROM house_tariffs
            WHERE house_id = ?
            ORDER BY start_year, start_month, start_day, created_at
            """,
            (house_id,),
        ).fetchall()
        if not rows:
            return

        with self.connection:
            for index, row in enumerate(rows):
                if index + 1 < len(rows):
                    next_row = rows[index + 1]
                    next_start = date(
                        int(next_row["start_year"]),
                        int(next_row["start_month"]),
                        int(next_row["start_day"]),
                    )
                    previous_day = next_start - timedelta(days=1)
                    end_year = previous_day.year
                    end_month = previous_day.month
                    end_day = previous_day.day
                else:
                    end_year, end_month, end_day = 9999, 12, 31

                self.connection.execute(
                    """
                    UPDATE house_tariffs
                    SET end_year = ?, end_month = ?, end_day = ?
                    WHERE id = ?
                    """,
                    (end_year, end_month, end_day, str(row["id"])),
                )

    @staticmethod
    def _template_from_row(row: sqlite3.Row) -> WorkTemplate:
        data = dict(row)
        data["source"] = TemplateSource(data["source"])
        return WorkTemplate(**data)

    @staticmethod
    def _template_item_from_row(row: sqlite3.Row) -> WorkTemplateItem:
        return WorkTemplateItem(**dict(row))

    @staticmethod
    def _work_list_from_row(row: sqlite3.Row) -> WorkList:
        data = dict(row)
        data["status"] = WorkListStatus(data["status"])
        return WorkList(**data)

    @staticmethod
    def _work_list_item_from_row(row: sqlite3.Row) -> WorkListItem:
        return WorkListItem(**dict(row))

    @staticmethod
    def _work_plan_from_row(row: sqlite3.Row) -> WorkPlan:
        return WorkPlan(**dict(row))

    @staticmethod
    def _work_plan_item_from_row(row: sqlite3.Row) -> WorkPlanItem:
        return WorkPlanItem(**dict(row))

    @staticmethod
    def _work_plan_month_from_row(row: sqlite3.Row) -> WorkPlanMonth:
        return WorkPlanMonth(**dict(row))

    @staticmethod
    def _work_fixation_period_from_row(row: sqlite3.Row) -> WorkFixationPeriod:
        return WorkFixationPeriod(**dict(row))

    @staticmethod
    def _work_fixation_item_from_row(row: sqlite3.Row) -> WorkFixationItem:
        return WorkFixationItem(**dict(row))

    @staticmethod
    def _work_fixation_unplanned_from_row(row: sqlite3.Row) -> WorkFixationUnplannedItem:
        return WorkFixationUnplannedItem(**dict(row))

    @staticmethod
    def _work_fixation_photo_from_row(row: sqlite3.Row) -> WorkFixationPhoto:
        return WorkFixationPhoto(**dict(row))

    def list_work_templates(self, organization_id: str) -> list[WorkTemplate]:
        rows = self.connection.execute(
            """
            SELECT * FROM work_templates
            WHERE organization_id = ?
            ORDER BY name COLLATE NOCASE
            """,
            (organization_id,),
        ).fetchall()
        return [self._template_from_row(row) for row in rows]

    def get_work_template(self, template_id: str) -> WorkTemplate | None:
        row = self.connection.execute(
            "SELECT * FROM work_templates WHERE id = ?", (template_id,)
        ).fetchone()
        return self._template_from_row(row) if row else None

    def list_work_template_items(self, template_id: str) -> list[WorkTemplateItem]:
        rows = self.connection.execute(
            """
            SELECT * FROM work_template_items
            WHERE template_id = ?
            ORDER BY position
            """,
            (template_id,),
        ).fetchall()
        return [self._template_item_from_row(row) for row in rows]

    def save_work_template_allocation_percentages(
        self,
        template_id: str,
        percentages: dict[int, str | None],
    ) -> None:
        """Save one reusable percentage distribution for a work template."""

        template_items = self.list_work_template_items(template_id)
        if not template_items:
            raise ValueError("В шаблоне нет работ и услуг.")

        expected_positions = {int(item.position) for item in template_items}
        supplied_positions = {int(position) for position in percentages}
        if expected_positions != supplied_positions:
            raise ValueError(
                "Состав перечня отличается от выбранного шаблона. "
                "Сохранить проценты для этого шаблона нельзя."
            )

        with self.connection:
            for item in template_items:
                self.connection.execute(
                    """
                    UPDATE work_template_items
                    SET allocation_percent = ?
                    WHERE id = ? AND template_id = ?
                    """,
                    (
                        _clean(percentages.get(int(item.position))),
                        item.id,
                        template_id,
                    ),
                )
            self.connection.execute(
                "UPDATE work_templates SET updated_at = ? WHERE id = ?",
                (_now(), template_id),
            )

    def create_work_template(
        self,
        *,
        organization_id: str,
        name: str,
        items: Iterable[object] = (),
        source: TemplateSource = TemplateSource.MANUAL,
        source_file_name: str | None = None,
        source_file_path: str | None = None,
        source_house_guid: str | None = None,
        file_type: str | None = None,
        file_version: str | None = None,
    ) -> WorkTemplate:
        template_id = str(uuid.uuid4())
        timestamp = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO work_templates (
                    id, organization_id, name, source, source_file_name, source_file_path,
                    source_house_guid, file_type, file_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    organization_id,
                    name.strip(),
                    str(source),
                    _clean(source_file_name),
                    _clean(source_file_path),
                    _clean(source_house_guid),
                    _clean(file_type),
                    _clean(file_version),
                    timestamp,
                    timestamp,
                ),
            )
            for position, item in enumerate(items, start=1):
                self.connection.execute(
                    """
                    INSERT INTO work_template_items (
                        id, template_id, position, work_code, work_name, work_guid,
                        price, volume, quantity, cost, allocation_percent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        template_id,
                        int(getattr(item, "position", position) or position),
                        _clean(getattr(item, "work_code", None)),
                        str(getattr(item, "work_name", "")).strip(),
                        _clean(getattr(item, "work_guid", None)),
                        _clean(getattr(item, "price", None)),
                        _clean(getattr(item, "volume", None)),
                        _clean(getattr(item, "quantity", None)),
                        _clean(getattr(item, "cost", None)),
                        _clean(getattr(item, "allocation_percent", None)),
                    ),
                )
        result = self.get_work_template(template_id)
        assert result is not None
        return result

    def set_work_template_source_path(self, template_id: str, source_file_path: str) -> None:
        self.connection.execute(
            """
            UPDATE work_templates
            SET source_file_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (source_file_path, _now(), template_id),
        )
        self.connection.commit()

    def rename_work_template(self, template_id: str, name: str) -> None:
        self.connection.execute(
            "UPDATE work_templates SET name = ?, updated_at = ? WHERE id = ?",
            (name.strip(), _now(), template_id),
        )
        self.connection.commit()

    def delete_work_template(self, template_id: str) -> None:
        self.connection.execute("DELETE FROM work_templates WHERE id = ?", (template_id,))
        self.connection.commit()

    def list_work_lists(self, house_id: str, base_year: int | None = None) -> list[WorkList]:
        if base_year is None:
            rows = self.connection.execute(
                """
                SELECT * FROM work_lists
                WHERE house_id = ?
                ORDER BY start_year, start_month, end_year, end_month, created_at
                """,
                (house_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM work_lists
                WHERE house_id = ? AND base_year = ?
                ORDER BY start_year, start_month, end_year, end_month, created_at
                """,
                (house_id, int(base_year)),
            ).fetchall()
        return [self._work_list_from_row(row) for row in rows]

    def get_work_list(self, work_list_id: str) -> WorkList | None:
        row = self.connection.execute(
            "SELECT * FROM work_lists WHERE id = ?", (work_list_id,)
        ).fetchone()
        return self._work_list_from_row(row) if row else None

    def list_work_list_items(self, work_list_id: str) -> list[WorkListItem]:
        rows = self.connection.execute(
            """
            SELECT * FROM work_list_items
            WHERE work_list_id = ?
            ORDER BY position
            """,
            (work_list_id,),
        ).fetchall()
        return [self._work_list_item_from_row(row) for row in rows]

    def create_work_list(
        self,
        *,
        house_id: str,
        base_year: int,
        template_id: str | None = None,
    ) -> WorkList:
        work_list_id = str(uuid.uuid4())
        timestamp = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO work_lists (
                    id, house_id, template_id, base_year,
                    start_year, start_month, end_year, end_month,
                    planned_total, planned_tariff, planned_area, planned_months,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, 12, NULL, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    work_list_id,
                    house_id,
                    template_id,
                    int(base_year),
                    int(base_year),
                    int(base_year),
                    str(WorkListStatus.LOCAL),
                    timestamp,
                    timestamp,
                ),
            )
            if template_id:
                rows = self.connection.execute(
                    """
                    SELECT * FROM work_template_items
                    WHERE template_id = ?
                    ORDER BY position
                    """,
                    (template_id,),
                ).fetchall()
                for row in rows:
                    self.connection.execute(
                        """
                        INSERT INTO work_list_items (
                            id, work_list_id, position, work_code, work_name, work_guid,
                            unit_name, price, volume, quantity, cost, allocation_percent
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            str(uuid.uuid4()),
                            work_list_id,
                            row["position"],
                            row["work_code"],
                            row["work_name"],
                            row["work_guid"],
                            None,
                            row["price"],
                            row["volume"],
                            row["quantity"],
                            row["cost"],
                        ),
                    )
        result = self.get_work_list(work_list_id)
        assert result is not None
        return result

    def clone_work_list(self, source_id: str, target_base_year: int) -> WorkList:
        source = self.get_work_list(source_id)
        if source is None:
            raise ValueError("Исходный перечень не найден.")

        target_base_year = int(target_base_year)
        year_shift = target_base_year - source.base_year
        work_list_id = str(uuid.uuid4())
        timestamp = _now()

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO work_lists (
                    id, house_id, template_id, base_year,
                    start_year, start_month, end_year, end_month,
                    planned_total, planned_tariff, planned_area, planned_months,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    work_list_id,
                    source.house_id,
                    source.template_id,
                    target_base_year,
                    source.start_year + year_shift,
                    source.start_month,
                    source.end_year + year_shift,
                    source.end_month,
                    source.planned_total,
                    source.planned_tariff,
                    source.planned_area,
                    source.planned_months,
                    str(WorkListStatus.LOCAL),
                    timestamp,
                    timestamp,
                ),
            )
            rows = self.connection.execute(
                """
                SELECT * FROM work_list_items
                WHERE work_list_id = ?
                ORDER BY position
                """,
                (source_id,),
            ).fetchall()
            for row in rows:
                self.connection.execute(
                    """
                    INSERT INTO work_list_items (
                        id, work_list_id, position, work_code, work_name, work_guid,
                        unit_name, price, volume, quantity, cost, allocation_percent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        work_list_id,
                        row["position"],
                        row["work_code"],
                        row["work_name"],
                        row["work_guid"],
                        row["unit_name"],
                        row["price"],
                        row["volume"],
                        row["quantity"],
                        row["cost"],
                        row["allocation_percent"],
                    ),
                )

        result = self.get_work_list(work_list_id)
        assert result is not None
        return result

    def list_placed_work_lists(self, house_id: str) -> list[WorkList]:
        rows = self.connection.execute(
            """
            SELECT * FROM work_lists
            WHERE house_id = ?
              AND (status = ? OR UPPER(COALESCE(gis_status, '')) = 'APPROVED')
              AND gis_work_list_guid IS NOT NULL
              AND gis_work_list_guid <> ''
            ORDER BY start_year, start_month, end_year, end_month, created_at
            """,
            (house_id, str(WorkListStatus.PLACED)),
        ).fetchall()
        return [self._work_list_from_row(row) for row in rows]

    def get_work_plan(self, work_plan_id: str) -> WorkPlan | None:
        row = self.connection.execute(
            "SELECT * FROM work_plans WHERE id = ?",
            (work_plan_id,),
        ).fetchone()
        return self._work_plan_from_row(row) if row else None

    def get_work_plan_for_list_year(
        self,
        work_list_id: str,
        year: int,
    ) -> WorkPlan | None:
        row = self.connection.execute(
            """
            SELECT * FROM work_plans
            WHERE work_list_id = ? AND year = ?
            """,
            (work_list_id, int(year)),
        ).fetchone()
        return self._work_plan_from_row(row) if row else None

    def get_or_create_work_plan(
        self,
        *,
        work_list_id: str,
        year: int,
    ) -> WorkPlan:
        work_list = self.get_work_list(work_list_id)
        if work_list is None:
            raise ValueError("Перечень работ и услуг не найден.")
        if (
            work_list.status != WorkListStatus.PLACED
            and str(work_list.gis_status or "").upper() != "APPROVED"
        ):
            raise ValueError(
                "Планирование доступно только после размещения перечня работ и услуг."
            )
        if not work_list.gis_work_list_guid:
            raise ValueError(
                "У размещенного перечня не сохранен GUID ГИС ЖКХ. "
                "Сначала проверьте статус перечня в ГИС."
            )

        year = int(year)
        if year < work_list.start_year or year > work_list.end_year:
            raise ValueError("Выбранный год не входит в период перечня.")

        existing = self.get_work_plan_for_list_year(work_list_id, year)
        if existing is not None:
            return existing

        plan_id = str(uuid.uuid4())
        timestamp = _now()
        items = self.list_work_list_items(work_list_id)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO work_plans (
                    id, work_list_id, year,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (plan_id, work_list_id, year, timestamp, timestamp),
            )
            for item in items:
                self.connection.execute(
                    """
                    INSERT INTO work_plan_items (
                        id, work_plan_id, work_list_item_id, position,
                        work_code, work_name, work_guid, list_quantity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        plan_id,
                        item.id,
                        item.position,
                        _clean(item.work_code),
                        item.work_name,
                        _clean(item.work_guid),
                        _clean(item.quantity),
                    ),
                )

        result = self.get_work_plan(plan_id)
        assert result is not None
        return result

    def list_work_plan_items(self, work_plan_id: str) -> list[WorkPlanItem]:
        rows = self.connection.execute(
            """
            SELECT * FROM work_plan_items
            WHERE work_plan_id = ?
            ORDER BY position
            """,
            (work_plan_id,),
        ).fetchall()
        return [self._work_plan_item_from_row(row) for row in rows]

    def list_work_plan_months(self, work_plan_id: str) -> list[WorkPlanMonth]:
        rows = self.connection.execute(
            """
            SELECT m.*
            FROM work_plan_months m
            JOIN work_plan_items i ON i.id = m.work_plan_item_id
            WHERE i.work_plan_id = ?
            ORDER BY i.position, m.month
            """,
            (work_plan_id,),
        ).fetchall()
        return [self._work_plan_month_from_row(row) for row in rows]

    def get_work_plan_month(
        self,
        work_plan_item_id: str,
        month: int,
    ) -> WorkPlanMonth | None:
        row = self.connection.execute(
            """
            SELECT * FROM work_plan_months
            WHERE work_plan_item_id = ? AND month = ?
            """,
            (work_plan_item_id, int(month)),
        ).fetchone()
        return self._work_plan_month_from_row(row) if row else None

    def update_work_plan_month(
        self,
        work_plan_item_id: str,
        *,
        month: int,
        count: int,
        dates: Iterable[str] = (),
    ) -> None:
        month = int(month)
        count = int(count)
        if not 1 <= month <= 12:
            raise ValueError("Месяц должен быть от 1 до 12.")
        if count < 0:
            raise ValueError("Количество в плане не может быть отрицательным.")

        item_row = self.connection.execute(
            """
            SELECT i.id, p.year
            FROM work_plan_items i
            JOIN work_plans p ON p.id = i.work_plan_id
            WHERE i.id = ?
            """,
            (work_plan_item_id,),
        ).fetchone()
        if item_row is None:
            raise ValueError("Строка плана не найдена.")

        year = int(item_row["year"])
        normalized_dates: list[str] = []
        seen_dates: set[str] = set()
        for raw in dates:
            text = str(raw or "").strip()
            if not text:
                continue
            try:
                parsed = date.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(f"Некорректная дата плана: {text}.") from exc
            if parsed.year != year or parsed.month != month:
                raise ValueError(
                    "Дата должна относиться к выбранному месяцу и году плана."
                )
            iso = parsed.isoformat()
            if iso not in seen_dates:
                seen_dates.add(iso)
                normalized_dates.append(iso)
        normalized_dates.sort()

        if len(normalized_dates) > count:
            raise ValueError(
                "Количество в месяце не может быть меньше числа выбранных дат."
            )

        plan_row = self.connection.execute(
            """
            SELECT p.id
            FROM work_plans p
            JOIN work_plan_items i ON i.work_plan_id = p.id
            WHERE i.id = ?
            """,
            (work_plan_item_id,),
        ).fetchone()
        assert plan_row is not None

        with self.connection:
            if count == 0 and not normalized_dates:
                self.connection.execute(
                    """
                    DELETE FROM work_plan_months
                    WHERE work_plan_item_id = ? AND month = ?
                    """,
                    (work_plan_item_id, month),
                )
            else:
                self.connection.execute(
                    """
                    INSERT INTO work_plan_months (
                        work_plan_item_id, month, count, dates_json
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(work_plan_item_id, month)
                    DO UPDATE SET
                        count = excluded.count,
                        dates_json = excluded.dates_json
                    """,
                    (
                        work_plan_item_id,
                        month,
                        count,
                        json.dumps(normalized_dates, ensure_ascii=False)
                        if normalized_dates
                        else None,
                    ),
                )
            self.connection.execute(
                "UPDATE work_plans SET updated_at = ? WHERE id = ?",
                (_now(), str(plan_row["id"])),
            )

    def apply_work_plan_distribution(
        self,
        work_plan_id: str,
        rows: Iterable[tuple[str, int, int]],
    ) -> None:
        valid_items = {
            row["id"]
            for row in self.connection.execute(
                "SELECT id FROM work_plan_items WHERE work_plan_id = ?",
                (work_plan_id,),
            ).fetchall()
        }
        timestamp = _now()
        with self.connection:
            for item_id, month, count in rows:
                if item_id not in valid_items:
                    raise ValueError("Строка не относится к выбранному плану.")
                month = int(month)
                count = int(count)
                if not 1 <= month <= 12 or count < 0:
                    raise ValueError("Некорректное количество или месяц плана.")
                if count == 0:
                    self.connection.execute(
                        """
                        DELETE FROM work_plan_months
                        WHERE work_plan_item_id = ? AND month = ?
                        """,
                        (item_id, month),
                    )
                else:
                    self.connection.execute(
                        """
                        INSERT INTO work_plan_months (
                            work_plan_item_id, month, count, dates_json
                        ) VALUES (?, ?, ?, NULL)
                        ON CONFLICT(work_plan_item_id, month)
                        DO UPDATE SET
                            count = excluded.count,
                            dates_json = NULL
                        """,
                        (item_id, month, count),
                    )
            self.connection.execute(
                "UPDATE work_plans SET updated_at = ? WHERE id = ?",
                (timestamp, work_plan_id),
            )

    def update_work_plan_gis_state(
        self,
        work_plan_id: str,
        *,
        gis_work_plan_guid: str | None = None,
        gis_status: str | None = None,
        gis_last_error: str | None = None,
        sync_time: str | None = None,
    ) -> None:
        updates: dict[str, object] = {
            "gis_last_sync_at": sync_time or _now(),
            "gis_last_error": _clean(gis_last_error),
            "updated_at": _now(),
        }
        if gis_work_plan_guid is not None:
            updates["gis_work_plan_guid"] = _clean(gis_work_plan_guid)
        if gis_status is not None:
            updates["gis_status"] = _clean(gis_status)

        clause = ", ".join(f"{key} = ?" for key in updates)
        values = [updates[key] for key in updates]
        values.append(work_plan_id)
        self.connection.execute(
            f"UPDATE work_plans SET {clause} WHERE id = ?",
            values,
        )
        self.connection.commit()

    def get_work_fixation_period(
        self,
        fixation_period_id: str,
    ) -> WorkFixationPeriod | None:
        row = self.connection.execute(
            "SELECT * FROM work_fixation_periods WHERE id = ?",
            (fixation_period_id,),
        ).fetchone()
        return self._work_fixation_period_from_row(row) if row else None

    def get_work_fixation_period_for_month(
        self,
        work_list_id: str,
        *,
        year: int,
        month: int,
    ) -> WorkFixationPeriod | None:
        row = self.connection.execute(
            """
            SELECT * FROM work_fixation_periods
            WHERE work_list_id = ? AND year = ? AND month = ?
            """,
            (work_list_id, int(year), int(month)),
        ).fetchone()
        return self._work_fixation_period_from_row(row) if row else None

    def list_work_fixation_periods(
        self,
        work_list_id: str,
        *,
        year: int | None = None,
    ) -> list[WorkFixationPeriod]:
        if year is None:
            rows = self.connection.execute(
                """
                SELECT * FROM work_fixation_periods
                WHERE work_list_id = ?
                ORDER BY year, month
                """,
                (work_list_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM work_fixation_periods
                WHERE work_list_id = ? AND year = ?
                ORDER BY month
                """,
                (work_list_id, int(year)),
            ).fetchall()
        return [self._work_fixation_period_from_row(row) for row in rows]

    def get_or_create_work_fixation_period(
        self,
        *,
        work_list_id: str,
        year: int,
        month: int,
    ) -> WorkFixationPeriod:
        work_list = self.get_work_list(work_list_id)
        if work_list is None:
            raise ValueError("Перечень работ и услуг не найден.")
        if (
            work_list.status != WorkListStatus.PLACED
            and str(work_list.gis_status or "").upper() != "APPROVED"
        ):
            raise ValueError(
                "Фиксация доступна только для размещенного перечня работ и услуг."
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

        existing = self.get_work_fixation_period_for_month(
            work_list_id,
            year=year,
            month=month,
        )
        if existing is not None:
            return existing

        fixation_id = str(uuid.uuid4())
        timestamp = _now()
        list_items = self.list_work_list_items(work_list_id)
        plan = self.get_work_plan_for_list_year(work_list_id, year)
        plan_items = self.list_work_plan_items(plan.id) if plan else []
        plan_item_by_list_item = {
            item.work_list_item_id: item
            for item in plan_items
            if item.work_list_item_id
        }
        plan_months = (
            {
                row.work_plan_item_id: row
                for row in self.list_work_plan_months(plan.id)
                if row.month == month
            }
            if plan
            else {}
        )

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO work_fixation_periods (
                    id, work_list_id, year, month,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fixation_id,
                    work_list_id,
                    year,
                    month,
                    timestamp,
                    timestamp,
                ),
            )

            for list_item in list_items:
                plan_item = plan_item_by_list_item.get(list_item.id)
                plan_month = (
                    plan_months.get(plan_item.id)
                    if plan_item is not None
                    else None
                )
                planned_count = int(plan_month.count) if plan_month else 0
                planned_cost = proportional_month_cost(
                    list_item.cost,
                    list_item.quantity,
                    planned_count,
                )
                self.connection.execute(
                    """
                    INSERT INTO work_fixation_items (
                        id, fixation_period_id,
                        work_list_item_id, work_plan_item_id,
                        position, work_code, work_name, work_guid, unit_name,
                        planned_price, planned_amount, planned_count,
                        planned_total_cost
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        fixation_id,
                        list_item.id,
                        plan_item.id if plan_item else None,
                        int(list_item.position),
                        _clean(list_item.work_code),
                        list_item.work_name,
                        _clean(list_item.work_guid),
                        _clean(list_item.unit_name),
                        _clean(list_item.price),
                        _clean(list_item.volume),
                        planned_count,
                        _clean(planned_cost),
                    ),
                )

        result = self.get_work_fixation_period(fixation_id)
        assert result is not None
        return result

    def refresh_work_fixation_plan_snapshot(
        self,
        fixation_period_id: str,
    ) -> None:
        period = self.get_work_fixation_period(fixation_period_id)
        if period is None:
            raise ValueError("Период фиксации не найден.")

        work_list_items = {
            item.id: item
            for item in self.list_work_list_items(period.work_list_id)
        }
        plan = self.get_work_plan_for_list_year(period.work_list_id, period.year)
        plan_items = self.list_work_plan_items(plan.id) if plan else []
        plan_by_list_item = {
            item.work_list_item_id: item
            for item in plan_items
            if item.work_list_item_id
        }
        plan_months = (
            {
                row.work_plan_item_id: row
                for row in self.list_work_plan_months(plan.id)
                if row.month == period.month
            }
            if plan
            else {}
        )

        rows = self.list_work_fixation_items(fixation_period_id)
        with self.connection:
            for item in rows:
                list_item = (
                    work_list_items.get(item.work_list_item_id)
                    if item.work_list_item_id
                    else None
                )
                if list_item is None:
                    continue
                plan_item = plan_by_list_item.get(list_item.id)
                plan_month = (
                    plan_months.get(plan_item.id)
                    if plan_item is not None
                    else None
                )
                planned_count = int(plan_month.count) if plan_month else 0
                planned_cost = proportional_month_cost(
                    list_item.cost,
                    list_item.quantity,
                    planned_count,
                )
                self.connection.execute(
                    """
                    UPDATE work_fixation_items
                    SET work_plan_item_id = ?,
                        unit_name = ?,
                        planned_price = ?,
                        planned_amount = ?,
                        planned_count = ?,
                        planned_total_cost = ?
                    WHERE id = ?
                    """,
                    (
                        plan_item.id if plan_item else None,
                        _clean(list_item.unit_name),
                        _clean(list_item.price),
                        _clean(list_item.volume),
                        planned_count,
                        _clean(planned_cost),
                        item.id,
                    ),
                )

    def list_work_fixation_items(
        self,
        fixation_period_id: str,
    ) -> list[WorkFixationItem]:
        rows = self.connection.execute(
            """
            SELECT * FROM work_fixation_items
            WHERE fixation_period_id = ?
            ORDER BY position
            """,
            (fixation_period_id,),
        ).fetchall()
        return [self._work_fixation_item_from_row(row) for row in rows]

    def list_work_fixation_unplanned_items(
        self,
        fixation_period_id: str,
    ) -> list[WorkFixationUnplannedItem]:
        rows = self.connection.execute(
            """
            SELECT * FROM work_fixation_unplanned_items
            WHERE fixation_period_id = ?
            ORDER BY position, id
            """,
            (fixation_period_id,),
        ).fetchall()
        return [self._work_fixation_unplanned_from_row(row) for row in rows]

    def add_work_fixation_unplanned_item(
        self,
        fixation_period_id: str,
        *,
        work_code: str,
        work_name: str,
        work_guid: str,
        work_type_code: str | None = None,
        work_type_guid: str | None = None,
        work_type_name: str | None = None,
        unit_name: str | None = None,
        fact_price: str | None = None,
        fact_amount: str | None = None,
        fact_count: int | None = None,
        fact_total_cost: str | None = None,
        comment: str | None = None,
    ) -> WorkFixationUnplannedItem:
        if not self.get_work_fixation_period(fixation_period_id):
            raise ValueError("Период фиксации не найден.")
        planned = self.list_work_fixation_items(fixation_period_id)
        if any(
            (item.work_guid and item.work_guid == work_guid)
            or (item.work_code and item.work_code == work_code)
            for item in planned
        ):
            raise ValueError(
                "Выбранная работа является плановой. Для внеплановой фиксации "
                "выберите другую работу или услугу."
            )
        existing = self.list_work_fixation_unplanned_items(fixation_period_id)
        if any(item.work_guid == work_guid for item in existing):
            raise ValueError("Эта внеплановая работа уже добавлена.")
        item_id = str(uuid.uuid4())
        position = max((item.position for item in existing), default=0) + 1
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO work_fixation_unplanned_items (
                    id, fixation_period_id, position,
                    work_code, work_name, work_guid,
                    work_type_code, work_type_guid, work_type_name, unit_name,
                    fact_price, fact_amount, fact_count, fact_total_cost, comment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id, fixation_period_id, position,
                    str(work_code), str(work_name), str(work_guid),
                    _clean(work_type_code), _clean(work_type_guid),
                    _clean(work_type_name), _clean(unit_name),
                    _clean(fact_price), _clean(fact_amount),
                    int(fact_count) if fact_count is not None else None,
                    _clean(fact_total_cost), _clean(comment),
                ),
            )
            self._touch_fixation_period(fixation_period_id)
        row = self.connection.execute(
            "SELECT * FROM work_fixation_unplanned_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        assert row is not None
        return self._work_fixation_unplanned_from_row(row)

    def update_work_fixation_unplanned_item(self, item_id: str, **fields: object) -> None:
        allowed = {
            "fact_price", "fact_amount", "fact_count",
            "fact_total_cost", "comment",
        }
        updates = {key: fields[key] for key in fields if key in allowed}
        if "fact_count" in updates:
            value = updates["fact_count"]
            if value is not None and int(value) < 0:
                raise ValueError("Фактическое количество не может быть отрицательным.")
            updates["fact_count"] = int(value) if value is not None else None
        for key in ("fact_price", "fact_amount", "fact_total_cost", "comment"):
            if key in updates:
                updates[key] = _clean(updates[key])
        row = self.connection.execute(
            "SELECT fixation_period_id FROM work_fixation_unplanned_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            raise ValueError("Внеплановая работа не найдена.")
        if updates:
            clause = ", ".join(f"{key} = ?" for key in updates)
            with self.connection:
                self.connection.execute(
                    f"UPDATE work_fixation_unplanned_items SET {clause} WHERE id = ?",
                    [updates[key] for key in updates] + [item_id],
                )
                self._touch_fixation_period(str(row["fixation_period_id"]))

    def delete_work_fixation_unplanned_item(self, item_id: str) -> None:
        row = self.connection.execute(
            """
            SELECT fixation_period_id, gis_fixed_work_guid
            FROM work_fixation_unplanned_items WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        if not row:
            return
        period_id = str(row["fixation_period_id"])
        period = self.get_work_fixation_period(period_id)
        managed = self._json_string_list(
            period.managed_unplanned_guids_json if period else None
        )
        guid = _clean(row["gis_fixed_work_guid"])
        if guid and guid not in managed:
            managed.append(guid)
        with self.connection:
            self.connection.execute(
                "DELETE FROM work_fixation_unplanned_items WHERE id = ?",
                (item_id,),
            )
            self.connection.execute(
                """
                UPDATE work_fixation_periods
                SET managed_unplanned_guids_json = ?, updated_at = ?,
                    gis_status = CASE WHEN gis_status = 'SAVED' THEN 'MODIFIED' ELSE gis_status END
                WHERE id = ?
                """,
                (
                    json.dumps(managed, ensure_ascii=False) if managed else None,
                    _now(),
                    period_id,
                ),
            )

    def list_work_fixation_photos(
        self,
        *,
        fixation_item_id: str | None = None,
        unplanned_item_id: str | None = None,
    ) -> list[WorkFixationPhoto]:
        if bool(fixation_item_id) == bool(unplanned_item_id):
            raise ValueError("Нужно указать ровно одну строку фиксации.")
        field = "fixation_item_id" if fixation_item_id else "unplanned_item_id"
        value = fixation_item_id or unplanned_item_id
        rows = self.connection.execute(
            f"SELECT * FROM work_fixation_photos WHERE {field} = ? ORDER BY created_at, id",
            (value,),
        ).fetchall()
        return [self._work_fixation_photo_from_row(row) for row in rows]

    def add_work_fixation_photo(
        self,
        *,
        fixation_item_id: str | None = None,
        unplanned_item_id: str | None = None,
        file_name: str,
        description: str | None,
        content_type: str,
        content: bytes,
    ) -> WorkFixationPhoto:
        if bool(fixation_item_id) == bool(unplanned_item_id):
            raise ValueError("Фото должно относиться ровно к одной работе.")
        if len(content) > 50 * 1024 * 1024:
            raise ValueError("Размер одного фото не должен превышать 50 МБ.")
        if len(content) == 0:
            raise ValueError("Файл фото пуст.")
        existing_photos = self.list_work_fixation_photos(
            fixation_item_id=fixation_item_id,
            unplanned_item_id=unplanned_item_id,
        )
        if len(existing_photos) >= 1000:
            raise ValueError("К одной работе можно прикрепить не более 1000 фото.")
        if fixation_item_id:
            row = self.connection.execute(
                "SELECT fixation_period_id FROM work_fixation_items WHERE id = ?",
                (fixation_item_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT fixation_period_id FROM work_fixation_unplanned_items WHERE id = ?",
                (unplanned_item_id,),
            ).fetchone()
        if not row:
            raise ValueError("Работа для фотографии не найдена.")
        period_id = str(row["fixation_period_id"])
        photo_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO work_fixation_photos (
                    id, fixation_period_id, fixation_item_id, unplanned_item_id,
                    file_name, description, content_type, file_size, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id, period_id, fixation_item_id, unplanned_item_id,
                    file_name, _clean(description), content_type,
                    len(content), sqlite3.Binary(content), _now(),
                ),
            )
            self._touch_fixation_period(period_id)
        row = self.connection.execute(
            "SELECT * FROM work_fixation_photos WHERE id = ?",
            (photo_id,),
        ).fetchone()
        assert row is not None
        return self._work_fixation_photo_from_row(row)

    def delete_work_fixation_photo(self, photo_id: str) -> None:
        row = self.connection.execute(
            """
            SELECT p.*, i.managed_photo_content_guids_json AS planned_managed,
                   u.managed_photo_content_guids_json AS unplanned_managed
            FROM work_fixation_photos p
            LEFT JOIN work_fixation_items i ON i.id = p.fixation_item_id
            LEFT JOIN work_fixation_unplanned_items u ON u.id = p.unplanned_item_id
            WHERE p.id = ?
            """,
            (photo_id,),
        ).fetchone()
        if not row:
            return
        period_id = str(row["fixation_period_id"])
        remote_guid = _clean(row["remote_content_guid"])
        owner_table = "work_fixation_items" if row["fixation_item_id"] else "work_fixation_unplanned_items"
        owner_id = row["fixation_item_id"] or row["unplanned_item_id"]
        raw_managed = row["planned_managed"] if row["fixation_item_id"] else row["unplanned_managed"]
        managed = self._json_string_list(raw_managed)
        if remote_guid and remote_guid not in managed:
            managed.append(remote_guid)
        with self.connection:
            self.connection.execute("DELETE FROM work_fixation_photos WHERE id = ?", (photo_id,))
            self.connection.execute(
                f"UPDATE {owner_table} SET managed_photo_content_guids_json = ? WHERE id = ?",
                (json.dumps(managed, ensure_ascii=False) if managed else None, owner_id),
            )
            self._touch_fixation_period(period_id)

    @staticmethod
    def _json_string_list(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []

    def _touch_fixation_period(self, fixation_period_id: str) -> None:
        period = self.get_work_fixation_period(fixation_period_id)
        next_status = (
            "MODIFIED" if period and str(period.gis_status or "").upper() == "SAVED"
            else "LOCAL"
        )
        self.connection.execute(
            """
            UPDATE work_fixation_periods
            SET gis_status = ?, gis_last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (next_status, _now(), fixation_period_id),
        )

    def update_work_fixation_item(
        self,
        item_id: str,
        *,
        fact_price: str | None,
        fact_amount: str | None,
        fact_count: int | None,
        fact_total_cost: str | None,
    ) -> None:
        if fact_count is not None and int(fact_count) < 0:
            raise ValueError("Фактическое количество не может быть отрицательным.")

        row = self.connection.execute(
            """
            SELECT fixation_period_id
            FROM work_fixation_items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Строка фиксации не найдена.")

        period_id = str(row["fixation_period_id"])
        period = self.get_work_fixation_period(period_id)
        if period is None:
            raise ValueError("Период фиксации не найден.")

        next_status = (
            "MODIFIED"
            if str(period.gis_status or "").upper() == "SAVED"
            else "LOCAL"
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE work_fixation_items
                SET fact_price = ?,
                    fact_amount = ?,
                    fact_count = ?,
                    fact_total_cost = ?
                WHERE id = ?
                """,
                (
                    _clean(fact_price),
                    _clean(fact_amount),
                    int(fact_count) if fact_count is not None else None,
                    _clean(fact_total_cost),
                    item_id,
                ),
            )
            self.connection.execute(
                """
                UPDATE work_fixation_periods
                SET gis_status = ?, gis_last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (next_status, _now(), period_id),
            )

    def fill_work_fixation_from_plan(self, fixation_period_id: str) -> None:
        period = self.get_work_fixation_period(fixation_period_id)
        if period is None:
            raise ValueError("Период фиксации не найден.")

        rows = self.list_work_fixation_items(fixation_period_id)
        next_status = (
            "MODIFIED"
            if str(period.gis_status or "").upper() == "SAVED"
            else "LOCAL"
        )
        with self.connection:
            for item in rows:
                has_plan = (
                    item.planned_count > 0
                    or item.planned_price is not None
                    or item.planned_amount is not None
                    or item.planned_total_cost is not None
                )
                self.connection.execute(
                    """
                    UPDATE work_fixation_items
                    SET fact_price = ?,
                        fact_amount = ?,
                        fact_count = ?,
                        fact_total_cost = ?
                    WHERE id = ?
                    """,
                    (
                        item.planned_price if has_plan else None,
                        item.planned_amount if has_plan else None,
                        item.planned_count if has_plan else None,
                        item.planned_total_cost if has_plan else None,
                        item.id,
                    ),
                )
            self.connection.execute(
                """
                UPDATE work_fixation_periods
                SET gis_status = ?, gis_last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (next_status, _now(), fixation_period_id),
            )

    def clear_work_fixation_period(self, fixation_period_id: str) -> None:
        period = self.get_work_fixation_period(fixation_period_id)
        if period is None:
            return
        next_status = (
            "MODIFIED"
            if str(period.gis_status or "").upper() == "SAVED"
            else "LOCAL"
        )
        with self.connection:
            self.connection.execute(
                """
                UPDATE work_fixation_items
                SET fact_price = NULL,
                    fact_amount = NULL,
                    fact_count = NULL,
                    fact_total_cost = NULL
                WHERE fixation_period_id = ?
                """,
                (fixation_period_id,),
            )
            self.connection.execute(
                """
                UPDATE work_fixation_periods
                SET gis_status = ?, gis_last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (next_status, _now(), fixation_period_id),
            )

    def update_work_fixation_gis_state(
        self,
        fixation_period_id: str,
        *,
        reporting_period_guid: str | None = None,
        gis_status: str | None = None,
        gis_last_error: str | None = None,
        fixed_item_guids: dict[str, str] | None = None,
        unplanned_item_guids: dict[str, str] | None = None,
        unplanned_item_metadata: dict[str, dict[str, str | None]] | None = None,
        photo_guids: dict[str, tuple[str | None, str | None]] | None = None,
        sync_time: str | None = None,
    ) -> None:
        updates: dict[str, object] = {
            "gis_last_sync_at": sync_time or _now(),
            "gis_last_error": _clean(gis_last_error),
            "updated_at": _now(),
        }
        if reporting_period_guid is not None:
            updates["reporting_period_guid"] = _clean(reporting_period_guid)
        if gis_status is not None:
            updates["gis_status"] = _clean(gis_status)

        with self.connection:
            clause = ", ".join(f"{key} = ?" for key in updates)
            values = [updates[key] for key in updates]
            values.append(fixation_period_id)
            self.connection.execute(
                f"UPDATE work_fixation_periods SET {clause} WHERE id = ?",
                values,
            )

            for item_id, guid in (fixed_item_guids or {}).items():
                self.connection.execute(
                    """
                    UPDATE work_fixation_items
                    SET gis_fixed_work_guid = ?
                    WHERE id = ? AND fixation_period_id = ?
                    """,
                    (_clean(guid), item_id, fixation_period_id),
                )

            for item_id, guid in (unplanned_item_guids or {}).items():
                self.connection.execute(
                    """
                    UPDATE work_fixation_unplanned_items
                    SET gis_fixed_work_guid = ?
                    WHERE id = ? AND fixation_period_id = ?
                    """,
                    (_clean(guid), item_id, fixation_period_id),
                )

            for item_id, metadata in (unplanned_item_metadata or {}).items():
                self.connection.execute(
                    """
                    UPDATE work_fixation_unplanned_items
                    SET work_type_code = COALESCE(?, work_type_code),
                        work_type_guid = COALESCE(?, work_type_guid),
                        work_type_name = COALESCE(?, work_type_name),
                        unit_name = COALESCE(?, unit_name)
                    WHERE id = ? AND fixation_period_id = ?
                    """,
                    (
                        _clean(metadata.get("work_type_code")),
                        _clean(metadata.get("work_type_guid")),
                        _clean(metadata.get("work_type_name")),
                        _clean(metadata.get("unit_name")),
                        item_id,
                        fixation_period_id,
                    ),
                )

            for photo_id, values in (photo_guids or {}).items():
                content_guid, photo_guid = values
                self.connection.execute(
                    """
                    UPDATE work_fixation_photos
                    SET remote_content_guid = ?, remote_photo_guid = ?
                    WHERE id = ? AND fixation_period_id = ?
                    """,
                    (
                        _clean(content_guid),
                        _clean(photo_guid),
                        photo_id,
                        fixation_period_id,
                    ),
                )

            planned_rows = self.connection.execute(
                "SELECT id FROM work_fixation_items WHERE fixation_period_id = ?",
                (fixation_period_id,),
            ).fetchall()
            for row in planned_rows:
                item_id = str(row["id"])
                photo_rows = self.connection.execute(
                    """
                    SELECT remote_content_guid
                    FROM work_fixation_photos
                    WHERE fixation_item_id = ?
                      AND remote_content_guid IS NOT NULL
                      AND remote_content_guid <> ''
                    ORDER BY created_at, id
                    """,
                    (item_id,),
                ).fetchall()
                managed = [str(item["remote_content_guid"]) for item in photo_rows]
                self.connection.execute(
                    """
                    UPDATE work_fixation_items
                    SET managed_photo_content_guids_json = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(managed, ensure_ascii=False) if managed else None,
                        item_id,
                    ),
                )

            unplanned_rows = self.connection.execute(
                """
                SELECT id, gis_fixed_work_guid
                FROM work_fixation_unplanned_items
                WHERE fixation_period_id = ?
                """,
                (fixation_period_id,),
            ).fetchall()
            managed_unplanned = []
            for row in unplanned_rows:
                item_id = str(row["id"])
                if row["gis_fixed_work_guid"]:
                    managed_unplanned.append(str(row["gis_fixed_work_guid"]))
                photo_rows = self.connection.execute(
                    """
                    SELECT remote_content_guid
                    FROM work_fixation_photos
                    WHERE unplanned_item_id = ?
                      AND remote_content_guid IS NOT NULL
                      AND remote_content_guid <> ''
                    ORDER BY created_at, id
                    """,
                    (item_id,),
                ).fetchall()
                managed_photos = [str(item["remote_content_guid"]) for item in photo_rows]
                self.connection.execute(
                    """
                    UPDATE work_fixation_unplanned_items
                    SET managed_photo_content_guids_json = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(managed_photos, ensure_ascii=False)
                        if managed_photos
                        else None,
                        item_id,
                    ),
                )

            self.connection.execute(
                """
                UPDATE work_fixation_periods
                SET managed_unplanned_guids_json = ?
                WHERE id = ?
                """,
                (
                    json.dumps(managed_unplanned, ensure_ascii=False)
                    if managed_unplanned
                    else None,
                    fixation_period_id,
                ),
            )

    def update_work_list_period(
        self,
        work_list_id: str,
        *,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
    ) -> None:
        self.connection.execute(
            """
            UPDATE work_lists
            SET start_year = ?, start_month = ?, end_year = ?, end_month = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                int(start_year),
                int(start_month),
                int(end_year),
                int(end_month),
                _now(),
                work_list_id,
            ),
        )
        self.connection.commit()

    def update_work_list_item(self, item_id: str, **fields: object) -> None:
        allowed = {
            "work_code",
            "work_name",
            "work_guid",
            "unit_name",
            "price",
            "volume",
            "quantity",
            "cost",
            "allocation_percent",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        clause = ", ".join(f"{key} = ?" for key in updates)
        values = [_clean(updates[key]) for key in updates]
        values.append(item_id)
        self.connection.execute(
            f"UPDATE work_list_items SET {clause} WHERE id = ?", values
        )
        self.connection.commit()

    def update_work_list_unit_names(
        self,
        work_list_id: str,
        units: dict[str, str | None],
    ) -> None:
        """Store unit names by work-list item id and propagate to fixation snapshots."""

        if not units:
            return
        with self.connection:
            for item_id, unit_name in units.items():
                cleaned = _clean(unit_name)
                self.connection.execute(
                    """
                    UPDATE work_list_items
                    SET unit_name = ?
                    WHERE id = ? AND work_list_id = ?
                    """,
                    (cleaned, str(item_id), work_list_id),
                )
                self.connection.execute(
                    """
                    UPDATE work_fixation_items
                    SET unit_name = ?
                    WHERE work_list_item_id = ?
                    """,
                    (cleaned, str(item_id)),
                )

    def apply_lazy_distribution(
        self,
        work_list_id: str,
        *,
        planned_total: str,
        planned_tariff: str | None = None,
        planned_area: str | None = None,
        planned_months: str | None = None,
        rows: Iterable[object],
    ) -> None:
        """Atomically persist one calculated quantity+cost distribution."""

        timestamp = _now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE work_lists
                SET planned_total = ?,
                    planned_tariff = ?,
                    planned_area = ?,
                    planned_months = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    _clean(planned_total),
                    _clean(planned_tariff),
                    _clean(planned_area),
                    _clean(planned_months),
                    timestamp,
                    work_list_id,
                ),
            )
            for row in rows:
                item_id = str(getattr(row, "item_id"))
                allocation_percent = _clean(getattr(row, "allocation_percent", None))
                quantity = _clean(getattr(row, "quantity", None))
                cost = _clean(getattr(row, "cost", None))
                self.connection.execute(
                    """
                    UPDATE work_list_items
                    SET price = NULL,
                        volume = NULL,
                        quantity = ?,
                        cost = ?,
                        allocation_percent = ?
                    WHERE id = ? AND work_list_id = ?
                    """,
                    (
                        quantity,
                        cost,
                        allocation_percent,
                        item_id,
                        work_list_id,
                    ),
                )

    def update_work_list_gis_state(
        self,
        work_list_id: str,
        *,
        gis_work_list_guid: str | None = None,
        gis_file_guid: str | None = None,
        gis_import_job_guid: str | None = None,
        gis_status: str | None = None,
        gis_last_error: str | None = None,
        sync_time: str | None = None,
    ) -> None:
        """Persist the last known GIS ЖКХ state for one local work list."""

        updates: dict[str, object] = {
            "gis_last_sync_at": sync_time or _now(),
            "gis_last_error": _clean(gis_last_error),
            "updated_at": _now(),
        }

        if gis_work_list_guid is not None:
            updates["gis_work_list_guid"] = _clean(gis_work_list_guid)
        if gis_file_guid is not None:
            updates["gis_file_guid"] = _clean(gis_file_guid)
        if gis_import_job_guid is not None:
            updates["gis_import_job_guid"] = _clean(gis_import_job_guid)
        if gis_status is not None:
            normalized = str(gis_status).strip().upper()
            updates["gis_status"] = normalized
            if normalized == "DRAFT":
                updates["status"] = str(WorkListStatus.GIS_DRAFT)
            elif normalized == "APPROVED":
                updates["status"] = str(WorkListStatus.PLACED)
            elif normalized == "NOT_FOUND":
                updates["status"] = str(WorkListStatus.LOCAL)
                updates["gis_work_list_guid"] = None

        clause = ", ".join(f"{key} = ?" for key in updates)
        values = [updates[key] for key in updates]
        values.append(work_list_id)

        self.connection.execute(
            f"UPDATE work_lists SET {clause} WHERE id = ?",
            values,
        )
        self.connection.commit()

    def clear_work_list_gis_project_guid(self, work_list_id: str) -> None:
        self.connection.execute(
            """
            UPDATE work_lists
            SET gis_work_list_guid = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_now(), work_list_id),
        )
        self.connection.commit()

    def clear_work_list_gis_error(self, work_list_id: str) -> None:
        self.connection.execute(
            """
            UPDATE work_lists
            SET gis_last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_now(), work_list_id),
        )
        self.connection.commit()

    def delete_work_list(self, work_list_id: str) -> None:
        self.connection.execute("DELETE FROM work_lists WHERE id = ?", (work_list_id,))
        self.connection.commit()



def _month_key(year: int, month: int) -> int:
    return int(year) * 12 + (int(month) - 1)


def _year_month_from_key(value: int) -> tuple[int, int]:
    return value // 12, value % 12 + 1


def _validate_month_point(year: int, month: int) -> None:
    if int(year) < 1900:
        raise ValueError("Год указан некорректно.")
    if not 1 <= int(month) <= 12:
        raise ValueError("Месяц должен быть от 1 до 12.")


def _validate_date_point(year: int, month: int, day: int) -> None:
    _validate_month_point(year, month)
    try:
        date(int(year), int(month), int(day))
    except ValueError as exc:
        raise ValueError("День месяца указан некорректно.") from exc


def _validate_date_precision(value: str) -> str:
    precision = str(value or "month").strip().lower()
    if precision not in {"month", "day"}:
        raise ValueError("Точность даты должна быть month или day.")
    return precision


def _last_day_of_month(year: int, month: int) -> int:
    if int(month) == 12:
        first_next = date(int(year) + 1, 1, 1)
    else:
        first_next = date(int(year), int(month) + 1, 1)
    return (first_next - timedelta(days=1)).day


def _validate_period(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> None:
    _validate_month_point(start_year, start_month)
    _validate_month_point(end_year, end_month)
    start = _month_key(start_year, start_month)
    end = _month_key(end_year, end_month)
    if end < start:
        raise ValueError("Окончание периода не может быть раньше начала.")


def _required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} не может быть пустым.")
    return text


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
