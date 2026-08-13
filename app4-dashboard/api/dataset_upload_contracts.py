"""HTTP contracts and pure validation helpers for dataset uploads."""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict, cast

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, field_validator

from shared.database import DatasetUploadFile, DatasetUploadSession
from shared.tenancy import dataset_prefix

from .security import upload_limits

DATASET_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".mrk",
    ".nav",
    ".obs",
    ".bin",
    ".rtk",
    ".txt",
    ".csv",
}
MIN_PART_BYTES = 5 * 1024 * 1024
MAX_PART_BYTES = 512 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
ACTIVE_UPLOAD_STATUSES = ("initializing", "uploading", "finalizing", "failed")


class UploadSessionFileRequest(BaseModel):  # type: ignore[misc]
    name: str = Field(min_length=1, max_length=512)
    size: int = Field(gt=0)
    content_type: str = Field(default="application/octet-stream", max_length=256)

    @field_validator("name")  # type: ignore[untyped-decorator]
    @classmethod
    def filename_only(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("name must be a plain filename")
        return value

    @field_validator("content_type")  # type: ignore[untyped-decorator]
    @classmethod
    def safe_content_type(cls, value: str) -> str:
        if any(character in value for character in "\r\n\x00"):
            raise ValueError("content_type contains control characters")
        return value


class UploadSessionRequest(BaseModel):  # type: ignore[misc]
    dataset_name: str = Field(min_length=1, max_length=256)
    files: list[UploadSessionFileRequest] = Field(min_length=1)


class CompletedPartRequest(BaseModel):  # type: ignore[misc]
    part_number: int = Field(ge=1, le=MAX_MULTIPART_PARTS)
    etag: str = Field(min_length=1, max_length=256)

    @field_validator("etag")  # type: ignore[untyped-decorator]
    @classmethod
    def safe_etag(cls, value: str) -> str:
        if any(character in value for character in "\r\n\x00"):
            raise ValueError("etag contains control characters")
        return value


class CompleteUploadFileRequest(BaseModel):  # type: ignore[misc]
    parts: list[CompletedPartRequest] = Field(
        min_length=1,
        max_length=MAX_MULTIPART_PARTS,
    )


class UploadFileDescriptor(TypedDict):
    file_id: str
    name: str
    size: int
    s3_key: str
    total_parts: int
    status: str


class UploadSessionResponse(TypedDict):
    session_id: str
    dataset: str
    status: str
    total: int
    total_bytes: int
    part_size: int
    expires_at: str
    files: list[UploadFileDescriptor]


class UploadPartUrlResponse(TypedDict):
    method: str
    url: str
    expires_in: int
    part_number: int
    expected_size: int


class UploadFileCompleteResponse(TypedDict):
    file_id: str
    name: str
    s3_key: str
    size: int
    etag: str
    status: str


class UploadFinalizeResponse(TypedDict):
    upload_id: str
    dataset: str
    total: int
    completed: int
    failed: int
    status: str
    manifest_s3_key: str


def sanitize_dataset_name(value: str) -> str:
    return "".join(
        (
            character
            if character.isascii() and (character.isalnum() or character in "_-")
            else "_"
        )
        for character in value.strip()
    )


def configured_part_size(max_file_size: int) -> int:
    raw = os.getenv("DRONEAI_UPLOAD_PART_BYTES", str(16 * 1024 * 1024))
    try:
        requested = int(raw)
    except ValueError as error:
        raise RuntimeError("DRONEAI_UPLOAD_PART_BYTES must be an integer") from error
    if not MIN_PART_BYTES <= requested <= MAX_PART_BYTES:
        raise RuntimeError(
            "DRONEAI_UPLOAD_PART_BYTES must be between 5 MiB and 512 MiB"
        )
    required = math.ceil(max_file_size / MAX_MULTIPART_PARTS)
    mebibyte = 1024 * 1024
    calculated = max(requested, math.ceil(required / mebibyte) * mebibyte)
    if calculated > MAX_PART_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File is too large for the configured multipart limits",
        )
    return calculated


def session_lifetime() -> timedelta:
    try:
        seconds = int(os.getenv("DRONEAI_UPLOAD_SESSION_SECONDS", "86400"))
    except ValueError as error:
        raise RuntimeError(
            "DRONEAI_UPLOAD_SESSION_SECONDS must be an integer"
        ) from error
    if not 900 <= seconds <= 7 * 86400:
        raise RuntimeError(
            "DRONEAI_UPLOAD_SESSION_SECONDS must be between 900 and 604800"
        )
    return timedelta(seconds=seconds)


