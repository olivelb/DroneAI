from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

import gaussian_ortho.cell_recovery as recovery_module
from gaussian_ortho.camera_footprint import NativeImageCrop
from gaussian_ortho.cell_recovery import (
    cell_recipe_sha256,
    load_cell_recovery_record,
    write_cell_recovery_record,
)
from gaussian_ortho.partition import CellBounds


def _bounds() -> CellBounds:
    return CellBounds(
        core_x_min=0.0,
        core_x_max=1.0,
        core_y_min=0.0,
        core_y_max=1.0,
        buffer_x_min=-0.1,
        buffer_x_max=1.1,
        buffer_y_min=-0.1,
        buffer_y_max=1.1,
        row=0,
        col=0,
    )


def _recipe(**changes: object) -> str:
    inputs = {
        "source_dataset_fingerprint": "source:sha256:fixture",
        "cell_label": "cell_0",
        "bounds": _bounds(),
        "camera_names": ["b.jpg", "a.jpg"],
        "image_crops": {
            "a.jpg": NativeImageCrop(
                source_x=0,
                source_y=0,
                width=10,
                height=8,
                source_width=10,
                source_height=8,
            )
        },
        "subset_parameters": {"min_track_length": 3},
        "training_parameters": {"iterations": 30_000},
    }
    inputs.update(changes)
    return cell_recipe_sha256(**inputs)


def _write_manifest(output: Path, *, dataset_fingerprint: str) -> None:
    point_cloud = output / "point_cloud.ply"
    point_cloud.write_bytes(b"ply fixture")
    manifest = {
        "contract_version": 1,
        "backend": "dronegs",
        "trainer_version": "fixture",
        "trainer_binary_sha256": "a" * 64,
        "git_revision": "fixture",
        "status": "completed",
        "dataset": {"fingerprint": dataset_fingerprint},
        "parameters": {
            "profile_id": "custom",
            "optimizer_profile": "mrnf-reference-absolute-v1",
            "pruning_policy": "mrnf-absolute-v1",
            "raster_profile": "reference-v1",
            "effective_raster_profile": "reference-v1",
            "test_split": "modulo",
            "test_guard_percent": 0,
            "initial_scale_policy": "projected-knn",
            "initial_max_projected_sigma_pixels": 8.0,
            "maximum_scale_growth_factor": 16.0,
            "adaptive_native_crop_tiles": 1,
        },
        "timings": {},
        "metrics": {},
        "artifacts": {
            "point_cloud.ply": {
                "path": "point_cloud.ply",
                "bytes": point_cloud.stat().st_size,
                "sha256": "b" * 64,
            }
        },
    }
    (output / "trainer_run.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def _write_completed_cell(output: Path, recipe: str) -> None:
    output.mkdir()
    dataset_fingerprint = "dataset:sha256:fixture"
    _write_manifest(output, dataset_fingerprint=dataset_fingerprint)
    (output / "canary_result.json").write_text(
        json.dumps({"status": "passed", "failed_metrics": []}),
        encoding="utf-8",
    )
    buffer = output / "buffer.ply"
    buffer.write_bytes(b"buffer fixture")
    write_cell_recovery_record(
        output,
        cell_label="cell_0",
        recipe_sha256=recipe,
        dataset_fingerprint=dataset_fingerprint,
        trainer_binary_sha256="a" * 64,
        buffer_path=buffer,
        gaussian_count=100,
        core_gaussian_count=80,
        bounds=_bounds(),
        subset_report={"selected_images": 10},
    )


def test_cell_recipe_is_order_stable_and_input_bound() -> None:
    baseline = _recipe()
    assert baseline == _recipe(camera_names=["a.jpg", "b.jpg"])
    assert baseline != _recipe(
        source_dataset_fingerprint="source:sha256:changed"
    )
    assert baseline != _recipe(
        subset_parameters={"min_track_length": 4}
    )
    assert baseline != _recipe(
        training_parameters={"iterations": 29_999}
    )


def test_completed_cell_record_loads_without_hashing_large_models(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "cell_0"
    recipe = _recipe()
    _write_completed_cell(output, recipe)

    hashed_names: list[str] = []
    real_sha256_file = recovery_module.sha256_file

    def record_hash(path: str | Path) -> str:
        hashed_names.append(Path(path).name)
        return real_sha256_file(path)

    monkeypatch.setattr(recovery_module, "sha256_file", record_hash)
    record = load_cell_recovery_record(
        output,
        expected_cell_label="cell_0",
        expected_recipe_sha256=recipe,
        expected_bounds=_bounds(),
        expected_trainer_binary_sha256="a" * 64,
    )

    assert record is not None
    assert record.dataset_fingerprint == "dataset:sha256:fixture"
    assert record.gaussian_count == 100
    assert record.core_gaussian_count == 80
    assert record.subset_report == {"selected_images": 10}
    assert record.bounds == _bounds()
    assert hashed_names == [
        "trainer_run.json",
        "canary_result.json",
    ]


def test_completed_cell_record_rejects_changed_recipe_or_small_contract(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cell_0"
    recipe = _recipe()
    _write_completed_cell(output, recipe)

    assert load_cell_recovery_record(
        output,
        expected_cell_label="cell_0",
        expected_recipe_sha256=_recipe(
            subset_parameters={"min_track_length": 5}
        ),
        expected_bounds=_bounds(),
        expected_trainer_binary_sha256="a" * 64,
    ) is None

    (output / "canary_result.json").write_text(
        json.dumps({"status": "failed", "failed_metrics": ["psnr"]}),
        encoding="utf-8",
    )
    assert load_cell_recovery_record(
        output,
        expected_cell_label="cell_0",
        expected_recipe_sha256=recipe,
        expected_bounds=_bounds(),
        expected_trainer_binary_sha256="a" * 64,
    ) is None


def test_completed_cell_record_rejects_changed_bounds(tmp_path: Path) -> None:
    output = tmp_path / "cell_0"
    recipe = _recipe()
    _write_completed_cell(output, recipe)
    changed_bounds = replace(_bounds(), core_x_max=0.9)

    assert load_cell_recovery_record(
        output,
        expected_cell_label="cell_0",
        expected_recipe_sha256=recipe,
        expected_bounds=changed_bounds,
        expected_trainer_binary_sha256="a" * 64,
    ) is None


def test_completed_cell_record_rejects_changed_model_size(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cell_0"
    recipe = _recipe()
    _write_completed_cell(output, recipe)
    (output / "point_cloud.ply").write_bytes(b"changed size")

    assert load_cell_recovery_record(
        output,
        expected_cell_label="cell_0",
        expected_recipe_sha256=recipe,
        expected_bounds=_bounds(),
        expected_trainer_binary_sha256="a" * 64,
    ) is None
