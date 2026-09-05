"""COLMAP subset export helpers shared by native Gaussian training."""

from __future__ import annotations

import os
import resource
import shutil
import struct
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from time import perf_counter
from typing import TypedDict

import numpy as np

from .camera_footprint import NativeImageCrop


class ColmapCameraRecord(TypedDict):
    model_id: int
    width: int
    height: int
    params: list[float]


class ColmapImageRecord(TypedDict):
    qw: float
    qx: float
    qy: float
    qz: float
    tx: float
    ty: float
    tz: float
    camera_id: int
    name: str
    xys: list[tuple[float, float]]
    point3D_ids: list[int]


class ColmapPointRecord(TypedDict):
    xyz: tuple[float, float, float]
    rgb: tuple[int, int, int]
    error: float
    track: list[tuple[int, int]]


def _copy_file_contents_atomically(source: Path, target: Path) -> None:
    """Copy bytes without POSIX metadata and publish only a complete file.

    Windows-backed WSL mounts can allow file creation while rejecting the
    ``utime`` call performed by :func:`shutil.copy2`.  A sibling temporary file
    also prevents an interrupted retry from treating a partial image as an
    already materialized subset member.
    """

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        shutil.copyfile(source, temporary_path)
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _coverage_balanced_point_ids(
    points: Mapping[int, ColmapPointRecord],
    maximum: int,
) -> set[int]:
    """Retain a deterministic, facade-wide seed when the GPU cap is smaller.

    One high-quality point is first retained per occupied cell in the two
    dominant PCA axes. Remaining capacity is filled by track length and
    reprojection quality. This prevents dense central masonry from evicting
    thin borders merely because COLMAP produced more points than DroneGS can
    fit in VRAM.
    """

    if maximum < 1:
        raise ValueError("max_points must be positive")
    if len(points) <= maximum:
        return set(points)

    point_ids = np.fromiter(points.keys(), dtype=np.int64, count=len(points))
    xyz = np.asarray([point["xyz"] for point in points.values()], dtype=np.float64)
    errors = np.fromiter(
        (float(point["error"]) for point in points.values()),
        dtype=np.float64,
        count=len(points),
    )
    tracks = np.fromiter(
        (len(point["track"]) for point in points.values()),
        dtype=np.int32,
        count=len(points),
    )

    sample = xyz
    if len(sample) > 50_000:
        sample = sample[
            np.linspace(0, len(sample) - 1, 50_000, dtype=np.int64)
        ]
    center = np.median(sample, axis=0)
    covariance = np.cov((sample - center).T)
    _, axes = np.linalg.eigh(covariance)
    projected = (xyz - center) @ axes[:, -2:]
    lower = np.quantile(projected, 0.001, axis=0)
    upper = np.quantile(projected, 0.999, axis=0)
    span = np.maximum(upper - lower, 1e-12)
    grid_side = max(2, int(np.ceil(np.sqrt(maximum * 1.25))))
    cells_xy = np.floor(
        np.clip((projected - lower) / span, 0.0, 1.0)
        * (grid_side - 1)
    ).astype(np.int64)
    cell_ids = cells_xy[:, 0] * grid_side + cells_xy[:, 1]

    # lexsort uses the final key as primary: cell, then longer tracks, lower
    # error, and finally point id for a stable tie-break.
    spatial_order = np.lexsort((point_ids, errors, -tracks, cell_ids))
    ordered_cells = cell_ids[spatial_order]
    first_in_cell: np.ndarray = np.ones(len(spatial_order), dtype=bool)
    first_in_cell[1:] = ordered_cells[1:] != ordered_cells[:-1]
    coverage_indices = spatial_order[first_in_cell]
    if len(coverage_indices) > maximum:
        coverage_indices = coverage_indices[
            np.linspace(0, len(coverage_indices) - 1, maximum, dtype=np.int64)
        ]
        return set(point_ids[coverage_indices].tolist())

    needed = maximum - len(coverage_indices)
    if needed:
        remaining = spatial_order[~first_in_cell]
        quality_order = np.lexsort(
            (point_ids[remaining], errors[remaining], -tracks[remaining])
        )
        coverage_indices = np.concatenate(
            (coverage_indices, remaining[quality_order[:needed]])
        )
    return set(point_ids[coverage_indices].tolist())


