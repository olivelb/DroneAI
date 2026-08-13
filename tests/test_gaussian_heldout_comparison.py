from __future__ import annotations

import csv
from pathlib import Path

import cv2
import numpy as np
import pytest

from tools.render_gaussian_heldout_comparison import render_comparisons


def _write_evaluation(
    root: Path,
    *,
    scores: list[float],
    target_offset: int = 0,
) -> Path:
    evaluation = root / "evaluation"
    predictions = evaluation / "predictions"
    targets = evaluation / "targets"
    predictions.mkdir(parents=True)
    targets.mkdir()
    fields = [
        "stage",
        "held_out_index",
        "image_name",
        "tile_index",
        "psnr",
        "ssim",
    ]
    with (evaluation / "metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index, score in enumerate(scores):
            writer.writerow(
                {
                    "stage": "final",
                    "held_out_index": index,
                    "image_name": f"image-{index}.jpg",
                    "tile_index": 0,
                    "psnr": score,
                    "ssim": 0.5 + index / 100,
                }
            )
            target = np.full(
                (32, 48, 3),
                50 + index + target_offset,
                dtype=np.uint8,
            )
            prediction = np.clip(target + index + 1, 0, 255).astype(np.uint8)
            assert cv2.imwrite(str(targets / f"{index:06d}.ppm"), target)
            assert cv2.imwrite(str(predictions / f"{index:06d}.ppm"), prediction)
    return evaluation


def test_render_comparisons_selects_weakest_and_median(tmp_path):
    baseline = _write_evaluation(tmp_path / "baseline", scores=[25.0, 25.0, 25.0])
    candidate = _write_evaluation(tmp_path / "candidate", scores=[30.0, 10.0, 20.0])

    report = render_comparisons(
        {"camera-only": baseline, "crop-aware": candidate},
        tmp_path / "comparison",
        panel_width=128,
    )

    assert report["common_held_out_views"] == 3
    assert report["selector_run"] == "crop-aware"
    assert report["selections"]["weakest"]["image_name"] == "image-1.jpg"
    assert report["selections"]["median"]["image_name"] == "image-2.jpg"
    assert (tmp_path / "comparison" / "weakest.png").is_file()
    assert (tmp_path / "comparison" / "median.png").is_file()
    assert (tmp_path / "comparison" / "comparison.json").is_file()


def test_render_comparisons_rejects_target_drift(tmp_path):
    baseline = _write_evaluation(tmp_path / "baseline", scores=[20.0])
    candidate = _write_evaluation(
        tmp_path / "candidate",
        scores=[21.0],
        target_offset=1,
    )

    with pytest.raises(ValueError, match="held-out target drift"):
        render_comparisons(
            {"camera-only": baseline, "crop-aware": candidate},
            tmp_path / "comparison",
            panel_width=128,
        )
