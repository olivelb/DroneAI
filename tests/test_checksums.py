from __future__ import annotations

import hashlib

import pytest

from shared.checksums import sha256_file


def test_sha256_file_streams_with_configurable_chunks(tmp_path) -> None:
    payload = b"droneai-artifact" * 257
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(payload)

    assert sha256_file(artifact, chunk_size=7) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_rejects_non_positive_chunk_size(tmp_path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"payload")

    with pytest.raises(ValueError, match="chunk_size"):
        sha256_file(artifact, chunk_size=0)
