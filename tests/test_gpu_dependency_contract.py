from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cupy_runtime_lock_includes_matching_cuda_toolkit() -> None:
    source = (ROOT / "requirements" / "colmap.in").read_text(encoding="utf-8")
    lock = (ROOT / "requirements" / "colmap.txt").read_text(encoding="utf-8")

    assert "cupy-cuda12x[ctk]" in source
    assert "cuda-toolkit[cublas,cudart,cufft,curand,cusolver,cusparse,nvrtc]==12.8.*" in source
    assert "cuda-toolkit==12.8.2.0" in lock
    assert "nvidia-cuda-nvrtc-cu12==12.8.93" in lock
    assert "nvidia-nvjitlink-cu12==12.8.93" in lock


def test_colmap_runtime_is_non_root_and_read_only() -> None:
    base_dockerfile = (ROOT / "app1-colmap" / "Dockerfile.base").read_text(encoding="utf-8")
    app_dockerfile = (ROOT / "app1-colmap" / "Dockerfile").read_text(encoding="utf-8")
    chart = (ROOT / "charts" / "drone-ai" / "templates" / "colmap-worker.yaml").read_text(encoding="utf-8")
    compose = (ROOT / "compose.local.yaml").read_text(encoding="utf-8")

    assert "useradd --uid 10001 --gid 10001" in base_dockerfile
    assert "USER 10001:10001" in base_dockerfile
    assert "USER 10001:10001" in app_dockerfile
    assert "runAsNonRoot: true" in chart
    assert "readOnlyRootFilesystem: true" in chart
    assert 'drop: ["ALL"]' in chart
    assert "mountPath: /tmp" in chart
    assert 'user: "10001:10001"' in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose


def test_ia_model_variants_use_a_writable_controlled_cache() -> None:
    dockerfile = (ROOT / "app2-ia" / "Dockerfile").read_text(encoding="utf-8")
    chart = (ROOT / "charts" / "drone-ai" / "templates" / "ia-worker.yaml").read_text(encoding="utf-8")

    assert "ENV AERIAL_BAKED_MODEL_DIR=/opt/modelzoo" in dockerfile
    assert "readOnlyRootFilesystem: true" in chart
    assert "- name: AERIAL_MODEL_DIR" in chart
    assert 'value: "/cache/modelzoo"' in chart
    assert "- name: AERIAL_BAKED_MODEL_DIR" in chart
    assert 'value: "/opt/modelzoo"' in chart
    assert "mountPath: /cache/modelzoo" in chart


def test_sam3_deployment_uses_an_immutable_hugging_face_revision() -> None:
    chart = (ROOT / "charts" / "drone-ai" / "templates" / "ia-worker.yaml").read_text(encoding="utf-8")
    values = (ROOT / "charts" / "drone-ai" / "values.yaml").read_text(encoding="utf-8")

    assert "- name: SAM3_MODEL_REVISION" in chart
    assert ".Values.iaWorker.sam3.revision" in chart
    assert "revision: 3c879f39826c281e95690f02c7821c4de09afae7" in values
