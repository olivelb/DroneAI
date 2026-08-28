import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from shared import storage


class _PresignedGetClient:
    def __init__(self):
        self.arguments = None

    def generate_presigned_url(self, operation, **arguments):
        self.arguments = (operation, arguments)
        return "https://objects.example/download"


def test_presigned_get_can_declare_zstd_response_encoding(monkeypatch):
    client = _PresignedGetClient()
    monkeypatch.setattr(storage, "_get_public_client", lambda: client)

    assert storage.get_presigned_url(
        "organizations/org-a/blobs/pack",
        expires=900,
        response_content_encoding="zstd",
    ) == "https://objects.example/download"
    assert client.arguments == (
        "get_object",
        {
            "Params": {
                "Bucket": storage.S3_BUCKET,
                "Key": "organizations/org-a/blobs/pack",
                "ResponseContentEncoding": "zstd",
            },
            "ExpiresIn": 900,
        },
    )


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


class _RecoveryPaginator:
    def paginate(self, **kwargs):
        assert kwargs == {"Bucket": storage.S3_BUCKET, "Prefix": "datasets/a.jpg"}
        return [
            {
                "Uploads": [
                    {"Key": "datasets/a.jpg", "UploadId": "upload-1"},
                    {"Key": "datasets/a.jpg.extra", "UploadId": "upload-2"},
                ]
            },
            {"Uploads": [{"Key": "datasets/a.jpg", "UploadId": "upload-3"}]},
        ]


class _RecoveryStorageClient:
    def head_object(self, *, Bucket, Key):
        assert Bucket == storage.S3_BUCKET
        if Key == "datasets/missing.jpg":
            raise _client_error("404")
        return {
            "ContentLength": 123,
            "ETag": '"object-etag"',
            "ContentType": "image/jpeg",
            "Metadata": {"DroneAI-Upload-Session": "session-1"},
        }

    def get_paginator(self, operation_name):
        assert operation_name == "list_multipart_uploads"
        return _RecoveryPaginator()


def test_recovery_storage_reads_identity_and_lists_only_exact_key(monkeypatch):
    client = _RecoveryStorageClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    assert storage.get_object_info("datasets/missing.jpg") is None
    assert storage.get_object_info("datasets/a.jpg") == {
        "key": "datasets/a.jpg",
        "size": 123,
        "etag": '"object-etag"',
        "content_type": "image/jpeg",
        "metadata": {"droneai-upload-session": "session-1"},
    }
    assert storage.list_multipart_uploads("datasets/a.jpg") == [
        "upload-1",
        "upload-3",
    ]


class _AdoptionStorageClient:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}
        self.copy_calls = 0
        self.uploads: dict[str, dict[str, object]] = {}

    def head_object(self, *, Bucket, Key):
        del Bucket
        if Key not in self.objects:
            raise _client_error("404")
        content, metadata, content_type = self.objects[Key]
        return {
            "ContentLength": len(content),
            "ETag": f'"{hashlib.sha256(content).hexdigest()}"',
            "ContentType": content_type,
            "Metadata": metadata,
        }

    def get_object(self, *, Bucket, Key):
        head = self.head_object(Bucket=Bucket, Key=Key)
        content, _metadata, _content_type = self.objects[Key]
        return {**head, "Body": io.BytesIO(content)}

    def put_object(self, *, Bucket, Key, Body, Metadata, **_kwargs):
        del Bucket
        content = Body if isinstance(Body, bytes) else Body.read()
        self.objects[Key] = (bytes(content), dict(Metadata), "")
        return {}

    def create_multipart_upload(self, *, Bucket, Key, Metadata, **kwargs):
        del Bucket
        upload_id = f"upload-{len(self.uploads) + 1}"
        self.uploads[upload_id] = {
            "key": Key,
            "metadata": dict(Metadata),
            "content_type": str(kwargs.get("ContentType") or ""),
            "parts": {},
        }
        return {"UploadId": upload_id}

    def upload_part_copy(
        self,
        *,
        Bucket,
        Key,
        UploadId,
        PartNumber,
        CopySource,
        CopySourceRange,
        CopySourceIfMatch,
    ):
        del Bucket, Key
        self.copy_calls += 1
        content, _metadata, _content_type = self.objects[CopySource["Key"]]
        assert CopySourceIfMatch == f'"{hashlib.sha256(content).hexdigest()}"'
        start, end = (
            int(value)
            for value in CopySourceRange.removeprefix("bytes=").split("-")
        )
        self.uploads[UploadId]["parts"][PartNumber] = content[start : end + 1]
        return {"CopyPartResult": {"ETag": f'"part-{PartNumber}"'}}

    def complete_multipart_upload(
        self,
        *,
        Bucket,
        Key,
        UploadId,
        MultipartUpload,
        IfNoneMatch,
    ):
        del Bucket, MultipartUpload
        assert IfNoneMatch == "*"
        upload = self.uploads.pop(UploadId)
        assert upload["key"] == Key
        content = b"".join(upload["parts"][index] for index in sorted(upload["parts"]))
        self.objects[Key] = (
            content,
            upload["metadata"],
            upload["content_type"],
        )
        return {}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        del Bucket, Key
        self.uploads.pop(UploadId, None)
        return {}


