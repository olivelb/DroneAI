"""Dataset browsing, preview, download, upload, and deletion routes."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse

from shared import storage

from ..image_preview import render_preview


router = APIRouter(tags=["datasets"])
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def sanitize_dataset_name(value: str, *, replacement: str = "") -> str:
    pattern = r"[^a-zA-Z0-9_\-.]" if not replacement else r"[^a-zA-Z0-9_-]"
    return re.sub(pattern, replacement, value.strip())


def image_count(keys: list[str]) -> int:
    return sum(1 for key in keys if key.lower().endswith(IMAGE_SUFFIXES))


@router.get("/browse")
def browse_path(prefix: str = "datasets/"):
    try:
        items = []
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
        return {"error": str(error)}


@router.get("/preview/{s3_key:path}")
def preview_image(
    s3_key: str,
    max_size: int = 4096,
    colormap: str = "",
):
    if not storage.file_exists(s3_key):
        return {"error": "File not found"}
    try:
        stream, _, _ = storage.get_object_stream(s3_key)
        try:
            raw = stream.read()
        finally:
            stream.close()
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
    except Exception as error:
        return {"error": f"Preview generation failed: {error}"}


@router.get("/datasets")
def list_datasets():
    try:
        results = []
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
    except Exception:
        return []


@router.delete("/datasets/{name}")
def delete_dataset(name: str):
    safe_name = sanitize_dataset_name(name)
    if not safe_name or safe_name != name.strip():
        return {"status": "error", "message": "Invalid dataset name"}
    try:
        deleted = storage.delete_prefix(f"datasets/{safe_name}/")
        return {
            "status": "success",
            "message": f"Dataset '{safe_name}' deleted.",
            "objects_deleted": deleted,
        }
    except Exception as error:
        return {"status": "error", "message": str(error)}


@router.get("/files/{s3_key:path}")
def get_file(s3_key: str):
    if not storage.file_exists(s3_key):
        return {"error": "File not found"}
    return RedirectResponse(
        url=storage.get_presigned_url(s3_key),
        status_code=302,
    )


@router.post("/datasets/upload-file")
async def upload_single_file(
    dataset_name: str = Query(...),
    file: UploadFile = File(...),
):
    safe_name = sanitize_dataset_name(dataset_name, replacement="_")
    if not safe_name:
        return {"error": "Invalid dataset name"}
    filename = Path(file.filename or "file").name
    s3_key = f"datasets/{safe_name}/{filename}"
    try:
        storage.put_object(s3_key, file.file)
        return {"name": filename, "s3_key": s3_key, "status": "ok"}
    except Exception as error:
        return {
            "name": filename,
            "status": "error",
            "error": str(error),
        }


@router.post("/datasets/upload")
async def upload_dataset_batch(
    dataset_name: str = Query(...),
    files: list[UploadFile] = File(...),
):
    safe_name = sanitize_dataset_name(dataset_name, replacement="_")
    if not safe_name:
        return {"error": "Invalid dataset name"}
    result = {
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
