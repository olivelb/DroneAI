#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Evaluate exact DroneGS held-out RGB pairs with official LPIPS."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import statistics
import tempfile
from typing import Iterable


@dataclass(frozen=True)
class EvaluationPair:
    index: int
    prediction: Path
    target: Path


def _indexed_ppm(directory: Path) -> dict[int, Path]:
    result: dict[int, Path] = {}
    if not directory.is_dir():
        raise FileNotFoundError(f"missing evaluation directory: {directory}")
    for path in directory.glob("*.ppm"):
        try:
            index = int(path.stem)
        except ValueError as exc:
            raise ValueError(
                f"evaluation filename must be a zero-padded integer: {path.name}"
            ) from exc
        if index in result:
            raise ValueError(f"duplicate evaluation index {index} in {directory}")
        result[index] = path
    return result


def discover_pairs(evaluation_dir: Path) -> list[EvaluationPair]:
    predictions = _indexed_ppm(evaluation_dir / "predictions")
    targets = _indexed_ppm(evaluation_dir / "targets")
    if not predictions:
        raise ValueError("LPIPS requires at least one exported held-out prediction")
    if predictions.keys() != targets.keys():
        missing_targets = sorted(predictions.keys() - targets.keys())
        missing_predictions = sorted(targets.keys() - predictions.keys())
        raise ValueError(
            "prediction/target indices differ: "
            f"missing_targets={missing_targets}, "
            f"missing_predictions={missing_predictions}"
        )
    return [
        EvaluationPair(index, predictions[index], targets[index])
        for index in sorted(predictions)
    ]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _image_names(metrics_csv: Path) -> dict[int, str]:
    names: dict[int, str] = {}
    if not metrics_csv.is_file():
        return names
    with metrics_csv.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row.get("stage") != "final":
                continue
            names[int(row["held_out_index"])] = row["image_name"]
    return names


def _load_lpips_runtime(net: str, version: str, requested_device: str):
    try:
        import lpips
        import torch
        from PIL import Image
        from torchvision.transforms.functional import pil_to_tensor
    except ImportError as exc:
        raise RuntimeError(
            "LPIPS runtime missing; use Dockerfile.lpips or install the "
            "pinned requirements-lpips.txt environment"
        ) from exc

    if requested_device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = requested_device
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("--device cuda requested but PyTorch CUDA is unavailable")

    model = lpips.LPIPS(net=net, version=version, verbose=False).to(device)
    model.eval()

    def load(path: Path):
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            tensor = pil_to_tensor(rgb).to(dtype=torch.float32).div_(255.0)
        return tensor.mul_(2.0).sub_(1.0).unsqueeze(0).to(device)

    return torch, model, load, device


def evaluate_pairs(
    pairs: Iterable[EvaluationPair],
    net: str,
    version: str,
    requested_device: str,
) -> tuple[list[float], str]:
    torch, model, load, device = _load_lpips_runtime(
        net, version, requested_device
    )
    values: list[float] = []
    with torch.inference_mode():
        for pair in pairs:
            prediction = load(pair.prediction)
            target = load(pair.target)
            if prediction.shape != target.shape:
                raise ValueError(
                    f"LPIPS pair {pair.index} shape mismatch: "
                    f"{tuple(prediction.shape)} vs {tuple(target.shape)}"
                )
            value = float(model(prediction, target).reshape(-1).mean().item())
            if not math.isfinite(value):
                raise RuntimeError(f"LPIPS pair {pair.index} is non-finite")
            values.append(value)
    return values, device


def write_results(
    evaluation_dir: Path,
    pairs: list[EvaluationPair],
    values: list[float],
    net: str,
    version: str,
    device: str,
) -> dict[str, object]:
    if len(pairs) != len(values) or not values:
        raise ValueError("LPIPS result cardinality mismatch")
    names = _image_names(evaluation_dir / "metrics.csv")
    rows = ["held_out_index,image_name,lpips\n"]
    for pair, value in zip(pairs, values, strict=True):
        escaped_name = names.get(pair.index, "").replace('"', '""')
        rows.append(f'{pair.index},"{escaped_name}",{value:.10g}\n')
    _atomic_text(evaluation_dir / "lpips.csv", "".join(rows))

    summary: dict[str, object] = {
        "metric": "LPIPS",
        "network": net,
        "version": version,
        "input_range": "[-1,1]",
        "pair_source": "exact_trainer_rgb8_ppm",
        "device": device,
        "views": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p95": _percentile(values, 0.95),
        "maximum": max(values),
    }
    _atomic_text(
        evaluation_dir / "lpips.json",
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    return summary


def update_manifest(
    manifest_path: Path, evaluation_dir: Path, summary: dict[str, object]
) -> None:
    with manifest_path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    metrics = manifest.setdefault("metrics", {})
    metrics["lpips"] = summary["mean"]
    metrics["lpips_network"] = summary["network"]
    metrics["lpips_version"] = summary["version"]
    metrics["lpips_views"] = summary["views"]
    artifacts = manifest.setdefault("artifacts", {})
    for filename in ("lpips.csv", "lpips.json"):
        path = evaluation_dir / filename
        key = f"evaluation/{filename}"
        artifacts[key] = {
            "path": str(path),
            "sha256": None,
            "bytes": path.stat().st_size,
        }
    _atomic_text(
        manifest_path,
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate exact DroneGS held-out prediction/target pairs"
    )
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--net", choices=("alex", "vgg", "squeeze"), default="alex")
    parser.add_argument("--version", default="0.1")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pairs = discover_pairs(args.evaluation_dir)
    values, device = evaluate_pairs(
        pairs, args.net, args.version, args.device
    )
    summary = write_results(
        args.evaluation_dir, pairs, values, args.net, args.version, device
    )
    if args.manifest is not None:
        update_manifest(args.manifest, args.evaluation_dir, summary)
    print(json.dumps({"event": "lpips_completed", **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
