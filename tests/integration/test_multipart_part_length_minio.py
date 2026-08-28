from __future__ import annotations

import json
from uuid import uuid4

import pytest
import requests

from shared import storage
from shared.artifact_manifest import ManifestParent
from shared.stage_workspace import publish_workspace, restore_workspace_measured, WorkspaceSelection
from tools.qualify_s3_conditional_multipart import qualify


@pytest.mark.integration
def test_presigned_part_length_is_enforced_and_observable() -> None:
    key = f"integration/multipart-length/{uuid4().hex}.bin"
    expected = b"bounded-part"
    upload_id = storage.create_multipart_upload(key)
    try:
        signed = storage.get_presigned_upload_part_url(
            key,
            upload_id,
            1,
            content_length=len(expected),
        )
        oversized = requests.put(
            signed,
            data=expected + b"oversized",
            timeout=15,
        )
        assert oversized.status_code == 403

        accepted = requests.put(signed, data=expected, timeout=15)
        assert accepted.status_code == 200, accepted.text
        assert storage.list_multipart_parts(key, upload_id) == [
            {
                "PartNumber": 1,
                "Size": len(expected),
                "ETag": accepted.headers["ETag"],
            }
        ]
    finally:
        storage.abort_multipart_upload(key, upload_id)


@pytest.mark.integration
def test_v3_workspace_overlay_and_tenant_isolation_on_s3(tmp_path):
    organization = f"v3-cleanup-{uuid4().hex}"
    other_organization = f"v3-other-{uuid4().hex}"
    source = tmp_path / "source"
    source.mkdir()
    (source / "base.bin").write_bytes(b"same-immutable-parent")
    object_keys = set()

    def publish(root, owner, run, **kwargs):
        published = publish_workspace(
            root,
            f"organizations/{owner}/missions/fixture/{run}",
            organization_id=owner,
            default_role="stage-state",
            **kwargs,
        )
        object_keys.add(published.manifest_key)
        payload = json.loads(storage.get_object_bytes(published.manifest_key))
        object_keys.update(entry["blob"]["key"] for entry in payload["files"])
        assert payload["schema_version"] == 3
        assert payload["organization_id"] == owner
        return published, payload

    try:
        parent, parent_payload = publish(source, organization, "parent")
        other, other_payload = publish(source, other_organization, "parent")
        assert parent_payload["files"][0]["blob"]["key"] != other_payload["files"][0]["blob"]["key"]
        assert other.reused_bytes == 0
        (source / "model.ply").write_bytes(b"new-model")
        child, child_payload = publish(
            source, organization, "child",
            role_overrides={"model.ply": "gaussian-model"},
            parents=(ManifestParent("parent", parent.manifest_key, parent.checksum_sha256),),
        )
        assert [entry["path"] for entry in child_payload["files"]] == ["model.ply"]
        assert child.reused_bytes == len(b"same-immutable-parent")

        restored = tmp_path / "restored"
        measured = restore_workspace_measured(
            child.manifest_key, restored, child.checksum_sha256,
            expected_organization_id=organization,
        )
        assert measured.file_count == 2
        assert (restored / "base.bin").read_bytes() == b"same-immutable-parent"
        assert (restored / "model.ply").read_bytes() == b"new-model"
        selected = tmp_path / "selected"
        restore_workspace_measured(
            child.manifest_key, selected, child.checksum_sha256,
            expected_organization_id=organization,
            selection=WorkspaceSelection(paths=frozenset({"model.ply"})),
        )
        assert (selected / "model.ply").read_bytes() == b"new-model"
        assert not (selected / "base.bin").exists()
        with pytest.raises(ValueError, match="organization does not match"):
            restore_workspace_measured(
                child.manifest_key, tmp_path / "denied", child.checksum_sha256,
                expected_organization_id=other_organization,
            )
        with pytest.raises(OSError, match="manifest checksum mismatch"):
            restore_workspace_measured(
                child.manifest_key, tmp_path / "bad-checksum", "0" * 64,
                expected_organization_id=organization,
            )
    finally:
        for key in sorted(object_keys):
            storage.delete_object(key)


@pytest.mark.integration
def test_tenant_conditional_multipart_probe_on_s3(tmp_path):
    organization = f"v3-multipart-{uuid4().hex}"
    probe = tmp_path / "probe.bin"
    probe.write_bytes(b"v3" * (3 * 1024**2))
    result = qualify(probe, organization_id=organization)
    assert result["status"] == "passed"
    assert result["key"].startswith(f"organizations/{organization}/blobs/sha256/")
    assert result["cleanup_verified"] is True
