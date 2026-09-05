from datetime import UTC, datetime, timedelta
import pytest
from shared.artifact_manifest import ArtifactManifest, ManifestBlob, ManifestFile, ManifestParent
from shared.cas_gc import CasObject, plan_cas_collection


def test_shared_blocks_and_holds_survive_mission_collection():
    now = datetime.now(UTC)
    parent = ArtifactManifest(3, (ManifestFile("x", "data", ManifestBlob("shared", 3, "a" * 64)),), organization_id="tenant-a")
    child = ArtifactManifest(3, (), (ManifestParent("parent", "parent/manifest.json", "b" * 64),), organization_id="tenant-a")
    blobs = [CasObject(key, 3, now - timedelta(days=8)) for key in ("shared", "orphan", "held")]
    blobs.append(CasObject("in-flight", 3, now))
    plan = plan_cas_collection({"parent/manifest.json": parent, "live/manifest.json": child}, ["live/manifest.json"], blobs, now=now, grace=timedelta(days=7), protected_keys=["held"])
    assert [blob.key for blob in plan] == ["orphan"]
    with pytest.raises(ValueError, match="Missing"):
        plan_cas_collection({}, ["missing"], blobs, now=now, grace=timedelta(days=7))