def test_adoption_copy_is_verified_resumable_and_conflict_safe(monkeypatch):
    client = _AdoptionStorageClient()
    client.objects["missions/legacy/result.bin"] = (
        b"immutable-result",
        {"sha256": hashlib.sha256(b"immutable-result").hexdigest()},
        "application/octet-stream",
    )
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    first = storage.copy_verified_object(
        "missions/legacy/result.bin",
        "organizations/acme/missions/legacy/result.bin",
    )
    second = storage.copy_verified_object(
        "missions/legacy/result.bin",
        "organizations/acme/missions/legacy/result.bin",
    )

    assert first["reused"] is False
    assert second["reused"] is True
    assert client.copy_calls == 1

    content, _metadata, content_type = client.objects[
        "organizations/acme/missions/legacy/result.bin"
    ]
    client.objects["organizations/acme/missions/legacy/result.bin"] = (
        content + b"tampered",
        {},
        content_type,
    )
    with pytest.raises(OSError, match="conflicts"):
        storage.copy_verified_object(
            "missions/legacy/result.bin",
            "organizations/acme/missions/legacy/result.bin",
        )


def test_adoption_copy_scales_parts_to_the_s3_part_limit(monkeypatch):
    client = _AdoptionStorageClient()
    client.objects["missions/legacy/large.bin"] = (
        b"0123456789",
        {},
        "application/octet-stream",
    )
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_MIN_PART_BYTES", 2)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_PART_BYTES", 2)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_MAX_PARTS", 3)

    storage.copy_verified_object(
        "missions/legacy/large.bin",
        "organizations/acme/missions/legacy/large.bin",
    )

    assert client.copy_calls == 3


def test_control_objects_are_bounded_immutable_and_verified(monkeypatch):
    client = _AdoptionStorageClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    first = storage.put_verified_bytes("plans/run.json", b'{"run":1}')
    second = storage.put_verified_bytes("plans/run.json", b'{"run":1}')

    assert first["reused"] is False
    assert second["reused"] is True
    assert storage.get_object_bytes("plans/run.json") == b'{"run":1}'
    with pytest.raises(OSError, match="conflict"):
        storage.put_verified_bytes("plans/run.json", b'{"run":2}')
    with pytest.raises(ValueError, match="exceeds"):
        storage.get_object_bytes("plans/run.json", max_bytes=2)


class _ConcurrentAdoptionStorageClient(_AdoptionStorageClient):
    def __init__(self):
        super().__init__()
        self.initial_heads = threading.Barrier(2)
        self.lock = threading.Lock()

    def head_object(self, *, Bucket, Key):
        with self.lock:
            exists = Key in self.objects
        if exists:
            return super().head_object(Bucket=Bucket, Key=Key)
        self.initial_heads.wait(timeout=5)
        raise _client_error("404")

    def put_object(self, *, Bucket, Key, Body, Metadata, **kwargs):
        del Bucket, kwargs
        content = Body if isinstance(Body, bytes) else Body.read()
        with self.lock:
            if Key in self.objects:
                raise _client_error("PreconditionFailed", "PutObject")
            self.objects[Key] = (bytes(content), dict(Metadata), "")
        return {}


def test_concurrent_identical_control_object_publishers_converge(monkeypatch):
    client = _ConcurrentAdoptionStorageClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: storage.put_verified_bytes(
                    "plans/concurrent.json",
                    b'{"run":1}',
                ),
                range(2),
            )
        )

    assert sorted(result["reused"] for result in results) == [False, True]
    assert storage.get_object_bytes("plans/concurrent.json") == b'{"run":1}'


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

    first = storage.publish_content_addressed_file(artifact, organization_id="acme")
    second = storage.publish_content_addressed_file(artifact, organization_id="acme")

    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert first.key == f"organizations/acme/blobs/sha256/{digest[:2]}/{digest}"
    assert first.reused is False
    assert first.transferred_bytes == artifact.stat().st_size
    assert second.reused is True
    assert second.transferred_bytes == 0
    assert len(client.put_calls) == 1
    assert client.put_calls[0]["IfNoneMatch"] == "*"


