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
    canonical_v3_bytes,
    content_addressed_blob_key,
    parse_artifact_manifest,
    validate_parent_graph,
)


def _tenant_blob(organization_id: str, content: bytes = b"model") -> ManifestBlob:
    checksum = hashlib.sha256(content).hexdigest()
    return ManifestBlob(
        key=content_addressed_blob_key(
            checksum,
            organization_id=organization_id,
        ),
        size_bytes=len(content),
        checksum_sha256=checksum,
    )


def _parent(artifact_id: str) -> ManifestParent:
    return ManifestParent(
        artifact_id=artifact_id,
        manifest_key=f"missions/example/{artifact_id}/manifest.json",
        checksum_sha256="a" * 64,
    )


@pytest.mark.parametrize("version", [1, 2, None, True, 3.0, "3"])
def test_reader_rejects_retired_or_invalid_manifest_versions(version):
    content = json.dumps({"schema_version": version, "files": []}).encode()
    with pytest.raises(ValueError, match="Unsupported workspace manifest schema"):
        parse_artifact_manifest(content)


def test_v3_canonicalization_sorts_files_and_parents_and_round_trips() -> None:
    manifest = ArtifactManifest(
        schema_version=ARTIFACT_MANIFEST_VERSION,
        files=(
            ManifestFile(path="z/model.ply", role="gaussian-model", blob=_tenant_blob("acme", b"z")),
            ManifestFile(path="a/state.json", role="stage-state", blob=_tenant_blob("acme", b"a")),
        ),
        parents=(_parent("parent-z"), _parent("parent-a")),
        organization_id="acme",
    )

    canonical = canonical_v3_bytes(manifest)
    reparsed = parse_artifact_manifest(canonical)

    assert canonical == canonical_v3_bytes(reparsed)
    assert [item.path for item in reparsed.files] == ["a/state.json", "z/model.ply"]
    assert [item.artifact_id for item in reparsed.parents] == ["parent-a", "parent-z"]


def test_v3_canonicalization_binds_blobs_to_one_organization() -> None:
    manifest = ArtifactManifest(
        schema_version=ARTIFACT_MANIFEST_VERSION,
        files=(
            ManifestFile(
                path="model.ply",
                role="gaussian-model",
                blob=_tenant_blob("acme"),
            ),
        ),
        organization_id="acme",
    )

    canonical = canonical_v3_bytes(manifest)
    reparsed = parse_artifact_manifest(canonical)

    assert canonical == canonical_v3_bytes(reparsed)
    assert reparsed.organization_id == "acme"
    assert reparsed.files[0].blob.key.startswith(
        "organizations/acme/blobs/sha256/"
    )

    payload = json.loads(canonical)
    payload["files"][0]["blob"]["key"] = _tenant_blob("other").key
    with pytest.raises(ValueError, match="not content-addressed"):
        parse_artifact_manifest(
            json.dumps(payload).encode(),
        )


def test_same_checksum_has_distinct_tenant_cas_keys() -> None:
    checksum = hashlib.sha256(b"shared-content").hexdigest()

    assert content_addressed_blob_key(
        checksum,
        organization_id="acme",
    ) != content_addressed_blob_key(checksum, organization_id="other")


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
def test_v3_reader_rejects_ambiguous_or_unsafe_content(mutator, message) -> None:
    canonical = canonical_v3_bytes(
        ArtifactManifest(
            schema_version=3,
            files=(ManifestFile(path="model.ply", role="gaussian-model", blob=_tenant_blob("acme")),),
            organization_id="acme",
        )
    )
    payload = json.loads(canonical)
    mutator(payload)

    with pytest.raises(ValueError, match=message):
        parse_artifact_manifest(
            json.dumps(payload).encode(),
        )


def test_parent_graph_accepts_external_parents_and_rejects_local_cycle() -> None:
    first = ArtifactManifest(
        schema_version=3,
        files=(),
        parents=(_parent("second"), _parent("external")),
        organization_id="acme",
    )
    second = ArtifactManifest(
        schema_version=3,
        files=(),
        parents=(_parent("first"),),
        organization_id="acme",
    )

    validate_parent_graph({"first": ArtifactManifest(3, (), (_parent("external"),), organization_id="acme")})
    with pytest.raises(ValueError, match="parent cycle"):
        validate_parent_graph({"first": first, "second": second})


@pytest.mark.parametrize("organization", [None, "", "legacy-unassigned", "../escape"])
def test_cas_key_requires_a_current_organization(organization):
    with pytest.raises(ValueError):
        content_addressed_blob_key("a" * 64, organization_id=organization)
