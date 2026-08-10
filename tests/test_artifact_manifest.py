from __future__ import annotations

import hashlib
import json

import pytest

from shared.artifact_manifest import (
    ARTIFACT_MANIFEST_VERSION,
    ArtifactManifest,
    ManifestBlob,
    ManifestFile,
    ManifestParent,
    canonical_v2_bytes,
    parse_artifact_manifest,
    validate_parent_graph,
)


def _blob(content: bytes = b"model") -> ManifestBlob:
    checksum = hashlib.sha256(content).hexdigest()
    return ManifestBlob(
        key=f"blobs/sha256/{checksum[:2]}/{checksum}",
        size_bytes=len(content),
        checksum_sha256=checksum,
    )


def _parent(artifact_id: str) -> ManifestParent:
    return ManifestParent(
        artifact_id=artifact_id,
        manifest_key=f"missions/example/{artifact_id}/manifest.json",
        checksum_sha256="a" * 64,
    )


def test_v1_reader_normalizes_prefix_files_without_changing_writer_contract() -> None:
    content = json.dumps(
        {
            "schema_version": 1,
            "files": [{"path": "sparse/cameras.bin", "size": 5, "sha256": "b" * 64}],
        }
    ).encode()

    manifest = parse_artifact_manifest(
        content,
        manifest_key="missions/example/run/reconstruction-workspace/manifest.json",
    )

    assert manifest.schema_version == 1
    assert manifest.parents == ()
    assert manifest.files[0].role == "legacy"
    assert manifest.files[0].blob.key.endswith(
        "/reconstruction-workspace/files/sparse/cameras.bin"
    )


def test_v2_canonicalization_sorts_files_and_parents_and_round_trips() -> None:
    manifest = ArtifactManifest(
        schema_version=ARTIFACT_MANIFEST_VERSION,
        files=(
            ManifestFile(path="z/model.ply", role="gaussian-model", blob=_blob(b"z")),
            ManifestFile(path="a/state.json", role="stage-state", blob=_blob(b"a")),
        ),
        parents=(_parent("parent-z"), _parent("parent-a")),
    )

    canonical = canonical_v2_bytes(manifest)
    reparsed = parse_artifact_manifest(canonical, manifest_key="ignored/manifest.json")

    assert canonical == canonical_v2_bytes(reparsed)
    assert [item.path for item in reparsed.files] == ["a/state.json", "z/model.ply"]
    assert [item.artifact_id for item in reparsed.parents] == ["parent-a", "parent-z"]


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda payload: payload["files"].append(payload["files"][0].copy()),
            "Duplicate workspace manifest path",
        ),
        (
            lambda payload: payload["files"][0].update(path="../escape"),
            "unsafe",
        ),
        (
            lambda payload: payload["files"][0]["blob"].update(
                key=f"blobs/sha256/ff/{'f' * 64}"
            ),
            "not content-addressed",
        ),
        (
            lambda payload: payload["files"][0].update(unexpected=True),
            "fields are invalid",
        ),
    ],
)
def test_v2_reader_rejects_ambiguous_or_unsafe_content(mutator, message) -> None:
    canonical = canonical_v2_bytes(
        ArtifactManifest(
            schema_version=2,
            files=(ManifestFile(path="model.ply", role="gaussian-model", blob=_blob()),),
        )
    )
    payload = json.loads(canonical)
    mutator(payload)

    with pytest.raises(ValueError, match=message):
        parse_artifact_manifest(
            json.dumps(payload).encode(),
            manifest_key="ignored/manifest.json",
        )


def test_parent_graph_accepts_external_parents_and_rejects_local_cycle() -> None:
    first = ArtifactManifest(
        schema_version=2,
        files=(),
        parents=(_parent("second"), _parent("external")),
    )
    second = ArtifactManifest(
        schema_version=2,
        files=(),
        parents=(_parent("first"),),
    )

    validate_parent_graph({"first": ArtifactManifest(2, (), (_parent("external"),))})
    with pytest.raises(ValueError, match="parent cycle"):
        validate_parent_graph({"first": first, "second": second})
