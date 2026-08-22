from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_distributed_stage_job_mode_is_explicit_immutable_and_bounded() -> None:
    entrypoint = _read("deploy.sh")
    common = _read("scripts/deploy/common.sh")
    distributed = _read("scripts/deploy/distributed.sh")

    assert "--stage-jobs GIT_SHA" in entrypoint
    assert "^[0-9a-f]{7,40}$" in entrypoint
    assert "STAGE_JOB_SERVICE_IMAGES" in common
    assert "drone-processing" not in common.split(
        "readonly STAGE_JOB_SERVICE_IMAGES=(", 1
    )[1].split(")", 1)[0]
    assert "global.requireImmutableImages=true" in distributed
    assert "colmapWorker.enabled=false" in distributed
    assert "iaWorker.enabled=false" in distributed
    assert "processingWorker.replicaCount=0" in distributed
    assert distributed.count("gpu_architecture=ampere") == 5
    assert distributed.count("stageJobs.executors.") == 22
    assert "stageJobs.executors.gaussian_viewer.command" in distributed
    assert distributed.count(".tolerations=") == 5
    assert "droneai.io/gpu-vram-at-least-24gb" in distributed
    assert "DRONEAI_GPU_VRAM_CLASS_GB" in distributed
    assert "--version 0.19.3" in distributed


def test_stage_job_mode_imports_only_images_used_by_the_q3_deployment() -> None:
    command = """
set -eu
STAGE_JOBS_IMAGE_TAG=f059ffe
source scripts/deploy/common.sh
active_service_images
"""
    completed = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.splitlines() == [
        "drone-colmap",
        "drone-ia",
        "drone-dashboard-api",
        "drone-dashboard-frontend",
    ]


def test_nvidia_plugin_exposes_one_physical_gpu_without_time_slicing() -> None:
    values = _read("nvdp-values.yaml")

    assert "runtimeClassName: nvidia" in values
    assert "timeSlicing" not in values
    assert "replicas:" not in values
