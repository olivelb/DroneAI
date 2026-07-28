"""S3-compatible object storage abstraction layer.

Works with MinIO (local) and any S3-compatible service (cloud).
All services use this module instead of direct filesystem I/O for
persistent data (datasets, mission artifacts, tiles, orthomosaics).
"""

import io
import logging
import os
from pathlib import Path
from typing import BinaryIO, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from shared.config import (
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

S3_PUBLIC_ENDPOINT = os.getenv("S3_PUBLIC_ENDPOINT", "")  # browser-reachable MinIO URL

# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client = None
_public_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            region_name=S3_REGION,
            config=BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
    return _client


def reset_client():
    """Reset the cached S3 client (useful for testing)."""
    global _client, _public_client
    _client = None
    _public_client = None


def _get_public_client():
    """Return a client using the public endpoint for presigned URLs.

    Falls back to the internal client when S3_PUBLIC_ENDPOINT is not set.
    """
    global _public_client
    if not S3_PUBLIC_ENDPOINT:
        return _get_client()
    if _public_client is None:
        _public_client = boto3.client(
            "s3",
            endpoint_url=S3_PUBLIC_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            region_name=S3_REGION,
            config=BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
    return _public_client


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def upload_file(local_path: str | Path, s3_key: str, bucket: Optional[str] = None) -> str:
    """Upload a local file to S3. Returns the s3_key."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")
    client.upload_file(str(local_path), bucket, s3_key)
    logger.debug("Uploaded %s → s3://%s/%s", local_path, bucket, s3_key)
    return s3_key


def download_file(s3_key: str, local_path: str | Path, bucket: Optional[str] = None) -> Path:
    """Download a file from S3 to a local path. Creates parent dirs. Returns local Path."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, s3_key, str(local_path))
    logger.debug("Downloaded s3://%s/%s → %s", bucket, s3_key, local_path)
    return local_path


def upload_directory(local_dir: str | Path, s3_prefix: str, bucket: Optional[str] = None) -> int:
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


def download_directory(s3_prefix: str, local_dir: str | Path, bucket: Optional[str] = None) -> int:
    """Download all objects under an S3 prefix to a local directory.

    Returns the number of files downloaded.
    """
    bucket = bucket or S3_BUCKET
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for key in list_objects(s3_prefix, bucket):
        relative = key[len(s3_prefix):].lstrip("/")
        if not relative:
            continue
        local_path = local_dir / relative
        download_file(key, local_path, bucket)
        count += 1
    logger.info("Downloaded %d files from s3://%s/%s → %s", count, bucket, s3_prefix, local_dir)
    return count


def list_objects(s3_prefix: str, bucket: Optional[str] = None, delimiter: str = "") -> list[str]:
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


def file_exists(s3_key: str, bucket: Optional[str] = None) -> bool:
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


def get_object_size(s3_key: str, bucket: Optional[str] = None) -> int:
    """Return the size in bytes of an S3 object."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    response = client.head_object(Bucket=bucket, Key=s3_key)
    return response["ContentLength"]


def delete_object(s3_key: str, bucket: Optional[str] = None) -> None:
    """Delete a single object from S3."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    client.delete_object(Bucket=bucket, Key=s3_key)
    logger.debug("Deleted s3://%s/%s", bucket, s3_key)


def delete_prefix(s3_prefix: str, bucket: Optional[str] = None) -> int:
    """Delete all objects under a prefix. Returns count of deleted objects."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    keys = list_objects(s3_prefix, bucket)
    if not keys:
        return 0
    # Delete in batches of 1000 (S3 limit)
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        deleted += len(batch)
    logger.info("Deleted %d objects under s3://%s/%s", deleted, bucket, s3_prefix)
    return deleted


def get_object_stream(s3_key: str, bucket: Optional[str] = None):
    """Return (stream, content_length, content_type) for an S3 object.

    The caller is responsible for reading and closing the stream.
    """
    bucket = bucket or S3_BUCKET
    client = _get_client()
    response = client.get_object(Bucket=bucket, Key=s3_key)
    return (
        response["Body"],
        response.get("ContentLength", 0),
        response.get("ContentType", "application/octet-stream"),
    )


def get_presigned_url(s3_key: str, expires: int = 3600, bucket: Optional[str] = None) -> str:
    """Generate a presigned GET URL for an S3 object.

    Uses S3_PUBLIC_ENDPOINT when set so the URL is reachable from browsers.
    *expires* is the URL lifetime in seconds (default 1 hour).
    """
    bucket = bucket or S3_BUCKET
    client = _get_public_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expires,
    )
    return url


def put_object(s3_key: str, data: bytes | BinaryIO, bucket: Optional[str] = None) -> str:
    """Upload raw bytes or a file-like object to S3. Returns the s3_key."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    body = data if isinstance(data, (bytes, bytearray, memoryview, io.IOBase)) else data
    client.put_object(Bucket=bucket, Key=s3_key, Body=body)
    logger.debug("Put object s3://%s/%s", bucket, s3_key)
    return s3_key


def ensure_bucket(bucket: Optional[str] = None) -> None:
    """Create the bucket if it doesn't already exist."""
    bucket = bucket or S3_BUCKET
    client = _get_client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)
        logger.info("Created bucket: %s", bucket)
