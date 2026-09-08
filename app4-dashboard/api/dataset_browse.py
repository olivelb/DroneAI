"""Browse immutable upload metadata, with one legacy recursive listing fallback."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypedDict

from sqlalchemy import func

from shared.database import Dataset, DatasetUploadFile

from .dataset_access import dataset_query
from .dataset_upload_contracts import IMAGE_SUFFIXES
from .security import Principal


class BrowseItem(TypedDict):
    name: str
    path: str
    is_dir: bool
    image_count: int


def catalog_browse_keys(
    session: Any, prefix: str, principal: Principal, requested_owner: str | None,
) -> list[str] | None:
    """Return owned immutable upload keys; None selects the legacy fallback."""
    parts = prefix.split("/")
    ancestors = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    dataset = (dataset_query(session, principal, requested_owner=requested_owner,
                             action="browse_catalog_files")
               .filter(Dataset.prefix.in_(ancestors))
               .order_by(func.length(Dataset.prefix).desc()).first())
    if dataset is None or dataset.upload_session_id is None:
        return None
    storage_prefix = f"{prefix}/"
    keys = [str(row[0]) for row in session.query(DatasetUploadFile.s3_key).filter(
        DatasetUploadFile.upload_session_id == dataset.upload_session_id,
        DatasetUploadFile.status == "completed",
        DatasetUploadFile.s3_key.startswith(storage_prefix, autoescape=True),
    ).all()]
    manifest = str(dataset.manifest_s3_key)
    if manifest.startswith(storage_prefix):
        keys.append(manifest)
    return keys


def browse_items_from_keys(prefix: str, keys: Iterable[str]) -> list[BrowseItem]:
    """Group direct entries and descendant image counts in one pass."""
    storage_prefix = f"{prefix}/"
    items: dict[str, BrowseItem] = {}
    for key in keys:
        if not key.startswith(storage_prefix):
            raise ValueError("Storage returned a key outside the authorized prefix")
        relative = key[len(storage_prefix):]
        if not relative:
            continue
        name, separator, remainder = relative.partition("/")
        path = f"{storage_prefix}{name}"
        is_dir = bool(separator)
        item_key = path + ("/" if is_dir else "")
        item = items.setdefault(item_key, {"name": name, "path": path,
                                          "is_dir": is_dir, "image_count": 0})
        if is_dir and remainder and any(key.lower().endswith(suffix) for suffix in IMAGE_SUFFIXES):
            item["image_count"] += 1
    return sorted(items.values(), key=lambda item: (not item["is_dir"], item["name"]))
