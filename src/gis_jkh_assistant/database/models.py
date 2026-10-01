"""Domain models stored in the local application database."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecordSource(StrEnum):
    """Origin of a local record."""

    IMPORT = "import"
    MANUAL = "manual"


class TemplateSource(StrEnum):
    """Origin of a user work-list template."""

    IMPORT = "import"
    MANUAL = "manual"
    BUILTIN = "builtin"


class WorkListStatus(StrEnum):
    """Local lifecycle state of a work and service list."""

    LOCAL = "local"
    GIS_DRAFT = "gis_draft"
    PLACED = "placed"


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
    charge_area: str | None = None
    residential_premises_count: int = 0
    non_residential_premises_count: int = 0
    source: RecordSource = RecordSource.MANUAL
    comment: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def total_premises_count(self) -> int:
        return self.residential_premises_count + self.non_residential_premises_count


@dataclass(frozen=True, slots=True)
class HouseTariff:
    id: str
    house_id: str
    amount: str
    start_year: int
    start_month: int
    end_year: int
    end_month: int
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def effective_from_label(self) -> str:
        return f"{self.start_month:02d}.{self.start_year}"

    @property
    def effective_to_label(self) -> str:
        if self.end_year >= 9999:
            return "до следующего изменения"
        return f"{self.end_month:02d}.{self.end_year}"

    @property
    def period_label(self) -> str:
        if self.end_year >= 9999:
            return f"с {self.effective_from_label}"
        return f"{self.effective_from_label} — {self.effective_to_label}"


@dataclass(frozen=True, slots=True)
class WorkTemplate:
    id: str
    organization_id: str
    name: str
    source: TemplateSource = TemplateSource.MANUAL
    source_file_name: str | None = None
    source_file_path: str | None = None
    source_house_guid: str | None = None
    file_type: str | None = None
    file_version: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class WorkTemplateItem:
    id: str
    template_id: str
    position: int
    work_code: str | None
    work_name: str
    work_guid: str | None = None
    price: str | None = None
    volume: str | None = None
    quantity: str | None = None
    cost: str | None = None
    allocation_percent: str | None = None


@dataclass(frozen=True, slots=True)
class WorkList:
    id: str
    house_id: str
    template_id: str | None
    base_year: int
    start_year: int
    start_month: int
    end_year: int
    end_month: int
    planned_total: str | None = None
    planned_tariff: str | None = None
    planned_area: str | None = None
    planned_months: str | None = None
    status: WorkListStatus = WorkListStatus.LOCAL
    gis_work_list_guid: str | None = None
    gis_file_guid: str | None = None
    gis_import_job_guid: str | None = None
    gis_status: str | None = None
    gis_last_sync_at: str | None = None
    gis_last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def period_label(self) -> str:
        return (
            f"{self.start_month:02d}.{self.start_year} — "
            f"{self.end_month:02d}.{self.end_year}"
        )


@dataclass(frozen=True, slots=True)
class WorkListItem:
    id: str
    work_list_id: str
    position: int
    work_code: str | None
    work_name: str
    work_guid: str | None = None
    price: str | None = None
    volume: str | None = None
    quantity: str | None = None
    cost: str | None = None
    allocation_percent: str | None = None
