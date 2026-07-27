"""COLMAP subset export helpers shared by native Gaussian training."""

from __future__ import annotations

import os
import struct
from pathlib import Path


def export_colmap_subset(
    source_sparse_dir: str,
    target_dir: str,
    camera_names: list[str],
    point_ids: set[int] | None = None,
    images_dir: str | None = None,
) -> str:
    """Write a filtered COLMAP sparse reconstruction for one training cell."""
    source = Path(source_sparse_dir)
    target_sparse = Path(target_dir) / "sparse" / "0"
    target_sparse.mkdir(parents=True, exist_ok=True)

    cameras = _read_colmap_cameras_bin(source / "cameras.bin")
    images = _read_colmap_images_bin(source / "images.bin")
    points = _read_colmap_points3d_bin(source / "points3D.bin")

    selected_names = set(camera_names)
    filtered_images = {
        image_id: image
        for image_id, image in images.items()
        if image["name"] in selected_names
    }
    used_camera_ids = {
        image["camera_id"] for image in filtered_images.values()
    }
    filtered_cameras = {
        camera_id: camera
        for camera_id, camera in cameras.items()
        if camera_id in used_camera_ids
    }

    if point_ids is None:
        visible_point_ids = {
            point_id
            for image in filtered_images.values()
            for point_id in image["point3D_ids"]
            if point_id != -1
        }
    else:
        visible_point_ids = point_ids
    filtered_points = {
        point_id: point
        for point_id, point in points.items()
        if point_id in visible_point_ids
    }

    _write_colmap_cameras_bin(
        filtered_cameras, target_sparse / "cameras.bin"
    )
    _write_colmap_images_bin(
        filtered_images, target_sparse / "images.bin"
    )
    _write_colmap_points3d_bin(
        filtered_points, target_sparse / "points3D.bin"
    )

    target_images = Path(target_dir) / "images"
    if images_dir and not target_images.exists():
        os.symlink(os.path.abspath(images_dir), target_images)
    return str(target_sparse)


def _read_colmap_cameras_bin(path: Path) -> dict:
    cameras = {}
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


def _read_colmap_images_bin(path: Path) -> dict:
    images = {}
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
            xys = []
            point3d_ids = []
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


def _read_colmap_points3d_bin(path: Path) -> dict:
    points = {}
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


def _write_colmap_cameras_bin(cameras: dict, path: Path) -> None:
    with path.open("wb") as stream:
        stream.write(struct.pack("<Q", len(cameras)))
        for camera_id, camera in cameras.items():
            stream.write(struct.pack("<I", camera_id))
            stream.write(struct.pack("<i", camera["model_id"]))
            stream.write(struct.pack("<Q", camera["width"]))
            stream.write(struct.pack("<Q", camera["height"]))
            for parameter in camera["params"]:
                stream.write(struct.pack("<d", parameter))


def _write_colmap_images_bin(images: dict, path: Path) -> None:
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


def _write_colmap_points3d_bin(points: dict, path: Path) -> None:
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
