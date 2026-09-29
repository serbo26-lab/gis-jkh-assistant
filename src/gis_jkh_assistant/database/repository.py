"""SQLite repository used by the desktop application."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .models import House, ManagementOrganization, RecordSource


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
            """
        )
        self.connection.commit()

    @staticmethod
    def _org_from_row(row: sqlite3.Row) -> ManagementOrganization:
        return ManagementOrganization(**dict(row))

    @staticmethod
    def _house_from_row(row: sqlite3.Row) -> House:
        data = dict(row)
        data["source"] = RecordSource(data["source"])
        return House(**data)

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
                fias_house_guid, gis_unique_house_number, cadastral_number,
                residential_premises_count, non_residential_premises_count,
                source, comment, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
