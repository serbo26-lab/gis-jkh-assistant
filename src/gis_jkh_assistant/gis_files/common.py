"""Common structures for version-tolerant GIS ЖКХ file readers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GisFileMetadata:
    """Metadata discovered in a GIS ЖКХ file.

    Version is informational. Readers validate the actual structure
    instead of rejecting a file only because its version is unfamiliar.
    """

    file_type: str | None = None
    version: str | None = None
