"""Immutable S3 publication algorithms independent of client construction."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

from botocore.exceptions import ClientError

from shared.artifact_manifest import content_addressed_blob_key
from shared.checksums import sha256_file

logger = logging.getLogger(__name__)

ObjectInfo = dict[str, int | str | dict[str, str]]
ObjectInfoReader = Callable[[str, str | None], ObjectInfo | None]
ObjectStreamReader = Callable[[str, str | None], tuple[BinaryIO, int, str]]


class ImmutableS3Client(Protocol):
    """S3 client surface required by immutable publication algorithms."""

    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def upload_part(self, **kwargs: Any) -> dict[str, Any]: ...

    def upload_part_copy(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]: ...

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]: ...

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ImmutableStorageSettings:
    single_put_max_bytes: int
    multipart_min_part_bytes: int
    multipart_part_bytes: int
    multipart_max_parts: int
    multipart_max_attempts: int
    max_object_bytes: int


@dataclass(frozen=True)
class ContentAddressedUpload:
    key: str
    size_bytes: int
    checksum_sha256: str
    reused: bool
    transferred_bytes: int


def client_error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def is_conditional_write_conflict(error: ClientError) -> bool:
    return client_error_code(error) in {
        "409",
        "412",
        "ConditionalRequestConflict",
        "PreconditionFailed",
    }


def multipart_part_size(size: int, settings: ImmutableStorageSettings) -> int:
    required_for_part_limit = (
        size + settings.multipart_max_parts - 1
    ) // settings.multipart_max_parts
    return max(
        settings.multipart_min_part_bytes,
        settings.multipart_part_bytes,
        required_for_part_limit,
    )


def abort_multipart_quietly(
    client: ImmutableS3Client,
    *,
    bucket: str,
    key: str,
    upload_id: str,
) -> None:
    try:
        client.abort_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
        )
    except Exception:
        logger.warning(
            "Failed to abort multipart immutable upload s3://%s/%s (%s)",
            bucket,
            key,
            upload_id,
            exc_info=True,
        )


def _verify_content_addressed_head(
    client: ImmutableS3Client,
    *,
    bucket: str,
    key: str,
    expected_size: int,
    expected_sha256: str,
) -> bool:
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if client_error_code(error) in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    remote_size = int(head.get("ContentLength", -1))
    remote_digest = str(head.get("Metadata", {}).get("sha256", ""))
    if remote_size != expected_size or remote_digest != expected_sha256:
        raise OSError(
            f"Content-addressed object conflict for s3://{bucket}/{key}: "
            f"size={remote_size}/{expected_size}, "
            f"sha256={remote_digest}/{expected_sha256}"
        )
    return True


def _publish_content_addressed_multipart(
    client: ImmutableS3Client,
    *,
    path: Path,
    bucket: str,
    key: str,
    size: int,
    digest: str,
    cancellation_check: Callable[[], None] | None,
    settings: ImmutableStorageSettings,
) -> None:
    response = client.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        Metadata={"sha256": digest},
    )
    upload_id = response.get("UploadId")
    if not isinstance(upload_id, str) or not upload_id:
        raise OSError("S3 did not return a multipart upload ID")
    completed = False
    try:
        part_size = multipart_part_size(size, settings)
        parts: list[dict[str, int | str]] = []
        with path.open("rb") as stream:
            part_number = 1
            while body := stream.read(part_size):
                if cancellation_check is not None:
                    cancellation_check()
                uploaded = client.upload_part(
                    Bucket=bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=body,
                    ContentLength=len(body),
                )
                etag = uploaded.get("ETag")
                if not isinstance(etag, str) or not etag:
                    raise OSError(f"S3 multipart part {part_number} has no ETag")
                parts.append({"ETag": etag, "PartNumber": part_number})
                part_number += 1
        if len(parts) > settings.multipart_max_parts:
            raise ValueError("CAS multipart upload exceeds the S3 part limit")
        if cancellation_check is not None:
            cancellation_check()
        client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
            IfNoneMatch="*",
        )
        completed = True
    finally:
        if not completed:
            abort_multipart_quietly(
                client,
                bucket=bucket,
                key=key,
                upload_id=upload_id,
            )


def publish_content_addressed_file(
    client: ImmutableS3Client,
    local_path: str | Path,
    *,
    bucket: str,
    organization_id: str,
    cancellation_check: Callable[[], None] | None,
    force_multipart: bool,
    settings: ImmutableStorageSettings,
) -> ContentAddressedUpload:
    """Publish one immutable CAS blob with conditional single/multipart PUT."""

    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"Required local artifact not found: {path}")
    size = path.stat().st_size
    if size > settings.max_object_bytes:
        raise ValueError("CAS publication exceeds the 5 TiB S3 object limit")
    digest = str(sha256_file(path))
    key = content_addressed_blob_key(
        digest,
        organization_id=organization_id,
    )
    if _verify_content_addressed_head(
        client,
        bucket=bucket,
        key=key,
        expected_size=size,
        expected_sha256=digest,
    ):
        return ContentAddressedUpload(key, size, digest, True, 0)

    if cancellation_check is not None:
        cancellation_check()
    if size <= settings.single_put_max_bytes and not force_multipart:
        try:
            with path.open("rb") as stream:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=stream,
                    ContentLength=size,
                    Metadata={"sha256": digest},
                    IfNoneMatch="*",
                )
        except ClientError as error:
            if not is_conditional_write_conflict(error):
                raise
            if not _verify_content_addressed_head(
                client,
                bucket=bucket,
                key=key,
                expected_size=size,
                expected_sha256=digest,
            ):
                raise OSError(
                    f"Concurrent CAS publication did not create s3://{bucket}/{key}"
                ) from error
            return ContentAddressedUpload(key, size, digest, True, 0)
    else:
        for attempt in range(1, settings.multipart_max_attempts + 1):
            try:
                _publish_content_addressed_multipart(
                    client,
                    path=path,
                    bucket=bucket,
                    key=key,
                    size=size,
                    digest=digest,
                    cancellation_check=cancellation_check,
                    settings=settings,
                )
                break
            except ClientError as error:
                if not is_conditional_write_conflict(error):
                    raise
                if _verify_content_addressed_head(
                    client,
                    bucket=bucket,
                    key=key,
                    expected_size=size,
                    expected_sha256=digest,
                ):
                    return ContentAddressedUpload(key, size, digest, True, 0)
                if attempt == settings.multipart_max_attempts:
                    raise OSError(
                        "Concurrent multipart CAS publication did not create "
                        f"s3://{bucket}/{key}"
                    ) from error

    if not _verify_content_addressed_head(
        client,
        bucket=bucket,
        key=key,
        expected_size=size,
        expected_sha256=digest,
    ):
        raise OSError(f"CAS publication disappeared for s3://{bucket}/{key}")
    logger.info(
        "Published immutable CAS blob %s -> s3://%s/%s (%d bytes)",
        path,
        bucket,
        key,
        size,
    )
    return ContentAddressedUpload(key, size, digest, False, size)


def put_verified_bytes(
    client: ImmutableS3Client,
    s3_key: str,
    data: bytes,
    *,
    bucket: str,
    object_info: ObjectInfoReader,
) -> dict[str, int | str | bool]:
    """Publish immutable control bytes or reuse an identical existing object."""

    digest = hashlib.sha256(data).hexdigest()
    existing = object_info(s3_key, bucket)
    if existing is not None:
        metadata = cast(dict[str, str], existing["metadata"])
        if int(cast(int, existing["size"])) != len(data) or metadata.get(
            "sha256"
        ) != digest:
            raise OSError(f"S3 immutable object conflict for s3://{bucket}/{s3_key}")
        return {
            "key": s3_key,
            "size": len(data),
            "sha256": digest,
            "reused": True,
        }
    reused = False
    try:
        client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=data,
            ContentLength=len(data),
            Metadata={"sha256": digest},
            IfNoneMatch="*",
        )
    except ClientError as error:
        if not is_conditional_write_conflict(error):
            raise
        reused = True
    verified = object_info(s3_key, bucket)
    if verified is None:
        raise OSError(f"S3 publication disappeared: {s3_key}")
    metadata = cast(dict[str, str], verified["metadata"])
    if int(cast(int, verified["size"])) != len(data) or metadata.get(
        "sha256"
    ) != digest:
        raise OSError(f"S3 publication verification failed: {s3_key}")
    return {
        "key": s3_key,
        "size": len(data),
        "sha256": digest,
        "reused": reused,
    }


def _copy_empty_object(
    client: ImmutableS3Client,
    source_key: str,
    target_key: str,
    *,
    bucket: str,
    source: ObjectInfo,
    metadata: dict[str, str],
    object_stream: ObjectStreamReader,
) -> bool:
    stream, _size, _content_type = object_stream(source_key, bucket)
    try:
        request: dict[str, Any] = {
            "Bucket": bucket,
            "Key": target_key,
            "Body": stream,
            "ContentLength": 0,
            "Metadata": metadata,
            "IfNoneMatch": "*",
        }
        if source["content_type"]:
            request["ContentType"] = source["content_type"]
        try:
            client.put_object(**request)
        except ClientError as error:
            if not is_conditional_write_conflict(error):
                raise
            return True
    finally:
        stream.close()
    return False


def _copy_multipart_object(
    client: ImmutableS3Client,
    source_key: str,
    target_key: str,
    *,
    bucket: str,
    source_size: int,
    source_etag: str,
    content_type: str | None,
    metadata: dict[str, str],
    settings: ImmutableStorageSettings,
) -> bool:
    creation: dict[str, Any] = {
        "Bucket": bucket,
        "Key": target_key,
        "Metadata": metadata,
    }
    if content_type:
        creation["ContentType"] = content_type
    upload = client.create_multipart_upload(**creation)
    upload_id = str(upload["UploadId"])
    completed = False
    try:
        part_size = multipart_part_size(source_size, settings)
        parts: list[dict[str, int | str]] = []
        for part_number, start in enumerate(
            range(0, source_size, part_size),
            start=1,
        ):
            end = min(start + part_size, source_size) - 1
            copied = client.upload_part_copy(
                Bucket=bucket,
                Key=target_key,
                UploadId=upload_id,
                PartNumber=part_number,
                CopySource={"Bucket": bucket, "Key": source_key},
                CopySourceRange=f"bytes={start}-{end}",
                CopySourceIfMatch=source_etag,
            )
            result = copied.get("CopyPartResult") or {}
            etag = result.get("ETag")
            if not isinstance(etag, str) or not etag:
                raise OSError(f"S3 adoption copy part {part_number} has no ETag")
            parts.append({"ETag": etag, "PartNumber": part_number})
        try:
            client.complete_multipart_upload(
                Bucket=bucket,
                Key=target_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
            )
            completed = True
        except ClientError as error:
            if not is_conditional_write_conflict(error):
                raise
            return True
    finally:
        if not completed:
            abort_multipart_quietly(
                client,
                bucket=bucket,
                key=target_key,
                upload_id=upload_id,
            )
    return False


def copy_verified_object(
    client: ImmutableS3Client,
    source_key: str,
    target_key: str,
    *,
    bucket: str,
    object_info: ObjectInfoReader,
    object_stream: ObjectStreamReader,
    settings: ImmutableStorageSettings,
) -> dict[str, int | str | bool]:
    """Copy one object idempotently and bind the target to its source identity."""

    if source_key == target_key:
        raise ValueError("Source and target object keys must differ")
    source = object_info(source_key, bucket)
    if source is None:
        raise FileNotFoundError(f"Missing S3 adoption source: {source_key}")
    source_size = int(cast(int, source["size"]))
    if source_size < 0:
        raise OSError(f"S3 adoption source has an invalid size: {source_key}")
    if source_size > settings.max_object_bytes:
        raise ValueError("S3 adoption source exceeds the 5 TiB object limit")
    source_etag = str(source["etag"])
    if not source_etag:
        raise OSError(f"S3 adoption source has no ETag: {source_key}")
    source_metadata = cast(dict[str, str], source["metadata"])
    source_key_digest = hashlib.sha256(source_key.encode()).hexdigest()
    adoption_metadata = {
        **source_metadata,
        "droneai-adoption-source-key-sha256": source_key_digest,
        "droneai-adoption-source-etag": source_etag,
    }

    def matches(value: ObjectInfo) -> bool:
        metadata = cast(dict[str, str], value["metadata"])
        return (
            int(cast(int, value["size"])) == source_size
            and metadata.get("droneai-adoption-source-key-sha256")
            == source_key_digest
            and metadata.get("droneai-adoption-source-etag") == source_etag
            and (
                not source_metadata.get("sha256")
                or metadata.get("sha256") == source_metadata["sha256"]
            )
        )

    existing = object_info(target_key, bucket)
    if existing is not None:
        if not matches(existing):
            raise OSError(
                f"S3 adoption target conflicts with its source: {target_key}"
            )
        return {
            "key": target_key,
            "size": source_size,
            "etag": source_etag,
            "reused": True,
        }

    if source_size == 0:
        reused = _copy_empty_object(
            client,
            source_key,
            target_key,
            bucket=bucket,
            source=source,
            metadata=adoption_metadata,
            object_stream=object_stream,
        )
    else:
        reused = _copy_multipart_object(
            client,
            source_key,
            target_key,
            bucket=bucket,
            source_size=source_size,
            source_etag=source_etag,
            content_type=cast(str | None, source["content_type"]),
            metadata=adoption_metadata,
            settings=settings,
        )
    verified = object_info(target_key, bucket)
    if verified is None or not matches(verified):
        raise OSError(f"S3 adoption copy verification failed: {target_key}")
    return {
        "key": target_key,
        "size": source_size,
        "etag": source_etag,
        "reused": reused,
    }
