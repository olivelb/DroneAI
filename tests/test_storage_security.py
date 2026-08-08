from pathlib import Path

import pytest

from shared import storage


def test_s3_client_uses_compatible_optional_response_checksums(monkeypatch):
    captured = {}
    client = object()

    def fake_client(_service, **kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(storage.boto3, "client", fake_client)
    monkeypatch.setattr(storage, "S3_REGION", "GRA")
    storage.reset_client()
    try:
        assert storage._get_client() is client
    finally:
        storage.reset_client()

    assert captured["config"].response_checksum_validation == "when_required"
    assert captured["region_name"] == "gra"


def test_download_directory_rejects_object_key_traversal(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        storage,
        "list_objects",
        lambda _prefix, _bucket=None: ["missions/mission-1/../../outside.txt"],
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
        lambda _prefix, _bucket=None: ["missions/mission-1/tiles/0.jpg"],
    )
    monkeypatch.setattr(
        storage,
        "download_file",
        lambda key, path, _bucket=None: downloaded.append((key, Path(path))),
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


class _DeleteClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.deleted_batches = []

    def delete_objects(self, **kwargs):
        self.deleted_batches.append([entry["Key"] for entry in kwargs["Delete"]["Objects"]])
        return next(self.responses)


def test_delete_prefix_retries_partial_object_errors(monkeypatch):
    client = _DeleteClient(
        [
            {
                "Errors": [
                    {
                        "Key": "missions/mission-1/b.tif",
                        "Code": "InternalError",
                    }
                ]
            },
            {},
        ]
    )
    listings = iter(
        [
            ["missions/mission-1/a.tif", "missions/mission-1/b.tif"],
            ["missions/mission-1/b.tif"],
            [],
        ]
    )
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "list_objects", lambda *_args: next(listings))

    deleted = storage.delete_prefix("missions/mission-1/")

    assert deleted == 2
    assert client.deleted_batches == [
        ["missions/mission-1/a.tif", "missions/mission-1/b.tif"],
        ["missions/mission-1/b.tif"],
    ]


def test_delete_prefix_retries_objects_found_during_reconciliation(monkeypatch):
    client = _DeleteClient([{}, {}])
    listings = iter(
        [
            ["datasets/site/a.jpg"],
            ["datasets/site/a.jpg"],
            [],
        ]
    )
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "list_objects", lambda *_args: next(listings))

    assert storage.delete_prefix("datasets/site/") == 1
    assert client.deleted_batches == [
        ["datasets/site/a.jpg"],
        ["datasets/site/a.jpg"],
    ]


def test_delete_prefix_raises_after_bounded_partial_failures(monkeypatch):
    error_response = {
        "Errors": [
            {
                "Key": "missions/mission-1/a.tif",
                "Code": "AccessDenied",
            }
        ]
    }
    client = _DeleteClient([error_response, error_response, error_response])
    listings = iter(
        [
            ["missions/mission-1/a.tif"],
            ["missions/mission-1/a.tif"],
            ["missions/mission-1/a.tif"],
            ["missions/mission-1/a.tif"],
        ]
    )
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "list_objects", lambda *_args: next(listings))
    monkeypatch.setattr(storage, "S3_DELETE_MAX_ATTEMPTS", 3)

    with pytest.raises(RuntimeError, match="after 3 attempts.*AccessDenied"):
        storage.delete_prefix("missions/mission-1/")

    assert len(client.deleted_batches) == 3
