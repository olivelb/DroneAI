from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from shared import storage
from tools import qualify_s3_conditional_multipart as qualification


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "test error"}},
        "CompleteMultipartUpload",
    )


class _ConditionalClient:
    def __init__(self, *, reject: bool) -> None:
        self.reject = reject
        self.aborted = []

    def create_multipart_upload(self, **_kwargs):
        return {"UploadId": "qualification-upload"}

    def upload_part(self, **_kwargs):
        return {"ETag": '"qualification-part"'}

    def complete_multipart_upload(self, **kwargs):
        assert kwargs["IfNoneMatch"] == "*"
        if self.reject:
            raise _client_error("PreconditionFailed")
        return {"ETag": '"unsafe-overwrite"'}

    def abort_multipart_upload(self, **kwargs):
        self.aborted.append(kwargs)
        return {}


def test_overwrite_probe_requires_conditional_conflict(monkeypatch):
    client = _ConditionalClient(reject=True)
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    assert qualification._conditional_overwrite_probe("blobs/probe") == (
        "PreconditionFailed"
    )
    assert len(client.aborted) == 1


def test_overwrite_probe_rejects_provider_that_ignores_condition(monkeypatch):
    client = _ConditionalClient(reject=False)
    monkeypatch.setattr(storage, "_get_client", lambda: client)

    with pytest.raises(RuntimeError, match="ignored If-None-Match"):
        qualification._conditional_overwrite_probe("blobs/probe")

    assert client.aborted == []


def test_qualification_reuses_and_cleans_random_probe(tmp_path, monkeypatch):
    probe = tmp_path / "probe.bin"
    probe.write_bytes(b"x" * qualification.MINIMUM_PROBE_BYTES)
    calls = []
    deleted = []

    def publish(path: Path, *, force_multipart: bool):
        calls.append((Path(path), force_multipart))
        return storage.ContentAddressedUpload(
            key="blobs/probe",
            size_bytes=probe.stat().st_size,
            checksum_sha256="a" * 64,
            reused=len(calls) == 2,
            transferred_bytes=0 if len(calls) == 2 else probe.stat().st_size,
        )

    monkeypatch.setattr(storage, "publish_content_addressed_file", publish)
    monkeypatch.setattr(
        qualification,
        "_conditional_overwrite_probe",
        lambda _key: "PreconditionFailed",
    )
    monkeypatch.setattr(storage, "verify_object_checksum", lambda *_args: None)
    monkeypatch.setattr(storage, "get_object_size", lambda _key: probe.stat().st_size)
    monkeypatch.setattr(storage, "delete_object", deleted.append)
    monkeypatch.setattr(storage, "file_exists", lambda _key: False)

    result = qualification.qualify(probe)

    assert result["status"] == "passed"
    assert result["cleanup_verified"] is True
    assert len(calls) == 2
    assert all(force for _path, force in calls)
    assert len(deleted) == 1