def test_content_addressed_publish_isolated_by_organization(tmp_path, monkeypatch):
    artifact = tmp_path / "model.ply"
    artifact.write_bytes(b"same-immutable-model")
    client = _CasClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    acme = storage.publish_content_addressed_file(
        artifact,
        organization_id="acme",
    )
    other = storage.publish_content_addressed_file(
        artifact,
        organization_id="other",
    )
    acme_retry = storage.publish_content_addressed_file(
        artifact,
        organization_id="acme",
    )

    assert acme.key.startswith("organizations/acme/blobs/sha256/")
    assert other.key.startswith("organizations/other/blobs/sha256/")
    assert acme.key != other.key
    assert acme.reused is False
    assert other.reused is False
    assert acme_retry.reused is True
    assert len(client.put_calls) == 2


def test_content_addressed_publish_rejects_conflicting_existing_object(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "model.ply"
    artifact.write_bytes(b"expected-content")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    key = f"organizations/acme/blobs/sha256/{digest[:2]}/{digest}"
    client = _CasClient()
    client.objects[key] = (b"wrong", {"sha256": "f" * 64})
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    with pytest.raises(OSError, match="Content-addressed object conflict"):
        storage.publish_content_addressed_file(artifact, organization_id="acme")

    assert client.put_calls == []


class _MultipartCasClient(_CasClient):
    def __init__(self):
        super().__init__()
        self.uploads = {}
        self.upload_parts = []
        self.complete_calls = []
        self.abort_calls = []
        self.fail_part = None
        self.conflict_after_publishing = False
        self.complete_error_code = None

    def create_multipart_upload(self, **kwargs):
        upload_id = f"upload-{len(self.uploads) + 1}"
        self.uploads[upload_id] = {
            "key": kwargs["Key"],
            "metadata": kwargs["Metadata"],
            "parts": {},
        }
        return {"UploadId": upload_id}

    def upload_part(self, **kwargs):
        part_number = kwargs["PartNumber"]
        if self.fail_part == part_number:
            raise _client_error("InternalError", "UploadPart")
        body = bytes(kwargs["Body"])
        assert kwargs["ContentLength"] == len(body)
        self.uploads[kwargs["UploadId"]]["parts"][part_number] = body
        self.upload_parts.append((part_number, body))
        return {"ETag": f'"part-{part_number}"'}

    def complete_multipart_upload(self, **kwargs):
        self.complete_calls.append(kwargs)
        upload = self.uploads[kwargs["UploadId"]]
        content = b"".join(upload["parts"][index] for index in sorted(upload["parts"]))
        if self.complete_error_code is not None:
            raise _client_error(self.complete_error_code, "CompleteMultipartUpload")
        if self.conflict_after_publishing:
            self.objects[kwargs["Key"]] = (content, upload["metadata"])
            raise _client_error("PreconditionFailed", "CompleteMultipartUpload")
        assert kwargs["IfNoneMatch"] == "*"
        if kwargs["Key"] in self.objects:
            raise _client_error("PreconditionFailed", "CompleteMultipartUpload")
        self.objects[kwargs["Key"]] = (content, upload["metadata"])
        return {"ETag": '"complete"'}

    def abort_multipart_upload(self, **kwargs):
        self.abort_calls.append(kwargs)
        self.uploads.pop(kwargs["UploadId"], None)
        return {}


def _force_test_multipart(monkeypatch):
    monkeypatch.setattr(storage, "S3_CAS_SINGLE_PUT_MAX_BYTES", 3)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_MIN_PART_BYTES", 2)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_PART_BYTES", 3)


