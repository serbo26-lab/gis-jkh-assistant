"""GIS ЖКХ integration helpers."""

from .local_gateway import (
    IntegrationError,
    IntegrationStatus,
    LocalIntegrationGateway,
)

__all__ = [
    "IntegrationError",
    "IntegrationStatus",
    "LocalIntegrationGateway",
]
