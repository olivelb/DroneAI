"""S3-compatible object storage abstraction layer.

Works with MinIO (local) and any S3-compatible service (cloud).
All services use this module instead of direct filesystem I/O for
persistent data (datasets, mission artifacts, tiles, orthomosaics).
"""

import logging
import os
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast
from collections.abc import Iterable

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from shared.checksums import sha256_file
from shared.config import (
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
)

logger = logging.getLogger(__name__)


class S3Paginator(Protocol):
    """Minimal paginator contract used by this storage boundary."""

    def paginate(self, **kwargs: Any) -> Iterable[dict[str, Any]]: ...


class S3Client(Protocol):
    """Subset of the dynamic boto3 S3 client consumed by DroneAI."""

    def upload_file(self, *args: Any, **kwargs: Any) -> None: ...

    def download_file(self, *args: Any, **kwargs: Any) -> None: ...

    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_paginator(self, operation_name: str) -> S3Paginator: ...

    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def generate_presigned_url(self, *args: Any, **kwargs: Any) -> str: ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_bucket(self, **kwargs: Any) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

S3_PUBLIC_ENDPOINT = os.getenv("S3_PUBLIC_ENDPOINT", "")  # browser-reachable MinIO URL
S3_DELETE_MAX_ATTEMPTS = max(1, int(os.getenv("S3_DELETE_MAX_ATTEMPTS", "3")))

# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client: S3Client | None = None
_public_client: S3Client | None = None


def _get_client() -> S3Client:
    global _client
    if _client is None:
        _client = cast(
            S3Client,
            boto3.client(
                "s3",
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=S3_ACCESS_KEY,
                aws_secret_access_key=S3_SECRET_KEY,
                # OVH exposes uppercase region codes through its cloud API,
                # while its S3 signature scope requires a lowercase region.
                region_name=S3_REGION.lower(),
                config=BotoConfig(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    response_checksum_validation="when_required",
                ),
            ),
        )
    return _client


def reset_client() -> None:
    """Reset the cached S3 client (useful for testing)."""
    global _client, _public_client
    _client = None
    _public_client = None


def _get_public_client() -> S3Client:
    """Return a client using the public endpoint for presigned URLs.

    Falls back to the internal client when S3_PUBLIC_ENDPOINT is not set.
    """
    global _public_client
    if not S3_PUBLIC_ENDPOINT:
        return _get_client()
    if _public_client is None:
        _public_client = cast(
            S3Client,
            boto3.client(
                "s3",
                endpoint_url=S3_PUBLIC_ENDPOINT,
                aws_access_key_id=S3_ACCESS_KEY,
                aws_secret_access_key=S3_SECRET_KEY,
                region_name=S3_REGION.lower(),
                config=BotoConfig(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    response_checksum_validation="when_required",
                ),
            ),
        )
    return _public_client


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def upload_file(local_path: str | Path, s3_key: str, bucket: str | None = None) -> str:
    """Upload a local file to S3. Returns the s3_key."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")
    client.upload_file(str(local_path), bucket, s3_key)
    logger.debug("Uploaded %s → s3://%s/%s", local_path, bucket, s3_key)
    return s3_key


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    return str(sha256_file(path, chunk_size=chunk_size))


def upload_verified_file(
    local_path: str | Path,
    s3_key: str,
    bucket: str | None = None,
) -> dict[str, int | str]:
    """Upload a required artifact and verify size and SHA-256 with HEAD.

    S3 multipart ETags are not content hashes, so the digest is stored as
    object metadata and checked after publication.
    """

    bucket = bucket or S3_BUCKET
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"Required local artifact not found: {path}")
    size = path.stat().st_size
    digest = _sha256_file(path)
    client = _get_client()
    client.upload_file(
        str(path),
        bucket,
        s3_key,
        ExtraArgs={"Metadata": {"sha256": digest}},
    )
    head = client.head_object(Bucket=bucket, Key=s3_key)
    remote_size = int(head.get("ContentLength", -1))
    remote_digest = str(head.get("Metadata", {}).get("sha256", ""))
    if remote_size != size or remote_digest != digest:
        raise OSError(
            f"S3 verification failed for s3://{bucket}/{s3_key}: "
            f"size={remote_size}/{size}, sha256={remote_digest}/{digest}"
        )
    logger.info(
        "Published and verified %s -> s3://%s/%s (%d bytes)",
        path,
        bucket,
        s3_key,
        size,
    )
    return {"key": s3_key, "size": size, "sha256": digest}


def download_file(s3_key: str, local_path: str | Path, bucket: str | None = None) -> Path:
    """Download a file from S3 to a local path. Creates parent dirs. Returns local Path."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, s3_key, str(local_path))
    logger.debug("Downloaded s3://%s/%s → %s", bucket, s3_key, local_path)
    return local_path


def upload_directory(local_dir: str | Path, s3_prefix: str, bucket: str | None = None) -> int:
    """Upload all files in a local directory (recursively) to S3 under the given prefix.

    Returns the number of files uploaded.
    """
    bucket = bucket or S3_BUCKET
    local_dir = Path(local_dir)
    if not local_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {local_dir}")
    count = 0
    for file_path in sorted(local_dir.rglob("*")):
        if file_path.is_file():
            relative = file_path.relative_to(local_dir)
            s3_key = f"{s3_prefix.rstrip('/')}/{relative.as_posix()}"
            upload_file(file_path, s3_key, bucket)
            count += 1
    logger.info("Uploaded %d files from %s → s3://%s/%s", count, local_dir, bucket, s3_prefix)
    return count


