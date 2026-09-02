import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from shared import stage_workspace
from shared.artifact_manifest import (
    ArtifactManifest,
    ManifestBlob,
    ManifestFile,
    ManifestParent,
    canonical_v3_bytes,
    content_addressed_blob_key,
)


@pytest.fixture
def fake_s3(tmp_path, monkeypatch):
    root = tmp_path / "s3"

    def upload(local_path, key):
        source = Path(local_path)
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        content = target.read_bytes()
        return {
            "key": key,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def download(key, local_path):
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / key, target)
        return target

    def publish_cas(
        local_path,
        *,
        organization_id=None,
        cancellation_check=None,
    ):
        if cancellation_check is not None:
            cancellation_check()
        source = Path(local_path)
        content = source.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        key = content_addressed_blob_key(
            checksum,
            organization_id=organization_id,
        )
        target = root / key
        reused = target.exists()
        if reused:
            assert target.read_bytes() == content
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return stage_workspace.storage.ContentAddressedUpload(
            key=key,
            size_bytes=len(content),
            checksum_sha256=checksum,
            reused=reused,
            transferred_bytes=0 if reused else len(content),
        )

    monkeypatch.setattr(stage_workspace.storage, "upload_verified_file", upload)
    monkeypatch.setattr(stage_workspace.storage, "download_file", download)
    monkeypatch.setattr(
        stage_workspace.storage,
        "publish_content_addressed_file",
        publish_cas,
    )
    return root


def test_workspace_round_trip_is_manifested_and_checksum_verified(tmp_path, fake_s3):
    source = tmp_path / "source"
    (source / "sparse" / "0").mkdir(parents=True)
    (source / "database.db").write_bytes(b"database")
    (source / "sparse" / "0" / "cameras.bin").write_bytes(b"cameras")

    published = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/example/stages/reconstruction/run-1",
        organization_id="acme", default_role="stage-workspace",
    )
    destination = tmp_path / "restored"
    count = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        destination,
        published.checksum_sha256,
        expected_organization_id="acme",
    )

    assert count.file_count == 2
    assert published.file_count == 2
    assert published.size_bytes == 15
    assert published.uploaded_bytes == 15 + published.manifest_size_bytes
    assert published.reused_bytes == 0
    assert published.upload_seconds >= 0
    assert (destination / "database.db").read_bytes() == b"database"
    assert (destination / "sparse" / "0" / "cameras.bin").read_bytes() == b"cameras"


def test_selective_restore_switch_is_strict_and_disabled_by_default(monkeypatch):
    monkeypatch.delenv(stage_workspace.ARTIFACT_SELECTIVE_RESTORE_ENV, raising=False)
    assert stage_workspace.artifact_selective_restore_enabled() is False
    monkeypatch.setenv(stage_workspace.ARTIFACT_SELECTIVE_RESTORE_ENV, "true")
    assert stage_workspace.artifact_selective_restore_enabled() is True
    monkeypatch.setenv(stage_workspace.ARTIFACT_SELECTIVE_RESTORE_ENV, "sometimes")
    with pytest.raises(ValueError, match="explicit boolean"):
        stage_workspace.artifact_selective_restore_enabled()


def test_measured_restore_reports_logical_and_transferred_bytes(tmp_path, fake_s3):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"model")
    published = stage_workspace.publish_workspace(source, "organizations/acme/missions/measured", organization_id="acme", default_role="stage-workspace")

    restored = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        tmp_path / "destination",
        published.checksum_sha256,
        expected_organization_id="acme",
    )
    provenance = stage_workspace.workspace_transfer_provenance(published, restored)

    assert restored.size_bytes == 5
    assert restored.file_count == 1
    assert restored.downloaded_bytes == 5 + restored.manifest_size_bytes
    assert restored.reused_bytes == 0
    assert restored.download_seconds >= 0
    assert provenance["publish"]["logical_bytes"] == 5
    assert provenance["restore"]["transferred_bytes"] == restored.downloaded_bytes
    assert provenance["manifest_schema_version"] == 3


