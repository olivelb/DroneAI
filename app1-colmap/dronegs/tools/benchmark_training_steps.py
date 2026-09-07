#!/usr/bin/env python3
"""Run and preserve a bounded native CUDA benchmark from an existing command/checkpoint."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import time


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def summarize(rows: list[dict[str, str]], views: int, repeats: int) -> dict:
    measured = [r for r in rows if r["warmup"] == "0"]
    if len(measured) != views * repeats:
        raise ValueError("incomplete benchmark sample count")
    result = {}
    for view in range(views):
        group = [r for r in measured if int(r["view"]) == view]
        if len(group) != repeats or len({r["repeat"] for r in group}) != repeats:
            raise ValueError("incomplete or duplicate per-view samples")
        identity = ["frame_index", "scene_index", "tile_index", "image_name", "width", "height",
                    "checkpoint_iteration", "step", "gaussians", "active_sh_degree", "mse_blend",
                    "collect_refinement_statistics"]
        if any(len({r[k] for r in group}) != 1 for k in identity):
            raise ValueError("benchmark state/view identity changed between repeats")
        metrics = {}
        for key in group[0]:
            if not (key.endswith("_ms") or key in {"loss", "restore_seconds", "conditioning_seconds"}):
                continue
            values = [float(r[key]) for r in group]
            if any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError(f"invalid measurement: {key}")
            median = statistics.median(values)
            metrics[key] = {"median": median, "min": min(values), "max": max(values),
                            "mad": statistics.median(abs(v - median) for v in values)}
        if metrics["total_ms"]["median"] <= 0 or metrics["wall_ms"]["median"] <= 0:
            raise ValueError("missing CUDA or wall timing")
        result[str(view)] = {"identity": {k: group[0][k] for k in identity}, "metrics": metrics}
    return result


def make_command(original: list[str], binary: Path, dataset: Path, checkpoint: Path,
                 output: Path, views: int, repeats: int, warmups: int) -> list[str]:
    if not original or len(original) % 2 != 1:
        raise ValueError("original argv must contain an executable and name/value pairs")
    pairs = list(zip(original[1::2], original[2::2]))
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate options in original argv")
    removed = {"--checkpoint-every", "--checkpoint-path", "--resume-from", "--stop-after",
               "--eval-every", "--eval-start", "--benchmark-views", "--benchmark-repeats", "--benchmark-warmups"}
    options = {k: v for k, v in pairs if k not in removed}
    options.update({"--data-path": str(dataset), "--output-path": str(output),
                    "--run-manifest": str(output / "trainer_run.json"), "--resume-from": str(checkpoint),
                    "--save-eval-images": "0", "--benchmark-views": str(views),
                    "--benchmark-repeats": str(repeats), "--benchmark-warmups": str(warmups)})
    return [str(binary), *(item for pair in options.items() for item in pair)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command-json", type=Path, required=True, help="Original command as {argv: [...]} or a list")
    parser.add_argument("--binary", type=Path, required=True, help="dronegs_training_step_benchmark executable")
    parser.add_argument("--data-path", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New experiment directory; never reused")
    parser.add_argument("--source-revision", required=True, help="Commit plus patch/snapshot identity")
    parser.add_argument("--views", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args()
    if not (1 <= args.views <= 100 and 1 <= args.repeats <= 100 and 0 <= args.warmups <= 100):
        parser.error("views/repeats must be 1..100 and warmups 0..100")
    binary, dataset, checkpoint, output = [p.resolve() for p in
        (args.binary, args.data_path, args.checkpoint, args.output)]
    if not binary.is_file() or not checkpoint.is_file() or not dataset.is_dir():
        parser.error("binary, dataset or checkpoint does not exist")
    if output == dataset or dataset in output.parents or output in dataset.parents:
        parser.error("output and dataset must be separate trees")
    original = json.loads(args.command_json.read_text())
    original = original["argv"] if isinstance(original, dict) else original
    command = make_command(original, binary, dataset, checkpoint, output / "native",
                           args.views, args.repeats, args.warmups)
    output.mkdir(parents=True, exist_ok=False)
    state = {"status": "running", "started_unix": time.time(), "source_revision": args.source_revision,
             "argv": command, "binary_sha256": sha256(binary), "checkpoint_sha256_before": sha256(checkpoint),
             "checkpoint_bytes": checkpoint.stat().st_size,
             "warmups_per_view": args.warmups, "repeats_per_view": args.repeats, "views": args.views,
             "protocol": "restore full checkpoint before each step; frames resident; no topology update; final checkpoint probes one step beyond budget with final objective; at least 250ms of forward-only GPU conditioning per sample; timings exclude restore, conditioning and decoding"}
    def save():
        (output / "benchmark.json").write_text(json.dumps(state, indent=2, allow_nan=False) + "\n")
    save()
    try:
        gpu = subprocess.run(["nvidia-smi", "-q"], capture_output=True, text=True, check=False)
        (output / "gpu-before.txt").write_text(gpu.stdout + gpu.stderr)
        with (output / "native.log").open("w") as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        state["returncode"] = process.returncode
        state["checkpoint_sha256_after"] = sha256(checkpoint)
        if state["checkpoint_sha256_before"] != state["checkpoint_sha256_after"]:
            raise RuntimeError("input checkpoint changed during benchmark")
        if process.returncode:
            raise RuntimeError(f"native benchmark failed ({process.returncode}); see native.log")
        with (output / "native/step_benchmark.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != args.views * (args.repeats + args.warmups):
            raise ValueError("unexpected total sample count")
        state["per_view"] = summarize(rows, args.views, args.repeats)
        state["status"] = "completed"
    except Exception as error:
        state["status"] = "failed"
        state["error"] = str(error)
    finally:
        state["finished_unix"] = time.time()
        save()
    print(json.dumps({"status": state["status"], "manifest": str(output / "benchmark.json")}))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
