from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training.dataset_identity import (  # noqa: E402
    compute_dataset_identity,
)


def _dataset(root: Path) -> Path:
    sparse = root / "sparse" / "0"
    sparse.mkdir(parents=True)
    images = root / "images"
    images.mkdir()
    (sparse / "cameras.bin").write_bytes(b"camera calibration")
    (sparse / "images.bin").write_bytes(b"camera poses")
    (sparse / "points3D.bin").write_bytes(b"sparse points")
    (images / "flight-01.jpg").write_bytes(b"a" * 200_000)
    return root


def test_dataset_identity_covers_sparse_model_and_image_content(tmp_path):
    dataset = _dataset(tmp_path / "dataset")
    original = compute_dataset_identity(dataset)

    (dataset / "sparse" / "0" / "cameras.bin").write_bytes(
        b"changed calibration"
    )
    calibration_changed = compute_dataset_identity(dataset)
    assert calibration_changed.fingerprint != original.fingerprint

    (dataset / "sparse" / "0" / "cameras.bin").write_bytes(
        b"camera calibration"
    )
    image = dataset / "images" / "flight-01.jpg"
    content = bytearray(image.read_bytes())
    content[len(content) // 2] = ord("b")
    image.write_bytes(content)
    image_changed = compute_dataset_identity(dataset)
    assert image_changed.fingerprint != original.fingerprint


def test_dataset_identity_is_relocation_and_mtime_stable(tmp_path):
    first = _dataset(tmp_path / "first")
    second = _dataset(tmp_path / "second")
    (second / "images" / "flight-01.jpg").touch()

    assert (
        compute_dataset_identity(first).fingerprint
        == compute_dataset_identity(second).fingerprint
    )