def _restrict_track_to_training_regions(
    track: list[tuple[int, int]],
    images: Mapping[int, ColmapImageRecord],
    crops: Mapping[str, NativeImageCrop],
) -> tuple[list[tuple[int, int]], int]:
    """Keep observations available to this resident cell's actual frames."""

    retained: list[tuple[int, int]] = []
    crop_rejections = 0
    for observation in track:
        image = images.get(observation[0])
        if image is None:
            continue
        crop = crops.get(image["name"])
        if crop is None:
            retained.append(observation)
            continue
        point2d_index = observation[1]
        if not 0 <= point2d_index < len(image["xys"]):
            crop_rejections += 1
            continue
        x, y = image["xys"][point2d_index]
        if (
            crop.source_x <= x < crop.source_x + crop.width
            and crop.source_y <= y < crop.source_y + crop.height
        ):
            retained.append(observation)
        else:
            crop_rejections += 1
    return retained, crop_rejections


def _provide_subset_images(
    images_dir: str,
    target_images: Path,
    image_names: Iterable[str],
) -> dict[str, object]:
    """Expose selected images without requiring symlink support.

    Native Linux workspaces retain the zero-copy directory symlink. Mounted
    filesystems such as WSL DrvFS/9p may reject symlink creation, so the
    portable fallback materialises only selected image names, preferring
    same-filesystem hardlinks before copying bytes. Existing directories are
    reconciled to make an interrupted export safe to retry.
    """
    source_images = Path(images_dir).resolve(strict=True)
    if not source_images.is_dir():
        raise NotADirectoryError(f"COLMAP image source is not a directory: {source_images}")
    selected_names = sorted(set(image_names))

    if os.path.lexists(target_images):
        if target_images.is_symlink():
            if target_images.resolve(strict=True) != source_images:
                raise RuntimeError(
                    f"COLMAP subset image link points outside the source: {target_images}"
                )
            return {
                "strategy": "symlink",
                "image_count": len(selected_names),
                "existing": len(selected_names),
                "hardlinked": 0,
                "copied": 0,
                "copied_bytes": 0,
            }
        if not target_images.is_dir():
            raise RuntimeError(
                f"COLMAP subset image target is not a directory: {target_images}"
            )
    else:
        try:
            os.symlink(source_images, target_images)
        except OSError:
            target_images.mkdir(parents=True, exist_ok=True)
        else:
            return {
                "strategy": "symlink",
                "image_count": len(selected_names),
                "existing": 0,
                "hardlinked": 0,
                "copied": 0,
                "copied_bytes": 0,
            }

    existing = 0
    hardlinked = 0
    copied = 0
    copied_bytes = 0
    for image_name in selected_names:
        relative = Path(image_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                f"COLMAP image name must stay inside the image directory: {image_name}"
            )
        source_image = source_images / relative
        if not source_image.is_file():
            raise FileNotFoundError(source_image)
        target_image = target_images / relative
        target_image.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(target_image):
            if not target_image.is_file():
                raise RuntimeError(
                    f"COLMAP subset image target is not a file: {target_image}"
                )
            existing += 1
            continue
        try:
            os.link(source_image, target_image)
        except OSError:
            _copy_file_contents_atomically(source_image, target_image)
            copied += 1
            copied_bytes += source_image.stat().st_size
        else:
            hardlinked += 1

    if existing == len(selected_names):
        strategy = "existing-directory"
    elif hardlinked and copied:
        strategy = "mixed"
    elif copied:
        strategy = "copy"
    else:
        strategy = "hardlink"
    return {
        "strategy": strategy,
        "image_count": len(selected_names),
        "existing": existing,
        "hardlinked": hardlinked,
        "copied": copied,
        "copied_bytes": copied_bytes,
    }


