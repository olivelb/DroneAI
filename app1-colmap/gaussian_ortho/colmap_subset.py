"""COLMAP subset export helpers shared by native Gaussian training."""

from __future__ import annotations

import os
import struct
from collections.abc import Mapping
from pathlib import Path
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
    source = Path(source_sparse_dir)
    target_sparse = Path(target_dir) / "sparse" / "0"
    target_sparse.mkdir(parents=True, exist_ok=True)

    cameras = _read_colmap_cameras_bin(source / "cameras.bin")
    images = _read_colmap_images_bin(source / "images.bin")
    points = _read_colmap_points3d_bin(source / "points3D.bin")

    selected_names = set(camera_names)
    filtered_images: dict[int, ColmapImageRecord] = {
        image_id: image
        for image_id, image in images.items()
        if image["name"] in selected_names
    }
    used_camera_ids: set[int] = {
        image["camera_id"] for image in filtered_images.values()
    }
    filtered_cameras: dict[int, ColmapCameraRecord] = {
        camera_id: camera
        for camera_id, camera in cameras.items()
        if camera_id in used_camera_ids
    }

    if point_ids is None:
        visible_point_ids: set[int] = {
            point_id
            for image in filtered_images.values()
            for point_id in image["point3D_ids"]
            if point_id != -1
        }
    else:
        visible_point_ids = point_ids
    filtered_points: dict[int, ColmapPointRecord] = {}
    for point_id, point in points.items():
        if point_id not in visible_point_ids:
            continue
        if max_point_error is not None and point["error"] > float(max_point_error):
            continue
        if len(point["track"]) < int(min_track_length):
            continue
        filtered_points[point_id] = {
            "xyz": point["xyz"],
            "rgb": point["rgb"],
            "error": point["error"],
            "track": [
                observation
                for observation in point["track"]
                if observation[0] in filtered_images
            ],
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
    valid_point_ids = set(filtered_points)
    for image in filtered_images.values():
        image["point3D_ids"] = [
            point_id if point_id in valid_point_ids else -1
            for point_id in image["point3D_ids"]
        ]

    _write_colmap_cameras_bin(
        filtered_cameras, target_sparse / "cameras.bin"
    )
    _write_colmap_images_bin(
        filtered_images, target_sparse / "images.bin"
    )
    _write_colmap_points3d_bin(
        filtered_points, target_sparse / "points3D.bin"
    )

    _write_native_image_regions(
        Path(target_dir) / "image_regions.tsv",
        filtered_images,
        filtered_cameras,
        image_crops or {},
    )

    target_images = Path(target_dir) / "images"
    if images_dir and not target_images.exists():
        os.symlink(os.path.abspath(images_dir), target_images)
    report: dict[str, object] = {
        "sparse_path": str(target_sparse),
        "points_before_cap": points_before_cap,
        "exported_points": len(filtered_points),
        "max_points": max_points,
        "coverage_balanced": len(filtered_points) < points_before_cap,
    }
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