def test_restore_reuses_verified_files_and_repairs_changed_files(tmp_path, fake_s3):
    source = tmp_path / "source"
    source.mkdir()
    (source / "stable.bin").write_bytes(b"stable")
    (source / "changed.bin").write_bytes(b"authoritative")
    published = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/incremental-restore",
        organization_id="acme",
        default_role="stage-workspace",
    )

    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "stable.bin").write_bytes(b"stable")
    (destination / "changed.bin").write_bytes(b"stale-runtime")

    restored = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        destination,
        published.checksum_sha256,
        expected_organization_id="acme",
    )

    assert restored.size_bytes == len(b"stableauthoritative")
    assert restored.reused_bytes == len(b"stable")
    assert restored.downloaded_bytes == (
        restored.manifest_size_bytes + len(b"authoritative")
    )
    assert (destination / "stable.bin").read_bytes() == b"stable"
    assert (destination / "changed.bin").read_bytes() == b"authoritative"


def test_interrupted_restore_never_exposes_or_retains_a_partial_file(
    tmp_path,
    fake_s3,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"authoritative-model")
    published = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/interrupted-restore",
        organization_id="acme",
        default_role="gaussian-model",
    )
    destination = tmp_path / "destination"
    original_download = stage_workspace.storage.download_file

    def interrupted_download(key, local_path):
        if key == published.manifest_key:
            return original_download(key, local_path)
        Path(local_path).write_bytes(b"partial")
        raise OSError("connection reset")

    monkeypatch.setattr(
        stage_workspace.storage,
        "download_file",
        interrupted_download,
    )
    with pytest.raises(OSError, match="connection reset"):
        stage_workspace.restore_workspace_measured(
            published.manifest_key,
            destination,
            published.checksum_sha256,
            expected_organization_id="acme",
        )

    assert not (destination / "model.bin").exists()
    assert list(destination.rglob("*.tmp")) == []

    monkeypatch.setattr(
        stage_workspace.storage,
        "download_file",
        original_download,
    )
    restored = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        destination,
        published.checksum_sha256,
        expected_organization_id="acme",
    )
    assert restored.downloaded_bytes == (
        restored.manifest_size_bytes + len(b"authoritative-model")
    )
    assert (destination / "model.bin").read_bytes() == b"authoritative-model"


def test_restore_verification_cache_avoids_rehashing_unchanged_files(
    tmp_path,
    fake_s3,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"large-immutable-model")
    published = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/cached-restore",
        organization_id="acme",
        default_role="gaussian-model",
    )
    destination = tmp_path / "destination"
    cache_path = tmp_path / "verification-cache" / "run.json"

    first = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        destination,
        published.checksum_sha256,
        expected_organization_id="acme",
        verification_cache_path=cache_path,
    )
    assert first.downloaded_bytes == (
        first.manifest_size_bytes + len(b"large-immutable-model")
    )
    assert cache_path.is_file()

    original_sha256_file = stage_workspace.sha256_file
    monkeypatch.setattr(
        stage_workspace,
        "sha256_file",
        lambda _path: pytest.fail("unchanged verified files must not be re-read"),
    )
    second = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        destination,
        published.checksum_sha256,
        expected_organization_id="acme",
        verification_cache_path=cache_path,
    )

    assert second.reused_bytes == len(b"large-immutable-model")
    assert second.downloaded_bytes == second.manifest_size_bytes

    model_path = destination / "model.bin"
    previous_mtime_ns = model_path.stat().st_mtime_ns
    model_path.write_bytes(b"tampered-model-bytes!")
    os.utime(
        model_path,
        ns=(model_path.stat().st_atime_ns, previous_mtime_ns + 1_000_000_000),
    )
    monkeypatch.setattr(stage_workspace, "sha256_file", original_sha256_file)
    repaired = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        destination,
        published.checksum_sha256,
        expected_organization_id="acme",
        verification_cache_path=cache_path,
    )

    assert repaired.downloaded_bytes == (
        repaired.manifest_size_bytes + len(b"large-immutable-model")
    )
    assert model_path.read_bytes() == b"large-immutable-model"


