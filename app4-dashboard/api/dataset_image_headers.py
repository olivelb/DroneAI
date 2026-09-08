"""Lightweight format admission before an uploaded image becomes ready."""
from pathlib import Path

from shared import storage

from .dataset_upload_contracts import IMAGE_SUFFIXES


def image_header_matches(filename: str, header: bytes) -> bool:
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if suffix in {".tif", ".tiff"}:
        return header.startswith((b"II*\x00", b"MM\x00*", b"II+\x00\x08\x00\x00\x00", b"MM\x00+\x00\x08\x00\x00"))
    return suffix not in IMAGE_SUFFIXES


def uploaded_image_header_matches(key: str, filename: str, size: int, etag: str) -> bool:
    if Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
        return True
    header = storage.get_object_prefix(key, expected_size=size, etag=etag)
    return image_header_matches(filename, header)
