#!/usr/bin/env python3
"""Render reproducible real/Gaussian held-out view comparisons."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class HeldOutView:
    image_name: str
    tile_index: int
    held_out_index: int
    psnr: float
    ssim: float

    @property
    def key(self) -> tuple[str, int]:
        return self.image_name, self.tile_index


def _load_final_views(evaluation_dir: Path) -> dict[tuple[str, int], HeldOutView]:
    metrics_path = evaluation_dir / "metrics.csv"
    with metrics_path.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if row["stage"] == "final"]
    if not rows:
        raise ValueError(f"no final held-out metrics in {metrics_path}")
    views = {
        (row["image_name"], int(row["tile_index"])): HeldOutView(
            image_name=row["image_name"],
            tile_index=int(row["tile_index"]),
            held_out_index=int(row["held_out_index"]),
            psnr=float(row["psnr"]),
            ssim=float(row["ssim"]),
        )
        for row in rows
    }
    if len(views) != len(rows):
        raise ValueError(f"duplicate final held-out view key in {metrics_path}")
    return views


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_rgb(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot decode held-out image {path}")
    return image


def _fit_panel(image: np.ndarray, width: int) -> np.ndarray:
    scale = min(1.0, width / image.shape[1])
    resized = cv2.resize(
        image,
        (
            max(1, round(image.shape[1] * scale)),
            max(1, round(image.shape[0] * scale)),
        ),
        interpolation=cv2.INTER_AREA,
    )
    canvas = np.full((resized.shape[0] + 52, width, 3), 246, dtype=np.uint8)
    x = (width - resized.shape[1]) // 2
    canvas[52:, x : x + resized.shape[1]] = resized
    return canvas


def _label(panel: np.ndarray, text: str) -> None:
    cv2.putText(
        panel,
        text,
        (14, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (20, 20, 20),
        2,
        cv2.LINE_AA,
    )


def _comparison_panel(
    evaluation_dirs: dict[str, Path],
    views: dict[str, HeldOutView],
    *,
    panel_width: int,
) -> tuple[np.ndarray, str]:
    first_label = next(iter(evaluation_dirs))
    first_view = views[first_label]
    first_target = evaluation_dirs[first_label] / "targets" / (f"{first_view.held_out_index:06d}.ppm")
    target_digest = _sha256(first_target)
    target = _read_rgb(first_target)
    images: list[np.ndarray] = [target]
    labels = ["Real target"]
    errors: list[np.ndarray] = []
    error_labels: list[str] = []
    for label, evaluation_dir in evaluation_dirs.items():
        view = views[label]
        target_path = evaluation_dir / "targets" / f"{view.held_out_index:06d}.ppm"
        if _sha256(target_path) != target_digest:
            raise ValueError(f"held-out target drift for {view.key!r} in {label}")
        prediction = _read_rgb(evaluation_dir / "predictions" / f"{view.held_out_index:06d}.ppm")
        if prediction.shape != target.shape:
            raise ValueError(f"held-out prediction dimensions changed in {label}")
        images.append(prediction)
        labels.append(f"{label}: {view.psnr:.3f} dB / {view.ssim:.3f}")
        errors.append(
            np.clip(
                np.abs(prediction.astype(np.int16) - target.astype(np.int16)) * 3,
                0,
                255,
            ).astype(np.uint8)
        )
        error_labels.append(f"{label}: absolute RGB error x3")
    panels = [_fit_panel(image, panel_width) for image in images + errors]
    for panel, label in zip(panels, labels + error_labels, strict=True):
        _label(panel, label)
    maximum_height = max(panel.shape[0] for panel in panels)
    for index, panel in enumerate(panels):
        if panel.shape[0] < maximum_height:
            panels[index] = cv2.copyMakeBorder(
                panel,
                0,
                maximum_height - panel.shape[0],
                0,
                0,
                cv2.BORDER_CONSTANT,
                value=(246, 246, 246),
            )
    divider = np.full((maximum_height, 6, 3), 48, dtype=np.uint8)
    montage = panels[0]
    for panel in panels[1:]:
        montage = np.concatenate((montage, divider, panel), axis=1)
    return montage, target_digest


def render_comparisons(
    evaluation_dirs: dict[str, Path],
    output_dir: Path,
    *,
    panel_width: int = 720,
) -> dict[str, object]:
    if len(evaluation_dirs) < 1:
        raise ValueError("at least one Gaussian run is required")
    if panel_width < 128:
        raise ValueError("panel width must be at least 128 pixels")
    all_views = {label: _load_final_views(path) for label, path in evaluation_dirs.items()}
    common_keys = set.intersection(*(set(views) for views in all_views.values()))
    if not common_keys:
        raise ValueError("Gaussian runs have no common held-out views")
    selector_label = next(reversed(evaluation_dirs))
    selector = all_views[selector_label]
    ordered = sorted(common_keys, key=lambda key: selector[key].psnr)
    selected = {
        "weakest": ordered[0],
        "median": ordered[(len(ordered) - 1) // 2],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, object] = {}
    for selection, key in selected.items():
        selected_views = {label: views[key] for label, views in all_views.items()}
        montage, target_digest = _comparison_panel(
            evaluation_dirs,
            selected_views,
            panel_width=panel_width,
        )
        output_path = output_dir / f"{selection}.png"
        if not cv2.imwrite(str(output_path), montage):
            raise RuntimeError(f"failed to write {output_path}")
        records[selection] = {
            "image_name": key[0],
            "tile_index": key[1],
            "target_sha256": target_digest,
            "output": str(output_path),
            "runs": {label: {"psnr": view.psnr, "ssim": view.ssim} for label, view in selected_views.items()},
        }
    report: dict[str, object] = {
        "contract_version": 1,
        "common_held_out_views": len(common_keys),
        "selector_run": selector_label,
        "selections": records,
    }
    (output_dir / "comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _parse_run(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label or not path:
        raise argparse.ArgumentTypeError("run must use LABEL=EVALUATION_DIR")
    return label, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, type=_parse_run)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--panel-width", type=int, default=720)
    args = parser.parse_args()
    evaluation_dirs = dict(args.run)
    if len(evaluation_dirs) != len(args.run):
        parser.error("run labels must be unique")
    report = render_comparisons(
        evaluation_dirs,
        args.output_dir,
        panel_width=args.panel_width,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