def test_content_addressed_multipart_publish_is_conditional_and_verified(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "large.bin"
    artifact.write_bytes(b"abcdefgh")
    client = _MultipartCasClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    _force_test_multipart(monkeypatch)
    cancellations = []

    result = storage.publish_content_addressed_file(
        artifact,
        cancellation_check=lambda: cancellations.append(True),
        organization_id="acme",
    )

    assert result.reused is False
    assert result.transferred_bytes == 8
    assert client.upload_parts == [(1, b"abc"), (2, b"def"), (3, b"gh")]
    assert client.complete_calls[0]["IfNoneMatch"] == "*"
    assert client.abort_calls == []
    assert len(cancellations) == 5
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert next(iter(client.objects.values()))[1] == {"sha256": digest}


def test_content_addressed_publish_can_force_provider_qualification_multipart(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "forced-multipart.bin"
    artifact.write_bytes(b"small-but-forced")
    client = _MultipartCasClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_MIN_PART_BYTES", 2)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_PART_BYTES", 3)

    result = storage.publish_content_addressed_file(
        artifact,
        force_multipart=True,
        organization_id="acme",
    )

    assert result.reused is False
    assert client.put_calls == []
    assert len(client.complete_calls) == 1


def test_content_addressed_multipart_conflict_verifies_winner_and_aborts(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "concurrent-large.bin"
    artifact.write_bytes(b"same-winner")
    client = _MultipartCasClient()
    client.conflict_after_publishing = True
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    _force_test_multipart(monkeypatch)

    result = storage.publish_content_addressed_file(artifact, organization_id="acme")

    assert result.reused is True
    assert result.transferred_bytes == 0
    assert len(client.abort_calls) == 1


def test_content_addressed_multipart_failure_aborts_without_publication(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "failed-large.bin"
    artifact.write_bytes(b"abcdefgh")
    client = _MultipartCasClient()
    client.fail_part = 2
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    _force_test_multipart(monkeypatch)

    with pytest.raises(ClientError):
        storage.publish_content_addressed_file(artifact, organization_id="acme")

    assert len(client.abort_calls) == 1
    assert client.objects == {}


def test_content_addressed_multipart_unsupported_condition_fails_closed(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "unsupported-condition.bin"
    artifact.write_bytes(b"abcdefgh")
    client = _MultipartCasClient()
    client.complete_error_code = "NotImplemented"
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    _force_test_multipart(monkeypatch)

    with pytest.raises(ClientError) as error:
        storage.publish_content_addressed_file(artifact, organization_id="acme")

    assert error.value.response["Error"]["Code"] == "NotImplemented"
    assert len(client.abort_calls) == 1
    assert client.objects == {}


def test_content_addressed_multipart_missing_conflict_winner_retries_bounded(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "missing-winner.bin"
    artifact.write_bytes(b"abcdefgh")
    client = _MultipartCasClient()
    client.complete_error_code = "ConditionalRequestConflict"
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    _force_test_multipart(monkeypatch)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_MAX_ATTEMPTS", 2)

    with pytest.raises(OSError, match="did not create"):
        storage.publish_content_addressed_file(artifact, organization_id="acme")

    assert len(client.complete_calls) == 2
    assert len(client.abort_calls) == 2
    assert client.objects == {}


def test_multipart_part_size_respects_maximum_part_count(monkeypatch):
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_MIN_PART_BYTES", 2)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_PART_BYTES", 2)
    monkeypatch.setattr(storage, "S3_CAS_MULTIPART_MAX_PARTS", 3)

    assert storage._multipart_part_size(10) == 4


def test_content_addressed_publish_rejects_above_s3_object_limit(
    tmp_path,
    monkeypatch,
):
    artifact = tmp_path / "oversized.bin"
    artifact.write_bytes(b"four")
    client = _MultipartCasClient()
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    monkeypatch.setattr(storage, "S3_CAS_MAX_OBJECT_BYTES", 3)

    with pytest.raises(ValueError, match="5 TiB"):
        storage.publish_content_addressed_file(artifact, organization_id="acme")

    assert client.uploads == {}


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
                lambda path: storage.publish_content_addressed_file(path, organization_id="acme"),
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

    def list_parts(self, **kwargs):
        self.listed = kwargs
        return {
            "Parts": [
                {"PartNumber": 1, "Size": 123, "ETag": '"part-etag"'}
            ],
            "IsTruncated": False,
        }

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
        content_length=123,
    )
    parts = storage.list_multipart_parts(
        "datasets/site/image.jpg",
        upload_id,
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
    assert parts == [
        {"PartNumber": 1, "Size": 123, "ETag": '"part-etag"'}
    ]
    assert client.listed["UploadId"] == "upload-123"
    assert client.completed["MultipartUpload"]["Parts"] == [
        {"PartNumber": 1, "ETag": '"part-etag"'}
    ]
    assert client.aborted["UploadId"] == "upload-456"
