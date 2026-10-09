"""Capture the selected organization's identity before an asynchronous command."""

from __future__ import annotations

from typing import Any

MIN_ORGANIZATION_ROUTING_HELPER_VERSION = (0, 1, 30)


def helper_supports_organization_routing(version: str | None) -> bool:
    try:
        parts = tuple(int(part) for part in (version or "").split("."))
    except ValueError:
        return False
    return len(parts) == 3 and parts >= MIN_ORGANIZATION_ROUTING_HELPER_VERSION


def with_organization_context(repository, organization_id, payload: dict[str, Any]) -> dict:
    from .local_gateway import IntegrationError

    organization = repository.get_organization(organization_id) if organization_id else None
    if organization is None or not organization.ogrn:
        raise IntegrationError("Выберите управляющую организацию перед обменом с ГИС ЖКХ.")
    context = {"ogrn": organization.ogrn}
    if organization.gis_organization_root_guid:
        context["organizationRootGuid"] = organization.gis_organization_root_guid
    return {**payload, "organizationContext": context}
