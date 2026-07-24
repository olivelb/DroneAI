from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training.benchmark import (  # noqa: E402
    BenchmarkBackend,
    BenchmarkCase,
    BenchmarkSuite,
    VramSampler,
    dataset_inventory,
    expand_command,
    load_benchmark_suite,
    percentile_nearest_rank,
    read_ply_vertex_count,
    run_benchmark_suite,
    summarize_observations,
)


PARAMETERS = {
    "iterations": 10,
    "strategy": "mrnf",
    "sh_degree": 1,
    "max_cap": 100,
    "resize_factor": 4,
    "max_width": 1024,
    "tile_mode": 4,
    "seed": 40,
}


def write_suite(path: Path, **overrides) -> None:
    payload = {
        "schema_version": 1,
        "name": "test-suite",
        "repetitions": 2,
        "backends": [{
            "name": "fake",
            "command": ["trainer", "--out", "{output_path}", "--seed", "{seed}"],
        }],
        "cases": [{
            "name": "tiny", "data_path": "${DATASET}", "parameters": PARAMETERS,
        }],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_suite_and_expand_command(tmp_path, monkeypatch):
    suite_path = tmp_path / "suite.json"
    write_suite(suite_path)
    monkeypatch.setenv("DATASET", str(tmp_path / "dataset"))
    suite = load_benchmark_suite(suite_path)
    command = expand_command(
        suite.backends[0], suite.cases[0], tmp_path / "out", repetition=2
    )
    assert suite.repetitions == 2
    assert command[-1] == "41"
    assert command[2] == str(tmp_path / "out")


def test_suite_validation_rejects_missing_parameter(tmp_path):
    suite_path = tmp_path / "suite.json"
    broken = dict(PARAMETERS)
    broken.pop("max_cap")
    write_suite(suite_path, cases=[{
        "name": "tiny", "data_path": "/tmp/data", "parameters": broken,
    }])
    with pytest.raises(ValueError, match="max_cap"):
        load_benchmark_suite(suite_path)


def test_dataset_inventory_changes_with_sparse_content(tmp_path):
    dataset = tmp_path / "dataset"
    images = dataset / "images"
    sparse = dataset / "sparse" / "0"
    images.mkdir(parents=True)
    sparse.mkdir(parents=True)
    (images / "a.jpg").write_bytes(b"image")
    (sparse / "cameras.bin").write_bytes(b"one")
    first = dataset_inventory(dataset)
    (sparse / "cameras.bin").write_bytes(b"two")
    second = dataset_inventory(dataset)
    assert first["image_count"] == 1
    assert first["fingerprint"] != second["fingerprint"]


def test_read_ply_vertex_count(tmp_path):
    ply = tmp_path / "point_cloud.ply"
    ply.write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 27\n"
        b"property float x\nend_header\n"
    )
    assert read_ply_vertex_count(ply) == 27


def test_percentile_and_summary():
    observations = [{
        "case": "case",
        "backend": "backend",
        "status": "completed",
        "timings": {"wall_seconds": value},
        "hardware": {"peak_vram_mib": None},
    } for value in (1.0, 2.0, 3.0, 4.0, 5.0)]
    observations.append({
        "case": "case",
        "backend": "backend",
        "status": "failed",
        "timings": {"wall_seconds": 100.0},
        "hardware": {"peak_vram_mib": None},
    })
    summary = summarize_observations(observations)[0]
    assert percentile_nearest_rank([1, 2, 3, 4, 5], 0.95) == 5
    assert summary["successful_runs"] == 5
    assert summary["wall_seconds"]["median"] == 3.0


def test_vram_sampler_uses_total_delta_only_as_fallback():
    sampler = VramSampler(pid=123)
    sampler.peak_total_delta_mib = 456.0
    assert sampler.stop() == 456.0

    sampler_with_pid = VramSampler(pid=123)
    sampler_with_pid.peak_mib = 321.0
    sampler_with_pid.peak_total_delta_mib = 456.0
    assert sampler_with_pid.stop() == 321.0


def test_end_to_end_fake_trainer(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset"
    (dataset / "images").mkdir(parents=True)
    (dataset / "images" / "image.jpg").write_bytes(b"image")
    monkeypatch.setenv("DATASET", str(dataset))
    code = (
        "from pathlib import Path; "
        "p=Path(r'{output_path}'); p.mkdir(parents=True, exist_ok=True); "
        "(p/'point_cloud.ply').write_bytes("
        "b'ply\\nformat ascii 1.0\\nelement vertex 3\\nend_header\\n')"
    )
    suite = BenchmarkSuite(
        name="fake-suite",
        repetitions=2,
        backends=(BenchmarkBackend(
            name="fake", command=(sys.executable, "-c", code),
        ),),
        cases=(BenchmarkCase("tiny", "${DATASET}", PARAMETERS),),
    )
    report = run_benchmark_suite(suite, tmp_path / "output")
    summary = report["summaries"][0]
    assert summary["successful_runs"] == 2
    assert report["runs"][0]["artifacts"]["point_cloud.ply"]["vertices"] == 3
    assert (tmp_path / "output" / "fake-suite" / "benchmark_summary.json").is_file()
