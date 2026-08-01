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
