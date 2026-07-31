"""Fast, bounded sparse-alignment helpers shared by workers and local tools.

The module deliberately keeps pairing and command construction independent
from Kafka/S3 so they can be unit-tested without a COLMAP installation.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

import numpy as np


CASPAR_CAMERA_MODELS = frozenset({"PINHOLE", "SIMPLE_RADIAL"})
CAMERA_MODEL_NAMES = {
    0: "SIMPLE_PINHOLE",
    1: "PINHOLE",
    2: "SIMPLE_RADIAL",
    3: "RADIAL",
    4: "OPENCV",
    5: "OPENCV_FISHEYE",
    6: "FULL_OPENCV",
    7: "FOV",
    8: "SIMPLE_RADIAL_FISHEYE",
    9: "RADIAL_FISHEYE",
    10: "THIN_PRISM_FISHEYE",
}


def atomic_write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def parse_colmap_reference_file(
    path: str | os.PathLike[str],
) -> list[dict[str, Any]]:
    """Read ``image_name x y z`` while tolerating spaces in image names."""

    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.rsplit(maxsplit=3)
            if len(fields) != 4:
                raise ValueError(
                    f"Invalid COLMAP reference line {line_number} in {path}: {line}"
                )
            name, x_raw, y_raw, z_raw = fields
            try:
                xyz = (float(x_raw), float(y_raw), float(z_raw))
            except ValueError as error:
                raise ValueError(
                    f"Invalid coordinates on line {line_number} in {path}: {line}"
                ) from error
            if not all(math.isfinite(value) for value in xyz):
                continue
            records.append({"name": name, "xyz": xyz})
    return records


def positioned_records_from_preflight(
    records: Iterable[dict[str, Any]],
    projected_crs: str,
) -> list[dict[str, Any]]:
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
    positioned: list[dict[str, Any]] = []
    for record in records:
        gps = record.get("gps")
        if not gps:
            continue
        x, y = transformer.transform(gps["longitude"], gps["latitude"])
        z = gps.get("altitude_m")
        positioned.append(
            {
                "name": record["file"],
                "xyz": (float(x), float(y), float(z or 0.0)),
            }
        )
    return positioned


def _temporal_groups(positioned: Sequence[dict[str, Any]]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(positioned):
        parent = PurePosixPath(str(record["name"]).replace("\\", "/")).parent.as_posix()
        groups[parent].append(index)
    return groups


def build_gps_pair_graph(
    positioned: Sequence[dict[str, Any]],
    *,
    max_neighbors: int = 32,
    min_neighbors: int = 8,
    temporal_neighbors: int = 6,
    max_distance_m: float = 0.0,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Build a bounded k-NN/temporal graph in projected coordinates.

    ``max_distance_m=0`` disables the distance cap. The cap is soft for the
    nearest ``min_neighbors`` so isolated turns or separate flight strips do
    not become disconnected merely because their spacing exceeds the guess.
    """

    if len(positioned) < 2:
        return [], {
            "positioned_images": len(positioned),
            "pair_count": 0,
            "minimum_degree": 0,
            "maximum_degree": 0,
            "mean_degree": 0.0,
        }
    if max_neighbors < 1:
        raise ValueError("max_neighbors must be positive")
    if min_neighbors < 1 or min_neighbors > max_neighbors:
        raise ValueError("min_neighbors must be between 1 and max_neighbors")
    if temporal_neighbors < 0:
        raise ValueError("temporal_neighbors cannot be negative")

    coordinates = np.asarray(
        [record["xyz"][:2] for record in positioned],
        dtype=np.float64,
    )
    pair_indices: set[tuple[int, int]] = set()

    def add_pair(first: int, second: int) -> None:
        if first == second:
            return
        pair_indices.add((first, second) if first < second else (second, first))

    query_count = min(len(positioned), max_neighbors + 1)
    try:
        from scipy.spatial import cKDTree

        distances, neighbor_indices = cKDTree(coordinates).query(
            coordinates,
            k=query_count,
            workers=-1,
        )
        if query_count == 1:
            distances = distances[:, np.newaxis]
            neighbor_indices = neighbor_indices[:, np.newaxis]
        for source_index, (row_distances, row_indices) in enumerate(
            zip(distances, neighbor_indices)
        ):
            accepted = 0
            for distance, target_index in zip(row_distances, row_indices):
                target_index = int(target_index)
                if target_index == source_index:
                    continue
                if (
                    max_distance_m > 0
                    and float(distance) > max_distance_m
                    and accepted >= min_neighbors
                ):
                    continue
                add_pair(source_index, target_index)
                accepted += 1
                if accepted >= max_neighbors:
                    break
    except ImportError:
        for source_index, source in enumerate(coordinates):
            distances = np.linalg.norm(coordinates - source, axis=1)
            candidates = np.argsort(distances)
            accepted = 0
            for target_index in candidates:
                target_index = int(target_index)
                if target_index == source_index:
                    continue
                if (
                    max_distance_m > 0
                    and float(distances[target_index]) > max_distance_m
                    and accepted >= min_neighbors
                ):
                    continue
                add_pair(source_index, target_index)
                accepted += 1
                if accepted >= max_neighbors:
                    break

    for group_indices in _temporal_groups(positioned).values():
        for local_index, source_index in enumerate(group_indices):
            start = max(0, local_index - temporal_neighbors)
            end = min(len(group_indices), local_index + temporal_neighbors + 1)
            for target_index in group_indices[start:end]:
                add_pair(source_index, target_index)

    degrees = [0] * len(positioned)
    pairs: list[tuple[str, str]] = []
    for first, second in sorted(pair_indices):
        degrees[first] += 1
        degrees[second] += 1
        pairs.append((str(positioned[first]["name"]), str(positioned[second]["name"])))

    stats = {
        "positioned_images": len(positioned),
        "pair_count": len(pairs),
        "minimum_degree": min(degrees),
        "maximum_degree": max(degrees),
        "mean_degree": float(sum(degrees) / len(degrees)),
        "max_neighbors": max_neighbors,
        "min_neighbors": min_neighbors,
        "temporal_neighbors": temporal_neighbors,
        "max_distance_m": max_distance_m,
    }
    return pairs, stats