def download_directory(s3_prefix: str, local_dir: str | Path, bucket: str | None = None) -> int:
    """Download all objects under an S3 prefix to a local directory.

    Returns the number of files downloaded.
    """
    bucket = bucket or S3_BUCKET
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    root = local_dir.resolve()
    count = 0
    for key in list_objects(s3_prefix, bucket):
        relative = key[len(s3_prefix) :].lstrip("/")
        if not relative:
            continue
        local_path = (root / relative).resolve()
        if local_path != root and root not in local_path.parents:
            raise ValueError(f"Unsafe S3 object key outside destination: {key}")
        download_file(key, local_path, bucket)
        count += 1
    logger.info("Downloaded %d files from s3://%s/%s → %s", count, bucket, s3_prefix, local_dir)
    return count


def list_objects(s3_prefix: str, bucket: str | None = None, delimiter: str = "") -> list[str]:
    """List all object keys under a prefix.

    If *delimiter* is set (e.g. ``"/"``), returns only the common prefixes
    (virtual directory listing).
    """
    bucket = bucket or S3_BUCKET
    client = _get_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=s3_prefix, Delimiter=delimiter)
    for page in pages:
        if delimiter:
            for cp in page.get("CommonPrefixes", []):
                keys.append(cp["Prefix"])
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def file_exists(s3_key: str, bucket: str | None = None) -> bool:
    """Check if an object exists in S3."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    try:
        client.head_object(Bucket=bucket, Key=s3_key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def get_object_size(s3_key: str, bucket: str | None = None) -> int:
    """Return the size in bytes of an S3 object."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    response = client.head_object(Bucket=bucket, Key=s3_key)
    return int(response["ContentLength"])


def delete_object(s3_key: str, bucket: str | None = None) -> None:
    """Delete a single object from S3."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    client.delete_object(Bucket=bucket, Key=s3_key)
    logger.debug("Deleted s3://%s/%s", bucket, s3_key)


def _delete_object_batch(
    client: S3Client,
    bucket: str,
    keys: list[str],
) -> list[dict[str, Any]]:
    response = client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
    )
    errors = response.get("Errors") or []
    if not isinstance(errors, list):
        raise RuntimeError("S3 DeleteObjects returned a malformed Errors field")
    return [error for error in errors if isinstance(error, dict)]


def _delete_error_keys(
    errors: list[dict[str, Any]],
    requested_keys: set[str],
) -> set[str]:
    failed = {str(error.get("Key")) for error in errors if error.get("Key") in requested_keys}
    if len(failed) != len(errors):
        raise RuntimeError("S3 DeleteObjects returned an error without a matching object key")
    return failed


def delete_prefix(s3_prefix: str, bucket: str | None = None) -> int:
    """Delete and reconcile all objects under a prefix."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    pending = sorted(set(list_objects(s3_prefix, bucket)))
    if not pending:
        return 0

    targeted = set(pending)
    last_errors: list[dict[str, Any]] = []
    for attempt in range(1, S3_DELETE_MAX_ATTEMPTS + 1):
        failed: set[str] = set()
        last_errors = []
        for index in range(0, len(pending), 1000):
            batch = pending[index : index + 1000]
            errors = _delete_object_batch(client, bucket, batch)
            last_errors.extend(errors)
            failed.update(_delete_error_keys(errors, set(batch)))

        remaining = set(list_objects(s3_prefix, bucket))
        targeted.update(remaining)
        pending = sorted(failed | remaining)
        if not pending:
            deleted = len(targeted)
            logger.info(
                "Deleted %d objects under s3://%s/%s",
                deleted,
                bucket,
                s3_prefix,
            )
            return deleted
        logger.warning(
            "S3 prefix deletion attempt %d/%d left %d objects under s3://%s/%s",
            attempt,
            S3_DELETE_MAX_ATTEMPTS,
            len(pending),
            bucket,
            s3_prefix,
        )

    error_summary = ", ".join(f"{error.get('Key', '?')} ({error.get('Code', 'unknown')})" for error in last_errors[:5])
    detail = f"; last errors: {error_summary}" if error_summary else ""
    raise RuntimeError(
        f"Failed to delete {len(pending)} objects under s3://{bucket}/{s3_prefix} "
        f"after {S3_DELETE_MAX_ATTEMPTS} attempts{detail}"
    )


def get_object_stream(
    s3_key: str,
    bucket: str | None = None,
) -> tuple[BinaryIO, int, str]:
    """Return (stream, content_length, content_type) for an S3 object.

    The caller is responsible for reading and closing the stream.
    """
    bucket = bucket or S3_BUCKET
    client = _get_client()
    response = client.get_object(Bucket=bucket, Key=s3_key)
    return (
        cast(BinaryIO, response["Body"]),
        int(response.get("ContentLength", 0)),
        str(response.get("ContentType", "application/octet-stream")),
    )


def get_presigned_url(
    s3_key: str,
    expires: int = 3600,
    bucket: str | None = None,
    *,
    public: bool = True,
) -> str:
    """Generate a presigned GET URL for an S3 object.

    Uses S3_PUBLIC_ENDPOINT when set so the URL is reachable from browsers.
    *expires* is the URL lifetime in seconds (default 1 hour).
    """
    bucket = bucket or S3_BUCKET
    client = _get_public_client() if public else _get_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expires,
    )
    return url


def put_object(s3_key: str, data: bytes | BinaryIO, bucket: str | None = None) -> str:
    """Upload raw bytes or a file-like object to S3. Returns the s3_key."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    client.put_object(Bucket=bucket, Key=s3_key, Body=data)
    logger.debug("Put object s3://%s/%s", bucket, s3_key)
    return s3_key


def ensure_bucket(bucket: str | None = None) -> None:
    """Create the bucket if it doesn't already exist."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)
        logger.info("Created bucket: %s", bucket)
