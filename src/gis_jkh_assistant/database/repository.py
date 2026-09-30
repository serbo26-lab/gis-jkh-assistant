"""SQLite repository used by the desktop application."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    House,
    HouseTariff,
    ManagementOrganization,
    RecordSource,
    TemplateSource,
    WorkList,
    WorkListItem,
    WorkListStatus,
    WorkTemplate,
    WorkTemplateItem,
)


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
                end_year INTEGER NOT NULL,
                end_month INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (house_id)
                    REFERENCES houses(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_house_tariffs_period
                ON house_tariffs(house_id, start_year, start_month, end_year, end_month);

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
                price TEXT,
                volume TEXT,
                quantity TEXT,
                cost TEXT,
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

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._ensure_column("houses", "charge_area", "TEXT")
        self._ensure_column("work_templates", "source_file_path", "TEXT")
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
            ORDER BY start_year, start_month, end_year, end_month, created_at
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
        end_year: int | None = None,
        end_month: int | None = None,
    ) -> HouseTariff:
        del end_year, end_month
        _validate_month_point(start_year, start_month)
        self._assert_unique_tariff_start(
            house_id,
            start_year=start_year,
            start_month=start_month,
        )

        tariff_id = str(uuid.uuid4())
        timestamp = _now()
        self.connection.execute(
            """
            INSERT INTO house_tariffs (
                id, house_id, amount,
                start_year, start_month, end_year, end_month,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tariff_id,
                house_id,
                _required_text(amount, "Тариф"),
                int(start_year),
                int(start_month),
                9999,
                12,
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
        end_year: int | None = None,
        end_month: int | None = None,
    ) -> None:
        del end_year, end_month
        _validate_month_point(start_year, start_month)

        existing = self.get_house_tariff(tariff_id)
        if existing is None:
            raise ValueError("Тариф не найден.")

        self._assert_unique_tariff_start(
            existing.house_id,
            start_year=start_year,
            start_month=start_month,
            exclude_id=tariff_id,
        )

        self.connection.execute(
            """
            UPDATE house_tariffs
            SET amount = ?,
                start_year = ?,
                start_month = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                _required_text(amount, "Тариф"),
                int(start_year),
                int(start_month),
                _now(),
                tariff_id,
            ),
        )
        self.connection.commit()
        self._recalculate_house_tariff_periods(existing.house_id)

    def delete_house_tariff(self, tariff_id: str) -> None:
        self.connection.execute(
            "DELETE FROM house_tariffs WHERE id = ?",
            (tariff_id,),
        )
        self.connection.commit()

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
        start_key = int(start_year) * 12 + int(start_month)
        end_key = int(end_year) * 12 + int(end_month)

        candidates = self.list_house_tariffs(house_id)
        matching = [
            item
            for item in candidates
            if (
                item.start_year * 12 + item.start_month <= start_key
                and item.end_year * 12 + item.end_month >= end_key
            )
        ]
        if not matching:
            return None
        return max(
            matching,
            key=lambda item: (item.start_year, item.start_month, item.updated_at or ""),
        )

    def _assert_unique_tariff_start(
        self,
        house_id: str,
        *,
        start_year: int,
        start_month: int,
        exclude_id: str | None = None,
    ) -> None:
        rows = self.list_house_tariffs(house_id)
        for item in rows:
            if exclude_id and item.id == exclude_id:
                continue
            if item.start_year == int(start_year) and item.start_month == int(start_month):
                raise ValueError(
                    "Для этого дома уже указан тариф, начинающий действовать "
                    f"с {int(start_month):02d}.{int(start_year)}."
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
            SELECT id, start_year, start_month
            FROM house_tariffs
            WHERE house_id = ?
            ORDER BY start_year, start_month, created_at
            """,
            (house_id,),
        ).fetchall()
        if not rows:
            return

        with self.connection:
            for index, row in enumerate(rows):
                if index + 1 < len(rows):
                    next_row = rows[index + 1]
                    previous_key = _month_key(
                        int(next_row["start_year"]),
                        int(next_row["start_month"]),
                    ) - 1
                    end_year, end_month = _year_month_from_key(previous_key)
                else:
                    end_year, end_month = 9999, 12

                self.connection.execute(
                    """
                    UPDATE house_tariffs
                    SET end_year = ?, end_month = ?
                    WHERE id = ?
                    """,
                    (end_year, end_month, str(row["id"])),
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
                        price, volume, quantity, cost
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            price, volume, quantity, cost, allocation_percent
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            str(uuid.uuid4()),
                            work_list_id,
                            row["position"],
                            row["work_code"],
                            row["work_name"],
                            row["work_guid"],
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
                        price, volume, quantity, cost, allocation_percent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        work_list_id,
                        row["position"],
                        row["work_code"],
                        row["work_name"],
                        row["work_guid"],
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



def _validate_period(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> None:
    start = int(start_year) * 12 + int(start_month)
    end = int(end_year) * 12 + int(end_month)
    if not 1 <= int(start_month) <= 12 or not 1 <= int(end_month) <= 12:
        raise ValueError("Месяц должен быть от 1 до 12.")
    if end < start:
        raise ValueError("Окончание периода тарифа не может быть раньше начала.")


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
