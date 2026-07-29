from pathlib import Path

import pytest

from shared import storage


def test_download_directory_rejects_object_key_traversal(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        storage,
        "list_objects",
        lambda _prefix, _bucket=None: [
            "missions/mission-1/../../outside.txt"
        ],
    )

    with pytest.raises(ValueError, match="Unsafe S3 object key"):
        storage.download_directory(
            "missions/mission-1/",
            tmp_path / "destination",
        )

    assert not (tmp_path / "outside.txt").exists()


def test_download_directory_keeps_valid_keys_below_destination(
    tmp_path,
    monkeypatch,
):
    downloaded: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        storage,
        "list_objects",
        lambda _prefix, _bucket=None: [
            "missions/mission-1/tiles/0.jpg"
        ],
    )
    monkeypatch.setattr(
        storage,
        "download_file",
        lambda key, path, _bucket=None: downloaded.append(
            (key, Path(path))
        ),
    )

    destination = tmp_path / "destination"
    assert (
        storage.download_directory(
            "missions/mission-1/",
            destination,
        )
        == 1
    )
    assert downloaded == [
        (
            "missions/mission-1/tiles/0.jpg",
            destination / "tiles" / "0.jpg",
        )
    ]


class _VerifiedClient:
    def __init__(self):
        self.metadata = {}
        self.size = 0

    def upload_file(self, filename, _bucket, _key, ExtraArgs):
        self.metadata = ExtraArgs["Metadata"]
        self.size = Path(filename).stat().st_size

    def head_object(self, **_kwargs):
        return {
            "ContentLength": self.size,
            "Metadata": self.metadata,
        }


def test_verified_upload_checks_size_and_sha256(tmp_path, monkeypatch):
    artifact = tmp_path / "orthomosaic.tif"
    artifact.write_bytes(b"required-geospatial-artifact")
    client = _VerifiedClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    result = storage.upload_verified_file(
        artifact,
        "missions/mission-1/orthomosaic.tif",
    )

    assert result["size"] == artifact.stat().st_size
    assert result["sha256"] == client.metadata["sha256"]