def test_restore_batches_verification_cache_writes(
    tmp_path,
    fake_s3,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    for index in range(3):
        (source / f"model-{index}.bin").write_bytes(f"model-{index}".encode())
    published = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/batched-cache",
        organization_id="acme",
        default_role="gaussian-model",
    )
    store_calls = 0
    original_store = stage_workspace._store_verification_cache

    def counted_store(path, files):
        nonlocal store_calls
        store_calls += 1
        return original_store(path, files)

    monkeypatch.setattr(stage_workspace, "_store_verification_cache", counted_store)
    stage_workspace.restore_workspace_measured(
        published.manifest_key,
        tmp_path / "destination",
        published.checksum_sha256,
        expected_organization_id="acme",
        verification_cache_path=tmp_path / "cache" / "run.json",
    )

    assert store_calls == 1


def test_restore_does_not_depend_on_verification_cache_persistence(
    tmp_path,
    fake_s3,
    monkeypatch,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"verified-model")
    published = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/cache-write-failure",
        organization_id="acme",
        default_role="gaussian-model",
    )
    def fail_store(*_args, **_kwargs):
        raise OSError("read-only cache")

    monkeypatch.setattr(stage_workspace, "_store_verification_cache", fail_store)

    restored = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        tmp_path / "destination",
        published.checksum_sha256,
        expected_organization_id="acme",
        verification_cache_path=tmp_path / "cache" / "run.json",
    )

    assert restored.file_count == 1
    assert (tmp_path / "destination" / "model.bin").read_bytes() == b"verified-model"


def test_role_only_parent_override_reuses_blob_without_reading_file(
    tmp_path,
    fake_s3,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = workspace / "model.ply"
    model.write_bytes(b"immutable-gaussians")
    parent = stage_workspace.publish_workspace(
        workspace,
        "organizations/acme/missions/role-parent",
        organization_id="acme",
        default_role="gaussian-partition-model",
    )

    monkeypatch.setattr(
        stage_workspace,
        "sha256_file",
        lambda _path: pytest.fail("role-only reuse must not re-read the blob"),
    )
    child = stage_workspace.publish_workspace(
        workspace,
        "organizations/acme/missions/role-child",
        organization_id="acme",
        default_role="gaussian-filtering-workspace",
        role_overrides={"model.ply": "filtered-gaussian-partition-model"},
        parents=(
            ManifestParent(
                "parent",
                parent.manifest_key,
                parent.checksum_sha256,
            ),
        ),
        unchanged_parent_paths=frozenset({"model.ply"}),
    )

    manifest = json.loads((fake_s3 / child.manifest_key).read_bytes())
    assert manifest["files"][0]["path"] == "model.ply"
    assert manifest["files"][0]["role"] == "filtered-gaussian-partition-model"
    assert child.uploaded_bytes == child.manifest_size_bytes
    assert child.reused_bytes == len(b"immutable-gaussians")


def test_exact_restore_prunes_unmanaged_files_and_symlinks(tmp_path, fake_s3):
    source = tmp_path / "source"
    source.mkdir()
    (source / "managed.bin").write_bytes(b"authoritative")
    published = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/exact-restore",
        organization_id="acme",
        default_role="stage-workspace",
    )
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "managed.bin").write_bytes(b"authoritative")
    (destination / "rogue.bin").write_bytes(b"untrusted")
    (destination / "rogue-link").symlink_to(tmp_path / "outside")

    restored = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        destination,
        published.checksum_sha256,
        expected_organization_id="acme",
        exact_inventory=True,
    )

    assert restored.reused_bytes == len(b"authoritative")
    assert restored.pruned_file_count == 2
    assert restored.pruned_bytes >= len(b"untrusted")
    assert sorted(path.name for path in destination.iterdir()) == ["managed.bin"]


