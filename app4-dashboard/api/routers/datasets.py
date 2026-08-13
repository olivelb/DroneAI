"""Dataset browsing, preview, download, upload, and deletion routes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict, cast

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import and_, or_

from shared import storage
from shared.database import Dataset, Mission, get_session
from shared.organization_saas import record_storage_release

from .. import dataset_uploads
from ..dataset_access import (
    authorize_storage_path,
    dataset_query,
    get_owned_dataset,
    normalize_storage_path,
)
from ..image_preview import PreviewTooLargeError, render_preview
from ..security import (
    Principal,
    bind_tenant_context,
    require_admin,
    require_authenticated,
    require_operator,
)

router = APIRouter(
    tags=["datasets"],
    dependencies=[Depends(bind_tenant_context)],
)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_INLINE_PREVIEW_BYTES = 64 * 1024 * 1024


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


def sanitize_dataset_name(value: str, *, replacement: str = "") -> str:
    pattern = r"[^a-zA-Z0-9_\-.]" if not replacement else r"[^a-zA-Z0-9_-]"
    return re.sub(pattern, replacement, value.strip())


def image_count(keys: list[str]) -> int:
    return sum(1 for key in keys if key.lower().endswith(IMAGE_SUFFIXES))


@router.get("/browse")
def browse_path(
    principal: Annotated[Principal, Depends(require_authenticated)],
    prefix: str = "datasets/",
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> list[BrowseItem]:
    try:
        normalized = normalize_storage_path(prefix)
        with get_session() as session:
            if normalized == "":
                datasets = dataset_query(
                    session,
                    principal,
                    requested_owner=owner_subject,
                    action="browse_root",
                ).all()
                return [
                    {
                        "name": "datasets",
                        "path": "datasets",
                        "is_dir": True,
                        "image_count": sum(
                            int(item.image_count)
                            for item in datasets
                        ),
                    }
                ] if datasets else []
            if normalized == "datasets":
                datasets = dataset_query(
                    session,
                    principal,
                    requested_owner=owner_subject,
                    action="browse_datasets",
                ).order_by(Dataset.name).all()
                return [
                    {
                        "name": str(dataset.name),
                        "path": str(dataset.prefix),
                        "is_dir": True,
                        "image_count": int(dataset.image_count),
                    }
                    for dataset in datasets
                ]
            authorized_prefix = authorize_storage_path(
                session,
                normalized,
                principal,
                requested_owner=owner_subject,
                action="browse_storage",
            )
        storage_prefix = f"{authorized_prefix}/"
        items: list[BrowseItem] = []
        for key in storage.list_objects(storage_prefix, delimiter="/"):
            if key.endswith("/") and key != storage_prefix:
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
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Object storage browse failed: {error}",
        ) from error


@router.get("/preview/{s3_key:path}")
def preview_image(
    s3_key: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    max_size: int = 4096,
    colormap: str = "",
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> StreamingResponse:
    with get_session() as session:
        authorized_key = authorize_storage_path(
            session,
            s3_key,
            principal,
            requested_owner=owner_subject,
            action="preview_storage",
        )
    if not storage.file_exists(authorized_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    try:
        preview_key = authorized_key
        if authorized_key.lower().endswith((".tif", ".tiff")):
            path = Path(authorized_key)
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
            headers={"Cache-Control": "private, max-age=3600"},
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
def list_datasets(
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DatasetItem]:
    try:
        with get_session() as session:
            datasets = dataset_query(
                session,
                principal,
                requested_owner=owner_subject,
                action="list",
            ).order_by(Dataset.name).offset(offset).limit(limit).all()
            return [
                {
                    "name": str(dataset.name),
                    "path": str(dataset.prefix),
                    "image_count": int(dataset.image_count),
                }
                for dataset in datasets
            ]
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Dataset listing unavailable: {error}",
        ) from error


@router.delete("/datasets/{name}")
def delete_dataset(
    name: str,
    principal: Annotated[Principal, Depends(require_admin)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> DatasetDeleteResponse:
    safe_name = sanitize_dataset_name(name)
    if not safe_name or safe_name != name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid dataset name",
        )
    with get_session() as session:
        dataset = get_owned_dataset(
            session,
            principal,
            name=safe_name,
            requested_owner=owner_subject,
            action="delete",
            statuses=("ready", "deleting", "deletion_failed"),
            for_update=True,
        )
        referenced = session.query(Mission.id).filter(
            or_(
                Mission.dataset_id == dataset.id,
                and_(
                    Mission.dataset_id.is_(None),
                    Mission.organization_id == dataset.organization_id,
                    Mission.owner_subject == dataset.owner_subject,
                    Mission.input_dataset == dataset.prefix,
                ),
            )
        ).first()
        if referenced is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Dataset is referenced by a mission and cannot be deleted",
            )
        mutable_dataset = cast(Any, dataset)
        mutable_dataset.status = "deleting"
        mutable_dataset.deletion_requested_at = datetime.now(UTC)
        mutable_dataset.deleted_at = None
        dataset_prefix = str(dataset.prefix)
    try:
        deleted = storage.delete_prefix(f"{dataset_prefix}/")
    except Exception as error:
        with get_session() as session:
            dataset = get_owned_dataset(
                session,
                principal,
                name=safe_name,
                requested_owner=owner_subject,
                action="delete_failure",
                statuses=("deleting", "deletion_failed"),
                for_update=True,
            )
            cast(Any, dataset).status = "deletion_failed"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Dataset deletion failed: {error}",
        ) from error
    with get_session() as session:
        dataset = get_owned_dataset(
            session,
            principal,
            name=safe_name,
            requested_owner=owner_subject,
            action="delete_complete",
            statuses=("deleting",),
            for_update=True,
        )
        mutable_dataset = cast(Any, dataset)
        mutable_dataset.status = "deleted"
        mutable_dataset.deleted_at = datetime.now(UTC)
        record_storage_release(
            session,
            organization_id=str(dataset.organization_id),
            resource_type="dataset",
            resource_id=str(dataset.dataset_id),
            released_bytes=int(dataset.total_bytes),
            actor_subject=principal.subject,
            idempotency_key=f"storage-released:dataset:{dataset.dataset_id}",
            details={"objects_deleted": deleted},
        )
    return {
        "status": "success",
        "message": f"Dataset '{safe_name}' deleted.",
        "objects_deleted": deleted,
    }


@router.get("/files/{s3_key:path}")
def get_file(
    s3_key: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    with get_session() as session:
        authorized_key = authorize_storage_path(
            session,
            s3_key,
            principal,
            requested_owner=owner_subject,
            action="download_storage",
        )
    if not storage.file_exists(authorized_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return RedirectResponse(
        url=storage.get_presigned_url(authorized_key),
        status_code=302,
    )


@router.post(
    "/datasets/upload",
    dependencies=[Depends(require_operator)],
)
def upload_dataset_batch() -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Not found",
    )


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
