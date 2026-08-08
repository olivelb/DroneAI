import hashlib

import pytest

from shared.model_provenance import (
    MODEL_MANIFEST_SCHEMA,
    build_model_manifest,
    immutable_revision,
    sha256_file,
    validate_model_manifest,
)


SAM3_REVISION = "3c879f39826c281e95690f02c7821c4de09afae7"


def test_model_artifacts_are_hashed_without_loading_them_whole(tmp_path):
    artifact = tmp_path / "weights.bin"
    artifact.write_bytes(b"model-weights")

    assert sha256_file(artifact, chunk_size=3) == hashlib.sha256(b"model-weights").hexdigest()


def test_sam3_manifest_requires_an_immutable_revision():
    with pytest.raises(ValueError, match="full 40-character"):
        immutable_revision("main")

    manifest = build_model_manifest(
        backend="sam3",
        repository="facebook/sam3",
        revision=SAM3_REVISION.upper(),
        artifact="model.safetensors",
        artifact_sha256="A" * 64,
        libraries={"transformers": "5.14.1"},
        runtime={"device": "cuda"},
        inference={"prompt": "car"},
    )

    assert manifest["schema"] == MODEL_MANIFEST_SCHEMA
    assert manifest["identity"]["revision"] == SAM3_REVISION
    assert manifest["identity"]["artifact_sha256"] == "a" * 64


def test_event_model_manifest_is_bounded_and_structurally_valid():
    with pytest.raises(ValueError, match="missing"):
        validate_model_manifest(None)

    with pytest.raises(ValueError, match="16 KiB"):
        validate_model_manifest(
            {
                "schema": MODEL_MANIFEST_SCHEMA,
                "backend": "yolo",
                "identity": {
                    "repository": "ultralytics/assets",
                    "revision": "v8.4.0",
                    "artifact": "weights.pt",
                    "artifact_sha256": "a" * 64,
                },
                "libraries": {},
                "runtime": {},
                "inference": {"padding": "x" * (17 * 1024)},
            }
        )