def test_v3_writer_publishes_only_incremental_files_and_restores_overlay(
    tmp_path,
    fake_s3,
):
    parent_workspace = tmp_path / "parent"
    parent_workspace.mkdir()
    (parent_workspace / "base.bin").write_bytes(b"stable-parent")
    parent = stage_workspace.publish_workspace(
        parent_workspace,
        "organizations/acme/missions/example/parent-v3",
        default_role="reconstruction-workspace",
        organization_id="acme",
    )
    child_workspace = tmp_path / "child"
    shutil.copytree(parent_workspace, child_workspace)
    (child_workspace / "model.ply").write_bytes(b"new-model")
    child = stage_workspace.publish_workspace(
        child_workspace,
        "organizations/acme/missions/example/child-v3",
        default_role="gaussian-training-workspace",
        role_overrides={"model.ply": "gaussian-model"},
        parents=(
            ManifestParent(
                "parent-artifact",
                parent.manifest_key,
                parent.checksum_sha256,
            ),
        ),
        organization_id="acme",
    )

    child_manifest = json.loads((fake_s3 / child.manifest_key).read_bytes())
    assert [entry["path"] for entry in child_manifest["files"]] == ["model.ply"]
    assert child_manifest["files"][0]["role"] == "gaussian-model"
    assert child.file_count == 2
    assert child.size_bytes == len(b"stable-parentnew-model")
    assert child.reused_bytes == len(b"stable-parent")

    restored = stage_workspace.restore_workspace_measured(
        child.manifest_key,
        tmp_path / "restored-child",
        child.checksum_sha256,
        expected_organization_id="acme",
    )
    assert restored.manifest_schema_version == 3
    assert (tmp_path / "restored-child" / "base.bin").read_bytes() == b"stable-parent"
    assert (tmp_path / "restored-child" / "model.ply").read_bytes() == b"new-model"


def test_v3_writer_isolates_identical_blobs_and_rejects_cross_tenant_restore(
    tmp_path,
    fake_s3,
):
    source = tmp_path / "tenant-source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"same-model")

    acme = stage_workspace.publish_workspace(
        source,
        "organizations/acme/missions/example/stages/model/run-a",
        default_role="stage-workspace",
        organization_id="acme",
    )
    other = stage_workspace.publish_workspace(
        source,
        "organizations/other/missions/example/stages/model/run-b",
        default_role="stage-workspace",
        organization_id="other",
    )
    acme_manifest = json.loads((fake_s3 / acme.manifest_key).read_bytes())
    other_manifest = json.loads((fake_s3 / other.manifest_key).read_bytes())

    assert acme.manifest_schema_version == 3
    assert acme_manifest["schema_version"] == 3
    assert acme_manifest["organization_id"] == "acme"
    assert acme_manifest["files"][0]["blob"]["key"] != (
        other_manifest["files"][0]["blob"]["key"]
    )
    restored = tmp_path / "tenant-restored"
    stage_workspace.restore_workspace_measured(
        acme.manifest_key,
        restored,
        acme.checksum_sha256,
        expected_organization_id="acme",
    )
    assert (restored / "model.bin").read_bytes() == b"same-model"

    with pytest.raises(ValueError, match="organization does not match"):
        stage_workspace.restore_workspace_measured(
            acme.manifest_key,
            tmp_path / "cross-tenant",
            acme.checksum_sha256,
            expected_organization_id="other",
        )


