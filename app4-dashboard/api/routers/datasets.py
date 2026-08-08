"""Dataset browsing, preview, download, upload, and deletion routes."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Annotated, NotRequired, TypedDict

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse, StreamingResponse

from shared import storage
from shared.database import get_session

from .. import dataset_uploads
from ..image_preview import PreviewTooLargeError, render_preview
from ..security import (
    Principal,
    require_admin,
    require_authenticated,
    require_operator,
    upload_limits,
)

router = APIRouter(
    tags=["datasets"],
    dependencies=[Depends(require_authenticated)],
)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_INLINE_PREVIEW_BYTES = 64 * 1024 * 1024
DATASET_SUFFIXES = dataset_uploads.DATASET_SUFFIXES


class BrowseItem(TypedDict):
    name: str
    path: str
    is_dir: bool
    image_count: int


class DatasetItem(TypedDict):
    name: str
    path: str
    image_count: int


class DatasetDeleteResponse(TypedDict):
    status: str
    message: str
    objects_deleted: int


class UploadFileResult(TypedDict):
    name: str
    status: str
    s3_key: NotRequired[str]
    error: NotRequired[str]


class UploadBatchResponse(TypedDict):
    upload_id: str
    dataset: str
    total: int
    completed: int
    failed: int
    status: str
    files: list[UploadFileResult]


def sanitize_dataset_name(value: str, *, replacement: str = "") -> str:
    pattern = r"[^a-zA-Z0-9_\-.]" if not replacement else r"[^a-zA-Z0-9_-]"
    return re.sub(pattern, replacement, value.strip())


def image_count(keys: list[str]) -> int:
    return sum(1 for key in keys if key.lower().endswith(IMAGE_SUFFIXES))


def upload_size(upload: UploadFile) -> int:
    if isinstance(upload.size, int):
        return upload.size
    position = upload.file.tell()
    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(position)
    return size


def validate_uploads(files: list[UploadFile]) -> None:
    limits = upload_limits()
    if len(files) > limits["max_files"]:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Upload contains too many files",
        )
    total_size = 0
    for upload in files:
        filename = Path(upload.filename or "").name
        if not filename or Path(filename).suffix.lower() not in DATASET_SUFFIXES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported dataset file: {filename or '<empty>'}",
            )
        size = upload_size(upload)
        if size > limits["max_file_bytes"]:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Dataset file exceeds quota: {filename}",
            )
        total_size += size
        if total_size > limits["max_batch_bytes"]:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Upload batch exceeds quota",
            )


@router.get("/browse")
def browse_path(prefix: str = "datasets/") -> list[BrowseItem]:
    try:
        items: list[BrowseItem] = []
        for key in storage.list_objects(prefix, delimiter="/"):
            if key.endswith("/") and key != prefix:
                items.append(
                    {
                        "name": key.rstrip("/").split("/")[-1],
                        "path": key.rstrip("/"),
                        "is_dir": True,
                        "image_count": image_count(storage.list_objects(key)),
                    }
                )
            elif not key.endswith("/"):
                items.append(
                    {
                        "name": key.split("/")[-1],
                        "path": key,
                        "is_dir": False,
                        "image_count": 0,
                    }
                )
        return sorted(
            items,
            key=lambda item: (not item["is_dir"], item["name"]),
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Object storage browse failed: {error}",
        ) from error


@router.get("/preview/{s3_key:path}")
def preview_image(
    s3_key: str,
    max_size: int = 4096,
    colormap: str = "",
) -> StreamingResponse:
    if not storage.file_exists(s3_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    try:
        preview_key = s3_key
        if s3_key.lower().endswith((".tif", ".tiff")):
            path = Path(s3_key)
            preview_key = str(path.with_name(f"{path.stem}.preview.webp")).replace("\\", "/")
            if not storage.file_exists(preview_key):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=("Large raster preview is not available; use the COG map tile endpoint"),
                )
        stream, content_length, _ = storage.get_object_stream(preview_key)
        if content_length > MAX_INLINE_PREVIEW_BYTES:
            stream.close()
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Preview source is too large",
            )
        try:
            raw = stream.read(MAX_INLINE_PREVIEW_BYTES + 1)
        finally:
            stream.close()
        if len(raw) > MAX_INLINE_PREVIEW_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Preview source is too large",
            )
        output = render_preview(
            raw,
            max_size=max_size,
            colormap=colormap,
        )
        return StreamingResponse(
            output,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except HTTPException:
        raise
    except PreviewTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Preview generation failed: {error}",
        ) from error


@router.get("/datasets")
def list_datasets() -> list[DatasetItem]:
    try:
        results: list[DatasetItem] = []
        for prefix in storage.list_objects("datasets/", delimiter="/"):
            if not prefix.endswith("/"):
                continue
            count = image_count(storage.list_objects(prefix))
            if count:
                results.append(
                    {
                        "name": prefix.rstrip("/").split("/")[-1],
                        "path": prefix.rstrip("/"),
                        "image_count": count,
                    }
                )
        return results
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Dataset listing unavailable: {error}",
        ) from error


@router.delete(
    "/datasets/{name}",
    dependencies=[Depends(require_admin)],
)
def delete_dataset(name: str) -> DatasetDeleteResponse:
    safe_name = sanitize_dataset_name(name)
    if not safe_name or safe_name != name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid dataset name",
        )
    try:
        deleted = storage.delete_prefix(f"datasets/{safe_name}/")
        return {
            "status": "success",
            "message": f"Dataset '{safe_name}' deleted.",
            "objects_deleted": deleted,
        }
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Dataset deletion failed: {error}",
        ) from error


@router.get("/files/{s3_key:path}")
def get_file(s3_key: str) -> RedirectResponse:
    if not storage.file_exists(s3_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return RedirectResponse(
        url=storage.get_presigned_url(s3_key),
        status_code=302,
    )


@router.post(
    "/datasets/upload",
    dependencies=[Depends(require_operator)],
)
def upload_dataset_batch(
    dataset_name: Annotated[str, Query()],
    files: Annotated[list[UploadFile], File()],
) -> UploadBatchResponse:
    validate_uploads(files)
    safe_name = sanitize_dataset_name(dataset_name, replacement="_")
    if not safe_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid dataset name",
        )
    result: UploadBatchResponse = {
        "upload_id": uuid.uuid4().hex[:12],
        "dataset": safe_name,
        "total": len(files),
        "completed": 0,
        "failed": 0,
        "status": "uploading",
        "files": [],
    }
    for index, upload in enumerate(files):
        filename = Path(upload.filename or f"file_{index}").name
        s3_key = f"datasets/{safe_name}/{filename}"
        try:
            storage.put_object(s3_key, upload.file)
            result["completed"] += 1
            result["files"].append(
                {
                    "name": filename,
                    "s3_key": s3_key,
                    "status": "ok",
                }
            )
        except Exception as error:
            result["failed"] += 1
            result["files"].append(
                {
                    "name": filename,
                    "status": "error",
                    "error": str(error),
                }
            )
    result["status"] = "done" if not result["failed"] else "partial"
    return result


@router.post(
    "/datasets/upload-sessions",
    status_code=status.HTTP_201_CREATED,
)
def create_direct_upload_session(
    request: dataset_uploads.UploadSessionRequest,
    principal: Annotated[Principal, Depends(require_operator)],
) -> dataset_uploads.UploadSessionResponse:
    with get_session() as session:
        return dataset_uploads.create_upload_session(session, request, principal)


@router.post(
    "/datasets/upload-sessions/{session_id}/files/{file_id}/parts/{part_number}",
)
def create_direct_upload_part_url(
    session_id: str,
    file_id: str,
    part_number: int,
    principal: Annotated[Principal, Depends(require_operator)],
) -> dataset_uploads.UploadPartUrlResponse:
    with get_session() as session:
        return dataset_uploads.create_part_url(
            session,
            session_id,
            file_id,
            part_number,
            principal,
        )


@router.post(
    "/datasets/upload-sessions/{session_id}/files/{file_id}/complete",
)
def complete_direct_upload_file(
    session_id: str,
    file_id: str,
    request: dataset_uploads.CompleteUploadFileRequest,
    principal: Annotated[Principal, Depends(require_operator)],
) -> dataset_uploads.UploadFileCompleteResponse:
    with get_session() as session:
        return dataset_uploads.complete_upload_file(
            session,
            session_id,
            file_id,
            request,
            principal,
        )


@router.post("/datasets/upload-sessions/{session_id}/complete")
def complete_direct_upload_session(
    session_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
) -> dataset_uploads.UploadFinalizeResponse:
    with get_session() as session:
        return dataset_uploads.finalize_upload_session(
            session,
            session_id,
            principal,
        )


@router.delete("/datasets/upload-sessions/{session_id}")
def abort_direct_upload_session(
    session_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
) -> dict[str, str]:
    with get_session() as session:
        return dataset_uploads.abort_upload_session(
            session,
            session_id,
            principal,
        )
