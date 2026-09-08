import importlib
import io
from types import SimpleNamespace

import pytest

from shared import storage

headers = importlib.import_module("app4-dashboard.api.dataset_image_headers")


@pytest.mark.parametrize("name,header", [
    ("photo.JPG", b"\xff\xd8\xff"), ("photo.png", b"\x89PNG\r\n\x1a\n"),
    ("photo.webp", b"RIFF1234WEBP"), ("photo.tif", b"II*\0"),
    ("photo.tiff", b"MM\0*"), ("large.tif", b"II+\0\x08\0\0\0"),
    ("large.tiff", b"MM\0+\0\x08\0\0"),
])
def test_image_signature_and_truncation(name, header):
    assert headers.image_header_matches(name, header)
    assert not headers.image_header_matches(name, header[:-1])
    assert not headers.image_header_matches(name, b"%PDF-1.7")


def test_opaque_sidecars_do_not_require_image_magic(monkeypatch):
    monkeypatch.setattr(storage, "get_object_prefix", lambda *args, **kwargs: pytest.fail("sidecar image probe"))
    assert headers.uploaded_image_header_matches("a.nav", "a.nav", 128, '"etag"')


@pytest.mark.parametrize("size", [3, 1024])
def test_prefix_get_is_bounded_conditional_and_closed(monkeypatch, size):
    length = min(size, 16)
    body = io.BytesIO(b"x" * length)
    calls = []
    def get_object(**kwargs):
        calls.append(kwargs)
        return {"Body": body, "ContentLength": length, "ContentRange": f"bytes 0-{length-1}/{size}",
                "ETag": '"known"', "ResponseMetadata": {"HTTPStatusCode": 206}}
    monkeypatch.setattr(storage, "_get_client", lambda: SimpleNamespace(get_object=get_object))
    assert storage.get_object_prefix("private.jpg", expected_size=size, etag='"known"', bucket="test") == b"x" * length
    assert calls == [{"Bucket": "test", "Key": "private.jpg", "Range": f"bytes=0-{length-1}", "IfMatch": '"known"'}]
    assert body.closed


@pytest.mark.parametrize("change", [
    {"ContentLength": 1024}, {"ContentRange": "bytes 1-16/1024"},
    {"ETag": '"different"'}, {"ResponseMetadata": {"HTTPStatusCode": 200}},
])
def test_mismatched_range_response_is_rejected_and_closed(monkeypatch, change):
    body = io.BytesIO(b"x" * 16)
    response = {"Body": body, "ContentLength": 16, "ContentRange": "bytes 0-15/1024",
                "ETag": '"known"', "ResponseMetadata": {"HTTPStatusCode": 206}, **change}
    monkeypatch.setattr(storage, "_get_client", lambda: SimpleNamespace(get_object=lambda **kwargs: response))
    with pytest.raises(OSError, match="verified upload"):
        storage.get_object_prefix("private.jpg", expected_size=1024, etag='"known"')
    assert body.closed
