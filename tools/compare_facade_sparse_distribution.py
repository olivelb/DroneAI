#!/usr/bin/env python3
"""Compare sparse facade coverage after aligning two COLMAP models by cameras."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_DIR = REPO_ROOT / "app1-colmap"
if str(APP1_DIR) not in sys.path:
    sys.path.insert(0, str(APP1_DIR))

from gaussian_ortho.facade_frame import estimate_facade_frame


def _camera_center(image) -> np.ndarray:
    cam_from_world = image.cam_from_world()
    rotation = cam_from_world.rotation.matrix()
    return -rotation.T @ cam_from_world.translation


def _camera_records(reconstruction) -> dict[str, SimpleNamespace]:
    records = {}
    for image in reconstruction.images.values():
        cam_from_world = image.cam_from_world()
        rotation = cam_from_world.rotation.matrix()
        records[image.name] = SimpleNamespace(
            T=_camera_center(image),
            R=rotation.T,
        )
    return records


def _quality_points(
    reconstruction,
    *,
    max_reprojection_error: float,
    min_track_length: int,
) -> np.ndarray:
    points = [
        np.asarray(point.xyz, dtype=np.float64)
        for point in reconstruction.points3D.values()
        if float(point.error) <= max_reprojection_error
        and point.track.length() >= min_track_length
    ]
    if len(points) < 30:
        raise ValueError("A sparse model needs at least 30 accepted points")
    return np.stack(points)


def fit_similarity(source: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Fit target ~= scale * rotation @ source + translation with Umeyama."""

    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("Similarity inputs must be matching N x 3 arrays")
    if len(source) < 3:
        raise ValueError("At least three correspondences are required")
    source_mean = source.mean(axis=0)
    target_mean = target.mean(axis=0)
    source_centered = source - source_mean
    target_centered = target - target_mean
    covariance = target_centered.T @ source_centered / len(source)
    u, singular_values, vt = np.linalg.svd(covariance)
    correction = np.eye(3)
    if np.linalg.det(u @ vt) < 0:
        correction[-1, -1] = -1
    rotation = u @ correction @ vt
    source_variance = float(np.mean(np.sum(source_centered**2, axis=1)))
    if source_variance <= 1e-12:
        raise ValueError("Camera correspondences have no usable baseline")
    scale = float(np.sum(singular_values * np.diag(correction)) / source_variance)
    translation = target_mean - scale * (rotation @ source_mean)
    return scale, rotation, translation


