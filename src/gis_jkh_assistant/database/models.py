"""Domain models stored in the local application database."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecordSource(StrEnum):
    """Origin of a local record."""

    IMPORT = "import"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ManagementOrganization:
    id: str
    name: str
    ogrn: str
    kpp: str | None = None
    comment: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class House:
    id: str
    organization_id: str
    address: str
    house_object_guid: str | None = None
    gis_house_guid: str | None = None
    fias_house_guid: str | None = None
    gis_unique_house_number: str | None = None
    cadastral_number: str | None = None
    residential_premises_count: int = 0
    non_residential_premises_count: int = 0
    source: RecordSource = RecordSource.MANUAL
    comment: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def total_premises_count(self) -> int:
        return self.residential_premises_count + self.non_residential_premises_count
