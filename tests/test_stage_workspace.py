import hashlib
import json
import shutil
from pathlib import Path

import pytest

from shared import stage_workspace


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

    monkeypatch.setattr(stage_workspace.storage, "upload_verified_file", upload)
    monkeypatch.setattr(stage_workspace.storage, "download_file", download)
    return root


def test_workspace_round_trip_is_manifested_and_checksum_verified(tmp_path, fake_s3):
    source = tmp_path / "source"
    (source / "sparse" / "0").mkdir(parents=True)
    (source / "database.db").write_bytes(b"database")
    (source / "sparse" / "0" / "cameras.bin").write_bytes(b"cameras")

    published = stage_workspace.publish_workspace(
        source,
        "missions/example/stages/reconstruction/run-1",
    )
    destination = tmp_path / "restored"
    count = stage_workspace.restore_workspace(
        published.manifest_key,
        destination,
        published.checksum_sha256,
    )

    assert count == 2
    assert published.file_count == 2
    assert published.size_bytes == 15
    assert published.uploaded_bytes == 15 + published.manifest_size_bytes
    assert published.reused_bytes == 0
    assert published.upload_seconds >= 0
    assert (destination / "database.db").read_bytes() == b"database"
    assert (destination / "sparse" / "0" / "cameras.bin").read_bytes() == b"cameras"


def test_measured_restore_reports_logical_and_transferred_bytes(tmp_path, fake_s3):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"model")
    published = stage_workspace.publish_workspace(source, "missions/measured")

    restored = stage_workspace.restore_workspace_measured(
        published.manifest_key,
        tmp_path / "destination",
        published.checksum_sha256,
    )
    provenance = stage_workspace.workspace_transfer_provenance(published, restored)

    assert restored.size_bytes == 5
    assert restored.file_count == 1
    assert restored.downloaded_bytes == 5 + restored.manifest_size_bytes
    assert restored.reused_bytes == 0
    assert restored.download_seconds >= 0
    assert provenance["publish"]["logical_bytes"] == 5
    assert provenance["restore"]["transferred_bytes"] == restored.downloaded_bytes
    assert provenance["manifest_schema_version"] == 1


def test_restore_rejects_manifest_path_traversal(tmp_path, fake_s3):
    manifest_key = "missions/example/manifest.json"
    payload = {
        "schema_version": 1,
        "files": [{"path": "../escape", "size": 0, "sha256": "0" * 64}],
    }
    content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    path = fake_s3 / manifest_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    with pytest.raises(ValueError, match="Unsafe"):
        stage_workspace.restore_workspace(
            manifest_key,
            tmp_path / "destination",
            hashlib.sha256(content).hexdigest(),
        )

    assert not (tmp_path / "escape").exists()


def test_restore_removes_corrupt_download(tmp_path, fake_s3):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"valid")
    published = stage_workspace.publish_workspace(source, "missions/corrupt")
    (fake_s3 / "missions/corrupt/files/model.bin").write_bytes(b"corrupt")
    destination = tmp_path / "destination"

    with pytest.raises(OSError, match="verification failed"):
        stage_workspace.restore_workspace(
            published.manifest_key,
            destination,
            published.checksum_sha256,
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
        stage_workspace.publish_workspace(source, "missions/symlink")


def test_resolve_workspace_path_accepts_nested_files_and_rejects_escape(tmp_path):
    assert stage_workspace.resolve_workspace_path(
        tmp_path,
        "products/orthomosaic.tif",
    ) == tmp_path / "products" / "orthomosaic.tif"

    with pytest.raises(ValueError, match="Unsafe workspace manifest path"):
        stage_workspace.resolve_workspace_path(tmp_path, "../secret.txt")