def export_colmap_subset(
    source_sparse_dir: str,
    target_dir: str,
    camera_names: list[str],
    point_ids: set[int] | None = None,
    images_dir: str | None = None,
    image_crops: Mapping[str, NativeImageCrop] | None = None,
    max_point_error: float | None = None,
    min_track_length: int = 0,
    max_points: int | None = None,
    return_report: bool = False,
) -> str | dict[str, object]:
    """Write a filtered COLMAP sparse reconstruction for one training cell."""
    export_started = perf_counter()
    timings: dict[str, float] = {}
    source = Path(source_sparse_dir)
    target_sparse = Path(target_dir) / "sparse" / "0"
    target_sparse.mkdir(parents=True, exist_ok=True)

    phase_started = perf_counter()
    cameras = _read_colmap_cameras_bin(source / "cameras.bin")
    timings["read_cameras"] = perf_counter() - phase_started
    phase_started = perf_counter()
    images = _read_colmap_images_bin(source / "images.bin")
    timings["read_images"] = perf_counter() - phase_started
    phase_started = perf_counter()
    points = _read_colmap_points3d_bin(source / "points3D.bin")
    timings["read_points"] = perf_counter() - phase_started

    phase_started = perf_counter()
    selected_names = set(camera_names)
    filtered_images: dict[int, ColmapImageRecord] = {
        image_id: image
        for image_id, image in images.items()
        if image["name"] in selected_names
    }
    timings["select_cameras"] = perf_counter() - phase_started

    phase_started = perf_counter()
    if point_ids is None:
        visible_point_ids: set[int] = {
            point_id
            for image in filtered_images.values()
            for point_id in image["point3D_ids"]
            if point_id != -1
        }
    else:
        visible_point_ids = point_ids
    crops = image_crops or {}
    unknown_crop_names = set(crops) - selected_names
    if unknown_crop_names:
        raise ValueError(
            "native image crops reference images outside the requested subset: "
            + ", ".join(sorted(unknown_crop_names))
        )
    filtered_points: dict[int, ColmapPointRecord] = {}
    rejected_for_restricted_track = 0
    observations_rejected_outside_native_crops = 0
    for point_id, point in points.items():
        if point_id not in visible_point_ids:
            continue
        if max_point_error is not None and point["error"] > float(max_point_error):
            continue
        restricted_track, crop_rejections = (
            _restrict_track_to_training_regions(
                point["track"],
                filtered_images,
                crops,
            )
        )
        observations_rejected_outside_native_crops += crop_rejections
        # A source point can satisfy the global COLMAP track gate while only
        # one of its observations belongs to this resident cell or falls in
        # its native image crops. Apply the invariant after both restrictions
        # so the exported training seed never claims unavailable evidence.
        if len(restricted_track) < int(min_track_length):
            rejected_for_restricted_track += 1
            continue
        filtered_points[point_id] = {
            "xyz": point["xyz"],
            "rgb": point["rgb"],
            "error": point["error"],
            "track": restricted_track,
        }
    points_before_cap = len(filtered_points)
    if max_points is not None and points_before_cap > int(max_points):
        retained_ids = _coverage_balanced_point_ids(
            filtered_points,
            int(max_points),
        )
        filtered_points = {
            point_id: point
            for point_id, point in filtered_points.items()
            if point_id in retained_ids
        }
    selected_images_before_support_filter = len(filtered_images)
    retained_observations = {
        (image_id, point2d_index): point_id
        for point_id, point in filtered_points.items()
        for image_id, point2d_index in point["track"]
    }
    for image_id, image in filtered_images.items():
        image["point3D_ids"] = [
            (
                point_id
                if retained_observations.get((image_id, point2d_index)) == point_id
                else -1
            )
            for point2d_index, point_id in enumerate(image["point3D_ids"])
        ]
    filtered_images = {
        image_id: image
        for image_id, image in filtered_images.items()
        if any(point_id != -1 for point_id in image["point3D_ids"])
    }
    images_rejected_without_point_support = (
        selected_images_before_support_filter - len(filtered_images)
    )
    if not filtered_images:
        raise RuntimeError(
            "COLMAP subset has no images with retained 3D observations"
        )
    used_camera_ids = {
        image["camera_id"] for image in filtered_images.values()
    }
    filtered_cameras: dict[int, ColmapCameraRecord] = {
        camera_id: camera
        for camera_id, camera in cameras.items()
        if camera_id in used_camera_ids
    }
    timings["filter_points"] = perf_counter() - phase_started
    phase_started = perf_counter()

    _write_colmap_cameras_bin(
        filtered_cameras, target_sparse / "cameras.bin"
    )
    _write_colmap_images_bin(
        filtered_images, target_sparse / "images.bin"
    )
    _write_colmap_points3d_bin(
        filtered_points, target_sparse / "points3D.bin"
    )
    timings["write_sparse"] = perf_counter() - phase_started
    phase_started = perf_counter()

    supported_image_names = {
        image["name"] for image in filtered_images.values()
    }
    _write_native_image_regions(
        Path(target_dir) / "image_regions.tsv",
        filtered_images,
        filtered_cameras,
        {
            name: crop for name, crop in crops.items()
            if name in supported_image_names
        },
    )
    timings["write_regions"] = perf_counter() - phase_started
    phase_started = perf_counter()

    image_transport: dict[str, object] | None = None
    if images_dir:
        image_transport = _provide_subset_images(
            images_dir,
            Path(target_dir) / "images",
            (image["name"] for image in filtered_images.values()),
        )
    timings["prepare_images"] = perf_counter() - phase_started
    exported_track_lengths = [
        len(point["track"])
        for point in filtered_points.values()
    ]
    timings["total"] = perf_counter() - export_started
    report: dict[str, object] = {
        "timings_seconds": timings,
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "sparse_path": str(target_sparse),
        "selected_images": len(filtered_images),
        "selected_images_before_support_filter": (
            selected_images_before_support_filter
        ),
        "images_rejected_without_point_support": images_rejected_without_point_support,
        "points_before_cap": points_before_cap,
        "exported_points": len(filtered_points),
        "max_points": max_points,
        "coverage_balanced": len(filtered_points) < points_before_cap,
        "points_rejected_for_restricted_track": (
            rejected_for_restricted_track
        ),
        "observations_rejected_outside_native_crops": (
            observations_rejected_outside_native_crops
        ),
        "track_scope": "selected-cameras-native-crops-and-supported-images-v2",
        "exported_observations": sum(exported_track_lengths),
        "minimum_exported_track_length": (
            min(exported_track_lengths) if exported_track_lengths else None
        ),
        "median_exported_track_length": (
            float(np.median(exported_track_lengths))
            if exported_track_lengths
            else None
        ),
        "mean_exported_track_length": (
            float(np.mean(exported_track_lengths))
            if exported_track_lengths
            else None
        ),
        "points_with_at_least_three_observations": sum(
            length >= 3 for length in exported_track_lengths
        ),
        "points_with_at_least_five_observations": sum(
            length >= 5 for length in exported_track_lengths
        ),
    }
    if image_transport is not None:
        report["image_transport"] = image_transport
    return report if return_report else str(target_sparse)