def test_v3_writer_rejects_implicit_parent_file_deletion(tmp_path, fake_s3):
    parent_workspace = tmp_path / "parent-delete"
    parent_workspace.mkdir()
    (parent_workspace / "required.bin").write_bytes(b"required")
    parent = stage_workspace.publish_workspace(
        parent_workspace,
        "organizations/acme/missions/example/parent-delete",
        default_role="stage-workspace",
        organization_id="acme",
    )
    child_workspace = tmp_path / "child-delete"
    child_workspace.mkdir()

    with pytest.raises(ValueError, match="cannot implicitly delete"):
        stage_workspace.publish_workspace(
            child_workspace,
            "organizations/acme/missions/example/child-delete",
            default_role="stage-workspace",
            parents=(
                ManifestParent(
                    "parent-artifact",
                    parent.manifest_key,
                    parent.checksum_sha256,
                ),
            ),
            organization_id="acme",
        )


def test_v3_writer_can_publish_partial_workspace_as_complete_parent_overlay(
    tmp_path,
    fake_s3,
):
    parent_workspace = tmp_path / "parent-partial"
    parent_workspace.mkdir()
    (parent_workspace / "base.bin").write_bytes(b"stable-parent")
    (parent_workspace / "orthomosaic.tif").write_bytes(b"ortho")
    parent = stage_workspace.publish_workspace(
        parent_workspace,
        "organizations/acme/missions/example/parent-partial",
        default_role="raster-product",
        organization_id="acme",
    )
    child_workspace = tmp_path / "child-partial"
    child_workspace.mkdir()
    (child_workspace / "orthomosaic.tif").write_bytes(b"ortho")
    (child_workspace / "detections.json").write_bytes(b"detections")

    child = stage_workspace.publish_workspace(
        child_workspace,
        "organizations/acme/missions/example/child-partial",
        default_role="detection-workspace",
        role_overrides={"detections.json": "detection-records"},
        parents=(
            ManifestParent(
                "parent-artifact",
                parent.manifest_key,
                parent.checksum_sha256,
            ),
        ),
        allow_partial_workspace=True,
        organization_id="acme",
    )

    child_manifest = json.loads((fake_s3 / child.manifest_key).read_bytes())
    assert [entry["path"] for entry in child_manifest["files"]] == [
        "detections.json"
    ]
    assert child.file_count == 3
    assert child.size_bytes == len(b"stable-parentorthodetections")
    assert child.reused_bytes == len(b"stable-parentortho")

    restored_root = tmp_path / "restored-partial"
    restored = stage_workspace.restore_workspace_measured(
        child.manifest_key,
        restored_root,
        child.checksum_sha256,
        expected_organization_id="acme",
    )
    assert restored.file_count == 3
    assert (restored_root / "base.bin").read_bytes() == b"stable-parent"
    assert (restored_root / "orthomosaic.tif").read_bytes() == b"ortho"
    assert (restored_root / "detections.json").read_bytes() == b"detections"


def test_restore_materializes_content_addressed_v3_blob(tmp_path, fake_s3):
    content = b"content-addressed-model"
    checksum = hashlib.sha256(content).hexdigest()
    blob_key = f"organizations/acme/blobs/sha256/{checksum[:2]}/{checksum}"
    blob_path = fake_s3 / blob_key
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(content)
    manifest_bytes = canonical_v3_bytes(
        ArtifactManifest(
            schema_version=3,
            files=(
                ManifestFile(
                    path="models/final.ply",
                    role="gaussian-model",
                    blob=ManifestBlob(
                        key=blob_key,
                        size_bytes=len(content),
                        checksum_sha256=checksum,
                    ),
                ),
            ),
            organization_id="acme",
        )
    )
    manifest_key = "organizations/acme/missions/example/v3/manifest.json"
    manifest_path = fake_s3 / manifest_key
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(manifest_bytes)

    restored = stage_workspace.restore_workspace_measured(
        manifest_key,
        tmp_path / "restored-v3",
        hashlib.sha256(manifest_bytes).hexdigest(),
        expected_organization_id="acme",
    )

    assert restored.file_count == 1
    assert restored.size_bytes == len(content)
    assert restored.manifest_schema_version == 3
    assert (tmp_path / "restored-v3" / "models" / "final.ply").read_bytes() == content


