#!/usr/bin/env python3
"""Compare DroneAI facade colour/depth products with a reference PLY."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.compare_facade_rasters import compare as compare_colours  # noqa: E402


PLY_SCALAR_TYPES = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "<i2",
    "int16": "<i2",
    "ushort": "<u2",
    "uint16": "<u2",
    "int": "<i4",
    "int32": "<i4",
    "uint": "<u4",
    "uint32": "<u4",
    "float": "<f4",
    "float32": "<f4",
    "double": "<f8",
    "float64": "<f8",
}


@dataclass(frozen=True)
class PlyVertices:
    records: np.memmap
    count: int
    data_offset: int


@dataclass(frozen=True)
class ReferenceRaster:
    colour: np.ndarray
    depth: np.ndarray
    valid: np.ndarray
    resolution: float
    extent: tuple[float, float, float, float]
    point_count: int
    frame: dict[str, Any]


def open_binary_ply_vertices(path: Path) -> PlyVertices:
    """Memory-map a scalar binary little-endian PLY vertex table."""

    properties: list[tuple[str, str]] = []
    vertex_count: int | None = None
    in_vertices = False
    with path.open("rb") as stream:
        if stream.readline().strip() != b"ply":
            raise ValueError("reference is not a PLY file")
        if stream.readline().strip() != b"format binary_little_endian 1.0":
            raise ValueError("reference PLY must be binary little-endian 1.0")
        while True:
            raw = stream.readline()
            if not raw:
                raise ValueError("reference PLY header has no end_header")
            line = raw.decode("ascii").strip()
            if line == "end_header":
                data_offset = stream.tell()
                break
            fields = line.split()
            if fields[:2] == ["element", "vertex"] and len(fields) == 3:
                vertex_count = int(fields[2])
                in_vertices = True
            elif fields[:1] == ["element"]:
                in_vertices = False
            elif in_vertices and fields[:1] == ["property"]:
                if len(fields) != 3 or fields[1] == "list":
                    raise ValueError("vertex list properties are not supported")
                try:
                    scalar_type = PLY_SCALAR_TYPES[fields[1]]
                except KeyError as error:
                    raise ValueError(
                        f"unsupported PLY scalar type {fields[1]!r}"
                    ) from error
                properties.append((fields[2], scalar_type))

    if vertex_count is None or vertex_count <= 0:
        raise ValueError("reference PLY has no vertices")
    names = {name for name, _scalar_type in properties}
    required = {"x", "y", "z", "red", "green", "blue"}
    if not required <= names:
        raise ValueError(
            "reference PLY needs x/y/z and red/green/blue vertex properties"
        )
    dtype = np.dtype(properties, align=False)
    expected_size = data_offset + vertex_count * dtype.itemsize
    if path.stat().st_size < expected_size:
        raise ValueError("reference PLY vertex payload is truncated")
    records: np.memmap = np.memmap(
        path,
        dtype=dtype,
        mode="r",
        offset=data_offset,
        shape=(vertex_count,),
    )
    return PlyVertices(records=records, count=vertex_count, data_offset=data_offset)


def _sample_xyz(
    vertices: PlyVertices,
    maximum: int = 500_000,
) -> NDArray[np.float64]:
    stride = max(1, math.ceil(vertices.count / maximum))
    sampled = vertices.records[::stride]
    xyz: NDArray[np.float64] = np.asarray(
        np.column_stack((sampled["x"], sampled["y"], sampled["z"])),
        dtype=np.float64,
    )
    finite = np.isfinite(xyz).all(axis=1)
    xyz = xyz[finite]
    if len(xyz) < 100:
        raise ValueError("reference PLY has too few finite vertices")
    return xyz


def estimate_reference_frame(vertices: PlyVertices) -> dict[str, Any]:
    """Fit a robust facade plane while preserving world vertical."""

    sample = _sample_xyz(vertices)
    working = sample
    center = np.median(working, axis=0)
    normal = np.array([1.0, 0.0, 0.0])
    for retained_fraction in (1.0, 0.95, 0.90, 0.85):
        if retained_fraction < 1.0:
            distance = np.abs((sample - center) @ normal)
            working = sample[distance <= np.quantile(distance, retained_fraction)]
        center = np.median(working, axis=0)
        covariance = np.cov((working - center).T)
        _eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        normal = eigenvectors[:, 0]
        normal /= np.linalg.norm(normal)

    world_up = np.array([0.0, 0.0, 1.0])
    vertical = world_up - normal * float(world_up @ normal)
    if np.linalg.norm(vertical) < 0.25:
        raise ValueError("reference facade plane is incompatible with world vertical")
    vertical /= np.linalg.norm(vertical)
    horizontal = np.cross(vertical, normal)
    horizontal /= np.linalg.norm(horizontal)
    if float(horizontal @ np.array([1.0, 0.0, 0.0])) < 0.0:
        horizontal = -horizontal
        normal = -normal
    axes = np.stack((horizontal, vertical, normal))
    local = (sample - center) @ axes.T
    distance = local[:, 2]
    return {
        "origin": center,
        "world_to_facade": axes,
        "sample_points": sample,
        "plane_rmse": float(np.sqrt(np.mean(np.square(distance)))),
        "plane_depth_p05_p95": np.quantile(distance, [0.05, 0.95]).tolist(),
    }


def rasterize_reference_ply(
    path: Path,
    *,
    requested_resolution: float,
    maximum_pixels: int = 24_000_000,
    chunk_size: int = 1_000_000,
) -> ReferenceRaster:
    """Project reference points into a bounded facade colour/depth raster."""

    if requested_resolution <= 0.0:
        raise ValueError("reference resolution must be positive")
    vertices = open_binary_ply_vertices(path)
    fitted = estimate_reference_frame(vertices)
    origin = np.asarray(fitted["origin"], dtype=np.float64)
    axes = np.asarray(fitted["world_to_facade"], dtype=np.float64)
    sample = np.asarray(fitted.pop("sample_points"), dtype=np.float64)
    sample_local = (sample - origin) @ axes.T
    x_min, x_max = np.quantile(sample_local[:, 0], [0.001, 0.999])
    y_min, y_max = np.quantile(sample_local[:, 1], [0.001, 0.999])
    if x_max <= x_min or y_max <= y_min:
        raise ValueError("reference facade extent is empty")

    resolution = float(requested_resolution)
    width = max(1, math.ceil((x_max - x_min) / resolution))
    height = max(1, math.ceil((y_max - y_min) / resolution))
    if width * height > maximum_pixels:
        resolution *= math.sqrt((width * height) / maximum_pixels)
        width = max(1, math.ceil((x_max - x_min) / resolution))
        height = max(1, math.ceil((y_max - y_min) / resolution))
    pixel_count = width * height
    count = np.zeros(pixel_count, dtype=np.uint64)
    depth_sum = np.zeros(pixel_count, dtype=np.float64)
    colour_sum = np.zeros((3, pixel_count), dtype=np.float64)
    accepted_points = 0

    records = vertices.records
    for start in range(0, vertices.count, chunk_size):
        batch = records[start : start + chunk_size]
        xyz = np.column_stack((batch["x"], batch["y"], batch["z"])).astype(
            np.float64,
            copy=False,
        )
        local = (xyz - origin) @ axes.T
        finite = np.isfinite(local).all(axis=1)
        px = np.floor((local[:, 0] - x_min) / resolution).astype(np.int64)
        py = np.floor((y_max - local[:, 1]) / resolution).astype(np.int64)
        valid = finite & (px >= 0) & (px < width) & (py >= 0) & (py < height)
        if not np.any(valid):
            continue
        index = py[valid] * width + px[valid]
        accepted_points += int(np.count_nonzero(valid))
        count += np.bincount(index, minlength=pixel_count).astype(np.uint64)
        depth_sum += np.bincount(
            index,
            weights=local[valid, 2],
            minlength=pixel_count,
        )
        for channel, name in enumerate(("red", "green", "blue")):
            colour_sum[channel] += np.bincount(
                index,
                weights=np.asarray(batch[name])[valid],
                minlength=pixel_count,
            )

    occupied = count > 0
    if not np.any(occupied):
        raise ValueError("reference facade raster has no occupied pixels")
    depth = np.full(pixel_count, np.nan, dtype=np.float32)
    depth[occupied] = (depth_sum[occupied] / count[occupied]).astype(np.float32)
    colour = np.full((pixel_count, 3), 255, dtype=np.uint8)
    for channel in range(3):
        colour[occupied, channel] = np.clip(
            np.rint(colour_sum[channel, occupied] / count[occupied]),
            0,
            255,
        ).astype(np.uint8)
    return ReferenceRaster(
        colour=colour.reshape(height, width, 3),
        depth=depth.reshape(height, width),
        valid=occupied.reshape(height, width),
        resolution=resolution,
        extent=(float(x_min), float(x_max), float(y_min), float(y_max)),
        point_count=accepted_points,
        frame={
            "origin": origin.tolist(),
            "world_to_facade": axes.tolist(),
            **fitted,
        },
    )


def compare_depth_arrays(
    candidate: np.ndarray,
    reference: np.ndarray,
    common: np.ndarray,
) -> dict[str, Any]:
    """Compare local depths with a fixed metric scale and free plane offset."""

    valid = common & np.isfinite(candidate) & np.isfinite(reference)
    if np.count_nonzero(valid) < 100:
        raise ValueError("facade depth products have too little common coverage")
    source = np.asarray(candidate[valid], dtype=np.float64)
    target = np.asarray(reference[valid], dtype=np.float64)
    alternatives = []
    for sign in (1.0, -1.0):
        offset = float(np.median(target - sign * source))
        residual = sign * source + offset - target
        alternatives.append((float(np.median(np.abs(residual))), sign, offset, residual))
    median_absolute, sign, offset, residual = min(alternatives, key=lambda item: item[0])
    absolute = np.abs(residual)
    correlation = float(np.corrcoef(sign * source, target)[0, 1])
    return {
        "common_pixels": int(np.count_nonzero(valid)),
        "candidate_depth_sign": int(sign),
        "fitted_plane_offset_m": offset,
        "median_error_m": float(np.median(residual)),
        "median_absolute_error_m": median_absolute,
        "p90_absolute_error_m": float(np.quantile(absolute, 0.90)),
        "p95_absolute_error_m": float(np.quantile(absolute, 0.95)),
        "rmse_m": float(np.sqrt(np.mean(np.square(residual)))),
        "correlation": correlation,
        "absolute_error": absolute,
        "valid": valid,
    }


def _read_height(path: Path) -> NDArray[np.float32]:
    height = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if height is None:
        raise FileNotFoundError(path)
    if height.ndim == 3:
        height = height[:, :, 0]
    result: NDArray[np.float32] = np.asarray(height, dtype=np.float32)
    return result


def compare_products(
    colour_path: Path,
    height_path: Path,
    reference_ply: Path,
    *,
    reference_preview_path: Path,
    resolution: float,
) -> tuple[dict[str, Any], np.ndarray]:
    reference = rasterize_reference_ply(
        reference_ply,
        requested_resolution=resolution,
    )
    reference_preview_path.parent.mkdir(parents=True, exist_ok=True)
    reference_bgr = cv2.cvtColor(reference.colour, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(reference_preview_path), reference_bgr):
        raise RuntimeError(f"failed to write {reference_preview_path}")

    colour_metrics, colour_preview = compare_colours(
        colour_path,
        reference_preview_path,
    )
    homography = np.asarray(
        colour_metrics["homography_source_to_reference"],
        dtype=np.float64,
    )
    candidate_height = _read_height(height_path)
    source_valid = np.isfinite(candidate_height)
    height, width = reference.depth.shape
    aligned_height = cv2.warpPerspective(
        candidate_height,
        homography,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderValue=float("nan"),
    )
    aligned_valid = cv2.warpPerspective(
        source_valid.astype(np.uint8),
        homography,
        (width, height),
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    ).astype(bool)
    depth = compare_depth_arrays(
        aligned_height,
        reference.depth,
        aligned_valid & reference.valid,
    )
    error = np.zeros((height, width), dtype=np.uint8)
    valid_error = depth.pop("valid")
    absolute_error = depth.pop("absolute_error")
    display_max = max(float(np.quantile(absolute_error, 0.95)), 1.0e-6)
    error[valid_error] = np.clip(
        np.rint(255.0 * absolute_error / display_max),
        0,
        255,
    ).astype(np.uint8)
    error_panel = cv2.applyColorMap(error, cv2.COLORMAP_TURBO)
    error_panel[~valid_error] = 0
    divider = np.zeros((height, 8, 3), dtype=np.uint8)
    preview = np.concatenate((colour_preview, divider, error_panel), axis=1)
    report = {
        "schema_version": 1,
        "candidate": {
            "colour": str(colour_path),
            "height": str(height_path),
        },
        "reference": {
            "point_cloud": str(reference_ply),
            "accepted_points": reference.point_count,
            "raster_width": width,
            "raster_height": height,
            "resolution_m": reference.resolution,
            "extent_m": list(reference.extent),
            "frame": reference.frame,
        },
        "colour": colour_metrics,
        "depth": {
            **depth,
            "error_preview_p95_m": display_max,
            "reference_occupied_ratio": float(np.mean(reference.valid)),
            "candidate_overlap_of_reference": float(
                np.count_nonzero(aligned_valid & reference.valid)
                / np.count_nonzero(reference.valid)
            ),
        },
    }
    return report, preview


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("colour", type=Path)
    parser.add_argument("height", type=Path)
    parser.add_argument("reference_ply", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--reference-preview", type=Path, required=True)
    parser.add_argument("--reference-resolution", type=float, default=0.01)
    args = parser.parse_args()
    report, preview = compare_products(
        args.colour,
        args.height,
        args.reference_ply,
        reference_preview_path=args.reference_preview,
        resolution=args.reference_resolution,
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.preview.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not cv2.imwrite(str(args.preview), preview):
        raise RuntimeError(f"failed to write {args.preview}")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
