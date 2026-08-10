import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from shared import storage


def _client_error(code: str, operation: str = "HeadObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "test error"}},
        operation,
    )


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


class _CasClient:
    def __init__(self):
        self.objects = {}
        self.put_calls = []

    def head_object(self, *, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise _client_error("404")
        content, metadata = self.objects[Key]
        return {"ContentLength": len(content), "Metadata": metadata}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        content = kwargs["Body"].read()
        self.objects[kwargs["Key"]] = (content, kwargs["Metadata"])
        return {}


def test_content_addressed_publish_uploads_once_then_reuses(tmp_path, monkeypatch):
    artifact = tmp_path / "model.ply"
    artifact.write_bytes(b"immutable-gaussian-model")
    client = _CasClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    first = storage.publish_content_addressed_file(artifact)
    second = storage.publish_content_addressed_file(artifact)

    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert first.key == f"blobs/sha256/{digest[:2]}/{digest}"
    assert first.reused is False
    assert first.transferred_bytes == artifact.stat().st_size
    assert second.reused is True
    assert second.transferred_bytes == 0
    assert len(client.put_calls) == 1
    assert client.put_calls[0]["IfNoneMatch"] == "*"


def test_content_addressed_publish_rejects_conflicting_existing_object(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "model.ply"
    artifact.write_bytes(b"expected-content")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    key = f"blobs/sha256/{digest[:2]}/{digest}"
    client = _CasClient()
    client.objects[key] = (b"wrong", {"sha256": "f" * 64})
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    with pytest.raises(OSError, match="Content-addressed object conflict"):
        storage.publish_content_addressed_file(artifact)

    assert client.put_calls == []


def test_content_addressed_publish_fails_closed_above_single_put_limit(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "oversized.bin"
    artifact.write_bytes(b"four")
    client = _CasClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "S3_CAS_SINGLE_PUT_MAX_BYTES", 3)

    with pytest.raises(ValueError, match="multipart CAS"):
        storage.publish_content_addressed_file(artifact)

    assert client.put_calls == []


class _ConcurrentCasClient(_CasClient):
    def __init__(self):
        super().__init__()
        self.initial_heads = threading.Barrier(2)
        self.lock = threading.Lock()

    def head_object(self, *, Bucket, Key):
        with self.lock:
            existing = self.objects.get(Key)
        if existing is not None:
            content, metadata = existing
            return {"ContentLength": len(content), "Metadata": metadata}
        self.initial_heads.wait(timeout=5)
        raise _client_error("404")

    def put_object(self, **kwargs):
        content = kwargs["Body"].read()
        with self.lock:
            self.put_calls.append(kwargs)
            if kwargs["Key"] in self.objects:
                raise _client_error("PreconditionFailed", "PutObject")
            self.objects[kwargs["Key"]] = (content, kwargs["Metadata"])
        return {}


def test_concurrent_content_addressed_publishers_converge_on_one_blob(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "large-model.ply"
    artifact.write_bytes(b"same-model-from-two-stage-jobs")
    client = _ConcurrentCasClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                storage.publish_content_addressed_file,
                (artifact, artifact),
            )
        )

    assert {result.key for result in results} == {results[0].key}
    assert sorted(result.reused for result in results) == [False, True]
    assert len(client.objects) == 1
    assert len(client.put_calls) == 2


def test_remote_object_checksum_verification(monkeypatch):
    client = _VerifiedClient()
    client.metadata = {"sha256": "a" * 64}
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    storage.verify_object_checksum("missions/mission-1/manifest.json", "a" * 64)

    with pytest.raises(OSError, match="checksum verification failed"):
        storage.verify_object_checksum(
            "missions/mission-1/manifest.json",
            "b" * 64,
        )


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


class _MultipartClient:
    def __init__(self):
        self.completed = None
        self.aborted = None

    def create_multipart_upload(self, **_kwargs):
        return {"UploadId": "upload-123"}

    def generate_presigned_url(self, operation, **kwargs):
        assert operation == "upload_part"
        assert kwargs["HttpMethod"] == "PUT"
        return "https://objects.example/upload-part"

    def complete_multipart_upload(self, **kwargs):
        self.completed = kwargs
        return {"ETag": '"multipart-etag"'}

    def head_object(self, **_kwargs):
        return {"ContentLength": 123, "ETag": '"multipart-etag"'}

    def abort_multipart_upload(self, **kwargs):
        self.aborted = kwargs
        return {}


def test_multipart_upload_helpers_presign_complete_and_abort(monkeypatch):
    client = _MultipartClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "_get_public_client", lambda: client)

    upload_id = storage.create_multipart_upload("datasets/site/image.jpg")
    url = storage.get_presigned_upload_part_url(
        "datasets/site/image.jpg",
        upload_id,
        1,
    )
    result = storage.complete_multipart_upload(
        "datasets/site/image.jpg",
        upload_id,
        [{"PartNumber": 1, "ETag": '"part-etag"'}],
    )
    storage.abort_multipart_upload("datasets/site/other.jpg", "upload-456")

    assert url == "https://objects.example/upload-part"
    assert result == {
        "key": "datasets/site/image.jpg",
        "size": 123,
        "etag": '"multipart-etag"',
    }
    assert client.completed["MultipartUpload"]["Parts"] == [
        {"PartNumber": 1, "ETag": '"part-etag"'}
    ]
    assert client.aborted["UploadId"] == "upload-456"