def _write_native_image_regions(
    path: Path,
    images: Mapping[int, ColmapImageRecord],
    cameras: Mapping[int, ColmapCameraRecord],
    crops: Mapping[str, NativeImageCrop],
) -> None:
    """Write the native decoder's deterministic per-image crop contract."""
    selected_names = {image["name"] for image in images.values()}
    unknown_names = set(crops) - selected_names
    if unknown_names:
        raise ValueError(
            "native image crops reference images outside the COLMAP subset: "
            + ", ".join(sorted(unknown_names))
        )
    lines = ["# dronegs-image-regions-v1"]
    for image in sorted(images.values(), key=lambda item: item["name"]):
        crop = crops.get(image["name"])
        if crop is None:
            continue
        if "\t" in image["name"] or "\n" in image["name"]:
            raise ValueError("native image crop names cannot contain tabs or newlines")
        camera = cameras[image["camera_id"]]
        if (
            crop.source_width != camera["width"]
            or crop.source_height != camera["height"]
        ):
            raise ValueError("native image crop dimensions do not match COLMAP")
        lines.append(
            "\t".join(
                (
                    image["name"],
                    str(crop.source_x),
                    str(crop.source_y),
                    str(crop.width),
                    str(crop.height),
                )
            )
        )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_colmap_cameras_bin(path: Path) -> dict[int, ColmapCameraRecord]:
    cameras: dict[int, ColmapCameraRecord] = {}
    with path.open("rb") as stream:
        (camera_count,) = struct.unpack("<Q", stream.read(8))
        for _ in range(camera_count):
            (camera_id,) = struct.unpack("<I", stream.read(4))
            (model_id,) = struct.unpack("<i", stream.read(4))
            (width,) = struct.unpack("<Q", stream.read(8))
            (height,) = struct.unpack("<Q", stream.read(8))
            parameter_count = {
                0: 3,
                1: 4,
                2: 4,
                3: 5,
                4: 4,
                5: 5,
                6: 8,
                7: 12,
                8: 4,
                9: 5,
            }.get(model_id, 4)
            parameters = struct.unpack(
                f"<{parameter_count}d",
                stream.read(8 * parameter_count),
            )
            cameras[camera_id] = {
                "model_id": model_id,
                "width": width,
                "height": height,
                "params": list(parameters),
            }
    return cameras


