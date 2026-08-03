from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
