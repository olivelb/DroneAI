import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_local_pipeline.py"
SPEC = importlib.util.spec_from_file_location("run_local_pipeline", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_stage_selection_and_force_propagation():
    assert MODULE.selected_stages("gaussian", "detection") == (
        "gaussian",
        "detection",
    )
    assert MODULE.propagated_forces(["gaussian"]) == {"gaussian", "detection"}


def test_standard_pipeline_selects_dronegs_explicitly():
    profile = MODULE.PROFILES["standard"]
    command = MODULE.stage_command(
        "gaussian",
        dataset=Path("/data"),
        workspace=Path("/work"),
        profile=profile,
        forced=False,
        keep_detection_tiles=False,
    )

    assert command[command.index("--backend") + 1] == "dronegs"


def test_standard_pipeline_uses_planimetric_survey_alignment_defaults():
    profile = MODULE.PROFILES["standard"]
    command = MODULE.stage_command(
        "colmap",
        dataset=Path("/data"),
        workspace=Path("/work"),
        profile=profile,
        forced=False,
        keep_detection_tiles=False,
    )

    expected_values = {
        "--matcher": "gps",
        "--engine": "auto",
        "--feature-type": "SIFT",
        "--matcher-type": "SIFT_BRUTEFORCE",
        "--camera-model": "SIMPLE_RADIAL",
        "--feature-max-image-size": "2400",
        "--feature-max-num-features": "4096",
        "--global-ba-iterations": "2",
        "--global-ceres-iterations": "50",
        "--mapping-timeout-seconds": "2400",
    }
    for flag, expected in expected_values.items():
        assert command[command.index(flag) + 1] == expected
    assert "--global-retriangulation" in command
    assert "--no-global-retriangulation" not in command


def test_fast_pipeline_uses_full_dataset_and_versioned_minimum_envelope():
    profile = MODULE.PROFILES["fast"]
    colmap = MODULE.stage_command(
        "colmap",
        dataset=Path("/data"),
        workspace=Path("/work"),
        profile=profile,
        forced=False,
        keep_detection_tiles=False,
    )
    gaussian = MODULE.stage_command(
        "gaussian",
        dataset=Path("/data"),
        workspace=Path("/work"),
        profile=profile,
        forced=False,
        keep_detection_tiles=False,
    )

    assert "--max-images" not in colmap
    assert colmap[colmap.index("--selection") + 1] == "uniform"
    assert colmap[colmap.index("--feature-max-image-size") + 1] == "1600"
    assert colmap[colmap.index("--feature-max-num-features") + 1] == "2048"
    assert gaussian[gaussian.index("--profile") + 1] == "fast"
    assert profile.detection_profile == "full"


def test_colmap_completion_requires_model_alignment_and_images(tmp_path):
    workspace = tmp_path / "workspace"
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        path = workspace / "dense" / "sparse" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (workspace / "alignment_transform.json").write_text("{}", encoding="utf-8")
    image_dir = workspace / "dense" / "images"
    image_dir.mkdir(parents=True)
    for index in range(3):
        (image_dir / f"{index}.jpg").touch()

    assert MODULE.colmap_complete(workspace)[0] is True
    (image_dir / "0.jpg").unlink()
    assert MODULE.colmap_complete(workspace)[0] is False


def test_gaussian_and_detection_completion_validate_reports_and_outputs(tmp_path):
    workspace = tmp_path / "workspace"
    profile = MODULE.PROFILES["standard"]
    write_json(
        workspace / "gaussian_run.low-memory.json",
        {"status": "completed"},
    )
    (workspace / "orthomosaic.low-memory.tif").touch()
    (workspace / "orthomosaic.low-memory.height.tif").touch()
    assert MODULE.gaussian_complete(workspace, profile)[0] is True

    output = workspace / "detection_runs" / "full"
    write_json(output / "detection_run.json", {"status": "completed"})
    for name in (
        "detections.json",
        "detections.geojson",
        "orthomosaic.annotated.tif",
    ):
        (output / name).touch()
    assert MODULE.detection_complete(workspace, profile)[0] is True


def test_manifest_stays_beside_unmarked_workspace_then_moves_inside(tmp_path):
    workspace = tmp_path / "workspace"
    payload = {"status": "running"}

    sidecar = MODULE.persist_manifest(workspace, payload)
    assert sidecar == tmp_path / ".workspace.pipeline_run.json"
    assert sidecar.is_file()

    workspace.mkdir()
    (workspace / MODULE.WORKSPACE_MARKER).write_text("{}", encoding="utf-8")
    final = MODULE.persist_manifest(workspace, payload)
    assert final == workspace / "pipeline_run.json"
    assert final.is_file()
    assert not sidecar.exists()


def test_bootstrap_log_stays_beside_workspace_then_moves_after_marking(tmp_path):
    workspace = tmp_path / "workspace"
    sidecar = MODULE.stage_log_path(workspace, "colmap")

    assert sidecar == tmp_path / ".workspace.colmap.log"
    assert not workspace.exists()
    sidecar.write_text("preflight\n", encoding="utf-8")
    workspace.mkdir()
    (workspace / MODULE.WORKSPACE_MARKER).write_text("{}", encoding="utf-8")

    final = MODULE.finalize_stage_log(workspace, "colmap", sidecar)

    assert final == workspace / "pipeline_logs" / "colmap.log"
    assert final.read_text(encoding="utf-8") == "preflight\n"
    assert not sidecar.exists()