def _read_colmap_images_bin(path: Path) -> dict[int, ColmapImageRecord]:
    images: dict[int, ColmapImageRecord] = {}
    with path.open("rb") as stream:
        (image_count,) = struct.unpack("<Q", stream.read(8))
        for _ in range(image_count):
            (image_id,) = struct.unpack("<I", stream.read(4))
            qw, qx, qy, qz = struct.unpack("<4d", stream.read(32))
            tx, ty, tz = struct.unpack("<3d", stream.read(24))
            (camera_id,) = struct.unpack("<I", stream.read(4))
            name_bytes = bytearray()
            while True:
                character = stream.read(1)
                if character == b"\x00":
                    break
                if not character:
                    raise ValueError("truncated COLMAP image name")
                name_bytes.extend(character)
            (point_count,) = struct.unpack("<Q", stream.read(8))
            xys: list[tuple[float, float]] = []
            point3d_ids: list[int] = []
            for _ in range(point_count):
                xys.append(struct.unpack("<2d", stream.read(16)))
                (point_id,) = struct.unpack("<q", stream.read(8))
                point3d_ids.append(point_id)
            images[image_id] = {
                "qw": qw,
                "qx": qx,
                "qy": qy,
                "qz": qz,
                "tx": tx,
                "ty": ty,
                "tz": tz,
                "camera_id": camera_id,
                "name": name_bytes.decode("utf-8"),
                "xys": xys,
                "point3D_ids": point3d_ids,
            }
    return images


def _read_colmap_points3d_bin(path: Path) -> dict[int, ColmapPointRecord]:
    points: dict[int, ColmapPointRecord] = {}
    with path.open("rb") as stream:
        (point_count,) = struct.unpack("<Q", stream.read(8))
        for _ in range(point_count):
            (point_id,) = struct.unpack("<Q", stream.read(8))
            xyz = struct.unpack("<3d", stream.read(24))
            rgb = struct.unpack("<3B", stream.read(3))
            (error,) = struct.unpack("<d", stream.read(8))
            (track_length,) = struct.unpack("<Q", stream.read(8))
            track = [
                struct.unpack("<2I", stream.read(8))
                for _ in range(track_length)
            ]
            points[point_id] = {
                "xyz": xyz,
                "rgb": rgb,
                "error": error,
                "track": track,
            }
    return points


def _write_colmap_cameras_bin(
    cameras: Mapping[int, ColmapCameraRecord],
    path: Path,
) -> None:
    with path.open("wb") as stream:
        stream.write(struct.pack("<Q", len(cameras)))
        for camera_id, camera in cameras.items():
            stream.write(struct.pack("<I", camera_id))
            stream.write(struct.pack("<i", camera["model_id"]))
            stream.write(struct.pack("<Q", camera["width"]))
            stream.write(struct.pack("<Q", camera["height"]))
            for parameter in camera["params"]:
                stream.write(struct.pack("<d", parameter))


def _write_colmap_images_bin(
    images: Mapping[int, ColmapImageRecord],
    path: Path,
) -> None:
    with path.open("wb") as stream:
        stream.write(struct.pack("<Q", len(images)))
        for image_id, image in images.items():
            stream.write(struct.pack("<I", image_id))
            stream.write(
                struct.pack(
                    "<4d",
                    image["qw"],
                    image["qx"],
                    image["qy"],
                    image["qz"],
                )
            )
            stream.write(
                struct.pack(
                    "<3d", image["tx"], image["ty"], image["tz"]
                )
            )
            stream.write(struct.pack("<I", image["camera_id"]))
            stream.write(image["name"].encode("utf-8") + b"\x00")
            stream.write(struct.pack("<Q", len(image["xys"])))
            for (x, y), point_id in zip(
                image["xys"], image["point3D_ids"], strict=True
            ):
                stream.write(struct.pack("<2d", x, y))
                stream.write(struct.pack("<q", point_id))


def _write_colmap_points3d_bin(
    points: Mapping[int, ColmapPointRecord],
    path: Path,
) -> None:
    with path.open("wb") as stream:
        stream.write(struct.pack("<Q", len(points)))
        for point_id, point in points.items():
            stream.write(struct.pack("<Q", point_id))
            stream.write(struct.pack("<3d", *point["xyz"]))
            stream.write(struct.pack("<3B", *point["rgb"]))
            stream.write(struct.pack("<d", point["error"]))
            stream.write(struct.pack("<Q", len(point["track"])))
            for image_id, point2d_index in point["track"]:
                stream.write(struct.pack("<2I", image_id, point2d_index))