def write_pair_list(
    path: str | os.PathLike[str],
    pairs: Iterable[tuple[str, str]],
) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized = sorted(set(pairs))
    temporary = destination.with_name(f"{destination.name}.tmp")
    temporary.write_text(
        "".join(f"{first} {second}\n" for first, second in normalized),
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return len(normalized)


def camera_models_in_database(
    database_path: str | os.PathLike[str],
) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT DISTINCT model FROM cameras").fetchall()
    return {CAMERA_MODEL_NAMES.get(int(row[0]), f"MODEL_{row[0]}") for row in rows}


def caspar_compatibility(
    database_path: str | os.PathLike[str],
) -> tuple[bool, set[str]]:
    models = camera_models_in_database(database_path)
    return bool(models) and models <= CASPAR_CAMERA_MODELS, models


def database_counts(database_path: str | os.PathLike[str]) -> dict[str, int]:
    if not Path(database_path).is_file():
        return {"images": 0, "two_view_geometries": 0}
    with sqlite3.connect(database_path) as connection:
        images = int(connection.execute("SELECT COUNT(*) FROM images").fetchone()[0])
        geometries = int(
            connection.execute("SELECT COUNT(*) FROM two_view_geometries").fetchone()[0]
        )
    return {"images": images, "two_view_geometries": geometries}


def build_mapping_command(
    engine: str,
    *,
    database_path: str | os.PathLike[str],
    image_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    gpu_index: str | int = "0",
    global_max_tracks: int = 2_000_000,
    global_ba_iterations: int = 1,
    global_ceres_iterations: int = 50,
    global_skip_retriangulation: bool = True,
    global_random_seed: int = 42,
    global_ba_min_track_length: int = 3,
    global_tri_complete_max_reproj_error: float = 15.0,
    global_tri_merge_max_reproj_error: float = 15.0,
    global_tri_min_angle: float = 1.0,
) -> list[str]:
    normalized = engine.strip().lower()
    common = [
        "colmap",
        "global_mapper" if normalized == "glomap" else "mapper",
        "--database_path",
        str(database_path),
        "--image_path",
        str(image_path),
        "--output_path",
        str(output_path),
    ]
    if normalized == "glomap":
        command = common + [
            "--GlobalMapper.gp_use_gpu",
            "1",
            "--GlobalMapper.gp_gpu_index",
            str(gpu_index),
            "--GlobalMapper.ba_ceres_use_gpu",
            "1",
            "--GlobalMapper.ba_ceres_gpu_index",
            str(gpu_index),
            "--GlobalMapper.ba_num_iterations",
            str(global_ba_iterations),
            "--GlobalMapper.ba_ceres_max_num_iterations",
            str(global_ceres_iterations),
            "--GlobalMapper.keep_max_num_tracks",
            str(global_max_tracks),
            "--GlobalMapper.random_seed",
            str(global_random_seed),
            "--GlobalMapper.ba_min_track_length",
            str(global_ba_min_track_length),
            "--GlobalMapper.tri_complete_max_reproj_error",
            str(global_tri_complete_max_reproj_error),
            "--GlobalMapper.tri_merge_max_reproj_error",
            str(global_tri_merge_max_reproj_error),
            "--GlobalMapper.tri_min_angle",
            str(global_tri_min_angle),
        ]
        if global_skip_retriangulation:
            command += ["--GlobalMapper.skip_retriangulation", "1"]
        return command
    if normalized == "caspar":
        return common + [
            "--Mapper.ba_local_backend",
            "CASPAR",
            "--Mapper.ba_global_backend",
            "CASPAR",
            "--Mapper.ba_gpu_index",
            str(gpu_index),
            "--Mapper.ba_refine_focal_length",
            "1",
            "--Mapper.ba_refine_extra_params",
            "1",
            "--Mapper.ba_refine_principal_point",
            "0",
            "--Mapper.ba_global_ignore_redundant_points3D",
            "1",
            "--Mapper.ba_local_max_num_iterations",
            "20",
            "--Mapper.ba_global_max_num_iterations",
            "30",
            "--Mapper.multiple_models",
            "0",
        ]
    if normalized == "ceres":
        return common + [
            "--Mapper.ba_local_backend",
            "CERES",
            "--Mapper.ba_global_backend",
            "CERES",
            "--Mapper.ba_use_gpu",
            "1",
            "--Mapper.ba_gpu_index",
            str(gpu_index),
            "--Mapper.ba_global_ignore_redundant_points3D",
            "1",
            "--Mapper.multiple_models",
            "0",
        ]
    raise ValueError(f"Unknown alignment engine: {engine}")


def choose_auto_fallback(camera_models: Iterable[str]) -> str:
    normalized = {str(model).upper() for model in camera_models}
    return "caspar" if normalized and normalized <= CASPAR_CAMERA_MODELS else "ceres"
