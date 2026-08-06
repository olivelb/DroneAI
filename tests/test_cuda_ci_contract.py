from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERAL_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CONTAINER_WORKFLOW = ROOT / ".github" / "workflows" / "cuda-containers.yml"
GPU_WORKFLOW = ROOT / ".github" / "workflows" / "dronegs-gpu-nightly.yml"
VALIDATION_SCRIPT = ROOT / "scripts" / "ci" / "validate_cuda_containers.sh"


def test_hosted_ci_builds_each_cuda_dockerfile_contract() -> None:
    workflow = CONTAINER_WORKFLOW.read_text(encoding="utf-8")
    script = VALIDATION_SCRIPT.read_text(encoding="utf-8")

    assert "scripts/ci/validate_cuda_containers.sh build" in workflow
    assert 'app1-colmap/dronegs/Dockerfile"' in script
    assert 'app1-colmap/Dockerfile.base"' in script
    assert 'app1-colmap/Dockerfile.local-gaussian"' in script
    assert script.count("--target dronegs-builder") == 2
    assert "-DDRONEGS_CUDA_ARCHITECTURES=portable" in script


def test_portable_cuda_build_only_runs_for_version_changes_or_manual_dispatch() -> None:
    general_workflow = GENERAL_WORKFLOW.read_text(encoding="utf-8")
    cuda_workflow = CONTAINER_WORKFLOW.read_text(encoding="utf-8")

    assert "DroneGS portable CUDA build" not in general_workflow
    assert "run: python3 scripts/ci/select_cuda_builds.py" in cuda_workflow
    assert cuda_workflow.count("if: needs.version-change.outputs.build_required == 'true'") == 2
    assert "workflow_dispatch" in cuda_workflow
    assert "dockerfile: app1-colmap/Dockerfile.base" in cuda_workflow
    assert "dockerfile: app1-colmap/Dockerfile.local-gaussian" in cuda_workflow


def test_gpu_nightly_executes_native_cuda_tests_in_container() -> None:
    workflow = GPU_WORKFLOW.read_text(encoding="utf-8")
    script = VALIDATION_SCRIPT.read_text(encoding="utf-8")

    assert "vars.DRONEGS_GPU_CI == 'true'" in workflow
    assert "runs-on: [self-hosted, linux, x64, gpu, cuda]" in workflow
    assert "scripts/ci/validate_cuda_containers.sh gpu" in workflow
    assert "docker run --rm --gpus all" in script
    assert "-DDRONEGS_CUDA_ARCHITECTURES=native" in script
    assert "ctest --test-dir /tmp/dronegs-gpu --output-on-failure" in script
    assert "smoke_runtime_images_on_gpu" in script
    assert "report_validation_context" in script
    assert "git -C" in script


def test_gpu_nightly_publishes_commit_scoped_qualification_evidence() -> None:
    workflow = GPU_WORKFLOW.read_text(encoding="utf-8")

    assert "set -o pipefail" in workflow
    assert "tee gpu-validation.log" in workflow
    assert "${GITHUB_STEP_SUMMARY}" in workflow
    assert "dronegs-gpu-validation-${{ github.sha }}" in workflow
    assert "retention-days: 30" in workflow


def test_gpu_contract_leaves_device_selection_to_the_driver() -> None:
    script = VALIDATION_SCRIPT.read_text(encoding="utf-8")

    forbidden_controls = (
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "--device=",
        "--device ",
        "cudaSetDevice",
    )
    for control in forbidden_controls:
        assert control not in script
