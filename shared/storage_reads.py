"""Bounded object reads independent of S3 client configuration."""
from collections.abc import Callable
from typing import Any, BinaryIO, Protocol, cast


class ObjectReader(Protocol):
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


def read_object_prefix(
    client: ObjectReader, s3_key: str, bucket: str, *,
    expected_size: int, etag: str, max_bytes: int = 16,
) -> bytes:
    """Read a bounded prefix of exactly the object previously verified by HEAD."""
    if not 1 <= max_bytes <= 64 * 1024 or expected_size < 1 or not etag:
        raise ValueError("A bounded range and verified object identity are required")
    length = min(max_bytes, expected_size)
    response = client.get_object(
        Bucket=bucket, Key=s3_key,
        Range=f"bytes=0-{length - 1}", IfMatch=etag,
    )
    stream = cast(BinaryIO, response["Body"])
    try:
        if (response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 206
                or response.get("ContentRange") != f"bytes 0-{length - 1}/{expected_size}"
                or int(response.get("ContentLength", -1)) != length
                or response.get("ETag") != etag):
            raise OSError("Object range response does not match the verified upload")
        payload = stream.read(length + 1)
        if len(payload) != length:
            raise OSError("Object range response length is inconsistent")
        return bytes(payload)
    finally:
        stream.close()


def read_control_object(
    stream_reader: Callable[[str, str | None], tuple[BinaryIO, int, str]],
    s3_key: str,
    bucket: str | None = None,
    *,
    max_bytes: int = 16 * 1024 * 1024,
) -> bytes:
    """Read one bounded control object and reject unexpectedly large payloads."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    stream, size, _content_type = stream_reader(s3_key, bucket)
    try:
        if size > max_bytes:
            raise ValueError(
                f"S3 control object exceeds {max_bytes} bytes: {s3_key}"
            )
        payload = stream.read(max_bytes + 1)
    finally:
        stream.close()
    if len(payload) != size:
        raise OSError(
            f"S3 object size changed while reading {s3_key}: "
            f"read={len(payload)}, expected={size}"
        )
    return bytes(payload)


