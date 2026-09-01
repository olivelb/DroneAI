from pathlib import Path

import pytest

from gaussian_ortho.colmap_subset import (
    _read_colmap_cameras_bin,
    _read_colmap_images_bin,
    _read_colmap_points3d_bin,
    _write_colmap_cameras_bin,
    _write_colmap_images_bin,
    _write_colmap_points3d_bin,
    export_colmap_subset,
)
from gaussian_ortho.camera_footprint import NativeImageCrop


def _write_single_image_model(source: Path, image_name: str) -> None:
    source.mkdir()
    _write_colmap_cameras_bin(
        {
            1: {
                "model_id": 1,
                "width": 16,
                "height": 12,
                "params": [10.0, 10.0, 8.0, 6.0],
            }
        },
        source / "cameras.bin",
    )
    _write_colmap_images_bin(
        {
            10: {
                "qw": 1.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "tx": 0.0,
                "ty": 0.0,
                "tz": 0.0,
                "camera_id": 1,
                "name": image_name,
                "xys": [(2.0, 3.0)],
                "point3D_ids": [100],
            }
        },
        source / "images.bin",
    )
    _write_colmap_points3d_bin(
        {
            100: {
                "xyz": (1.0, 2.0, 3.0),
                "rgb": (4, 5, 6),
                "error": 0.1,
                "track": [(10, 0)],
            }
        },
        source / "points3D.bin",
    )


def test_gaussian_pipeline_imports_without_legacy_backend() -> None:
    from gaussian_ortho.generate_gaussian_orthophoto import (
        generate_gaussian_orthophoto,
    )

    assert callable(generate_gaussian_orthophoto)


