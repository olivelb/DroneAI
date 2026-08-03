#!/usr/bin/env python3
"""Align and compare a rendered facade elevation with a reference raster."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def _sharpness(gray: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(gx, gy)
    values = mask.astype(bool)
    if not np.any(values):
        raise ValueError("the aligned rasters have no common content")
    return {
        "laplacian_variance": float(np.var(laplacian[values])),
        "gradient_mean": float(np.mean(gradient[values])),
        "gradient_p90": float(np.percentile(gradient[values], 90)),
    }


def _coverage_grid(
    aligned_content: np.ndarray,
    reference_content: np.ndarray,
    *,
    rows: int = 12,
    columns: int = 8,
) -> dict:
    """Report local coverage so a missing border cannot hide in a global ratio."""

    height, width = reference_content.shape
    cells = []
    for row in range(rows):
        y0 = row * height // rows
        y1 = (row + 1) * height // rows
        for column in range(columns):
            x0 = column * width // columns
            x1 = (column + 1) * width // columns
            reference_cell = reference_content[y0:y1, x0:x1]
            reference_pixels = int(np.count_nonzero(reference_cell))
            if reference_pixels == 0:
                continue
            covered = int(
                np.count_nonzero(
                    aligned_content[y0:y1, x0:x1] & reference_cell
                )
            )
            cells.append(
                {
                    "row": row,
                    "column": column,
                    "coverage": float(covered / reference_pixels),
                }
            )

    values = np.asarray([cell["coverage"] for cell in cells], dtype=np.float64)
    if values.size == 0:
        raise ValueError("the reference raster has no measurable content cells")
    return {
        "rows": rows,
        "columns": columns,
        "minimum": float(np.min(values)),
        "p10": float(np.percentile(values, 10)),
        "median": float(np.median(values)),
        "cells_below_90_percent": int(np.count_nonzero(values < 0.9)),
        "worst_cells": sorted(cells, key=lambda cell: cell["coverage"])[:10],
    }


def compare(source_path: Path, reference_path: Path) -> tuple[dict, np.ndarray]:
    source = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    reference = cv2.imread(str(reference_path), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError(source_path)
    if reference is None:
        raise FileNotFoundError(reference_path)

    source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=20_000)
    source_keypoints, source_descriptors = sift.detectAndCompute(source_gray, None)
    reference_keypoints, reference_descriptors = sift.detectAndCompute(
        reference_gray, None
    )
    if source_descriptors is None or reference_descriptors is None:
        raise ValueError("SIFT could not find descriptors in both rasters")

    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(
        source_descriptors, reference_descriptors, k=2
    )
    matches = [first for first, second in pairs if first.distance < 0.75 * second.distance]
    if len(matches) < 8:
        raise ValueError(f"only {len(matches)} robust SIFT matches")
    source_points = np.float32(
        [source_keypoints[match.queryIdx].pt for match in matches]
    )
    reference_points = np.float32(
        [reference_keypoints[match.trainIdx].pt for match in matches]
    )
    homography, inlier_mask = cv2.findHomography(
        source_points, reference_points, cv2.RANSAC, 5.0
    )
    if homography is None or inlier_mask is None:
        raise ValueError("robust homography estimation failed")
    inliers = inlier_mask.ravel().astype(bool)
    projected = cv2.perspectiveTransform(
        source_points[inliers, None, :], homography
    )[:, 0, :]
    residuals = np.linalg.norm(projected - reference_points[inliers], axis=1)

    height, width = reference.shape[:2]
    aligned = cv2.warpPerspective(
        source,
        homography,
        (width, height),
        flags=cv2.INTER_LANCZOS4,
        borderValue=(255, 255, 255),
    )
    reference_content = np.min(reference, axis=2) < 248
    reference_pixels = int(np.count_nonzero(reference_content))
    coverage_by_threshold = {}
    aligned_by_threshold = {}
    for threshold in (248, 240, 232, 224):
        source_content = np.min(source, axis=2) < threshold
        aligned_content = cv2.warpPerspective(
            source_content.astype(np.uint8),
            homography,
            (width, height),
            flags=cv2.INTER_NEAREST,
            borderValue=0,
        ).astype(bool)
        aligned_by_threshold[threshold] = aligned_content
        coverage_by_threshold[str(threshold)] = (
            float(
                np.count_nonzero(aligned_content & reference_content)
                / reference_pixels
            )
            if reference_pixels
            else 0.0
        )
    aligned_content = aligned_by_threshold[248]
    common = aligned_content & reference_content

    metrics = {
        "ratio_matches": len(matches),
        "homography_inliers": int(np.count_nonzero(inliers)),
        "homography_residual_px": {
            "median": float(np.median(residuals)),
            "rmse": float(np.sqrt(np.mean(np.square(residuals)))),
            "p90": float(np.percentile(residuals, 90)),
        },
        "coverage_of_reference_content": (
            float(np.count_nonzero(common) / reference_pixels)
            if reference_pixels
            else 0.0
        ),
        "coverage_by_source_darkness_threshold": coverage_by_threshold,
        "coverage_grid": {
            "nominal_248": _coverage_grid(
                aligned_by_threshold[248], reference_content
            ),
            "usable_240": _coverage_grid(
                aligned_by_threshold[240], reference_content
            ),
        },
        "sharpness_common_mask": {
            "reference": _sharpness(reference_gray, common),
            "source": _sharpness(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), common),
        },
        "homography_source_to_reference": homography.tolist(),
    }
    divider = np.zeros((height, 8, 3), dtype=np.uint8)
    comparison = np.concatenate([reference, divider, aligned], axis=1)
    return metrics, comparison


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    args = parser.parse_args()

    metrics, preview = compare(args.source, args.reference)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.preview.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    if not cv2.imwrite(str(args.preview), preview):
        raise RuntimeError(f"failed to write {args.preview}")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
