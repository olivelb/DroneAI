from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cupy_runtime_lock_includes_matching_cuda_toolkit() -> None:
    source = (ROOT / "requirements" / "colmap.in").read_text(encoding="utf-8")
    lock = (ROOT / "requirements" / "colmap.txt").read_text(encoding="utf-8")
    local_gaussian_lock = (ROOT / "requirements" / "local-gaussian.txt").read_text(encoding="utf-8")

    assert "cupy-cuda12x[ctk]" in source
    assert "cuda-toolkit[cublas,cudart,cufft,curand,cusolver,cusparse,nvrtc]==12.9.*" in source
    assert "nvidia-nvjitlink-cu12==12.9.86" in source

    expected_cuda_packages = (
        "cuda-toolkit==12.9.2.0",
        "nvidia-cublas-cu12==12.9.2.10",
        "nvidia-cuda-nvrtc-cu12==12.9.86",
        "nvidia-cuda-runtime-cu12==12.9.79",
        "nvidia-cufft-cu12==11.4.1.4",
        "nvidia-curand-cu12==10.3.10.19",
        "nvidia-cusolver-cu12==11.7.5.82",
        "nvidia-cusparse-cu12==12.5.10.65",
        "nvidia-nvjitlink-cu12==12.9.86",
    )
    for package in expected_cuda_packages:
        assert package in lock
        assert package in local_gaussian_lock


def test_active_cuda_runtime_contracts_are_aligned_to_12_9_2() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    deployment = (ROOT / "scripts" / "deploy" / "common.sh").read_text(encoding="utf-8")
    cloud_guide = (ROOT / "CLOUD_DEPLOYMENT_OVHCLOUD_K3S.md").read_text(encoding="utf-8")
    cmake = (ROOT / "app1-colmap" / "dronegs" / "CMakeLists.txt").read_text(encoding="utf-8")
    manifest = (ROOT / "app1-colmap" / "dronegs" / "src" / "manifest.cpp").read_text(encoding="utf-8")
    license_inventory = (ROOT / "docs" / "dronegs" / "GPL_COMPONENTS.md").read_text(encoding="utf-8")
    ia_dockerfile = (ROOT / "app2-ia" / "Dockerfile").read_text(encoding="utf-8")
    ia_lock = (ROOT / "requirements" / "ia-extra.txt").read_text(encoding="utf-8")

    assert "nvidia/cuda:12.9.2-devel-ubuntu24.04" in workflow
    assert "nvidia/cuda:12.9.2-base-ubuntu24.04" in deployment
    assert "nvidia/cuda:12.9.2-runtime-ubuntu24.04" in cloud_guide
    assert 'DRONEGS_CUDA_RUNTIME_VERSION="${DRONEGS_CUDA_RUNTIME_VERSION}"' in cmake
    assert "DRONEGS_CUDA_RUNTIME_VERSION" in manifest
    assert '\"cuda_runtime\": \"12.8\"' not in manifest
    assert "NVIDIA CUB | 2.8.2 from CUDA Toolkit 12.9.2" in license_inventory
    assert "nvidia/cuda:12.9.2-base-ubuntu24.04@sha256:" in ia_dockerfile
    assert "torch==2.13.0+cu129" in ia_lock
    assert "torchvision==0.28.0+cu129" in ia_lock
    assert "nvidia-cuda-runtime-cu12==12.9.79" in ia_lock
    assert "nvidia-cuda-runtime==" not in ia_lock


def test_colmap_runtime_is_non_root_and_read_only() -> None:
    base_dockerfile = (ROOT / "app1-colmap" / "Dockerfile.base").read_text(encoding="utf-8")
    app_dockerfile = (ROOT / "app1-colmap" / "Dockerfile").read_text(encoding="utf-8")
    chart = (ROOT / "app4-dashboard/api/kubernetes_jobs.py").read_text()

    assert "useradd --uid 10001 --gid 10001" in base_dockerfile
    assert "USER 10001:10001" in base_dockerfile
    assert "USER 10001:10001" in app_dockerfile
    assert '"runAsNonRoot": True' in chart
    assert '"readOnlyRootFilesystem": True' in chart
    assert '"drop": ["ALL"]' in chart
    assert '"mountPath": "/tmp"' in chart


def test_ia_model_variants_use_a_writable_controlled_cache() -> None:
    dockerfile = (ROOT / "app2-ia" / "Dockerfile").read_text(encoding="utf-8")
    chart = (ROOT / "app4-dashboard" / "api" / "kubernetes_jobs.py").read_text(encoding="utf-8")

    assert "ENV AERIAL_BAKED_MODEL_DIR=/opt/modelzoo" in dockerfile
    assert "ENV AERIAL_MODEL_RELEASE=v8.4.0" in dockerfile
    assert (
        "ENV AERIAL_MODEL_SHA256="
        "8674b0c24bf68aab5eb45009e0ac3808ce432237edf8cb5c50ae2191cb263a2b"
    ) in dockerfile
    assert '"readOnlyRootFilesystem": True' in chart
    assert "install -d --owner=10001 --group=10001 /cache/huggingface" in dockerfile
    assert '"mountPath": "/cache"' in chart
    assert '"sizeLimit": resources["ephemeral_storage_limit"]' in chart


def test_sam3_deployment_uses_an_immutable_hugging_face_revision() -> None:
    values = (ROOT / "charts" / "drone-ai" / "values.yaml").read_text(encoding="utf-8")

    control = (ROOT / "charts/drone-ai/templates/_control-env.tpl").read_text()
    assert "- name: DRONEAI_STAGE_SAM3_MODEL_REVISION" in control
    assert ".Values.stageJobs.sam3.revision" in control
    assert "revision: 3c879f39826c281e95690f02c7821c4de09afae7" in values
