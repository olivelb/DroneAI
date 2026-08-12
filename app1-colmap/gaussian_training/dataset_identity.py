"""Stable content identity for a COLMAP training dataset."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


IDENTITY_VERSION = "droneai-colmap-dataset-v3"
SPARSE_FILENAMES = ("cameras.bin", "images.bin", "points3D.bin")
IMAGE_REGIONS_FILENAME = "image_regions.tsv"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
SAMPLE_BYTES = 64 * 1024


@dataclass(frozen=True)
class DatasetIdentity:
    fingerprint: str
    image_count: int
    sparse_sha256: str
    image_inventory_sha256: str
    image_regions_sha256: str | None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sample_file(path: Path) -> str:
    """Hash deterministic first/middle/last samples without loading the image."""

    size = path.stat().st_size
    offsets = {0}
    if size > SAMPLE_BYTES:
        offsets.add(max(0, size // 2 - SAMPLE_BYTES // 2))
        offsets.add(max(0, size - SAMPLE_BYTES))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for offset in sorted(offsets):
            stream.seek(offset)
            digest.update(offset.to_bytes(8, "little"))
            digest.update(stream.read(SAMPLE_BYTES))
    return digest.hexdigest()


def _find_sparse(data_path: Path) -> Path:
    for candidate in (data_path / "sparse" / "0", data_path / "sparse"):
        if all((candidate / name).is_file() for name in SPARSE_FILENAMES):
            return candidate
    raise FileNotFoundError(f"COLMAP sparse model not found under {data_path}")


def compute_dataset_identity(data_path: str | Path) -> DatasetIdentity:
    """Fingerprint sparse geometry and a relocation-stable image inventory.

    Image mtimes are intentionally not hashed: S3 downloads change local mtimes
    across pods. Content samples, paths and sizes still invalidate modified
    imagery while keeping distributed resume stable.
    """

    root = Path(data_path).resolve()
    sparse = _find_sparse(root)
    images_dir = root / "images"
    if not images_dir.is_dir():
        raise FileNotFoundError(f"COLMAP images directory not found: {images_dir}")

    sparse_digest = hashlib.sha256()
    for name in SPARSE_FILENAMES:
        path = sparse / name
        sparse_digest.update(name.encode("utf-8"))
        sparse_digest.update(bytes.fromhex(_hash_file(path)))
    image_regions_path = root / IMAGE_REGIONS_FILENAME
    image_regions_sha256 = (
        _hash_file(image_regions_path)
        if image_regions_path.is_file()
        else None
    )
    sparse_digest.update(IMAGE_REGIONS_FILENAME.encode("utf-8"))
    sparse_digest.update(
        bytes.fromhex(image_regions_sha256)
        if image_regions_sha256 is not None
        else b"absent"
    )

    inventory = []
    for path in sorted(
        (
            item
            for item in images_dir.rglob("*")
            if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=lambda item: item.relative_to(images_dir).as_posix(),
    ):
        stat = path.stat()
        inventory.append(
            {
                "path": path.relative_to(images_dir).as_posix(),
                "bytes": stat.st_size,
                "sample_sha256": _sample_file(path),
            }
        )
    if not inventory:
        raise ValueError(f"COLMAP dataset has no supported images: {images_dir}")

    inventory_bytes = json.dumps(
        inventory,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    inventory_digest = hashlib.sha256(inventory_bytes).hexdigest()
    combined = hashlib.sha256()
    combined.update(IDENTITY_VERSION.encode("ascii"))
    combined.update(sparse_digest.digest())
    combined.update(bytes.fromhex(inventory_digest))
    return DatasetIdentity(
        fingerprint=f"{IDENTITY_VERSION}:sha256:{combined.hexdigest()}",
        image_count=len(inventory),
        sparse_sha256=sparse_digest.hexdigest(),
        image_inventory_sha256=inventory_digest,
        image_regions_sha256=image_regions_sha256,
    )