def part_url_lifetime() -> int:
    try:
        seconds = int(os.getenv("DRONEAI_UPLOAD_PART_URL_SECONDS", "900"))
    except ValueError as error:
        raise RuntimeError(
            "DRONEAI_UPLOAD_PART_URL_SECONDS must be an integer"
        ) from error
    if not 60 <= seconds <= 3600:
        raise RuntimeError(
            "DRONEAI_UPLOAD_PART_URL_SECONDS must be between 60 and 3600"
        )
    return seconds


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def validate_request(request: UploadSessionRequest) -> tuple[str, int]:
    limits = upload_limits()
    if len(request.files) > limits["max_files"]:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Upload contains too many files",
        )
    safe_name = sanitize_dataset_name(request.dataset_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")
    total_size = 0
    filenames: set[str] = set()
    for item in request.files:
        if Path(item.name).suffix.lower() not in DATASET_SUFFIXES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported dataset file: {item.name}",
            )
        if item.name in filenames:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate filename: {item.name}",
            )
        filenames.add(item.name)
        if item.size > limits["max_file_bytes"]:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Dataset file exceeds quota: {item.name}",
            )
        total_size += item.size
        if total_size > limits["max_batch_bytes"]:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Upload batch exceeds quota",
            )
    return safe_name, total_size


def serialize_session(record: DatasetUploadSession) -> UploadSessionResponse:
    part_size = int(record.part_size)
    files = sorted(
        (item for item in record.files if item.status == "uploading"),
        key=lambda item: item.filename,
    )
    return {
        "session_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "status": str(record.status),
        "total": int(record.file_count),
        "total_bytes": int(record.total_bytes),
        "part_size": part_size,
        "expires_at": aware(cast(datetime, record.expires_at)).isoformat(),
        "files": [
            {
                "file_id": str(item.file_id),
                "name": str(item.filename),
                "size": int(item.size_bytes),
                "s3_key": str(item.s3_key),
                "total_parts": math.ceil(int(item.size_bytes) / part_size),
                "status": str(item.status),
            }
            for item in files
        ],
    }


def matching_upload_request(
    record: DatasetUploadSession,
    request: UploadSessionRequest,
) -> bool:
    requested = sorted(
        (item.name, int(item.size), item.content_type or "application/octet-stream")
        for item in request.files
    )
    persisted = sorted(
        (str(item.filename), int(item.size_bytes), str(item.content_type))
        for item in record.files
    )
    return requested == persisted


def completed_file_response(
    file_record: DatasetUploadFile,
) -> UploadFileCompleteResponse:
    return {
        "file_id": str(file_record.file_id),
        "name": str(file_record.filename),
        "s3_key": str(file_record.s3_key),
        "size": int(file_record.size_bytes),
        "etag": str(file_record.etag or ""),
        "status": "completed",
    }


def normalized_parts(
    request: CompleteUploadFileRequest,
    expected: int,
) -> list[dict[str, int | str]]:
    ordered = sorted(request.parts, key=lambda part: part.part_number)
    if [part.part_number for part in ordered] != list(range(1, expected + 1)):
        raise HTTPException(
            status_code=400,
            detail="Completed parts must contain every part exactly once",
        )
    return [{"part_number": part.part_number, "etag": part.etag} for part in ordered]


def stored_s3_parts(file_record: DatasetUploadFile) -> list[dict[str, int | str]]:
    raw_parts = file_record.completed_parts
    if not isinstance(raw_parts, list) or not raw_parts:
        raise RuntimeError("Completing upload has no durable multipart part list")
    return [
        {
            "PartNumber": int(cast(dict[str, int | str], part)["part_number"]),
            "ETag": str(cast(dict[str, int | str], part)["etag"]),
        }
        for part in raw_parts
    ]


def object_identity(
    record: DatasetUploadSession,
    file_record: DatasetUploadFile,
    info: dict[str, int | str | dict[str, str]],
) -> tuple[bool, bool]:
    metadata = cast(dict[str, str], info["metadata"])
    session_matches = metadata.get("droneai-upload-session") == str(record.session_id)
    persisted_file_id = metadata.get("droneai-upload-file")
    file_matches = persisted_file_id in {None, "", str(file_record.file_id)}
    expected_size_matches = metadata.get("expected-size") == str(file_record.size_bytes)
    owned = session_matches and file_matches
    valid = (
        owned
        and expected_size_matches
        and int(cast(int | str, info["size"])) == int(file_record.size_bytes)
    )
    return owned, valid


def record_dataset_prefix(record: DatasetUploadSession) -> str:
    """Recover the durable prefix from file intent across v1/v2 layouts."""

    first_file = next(iter(record.files), None)
    if first_file is not None:
        return str(first_file.s3_key).rsplit("/", 1)[0]
    return cast(
        str,
        dataset_prefix(str(record.organization_id), str(record.dataset_name)),
    )


def finalize_response(record: DatasetUploadSession) -> UploadFinalizeResponse:
    return {
        "upload_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "total": int(record.file_count),
        "completed": int(record.file_count),
        "failed": 0,
        "status": "done",
        "manifest_s3_key": f"{record_dataset_prefix(record)}/dataset-manifest.json",
    }