def _store_blob(fake_s3: Path, content: bytes) -> ManifestBlob:
    checksum = hashlib.sha256(content).hexdigest()
    key = f"organizations/acme/blobs/sha256/{checksum[:2]}/{checksum}"
    path = fake_s3 / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return ManifestBlob(key=key, size_bytes=len(content), checksum_sha256=checksum)


def _store_manifest(fake_s3: Path, key: str, manifest: ArtifactManifest) -> str:
    content = canonical_v3_bytes(manifest)
    path = fake_s3 / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def test_selective_overlay_restores_child_override_only(tmp_path, fake_s3):
    old_model = _store_blob(fake_s3, b"old-model")
    state = _store_blob(fake_s3, b"state")
    new_model = _store_blob(fake_s3, b"new-model")
    parent_key = "organizations/acme/missions/example/parent/manifest.json"
    parent_checksum = _store_manifest(
        fake_s3,
        parent_key,
        ArtifactManifest(
            3,
            (
                ManifestFile("model.ply", "gaussian-model", old_model),
                ManifestFile("state.json", "stage-state", state),
            ),
            organization_id="acme",
        ),
    )
    child_key = "organizations/acme/missions/example/child/manifest.json"
    child_checksum = _store_manifest(
        fake_s3,
        child_key,
        ArtifactManifest(
            3,
            (ManifestFile("model.ply", "gaussian-model", new_model),),
            (ManifestParent("parent", parent_key, parent_checksum),),
            organization_id="acme",
        ),
    )

    restored = stage_workspace.restore_workspace_measured(
        child_key,
        tmp_path / "selected",
        child_checksum,
        selection=stage_workspace.WorkspaceSelection(
            roles=frozenset({"gaussian-model"})
        ),
        expected_organization_id="acme",
    )

    assert restored.file_count == 1
    assert (tmp_path / "selected" / "model.ply").read_bytes() == b"new-model"
    assert not (tmp_path / "selected" / "state.json").exists()
    assert restored.manifest_size_bytes == (
        (fake_s3 / parent_key).stat().st_size + (fake_s3 / child_key).stat().st_size
    )


def test_overlay_rejects_conflicting_sibling_parent_paths(tmp_path, fake_s3):
    parents = []
    for name, content in (("left", b"left"), ("right", b"right")):
        key = f"organizations/acme/missions/example/{name}/manifest.json"
        checksum = _store_manifest(
            fake_s3,
            key,
            ArtifactManifest(
                3,
                (ManifestFile("shared.bin", "stage-state", _store_blob(fake_s3, content)),),
                organization_id="acme",
            ),
        )
        parents.append(ManifestParent(name, key, checksum))
    child_key = "organizations/acme/missions/example/conflict/manifest.json"
    child_checksum = _store_manifest(
        fake_s3,
        child_key,
        ArtifactManifest(3, (), tuple(parents), organization_id="acme"),
    )

    with pytest.raises(ValueError, match="Conflicting parent overlay path"):
        stage_workspace.restore_workspace_measured(
            child_key,
            tmp_path / "conflict",
            child_checksum,
            expected_organization_id="acme",
        )


def test_selective_overlay_rejects_missing_explicit_path(tmp_path, fake_s3):
    key = "organizations/acme/missions/example/empty/manifest.json"
    checksum = _store_manifest(fake_s3, key, ArtifactManifest(3, (), organization_id="acme"))

    with pytest.raises(ValueError, match="selection paths are missing"):
        stage_workspace.restore_workspace_measured(
            key,
            tmp_path / "missing",
            checksum,
            selection=stage_workspace.WorkspaceSelection(
                paths=frozenset({"absent.bin"})
            ),
            expected_organization_id="acme",
        )