def robust_similarity(
    source: np.ndarray,
    target: np.ndarray,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mask = np.ones(len(source), dtype=bool)
    for _ in range(6):
        scale, rotation, translation = fit_similarity(source[mask], target[mask])
        transformed = (scale * (rotation @ source.T)).T + translation
        residuals = np.linalg.norm(transformed - target, axis=1)
        median = float(np.median(residuals[mask]))
        mad = float(np.median(np.abs(residuals[mask] - median)))
        threshold = median + max(4.0 * 1.4826 * mad, 1e-9)
        updated = residuals <= threshold
        if updated.sum() < max(20, math.ceil(len(source) * 0.5)):
            updated = residuals <= np.quantile(residuals, 0.85)
        if np.array_equal(updated, mask):
            break
        mask = updated
    scale, rotation, translation = fit_similarity(source[mask], target[mask])
    transformed = (scale * (rotation @ source.T)).T + translation
    residuals = np.linalg.norm(transformed - target, axis=1)
    return scale, rotation, translation, mask, residuals


def _grid_metrics(counts: np.ndarray) -> dict:
    flat = counts.ravel().astype(np.float64)
    total = float(flat.sum())
    occupied = flat > 0
    probabilities = flat[occupied] / max(total, 1.0)
    entropy = float(-np.sum(probabilities * np.log(probabilities)))
    effective_cells = float(np.exp(entropy)) if len(probabilities) else 0.0
    descending = np.sort(flat)[::-1]

    def top_concentration(fraction: float) -> float:
        cells = max(1, math.ceil(len(flat) * fraction))
        return float(descending[:cells].sum() / max(total, 1.0))

    return {
        "points_in_bounds": int(total),
        "occupied_cells": int(occupied.sum()),
        "occupied_cell_ratio": float(occupied.mean()),
        "effective_cells": effective_cells,
        "effective_cell_ratio": effective_cells / len(flat),
        "top_1pct_cell_concentration": top_concentration(0.01),
        "top_5pct_cell_concentration": top_concentration(0.05),
        "top_10pct_cell_concentration": top_concentration(0.10),
        "median_points_per_occupied_cell": (
            float(np.median(flat[occupied])) if occupied.any() else 0.0
        ),
        "p90_points_per_occupied_cell": (
            float(np.quantile(flat[occupied], 0.90)) if occupied.any() else 0.0
        ),
    }


def compare_grids(reference: np.ndarray, candidate: np.ndarray) -> dict:
    reference_occupied = reference > 0
    candidate_occupied = candidate > 0
    union = reference_occupied | candidate_occupied
    intersection = reference_occupied & candidate_occupied
    return {
        "reference_cells_retained": float(
            candidate_occupied[reference_occupied].mean()
        ),
        "intersection_over_union": float(
            intersection.sum() / max(int(union.sum()), 1)
        ),
        "candidate_new_cell_ratio": float(
            (candidate_occupied & ~reference_occupied).sum()
            / max(int(candidate_occupied.sum()), 1)
        ),
    }


def _heatmap(counts: np.ndarray, scale_max: float) -> np.ndarray:
    normalized = np.log1p(counts) / max(math.log1p(scale_max), 1e-9)
    normalized = np.clip(normalized, 0.0, 1.0)
    red = np.clip(2.0 * normalized - 0.25, 0.0, 1.0)
    green = np.clip(2.0 * normalized, 0.0, 1.0)
    blue = np.clip(1.4 - 2.0 * normalized, 0.0, 1.0)
    return (255 * np.stack([red, green, blue], axis=-1)).astype(np.uint8)


def write_preview(
    path: Path,
    reference: np.ndarray,
    candidate: np.ndarray,
) -> None:
    nonzero = np.concatenate(
        [reference[reference > 0], candidate[candidate > 0]]
    )
    scale_max = float(np.quantile(nonzero, 0.99)) if len(nonzero) else 1.0
    panels = []
    for label, counts in (("reference", reference), ("without detail views", candidate)):
        pixels = np.flipud(_heatmap(counts, scale_max))
        panel = Image.fromarray(pixels).resize(
            (counts.shape[1] * 8, counts.shape[0] * 8),
            Image.Resampling.NEAREST,
        )
        canvas = Image.new("RGB", (panel.width, panel.height + 28), "white")
        canvas.paste(panel, (0, 28))
        ImageDraw.Draw(canvas).text((8, 7), label, fill="black")
        panels.append(canvas)
    output = Image.new(
        "RGB",
        (panels[0].width + panels[1].width + 8, panels[0].height),
        "white",
    )
    output.paste(panels[0], (0, 0))
    output.paste(panels[1], (panels[0].width + 8, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    output.save(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_model", type=Path)
    parser.add_argument("candidate_model", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--max-reprojection-error", type=float, default=2.0)
    parser.add_argument("--min-track-length", type=int, default=2)
    parser.add_argument("--grid-width", type=int, default=96)
    parser.add_argument("--grid-height", type=int, default=64)
    return parser.parse_args()


def main() -> int:
    import pycolmap

    args = parse_args()
    reference = pycolmap.Reconstruction(str(args.reference_model))
    candidate = pycolmap.Reconstruction(str(args.candidate_model))
    reference_cameras = _camera_records(reference)
    candidate_cameras = _camera_records(candidate)
    common_names = sorted(reference_cameras.keys() & candidate_cameras.keys())
    if len(common_names) < 20:
        raise ValueError("Models need at least 20 common registered images")
    source_centers = np.stack([candidate_cameras[name].T for name in common_names])
    target_centers = np.stack([reference_cameras[name].T for name in common_names])
    scale, rotation, translation, inliers, residuals = robust_similarity(
        source_centers,
        target_centers,
    )

    reference_points = _quality_points(
        reference,
        max_reprojection_error=args.max_reprojection_error,
        min_track_length=args.min_track_length,
    )
    candidate_points = _quality_points(
        candidate,
        max_reprojection_error=args.max_reprojection_error,
        min_track_length=args.min_track_length,
    )
    candidate_points = (scale * (rotation @ candidate_points.T)).T + translation
    frame = estimate_facade_frame(
        reference_points,
        list(reference_cameras.values()),
    )
    reference_local = (
        frame.world_to_facade @ (reference_points - frame.origin).T
    ).T
    candidate_local = (
        frame.world_to_facade @ (candidate_points - frame.origin).T
    ).T
    x_bounds = np.quantile(reference_local[:, 0], [0.002, 0.998])
    y_bounds = np.quantile(reference_local[:, 1], [0.002, 0.998])
    histogram_range = (tuple(y_bounds), tuple(x_bounds))
    bins = (args.grid_height, args.grid_width)
    reference_grid, _, _ = np.histogram2d(
        reference_local[:, 1],
        reference_local[:, 0],
        bins=bins,
        range=histogram_range,
    )
    candidate_grid, _, _ = np.histogram2d(
        candidate_local[:, 1],
        candidate_local[:, 0],
        bins=bins,
        range=histogram_range,
    )

    payload = {
        "schema_version": 1,
        "reference_model": str(args.reference_model),
        "candidate_model": str(args.candidate_model),
        "quality_gate": {
            "max_reprojection_error_px": args.max_reprojection_error,
            "min_track_length": args.min_track_length,
        },
        "alignment": {
            "common_cameras": len(common_names),
            "inlier_cameras": int(inliers.sum()),
            "scale_candidate_to_reference": scale,
            "rotation_candidate_to_reference": rotation.tolist(),
            "translation_candidate_to_reference": translation.tolist(),
            "median_camera_residual": float(np.median(residuals[inliers])),
            "p90_camera_residual": float(np.quantile(residuals[inliers], 0.90)),
        },
        "facade_frame": frame.as_dict(),
        "grid": {
            "width": args.grid_width,
            "height": args.grid_height,
            "x_bounds_reference_units": x_bounds.tolist(),
            "y_bounds_reference_units": y_bounds.tolist(),
        },
        "reference": {
            "registered_images": len(reference.images),
            "accepted_points": len(reference_points),
            **_grid_metrics(reference_grid),
        },
        "candidate": {
            "registered_images": len(candidate.images),
            "accepted_points": len(candidate_points),
            **_grid_metrics(candidate_grid),
        },
        "coverage_comparison": compare_grids(reference_grid, candidate_grid),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if args.preview:
        write_preview(args.preview, reference_grid, candidate_grid)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
