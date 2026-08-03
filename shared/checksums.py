"""Streaming checksums shared by artifact and provenance workflows."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(
    path: str | Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> str:
    """Return the SHA-256 digest of *path* without loading it into memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