@pytest.mark.parametrize(
    "selection",
    [
        stage_workspace.WorkspaceSelection,
        lambda: stage_workspace.WorkspaceSelection(roles=frozenset({"Invalid Role"})),
        lambda: stage_workspace.WorkspaceSelection(paths=frozenset({"windows\\path"})),
    ],
)
def test_workspace_selection_rejects_empty_or_noncanonical_values(selection):
    with pytest.raises(ValueError):
        selection()


def test_restore_rejects_manifest_path_traversal(tmp_path, fake_s3):
    manifest_key = "organizations/acme/missions/example/manifest.json"
    payload = {
        "schema_version": 3,
        "organization_id": "acme",
        "parents": [],
        "files": [{"path": "../escape", "role": "stage-state", "blob": {}}],
    }
    content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    path = fake_s3 / manifest_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    with pytest.raises(ValueError, match="unsafe"):
        stage_workspace.restore_workspace_measured(
            manifest_key,
            tmp_path / "destination",
            hashlib.sha256(content).hexdigest(),
            expected_organization_id="acme",
        )

    assert not (tmp_path / "escape").exists()


def test_restore_removes_corrupt_download(tmp_path, fake_s3):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"valid")
    published = stage_workspace.publish_workspace(source, "organizations/acme/missions/corrupt", organization_id="acme", default_role="stage-workspace")
    manifest = json.loads((fake_s3 / published.manifest_key).read_bytes())
    (fake_s3 / manifest["files"][0]["blob"]["key"]).write_bytes(b"corrupt")
    destination = tmp_path / "destination"

    with pytest.raises(OSError, match="verification failed"):
        stage_workspace.restore_workspace_measured(
            published.manifest_key,
            destination,
            published.checksum_sha256,
            expected_organization_id="acme",
        )

    assert not (destination / "model.bin").exists()


def test_publish_rejects_symbolic_links(tmp_path, fake_s3):
    source = tmp_path / "source"
    source.mkdir()
    target = source / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = source / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links unavailable")

    with pytest.raises(ValueError, match="symbolic link"):
        stage_workspace.publish_workspace(source, "organizations/acme/missions/symlink", organization_id="acme", default_role="stage-workspace")


def test_resolve_workspace_path_accepts_nested_files_and_rejects_escape(tmp_path):
    assert stage_workspace.resolve_workspace_path(
        tmp_path,
        "products/orthomosaic.tif",
    ) == tmp_path / "products" / "orthomosaic.tif"

    with pytest.raises(ValueError, match="Unsafe workspace manifest path"):
        stage_workspace.resolve_workspace_path(tmp_path, "../secret.txt")


@pytest.mark.parametrize("organization", [None, "legacy-unassigned"])
def test_publication_rejects_missing_or_retired_organization_before_upload(
    tmp_path, fake_s3, organization,
):
    source = tmp_path / "invalid-tenant"
    source.mkdir()
    (source / "model.ply").write_bytes(b"model")
    with pytest.raises(ValueError, match="organization"):
        stage_workspace.publish_workspace(
            source,
            "organizations/acme/missions/example/run",
            default_role="gaussian-model",
            organization_id=organization,
        )
    assert not fake_s3.exists()


def test_v3_overlay_rejects_cross_tenant_parent_before_blob_restore(tmp_path, fake_s3):
    source = tmp_path / "parent-source"
    source.mkdir()
    (source / "private.bin").write_bytes(b"private")
    parent = stage_workspace.publish_workspace(
        source,
        "organizations/other/missions/example/parent",
        default_role="stage-state",
        organization_id="other",
    )
    child = ArtifactManifest(
        3, (),
        (ManifestParent("parent", parent.manifest_key, parent.checksum_sha256),),
        organization_id="acme",
    )
    child_key = "organizations/acme/missions/example/child/manifest.json"
    checksum = _store_manifest(fake_s3, child_key, child)
    destination = tmp_path / "denied"
    with pytest.raises(ValueError, match="organization does not match"):
        stage_workspace.restore_workspace_measured(
            child_key, destination, checksum, expected_organization_id="acme",
        )
    assert not (destination / "private.bin").exists()
