#!/usr/bin/env python3
"""A/B benchmark the legacy and Mip-filtered orthographic rasterizers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
sys.path.insert(0, str(APP1_ROOT))

from gaussian_ortho.gaussian_model import GaussianModel  # noqa: E402
from gaussian_ortho.ortho_renderer import (  # noqa: E402
    compute_ortho_extent,
    render_orthophoto,
)


def parse_center(value: str) -> tuple[float, float]:
    try:
        x, y = value.split(",", 1)
        return float(x), float(y)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("centre must be X,Y") from error


def sharpness_metrics(rgb: np.ndarray) -> dict[str, float]:
    gray = (
        0.2126 * rgb[..., 0].astype(np.float64)
        + 0.7152 * rgb[..., 1].astype(np.float64)
        + 0.0722 * rgb[..., 2].astype(np.float64)
    ) / 255.0
    dx = np.diff(gray, axis=1)
    dy = np.diff(gray, axis=0)
    laplacian = (
        -4.0 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
    )
    return {
        "gradient_rms": float(np.sqrt((np.mean(dx * dx) + np.mean(dy * dy)) / 2)),
        "laplacian_variance": float(np.var(laplacian)),
        "nonwhite_fraction": float(np.mean(np.min(rgb, axis=2) < 250)),
        "mean_luminance": float(np.mean(gray)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ply", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--center", action="append", type=parse_center)
    parser.add_argument("--half-size", type=float, default=3.0)
    parser.add_argument("--gsd", type=float, default=0.005)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model = GaussianModel(sh_degree=3)
    model.load_ply(str(args.ply))
    model.active_sh_degree = min(3, model._features_rest.shape[1] and 3)
    full_extent = compute_ortho_extent(model, pad=0.0)
    centres = args.center or [
        (
            (full_extent[0] + full_extent[1]) / 2,
            (full_extent[2] + full_extent[3]) / 2,
        )
    ]
    variants = {
        "legacy-0.3-uncompensated": (0.3, False),
        "unfiltered-0.0": (0.0, False),
        "mip-0.03-compensated": (0.03, True),
        "mip-0.1-compensated": (0.1, True),
    }
    report = {
        "schema_version": 1,
        "ply": str(args.ply.resolve()),
        "gaussians": int(model.num_gaussians),
        "gsd": args.gsd,
        "half_size": args.half_size,
        "crops": [],
    }
    z_min, z_max = full_extent[4], full_extent[5]
    for index, (x, y) in enumerate(centres, start=1):
        crop_report = {"index": index, "center": [x, y], "variants": {}}
        extent = (
            x - args.half_size,
            x + args.half_size,
            y - args.half_size,
            y + args.half_size,
            z_min,
            z_max,
        )
        for name, (variance, compensate) in variants.items():
            result = render_orthophoto(
                model,
                gsd=args.gsd,
                extent=extent,
                mip_filter_variance=variance,
                mip_filter_compensation=compensate,
            )
            rgb = result["rgb"]
            image_path = args.output_dir / f"crop-{index:02d}-{name}.png"
            Image.fromarray(rgb).save(image_path)
            crop_report["variants"][name] = {
                "image": str(image_path.resolve()),
                **sharpness_metrics(rgb),
            }
        report["crops"].append(crop_report)

    metric_names = ("gradient_rms", "laplacian_variance", "nonwhite_fraction")
    report["aggregate"] = {
        name: {
            metric: float(
                np.mean(
                    [crop["variants"][name][metric] for crop in report["crops"]]
                )
            )
            for metric in metric_names
        }
        for name in variants
    }
    output = args.output_dir / "report.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["aggregate"], indent=2))


if __name__ == "__main__":
    main()
