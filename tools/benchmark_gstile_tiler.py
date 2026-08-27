#!/usr/bin/env python3
"""Generate and benchmark deterministic GSTile tiler workloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np


PROPERTY_NAMES = (
    "x",
    "y",
    "z",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    *(f"f_rest_{index}" for index in range(45)),
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
    *(f"opacity_sh_{index}" for index in range(15)),
)
PLY_DTYPE = np.dtype([(name, "<f4") for name in PROPERTY_NAMES])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _generate(source: Path, count: int, chunk_records: int) -> dict[str, Any]:
    if source.exists():
        raise FileExistsError(f"Benchmark fixture is immutable: {source}")
    if count < 1 or chunk_records < 1:
        raise ValueError("Benchmark record counts must be positive")
    source.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "ply",
        "format binary_little_endian 1.0",
        f"element vertex {count}",
        *(f"property float {name}" for name in PROPERTY_NAMES),
        "end_header",
        "",
    ]
    with source.open("xb") as handle:
        handle.write("\n".join(header).encode("ascii"))
        for start in range(0, count, chunk_records):
            stop = min(start + chunk_records, count)
            indices = np.arange(start, stop, dtype=np.float64)
            sequence = (-2.0 + 5.0 * indices / max(count - 1, 1)).astype(np.float32)
            records = np.zeros(stop - start, dtype=PLY_DTYPE)
            records["x"] = sequence
            records["y"] = np.sin(sequence)
            records["z"] = np.cos(sequence) * 0.25
            records["f_dc_0"] = sequence * 0.1
            records["f_dc_1"] = 0.2
            records["f_dc_2"] = -0.3
            for index in range(45):
                records[f"f_rest_{index}"] = sequence * ((index + 1) / 1000.0)
            records["opacity"] = sequence * 0.4
            records["scale_0"] = -4.0 + sequence * 0.01
            records["scale_1"] = -3.0
            records["scale_2"] = -2.0 - sequence * 0.01
            records["rot_0"] = 1.0
            for index in range(15):
                records[f"opacity_sh_{index}"] = sequence * ((index + 1) / 300.0)
            records.tofile(handle)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "path": str(source.resolve()),
        "records": count,
        "bytes": source.stat().st_size,
        "sha256": _sha256(source),
    }


def _git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    source = arguments.source.resolve()
    output = arguments.output.resolve()
    implementation = arguments.implementation_root.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists():
        raise FileExistsError(f"Benchmark output is immutable: {output}")
    command = [
        sys.executable,
        str(implementation / "tools" / "build_gstiles.py"),
        str(source),
        str(output),
        "--leaf-size",
        str(arguments.leaf_size),
        "--chunk-records",
        str(arguments.chunk_records),
    ]
    if arguments.pack_target_bytes is not None:
        command.extend(["--pack-target-bytes", str(arguments.pack_target_bytes)])
    if arguments.pack_workers is not None:
        command.extend(["--pack-workers", str(arguments.pack_workers)])
    if arguments.pack_pending_bytes is not None:
        command.extend(["--pack-pending-bytes", str(arguments.pack_pending_bytes)])
    if arguments.progress_jsonl:
        command.append("--progress-jsonl")
    if arguments.lod_proxy_size is not None:
        command.extend(
            [
                "--lod-proxy-size",
                str(arguments.lod_proxy_size),
                "--lod-proxy-strategy",
                arguments.lod_proxy_strategy,
            ]
        )
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=implementation,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
    )
    wall_seconds = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if completed.returncode != 0:
        raise RuntimeError(
            f"GSTile benchmark failed with {completed.returncode}:\n{completed.stderr}"
        )
    build_result = json.loads(completed.stdout.strip().splitlines()[-1])
    status = _git_value(implementation, "status", "--short")
    return {
        "schema": "droneai-gstile-tiler-benchmark",
        "version": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "implementation": {
            "root": str(implementation),
            "commit": _git_value(implementation, "rev-parse", "HEAD"),
            "dirty": bool(status),
            "status": status.splitlines(),
            "python": sys.version.split()[0],
            "numpy": np.__version__,
        },
        "fixture": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": _sha256(source),
        },
        "configuration": {
            "leafSize": arguments.leaf_size,
            "chunkRecords": arguments.chunk_records,
            "packTargetBytes": arguments.pack_target_bytes,
            "packWorkers": arguments.pack_workers,
            "packPendingBytes": arguments.pack_pending_bytes,
            "lodProxySize": arguments.lod_proxy_size,
            "lodProxyStrategy": arguments.lod_proxy_strategy,
            "command": command,
        },
        "measurements": {
            "wallSeconds": wall_seconds,
            "userCpuSeconds": after.ru_utime - before.ru_utime,
            "systemCpuSeconds": after.ru_stime - before.ru_stime,
            "maximumRssKiB": after.ru_maxrss,
            "filesystemInputBlocks": after.ru_inblock - before.ru_inblock,
            "filesystemOutputBlocks": after.ru_oublock - before.ru_oublock,
        },
        "result": build_result,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("source", type=Path)
    generate.add_argument("--records", type=int, required=True)
    generate.add_argument("--chunk-records", type=int, default=131_072)
    run = commands.add_parser("run")
    run.add_argument("source", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--implementation-root", type=Path, required=True)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument("--leaf-size", type=int, default=65_536)
    run.add_argument("--chunk-records", type=int, default=131_072)
    run.add_argument("--pack-target-bytes", type=int)
    run.add_argument("--pack-workers", type=int, choices=(1, 2, 4))
    run.add_argument("--pack-pending-bytes", type=int)
    run.add_argument("--progress-jsonl", action="store_true")
    run.add_argument("--lod-proxy-size", type=int)
    run.add_argument(
        "--lod-proxy-strategy",
        choices=("adaptive-moment", "moment-matched", "spatial-stratified", "minhash"),
        default="moment-matched",
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "generate":
        report = _generate(arguments.source.resolve(), arguments.records, arguments.chunk_records)
    else:
        report = _run(arguments)
        if arguments.report.exists():
            raise FileExistsError(f"Benchmark report is immutable: {arguments.report}")
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
