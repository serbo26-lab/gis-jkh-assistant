from gis_jkh_assistant.gis_files.common import GisFileMetadata


def test_unknown_version_is_allowed_as_metadata() -> None:
    metadata = GisFileMetadata(file_type="HCS-OBJ", version="99.99.99")
    assert metadata.file_type == "HCS-OBJ"
    assert metadata.version == "99.99.99"
