from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_ortho.generate_gaussian_orthophoto import (  # noqa: E402
    _quarantine_incompatible_dronegs_output,
)
from gaussian_training.manifest_contract import (  # noqa: E402
    DuplicateManifestKeyError,
    load_run_manifest,
    manifest_matches_ply,
    promote_run_manifest,
)


def _manifest(ply: Path) -> dict:
    return {
        "contract_version": 1,
        "backend": "dronegs-native-mrnf-fastgs",
        "trainer_version": "test",
        "git_revision": "test",
        "status": "completed",
        "dataset": {"fingerprint": "dataset-v2:test"},
        "parameters": {
            "profile_id": "DRONEGS_PRODUCTION_PROFILE_V1",
            "optimizer_profile": "reference-absolute",
            "pruning_policy": "spatial-bounds",
            "raster_profile": "fastgs",
            "effective_raster_profile": "fastgs",
            "initial_scale_policy": "local-knn",
            "initial_max_projected_sigma_pixels": 2.0,
            "maximum_scale_growth_factor": 54.59815,
            "adaptive_native_crop_tiles": 0,
            "test_split": "modulo",
            "test_guard_percent": 0,
        },
        "timings": {},
        "metrics": {},
        "artifacts": {
            "point_cloud.ply": {
                "path": str(ply),
                "sha256": None,
                "bytes": 0,
            }
        },
    }


def test_manifest_loader_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "trainer_run.json"
    path.write_text(
        '{"contract_version":1,"parameters":{"raster_profile":"bounded","raster_profile":"fastgs"}}',
        encoding="utf-8",
    )

    with pytest.raises(
        DuplicateManifestKeyError,
        match="raster_profile",
    ):
        load_run_manifest(path)


def test_manifest_promotion_hashes_ply_and_detects_tampering(tmp_path):
    ply = tmp_path / "point_cloud.ply"
    ply.write_bytes(b"ply\nfixture")
    path = tmp_path / "trainer_run.json"
    path.write_text(json.dumps(_manifest(ply)), encoding="utf-8")

    promoted = promote_run_manifest(
        path,
        ply_path=ply,
        trainer_binary_sha256="a" * 64,
    )

    assert manifest_matches_ply(promoted, ply)
    assert promoted["artifacts"]["point_cloud.ply"]["bytes"] == len(b"ply\nfixture")
    ply.write_bytes(b"ply\ntampered")
    assert not manifest_matches_ply(promoted, ply)


def test_incompatible_completed_output_is_preserved_before_retraining(
    tmp_path,
):
    output = tmp_path / "mission" / "full"
    output.mkdir(parents=True)
    (output / "trainer_run.json").write_text(
        '{"contract_version":1}',
        encoding="utf-8",
    )

    quarantined = _quarantine_incompatible_dronegs_output(output)

    assert quarantined is not None
    assert not output.exists()
    assert (quarantined / "trainer_run.json").is_file()
    assert ".incompatible" in quarantined.parts