def test_export_colmap_subset_filters_images_cameras_and_points(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_colmap_cameras_bin(
        {
            1: {
                "model_id": 1,
                "width": 16,
                "height": 12,
                "params": [10.0, 10.0, 8.0, 6.0],
            },
            2: {
                "model_id": 1,
                "width": 16,
                "height": 12,
                "params": [11.0, 11.0, 8.0, 6.0],
            },
        },
        source / "cameras.bin",
    )
    image_template = {
        "qw": 1.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
        "tx": 0.0,
        "ty": 0.0,
        "tz": 0.0,
        "xys": [(2.0, 3.0)],
    }
    _write_colmap_images_bin(
        {
            10: {
                **image_template,
                "camera_id": 1,
                "name": "keep.jpg",
                "point3D_ids": [100],
            },
            20: {
                **image_template,
                "camera_id": 2,
                "name": "drop.jpg",
                "point3D_ids": [200],
            },
        },
        source / "images.bin",
    )
    _write_colmap_points3d_bin(
        {
            100: {
                "xyz": (1.0, 2.0, 3.0),
                "rgb": (4, 5, 6),
                "error": 0.1,
                "track": [(10, 0)],
            },
            200: {
                "xyz": (7.0, 8.0, 9.0),
                "rgb": (10, 11, 12),
                "error": 0.2,
                "track": [(20, 0)],
            },
        },
        source / "points3D.bin",
    )
    source_images = tmp_path / "images"
    source_images.mkdir()

    output = Path(
        export_colmap_subset(
            str(source),
            str(tmp_path / "cell"),
            ["keep.jpg"],
            images_dir=str(source_images),
            image_crops={
                "keep.jpg": NativeImageCrop(
                    source_x=2,
                    source_y=3,
                    width=10,
                    height=8,
                    source_width=16,
                    source_height=12,
                )
            },
        )
    )

    assert set(_read_colmap_cameras_bin(output / "cameras.bin")) == {1}
    assert set(_read_colmap_images_bin(output / "images.bin")) == {10}
    assert set(_read_colmap_points3d_bin(output / "points3D.bin")) == {100}
    assert (tmp_path / "cell" / "images").resolve() == source_images.resolve()
    assert (tmp_path / "cell" / "image_regions.tsv").read_text(
        encoding="utf-8"
    ) == "# dronegs-image-regions-v1\nkeep.jpg\t2\t3\t10\t8\n"


def test_native_training_workspace_uses_a_zero_copy_image_directory(tmp_path: Path):
    source = tmp_path / "source"
    image_name = "keep.jpg"
    _write_single_image_model(source, image_name)
    source_images = tmp_path / "images"
    source_images.mkdir()
    (source_images / image_name).write_bytes(b"unchanged jpeg")

    report = export_colmap_subset(
        str(source),
        str(tmp_path / "native-workspace"),
        [image_name],
        images_dir=str(source_images),
        return_report=True,
    )

    image_directory = tmp_path / "native-workspace" / "images"
    assert image_directory.is_symlink()
    assert image_directory.resolve() == source_images.resolve()
    assert report["image_transport"] == {
        "strategy": "symlink",
        "image_count": 1,
        "existing": 0,
        "hardlinked": 0,
        "copied": 0,
        "copied_bytes": 0,
    }


def test_export_colmap_subset_applies_track_gate_after_camera_restriction(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_colmap_cameras_bin(
        {
            1: {
                "model_id": 1,
                "width": 16,
                "height": 12,
                "params": [10.0, 10.0, 8.0, 6.0],
            }
        },
        source / "cameras.bin",
    )
    image_template = {
        "qw": 1.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
        "tx": 0.0,
        "ty": 0.0,
        "tz": 0.0,
        "camera_id": 1,
        "xys": [(2.0, 3.0), (4.0, 5.0)],
    }
    _write_colmap_images_bin(
        {
            10: {
                **image_template,
                "name": "keep-a.jpg",
                "point3D_ids": [100, 101],
            },
            20: {
                **image_template,
                "name": "keep-b.jpg",
                "point3D_ids": [-1, 101],
            },
            30: {
                **image_template,
                "name": "outside.jpg",
                "point3D_ids": [100, -1],
            },
        },
        source / "images.bin",
    )
    _write_colmap_points3d_bin(
        {
            100: {
                "xyz": (1.0, 2.0, 3.0),
                "rgb": (4, 5, 6),
                "error": 0.1,
                "track": [(10, 0), (30, 0)],
            },
            101: {
                "xyz": (7.0, 8.0, 9.0),
                "rgb": (10, 11, 12),
                "error": 0.2,
                "track": [(10, 1), (20, 1)],
            },
        },
        source / "points3D.bin",
    )

    report = export_colmap_subset(
        str(source),
        str(tmp_path / "cell"),
        ["keep-a.jpg", "keep-b.jpg"],
        min_track_length=2,
        return_report=True,
    )

    assert isinstance(report, dict)
    exported = _read_colmap_points3d_bin(
        tmp_path / "cell" / "sparse" / "0" / "points3D.bin"
    )
    images = _read_colmap_images_bin(
        tmp_path / "cell" / "sparse" / "0" / "images.bin"
    )
    assert set(exported) == {101}
    assert exported[101]["track"] == [(10, 1), (20, 1)]
    assert images[10]["point3D_ids"] == [-1, 101]
    timings = report.pop("timings_seconds")
    process_peak_rss_kib = report.pop("process_peak_rss_kib")
    assert process_peak_rss_kib > 0
    assert set(timings) == {
        "read_cameras", "read_images", "read_points", "select_cameras",
        "filter_points", "write_sparse", "write_regions", "prepare_images", "total",
    }
    assert all(value >= 0.0 for value in timings.values())
    assert report == {
        "sparse_path": str(tmp_path / "cell" / "sparse" / "0"),
        "selected_images": 2,
        "selected_images_before_support_filter": 2,
        "images_rejected_without_point_support": 0,
        "points_before_cap": 1,
        "exported_points": 1,
        "max_points": None,
        "coverage_balanced": False,
        "points_rejected_for_restricted_track": 1,
        "observations_rejected_outside_native_crops": 0,
        "track_scope": "selected-cameras-native-crops-and-supported-images-v2",
        "exported_observations": 2,
        "minimum_exported_track_length": 2,
        "median_exported_track_length": 2.0,
        "mean_exported_track_length": 2.0,
        "points_with_at_least_three_observations": 0,
        "points_with_at_least_five_observations": 0,
    }

    with pytest.raises(
        RuntimeError,
        match="no images with retained 3D observations",
    ):
        export_colmap_subset(
            str(source),
            str(tmp_path / "cropped-cell"),
            ["keep-a.jpg", "keep-b.jpg"],
            image_crops={
                "keep-b.jpg": NativeImageCrop(
                    source_x=0,
                    source_y=0,
                    width=3,
                    height=12,
                    source_width=16,
                    source_height=12,
                )
            },
            min_track_length=2,
            return_report=True,
        )


def test_export_colmap_subset_removes_images_without_retained_3d_observations(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_colmap_cameras_bin(
        {
            1: {
                "model_id": 1,
                "width": 16,
                "height": 12,
                "params": [10.0, 10.0, 8.0, 6.0],
            },
            2: {
                "model_id": 1,
                "width": 16,
                "height": 12,
                "params": [11.0, 11.0, 8.0, 6.0],
            },
        },
        source / "cameras.bin",
    )
    image_template = {
        "qw": 1.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
        "tx": 0.0,
        "ty": 0.0,
        "tz": 0.0,
        "xys": [(2.0, 3.0)],
    }
    _write_colmap_images_bin(
        {
            10: {
                **image_template,
                "camera_id": 1,
                "name": "supported.jpg",
                "point3D_ids": [100],
            },
            20: {
                **image_template,
                "camera_id": 2,
                "name": "unsupported.jpg",
                "point3D_ids": [200],
            },
        },
        source / "images.bin",
    )
    _write_colmap_points3d_bin(
        {
            100: {
                "xyz": (1.0, 2.0, 3.0),
                "rgb": (4, 5, 6),
                "error": 0.1,
                "track": [(10, 0)],
            },
            200: {
                "xyz": (7.0, 8.0, 9.0),
                "rgb": (10, 11, 12),
                "error": 3.0,
                "track": [(20, 0)],
            },
        },
        source / "points3D.bin",
    )
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "supported.jpg").write_bytes(b"supported")
    (images_dir / "unsupported.jpg").write_bytes(b"unsupported")

    report = export_colmap_subset(
        str(source),
        str(tmp_path / "cell"),
        ["supported.jpg", "unsupported.jpg"],
        images_dir=str(images_dir),
        max_point_error=1.0,
        image_crops={
            "supported.jpg": NativeImageCrop(
                source_x=0,
                source_y=0,
                width=16,
                height=12,
                source_width=16,
                source_height=12,
            ),
            "unsupported.jpg": NativeImageCrop(
                source_x=0,
                source_y=0,
                width=16,
                height=12,
                source_width=16,
                source_height=12,
            ),
        },
        return_report=True,
    )

    assert isinstance(report, dict)
    sparse = tmp_path / "cell" / "sparse" / "0"
    assert set(_read_colmap_images_bin(sparse / "images.bin")) == {10}
    assert set(_read_colmap_cameras_bin(sparse / "cameras.bin")) == {1}
    assert report["selected_images_before_support_filter"] == 2
    assert report["selected_images"] == 1
    assert report["images_rejected_without_point_support"] == 1
    assert report["image_transport"]["image_count"] == 1
    assert (tmp_path / "cell" / "image_regions.tsv").read_text(
        encoding="utf-8"
    ) == "# dronegs-image-regions-v1\nsupported.jpg\t0\t0\t16\t12\n"


def test_export_colmap_subset_rejects_a_cell_without_supported_images(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_single_image_model(source, "unsupported.jpg")

    with pytest.raises(
        RuntimeError,
        match="no images with retained 3D observations",
    ):
        export_colmap_subset(
            str(source),
            str(tmp_path / "cell"),
            ["unsupported.jpg"],
            max_point_error=0.01,
        )


def test_export_colmap_subset_uses_hardlinks_when_symlinks_are_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    image_name = "nested/keep.jpg"
    _write_single_image_model(source, image_name)
    source_images = tmp_path / "images"
    source_image = source_images / image_name
    source_image.parent.mkdir(parents=True)
    source_image.write_bytes(b"jpeg fixture")

    def reject_symlink(*_args, **_kwargs) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("gaussian_ortho.colmap_subset.os.symlink", reject_symlink)

    report = export_colmap_subset(
        str(source),
        str(tmp_path / "cell"),
        [image_name],
        images_dir=str(source_images),
        return_report=True,
    )

    target_image = tmp_path / "cell" / "images" / image_name
    assert target_image.read_bytes() == b"jpeg fixture"
    assert target_image.stat().st_ino == source_image.stat().st_ino
    assert report["image_transport"] == {
        "strategy": "hardlink",
        "image_count": 1,
        "existing": 0,
        "hardlinked": 1,
        "copied": 0,
        "copied_bytes": 0,
    }


def test_export_colmap_subset_copies_when_links_are_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    image_name = "keep.jpg"
    _write_single_image_model(source, image_name)
    source_images = tmp_path / "images"
    source_images.mkdir()
    source_image = source_images / image_name
    source_image.write_bytes(b"jpeg fixture")

    def reject_link(*_args, **_kwargs) -> None:
        raise PermissionError(1, "Operation not permitted")

    def reject_metadata(*_args, **_kwargs) -> None:
        raise PermissionError(1, "DrvFS rejects utime")

    monkeypatch.setattr("gaussian_ortho.colmap_subset.os.symlink", reject_link)
    monkeypatch.setattr("gaussian_ortho.colmap_subset.os.link", reject_link)
    monkeypatch.setattr("gaussian_ortho.colmap_subset.shutil.copystat", reject_metadata)

    first_report = export_colmap_subset(
        str(source),
        str(tmp_path / "cell"),
        [image_name],
        images_dir=str(source_images),
        return_report=True,
    )
    second_report = export_colmap_subset(
        str(source),
        str(tmp_path / "cell"),
        [image_name],
        images_dir=str(source_images),
        return_report=True,
    )

    target_image = tmp_path / "cell" / "images" / image_name
    assert target_image.read_bytes() == b"jpeg fixture"
    assert target_image.stat().st_ino != source_image.stat().st_ino
    assert first_report["image_transport"] == {
        "strategy": "copy",
        "image_count": 1,
        "existing": 0,
        "hardlinked": 0,
        "copied": 1,
        "copied_bytes": len(b"jpeg fixture"),
    }
    assert second_report["image_transport"] == {
        "strategy": "existing-directory",
        "image_count": 1,
        "existing": 1,
        "hardlinked": 0,
        "copied": 0,
        "copied_bytes": 0,
    }


def test_export_colmap_subset_does_not_publish_an_interrupted_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    image_name = "keep.jpg"
    _write_single_image_model(source, image_name)
    source_images = tmp_path / "images"
    source_images.mkdir()
    source_image = source_images / image_name
    source_image.write_bytes(b"complete jpeg fixture")

    def reject_link(*_args, **_kwargs) -> None:
        raise PermissionError(1, "Operation not permitted")

    def interrupt_copy(_source: Path, destination: Path) -> None:
        Path(destination).write_bytes(b"partial")
        raise OSError("simulated interrupted copy")

    monkeypatch.setattr("gaussian_ortho.colmap_subset.os.symlink", reject_link)
    monkeypatch.setattr("gaussian_ortho.colmap_subset.os.link", reject_link)
    monkeypatch.setattr("gaussian_ortho.colmap_subset.shutil.copyfile", interrupt_copy)

    with pytest.raises(OSError, match="simulated interrupted copy"):
        export_colmap_subset(
            str(source),
            str(tmp_path / "cell"),
            [image_name],
            images_dir=str(source_images),
            return_report=True,
        )

    target_images = tmp_path / "cell" / "images"
    assert not (target_images / image_name).exists()
    assert not list(target_images.glob(f".{image_name}.*.tmp"))
