#!/usr/bin/env python3
"""Compare DroneAI ortho/DEM outputs with georeferenced reference rasters.

The comparison is performed on a shared projected grid. It is deliberately
independent of the native pixel sizes so a higher-resolution candidate is not
penalised merely because the reference product is coarser.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio  # type: ignore[import-untyped]
from PIL import Image
from rasterio.enums import Resampling  # type: ignore[import-untyped]
from rasterio.transform import from_bounds  # type: ignore[import-untyped]
from rasterio.warp import reproject, transform  # type: ignore[import-untyped]


def _intersection_bounds(*datasets: rasterio.DatasetReader) -> tuple[float, ...]:
    crs = datasets[0].crs
    if crs is None or any(dataset.crs != crs for dataset in datasets[1:]):
        raise ValueError("all rasters must use the same defined CRS")
    left = max(dataset.bounds.left for dataset in datasets)
    bottom = max(dataset.bounds.bottom for dataset in datasets)
    right = min(dataset.bounds.right for dataset in datasets)
    top = min(dataset.bounds.top for dataset in datasets)
    if left >= right or bottom >= top:
        raise ValueError("rasters do not overlap")
    return left, bottom, right, top


def _comparison_grid(
    bounds: tuple[float, ...], max_dimension: int
) -> tuple[int, int, rasterio.Affine]:
    left, bottom, right, top = bounds
    width_m = right - left
    height_m = top - bottom
    scale = max_dimension / max(width_m, height_m)
    width = max(1, round(width_m * scale))
    height = max(1, round(height_m * scale))
    return width, height, from_bounds(*bounds, width, height)


def _warp_band(
    dataset: rasterio.DatasetReader,
    band: int,
    *,
    width: int,
    height: int,
    dst_transform: rasterio.Affine,
    resampling: Resampling,
    dtype: np.dtype[Any] = np.dtype("float32"),
    dst_nodata: float = math.nan,
) -> np.ndarray:
    destination = np.full((height, width), dst_nodata, dtype=dtype)
    reproject(
        source=rasterio.band(dataset, band),
        destination=destination,
        src_transform=dataset.transform,
        src_crs=dataset.crs,
        src_nodata=dataset.nodata,
        dst_transform=dst_transform,
        dst_crs=dataset.crs,
        dst_nodata=dst_nodata,
        resampling=resampling,
        num_threads=4,
    )
    return destination


def _stats(values: np.ndarray) -> dict[str, float | int]:
    finite = np.asarray(values[np.isfinite(values)], dtype=np.float64)
    if finite.size == 0:
        raise ValueError("no finite values to summarise")
    return {
        "count": int(finite.size),
        "mean": float(np.mean(finite)),
        "median": float(np.median(finite)),
        "standard_deviation": float(np.std(finite)),
        "rmse": float(np.sqrt(np.mean(np.square(finite)))),
        "p05": float(np.percentile(finite, 5)),
        "p95": float(np.percentile(finite, 95)),
        "minimum": float(np.min(finite)),
        "maximum": float(np.max(finite)),
    }


def _correlation(first: np.ndarray, second: np.ndarray) -> float | None:
    if first.size < 2:
        return None
    first_values = first.astype(np.float64)
    second_values = second.astype(np.float64)
    if np.std(first_values) == 0 or np.std(second_values) == 0:
        return None
    return float(np.corrcoef(first_values, second_values)[0, 1])


def _write_rgb(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8), mode="RGB").save(path)


def _phase_correlation(
    reference_rgb: np.ndarray,
    candidate_rgb: np.ndarray,
    valid: np.ndarray,
    *,
    pixel_size_m: tuple[float, float],
) -> dict[str, Any]:
    rows, columns = np.where(valid)
    if rows.size == 0:
        raise ValueError("no common valid pixels for phase correlation")
    row_slice = slice(int(rows.min()), int(rows.max()) + 1)
    column_slice = slice(int(columns.min()), int(columns.max()) + 1)
    mask = valid[row_slice, column_slice]
    reference = np.mean(reference_rgb[row_slice, column_slice], axis=2).astype(
        np.float32
    )
    candidate = np.mean(candidate_rgb[row_slice, column_slice], axis=2).astype(
        np.float32
    )
    for image in (reference, candidate):
        values = image[mask]
        image -= float(np.mean(values))
        image /= max(float(np.std(values)), 1.0)
        image[~mask] = 0
    window = np.outer(
        np.hanning(reference.shape[0]), np.hanning(reference.shape[1])
    ).astype(np.float32)

    def measure(first: np.ndarray, second: np.ndarray) -> dict[str, Any]:
        first_spectrum = np.fft.rfft2(first * window)
        second_spectrum = np.fft.rfft2(second * window)
        cross_power = second_spectrum * np.conjugate(first_spectrum)
        magnitude = np.abs(cross_power)
        cross_power /= np.maximum(magnitude, np.finfo(np.float64).eps)
        correlation = np.fft.irfft2(cross_power, s=first.shape).real
        peak_indices = np.unravel_index(np.argmax(correlation), correlation.shape)
        peak_row, peak_column = int(peak_indices[0]), int(peak_indices[1])

        def subpixel_offset(index: int, size: int, values: np.ndarray) -> float:
            previous = float(values[(index - 1) % size])
            center = float(values[index])
            following = float(values[(index + 1) % size])
            denominator = previous - 2 * center + following
            return 0.0 if abs(denominator) < 1e-12 else 0.5 * (previous - following) / denominator

        shift_y = float(peak_row) + subpixel_offset(
            peak_row, correlation.shape[0], correlation[:, peak_column]
        )
        shift_x = float(peak_column) + subpixel_offset(
            peak_column, correlation.shape[1], correlation[peak_row, :]
        )
        if shift_y > correlation.shape[0] / 2:
            shift_y -= correlation.shape[0]
        if shift_x > correlation.shape[1] / 2:
            shift_x -= correlation.shape[1]

        sidelobes = correlation.copy()
        radius = 5
        row_indices = np.arange(peak_row - radius, peak_row + radius + 1) % correlation.shape[0]
        column_indices = np.arange(
            peak_column - radius, peak_column + radius + 1
        ) % correlation.shape[1]
        sidelobes[np.ix_(row_indices, column_indices)] = np.nan
        peak_to_sidelobe_ratio = (
            float(
                (correlation[peak_row, peak_column] - np.nanmean(sidelobes))
                / max(float(np.nanstd(sidelobes)), np.finfo(np.float64).eps)
            )
        )
        return {
            "candidate_relative_to_reference_px": [shift_x, shift_y],
            "candidate_relative_to_reference_m": [
                shift_x * pixel_size_m[0],
                shift_y * pixel_size_m[1],
            ],
            "peak_to_sidelobe_ratio": peak_to_sidelobe_ratio,
        }

    reference_dy, reference_dx = np.gradient(reference)
    candidate_dy, candidate_dx = np.gradient(candidate)
    reference_gradient = np.hypot(reference_dx, reference_dy)
    candidate_gradient = np.hypot(candidate_dx, candidate_dy)
    return {
        "grayscale": measure(reference, candidate),
        "gradient_magnitude": measure(reference_gradient, candidate_gradient),
    }


def compare_dem(
    candidate_path: Path,
    reference_path: Path,
    *,
    max_dimension: int,
    preview_path: Path,
) -> dict[str, Any]:
    with rasterio.open(candidate_path) as candidate, rasterio.open(reference_path) as reference:
        bounds = _intersection_bounds(candidate, reference)
        width, height, grid_transform = _comparison_grid(bounds, max_dimension)
        candidate_z = _warp_band(
            candidate,
            1,
            width=width,
            height=height,
            dst_transform=grid_transform,
            resampling=Resampling.bilinear,
        )
        reference_z = _warp_band(
            reference,
            1,
            width=width,
            height=height,
            dst_transform=grid_transform,
            resampling=Resampling.bilinear,
        )
        valid = np.isfinite(candidate_z) & np.isfinite(reference_z)
        if not np.any(valid):
            raise ValueError("DEMs have no common valid samples")
        signed = candidate_z[valid].astype(np.float64) - reference_z[valid]
        absolute = np.abs(signed)
        bias = float(np.mean(signed))
        corrected = signed - bias

        low, high = np.percentile(signed, [2, 98])
        amplitude = max(abs(float(low)), abs(float(high)), 0.01)
        normalized = np.clip((signed + amplitude) / (2 * amplitude), 0, 1)
        heat = np.full((height, width, 3), 255, dtype=np.uint8)
        colors = np.column_stack(
            (
                255 * normalized,
                255 * (1 - np.abs(2 * normalized - 1)),
                255 * (1 - normalized),
            )
        )
        heat[valid] = colors.astype(np.uint8)
        _write_rgb(preview_path, heat)

        return {
            "candidate": str(candidate_path),
            "reference": str(reference_path),
            "crs": candidate.crs.to_string() if candidate.crs else None,
            "overlap_bounds": list(bounds),
            "comparison_grid": {"width": width, "height": height},
            "valid_sample_ratio": float(np.count_nonzero(valid) / valid.size),
            "candidate_height_m": _stats(candidate_z[valid]),
            "reference_height_m": _stats(reference_z[valid]),
            "signed_difference_candidate_minus_reference_m": _stats(signed),
            "absolute_difference_m": _stats(absolute),
            "vertical_bias_m": bias,
            "bias_corrected_rmse_m": float(np.sqrt(np.mean(np.square(corrected)))),
            "height_correlation": _correlation(candidate_z[valid], reference_z[valid]),
            "difference_preview_scale_m": [-amplitude, amplitude],
        }


def _warp_rgb(
    dataset: rasterio.DatasetReader,
    *,
    width: int,
    height: int,
    dst_transform: rasterio.Affine,
) -> tuple[np.ndarray, np.ndarray]:
    rgb = np.stack(
        [
            _warp_band(
                dataset,
                band,
                width=width,
                height=height,
                dst_transform=dst_transform,
                resampling=Resampling.bilinear,
                dst_nodata=0,
            )
            for band in range(1, 4)
        ],
        axis=2,
    )
    if dataset.count >= 4 and dataset.colorinterp[3].name == "alpha":
        alpha = _warp_band(
            dataset,
            4,
            width=width,
            height=height,
            dst_transform=dst_transform,
            resampling=Resampling.nearest,
            dst_nodata=0,
        )
        valid = alpha > 0
    else:
        valid = np.any(rgb > 0, axis=2)
    return rgb, valid


def compare_ortho(
    candidate_path: Path,
    reference_path: Path,
    *,
    max_dimension: int,
    preview_path: Path,
) -> dict[str, Any]:
    with rasterio.open(candidate_path) as candidate, rasterio.open(reference_path) as reference:
        bounds = _intersection_bounds(candidate, reference)
        width, height, grid_transform = _comparison_grid(bounds, max_dimension)
        candidate_rgb, candidate_valid = _warp_rgb(
            candidate, width=width, height=height, dst_transform=grid_transform
        )
        reference_rgb, reference_valid = _warp_rgb(
            reference, width=width, height=height, dst_transform=grid_transform
        )
        valid = candidate_valid & reference_valid
        if not np.any(valid):
            raise ValueError("orthomosaics have no common valid samples")
        candidate_values = candidate_rgb[valid].astype(np.float64)
        reference_values = reference_rgb[valid].astype(np.float64)
        delta = candidate_values - reference_values
        mse = float(np.mean(np.square(delta)))
        grayscale_candidate = np.mean(candidate_values, axis=1)
        grayscale_reference = np.mean(reference_values, axis=1)
        means = np.mean(delta, axis=0)
        pixel_size_m = (
            (bounds[2] - bounds[0]) / width,
            (bounds[3] - bounds[1]) / height,
        )

        candidate_preview = np.full_like(candidate_rgb, 255)
        reference_preview = np.full_like(reference_rgb, 255)
        difference_preview = np.full_like(candidate_rgb, 255)
        candidate_preview[valid] = candidate_rgb[valid]
        reference_preview[valid] = reference_rgb[valid]
        difference_preview[valid] = np.clip(np.abs(delta) * 3, 0, 255)
        separator = np.zeros((height, 8, 3), dtype=np.float32)
        _write_rgb(
            preview_path,
            np.concatenate(
                [reference_preview, separator, candidate_preview, separator, difference_preview],
                axis=1,
            ),
        )

        return {
            "candidate": str(candidate_path),
            "reference": str(reference_path),
            "crs": candidate.crs.to_string() if candidate.crs else None,
            "overlap_bounds": list(bounds),
            "comparison_grid": {"width": width, "height": height},
            "common_valid_sample_ratio": float(np.count_nonzero(valid) / valid.size),
            "candidate_coverage_of_reference": float(
                np.count_nonzero(candidate_valid & reference_valid)
                / np.count_nonzero(reference_valid)
            ),
            "rgb_signed_bias_candidate_minus_reference": {
                "red": float(means[0]),
                "green": float(means[1]),
                "blue": float(means[2]),
            },
            "rgb_mae": float(np.mean(np.abs(delta))),
            "rgb_rmse": math.sqrt(mse),
            "psnr_db": math.inf if mse == 0 else float(20 * math.log10(255 / math.sqrt(mse))),
            "grayscale_correlation": _correlation(grayscale_candidate, grayscale_reference),
            "coarse_registration": _phase_correlation(
                reference_rgb,
                candidate_rgb,
                valid,
                pixel_size_m=pixel_size_m,
            ),
            "preview_panels": ["reference", "candidate", "absolute RGB difference x3"],
        }


def _bilinear_sample(dataset: rasterio.DatasetReader, x: float, y: float) -> float | None:
    column, row = (~dataset.transform) * (x, y)
    c0, r0 = math.floor(column), math.floor(row)
    if c0 < 0 or r0 < 0 or c0 + 1 >= dataset.width or r0 + 1 >= dataset.height:
        return None
    values = dataset.read(1, window=((r0, r0 + 2), (c0, c0 + 2))).astype(np.float64)
    if not np.all(np.isfinite(values)) or (
        dataset.nodata is not None
        and math.isfinite(dataset.nodata)
        and np.any(values == dataset.nodata)
    ):
        return None
    dx, dy = column - c0, row - r0
    return float(
        values[0, 0] * (1 - dx) * (1 - dy)
        + values[0, 1] * dx * (1 - dy)
        + values[1, 0] * (1 - dx) * dy
        + values[1, 1] * dx * dy
    )


def compare_checkpoints(
    candidate_dem_path: Path,
    reference_dem_path: Path,
    gcp_path: Path,
    accuracy_path: Path,
) -> dict[str, Any]:
    roles: dict[str, str] = {}
    with accuracy_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            roles[row["point_id"]] = row["role"]
    surveyed: dict[str, tuple[float, float, float]] = {}
    lines = gcp_path.read_text(encoding="utf-8").splitlines()
    source_crs = lines[0].strip()
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        point_id = fields[-1]
        if roles.get(point_id) == "checkpoint" and point_id not in surveyed:
            surveyed[point_id] = (
                float(fields[0]),
                float(fields[1]),
                float(fields[2]),
            )

    with rasterio.open(candidate_dem_path) as candidate, rasterio.open(reference_dem_path) as reference:
        if candidate.crs != reference.crs:
            raise ValueError("candidate and reference DEM CRS differ")
        source_x = [value[0] for value in surveyed.values()]
        source_y = [value[1] for value in surveyed.values()]
        target_x, target_y = transform(source_crs, candidate.crs, source_x, source_y)
        points: list[dict[str, Any]] = []
        for (point_id, (_, _, surveyed_z)), x, y in zip(
            surveyed.items(), target_x, target_y, strict=True
        ):
            candidate_z = _bilinear_sample(candidate, x, y)
            reference_z = _bilinear_sample(reference, x, y)
            result: dict[str, Any] = {
                "point_id": point_id,
                "projected_xy": [x, y],
                "surveyed_z_m": surveyed_z,
                "candidate_z_m": candidate_z,
                "reference_z_m": reference_z,
            }
            if candidate_z is not None:
                result["candidate_minus_survey_m"] = candidate_z - surveyed_z
            if reference_z is not None:
                result["reference_minus_survey_m"] = reference_z - surveyed_z
            if candidate_z is not None and reference_z is not None:
                result["candidate_minus_reference_m"] = candidate_z - reference_z
            points.append(result)

    candidate_errors = np.asarray(
        [point["candidate_minus_survey_m"] for point in points if "candidate_minus_survey_m" in point]
    )
    reference_errors = np.asarray(
        [point["reference_minus_survey_m"] for point in points if "reference_minus_survey_m" in point]
    )
    differences = np.asarray(
        [point["candidate_minus_reference_m"] for point in points if "candidate_minus_reference_m" in point]
    )
    candidate_points = [
        point for point in points if "candidate_minus_survey_m" in point
    ]
    plane_diagnostic = None
    if len(candidate_points) >= 3:
        coordinates = np.asarray(
            [point["projected_xy"] for point in candidate_points], dtype=np.float64
        )
        errors = np.asarray(
            [point["candidate_minus_survey_m"] for point in candidate_points],
            dtype=np.float64,
        )
        centroid = np.mean(coordinates, axis=0)
        design = np.column_stack(
            (
                np.ones(len(candidate_points)),
                coordinates[:, 0] - centroid[0],
                coordinates[:, 1] - centroid[1],
            )
        )
        coefficients, _, _, _ = np.linalg.lstsq(design, errors, rcond=None)
        residuals = errors - design @ coefficients
        plane_diagnostic = {
            "purpose": "diagnostic only; not a correction or release metric",
            "centroid_xy": centroid.tolist(),
            "intercept_at_centroid_m": float(coefficients[0]),
            "slope_x_m_per_m": float(coefficients[1]),
            "slope_y_m_per_m": float(coefficients[2]),
            "tilt_x_degrees": float(np.degrees(np.arctan(coefficients[1]))),
            "tilt_y_degrees": float(np.degrees(np.arctan(coefficients[2]))),
            "plane_corrected_rmse_m": float(
                np.sqrt(np.mean(np.square(residuals)))
            ),
        }
    return {
        "source_crs": source_crs,
        "raster_crs": str(candidate.crs),
        "checkpoint_count": len(points),
        "candidate_minus_survey_m": _stats(candidate_errors),
        "reference_minus_survey_m": _stats(reference_errors),
        "candidate_minus_reference_m": _stats(differences),
        "candidate_error_plane_diagnostic": plane_diagnostic,
        "points": points,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-ortho", type=Path, required=True)
    parser.add_argument("--candidate-dem", type=Path, required=True)
    parser.add_argument("--reference-ortho", type=Path, required=True)
    parser.add_argument("--reference-dem", type=Path, required=True)
    parser.add_argument("--gcp-list", type=Path, required=True)
    parser.add_argument("--gcp-accuracy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-dimension", type=int, default=2048)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "method": "shared projected grid; bilinear continuous resampling",
        "dem": compare_dem(
            args.candidate_dem,
            args.reference_dem,
            max_dimension=args.max_dimension,
            preview_path=args.output_dir / "dem_difference.png",
        ),
        "orthomosaic": compare_ortho(
            args.candidate_ortho,
            args.reference_ortho,
            max_dimension=args.max_dimension,
            preview_path=args.output_dir / "orthomosaic_comparison.png",
        ),
        "checkpoints": compare_checkpoints(
            args.candidate_dem,
            args.reference_dem,
            args.gcp_list,
            args.gcp_accuracy,
        ),
    }
    output_path = args.output_dir / "comparison.json"
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
