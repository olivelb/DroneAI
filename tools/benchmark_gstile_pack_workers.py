#!/usr/bin/env python3
"""Bounded 1/2/4-thread codec probe; not a full tiler or disk benchmark."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import zstandard

from gstile_cli_common import configure_repository_imports

configure_repository_imports(__file__)

from gaussian_tiles.format import encode_pack, validate_pack_content  # noqa: E402
from benchmark_gstile_tiler import PLY_DTYPE  # noqa: E402


def bounded_ordered_map(function, values, workers):
    """At most workers submitted tasks; consume in order, propagate failures."""
    if workers not in (1, 2, 4):
        raise ValueError("workers must be 1, 2 or 4")
    iterator = iter(values)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = deque(executor.submit(function, value) for value in itertools.islice(iterator, workers))
        try:
            while pending:
                yield pending.popleft().result()
                for value in itertools.islice(iterator, 1):
                    pending.append(executor.submit(function, value))
        finally:
            for future in pending:
                future.cancel()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def compress(content):
    # Match production settings; each independent job owns its own context.
    return zstandard.ZstdCompressor(level=1, write_checksum=True, write_content_size=True).compress(content)


def signature(content, compressed, metadata=None):
    return {"rawSha256": sha(content), "zstdSha256": sha(compressed),
            "rawBytes": len(content), "zstdBytes": len(compressed), "metadata": metadata}


def synthetic_records(count):
    """Repeatable SH3/directional fixture, explicitly not an original PLY."""
    rng = np.random.default_rng(20260827)
    records = np.empty(count, dtype=PLY_DTYPE)
    for name in PLY_DTYPE.names:
        records[name] = rng.normal(0, 0.1, count).astype(np.float32)
    for name in ("scale_0", "scale_1", "scale_2"):
        records[name] -= 3
    records["rot_0"] += 1
    records.flags.writeable = False
    source_ids = np.arange(count, dtype=np.uint64) + np.uint64(2**53)
    source_ids.flags.writeable = False
    return records, source_ids


def timed(function, jobs, workers):
    before = resource.getrusage(resource.RUSAGE_SELF)
    start = time.perf_counter()
    results = list(bounded_ordered_map(function, jobs, workers))
    elapsed = time.perf_counter() - start
    after = resource.getrusage(resource.RUSAGE_SELF)
    return results, {"workers": workers, "wallSeconds": elapsed,
                     "userCpuSeconds": after.ru_utime-before.ru_utime,
                     "systemCpuSeconds": after.ru_stime-before.ru_stime,
                     "processHighWaterRssKiB": after.ru_maxrss}


def run(fixtures: Path, output: Path, tasks: int, count: int):
    if not 4 <= tasks <= 192 or not 1 <= count <= 65536:
        raise ValueError("Use 4..192 tasks and 1..65536 records")
    if output.exists():
        raise FileExistsError(f"Immutable benchmark output already exists: {output}")
    index = json.loads((fixtures / "index.json").read_text())
    contents = []
    inventory = []
    for name in ("proxy", "leaf", "large-leaf"):
        content = (fixtures / f"{name}.pack").read_bytes()
        expected = next(item for item in index["fixtures"] if item["name"] == name)
        if sha(content) != expected["packSha256"]:
            raise ValueError(f"Fixture hash mismatch: {name}")
        records, _ = validate_pack_content(content)
        if zstandard.ZstdDecompressor().decompress(compress(content)) != content:
            raise ValueError("Zstd roundtrip differs")
        contents.append(content)
        inventory.append({"name": name, "sha256": sha(content), "bytes": len(content), "records": records})
    records, ids = synthetic_records(count)
    original_hash = sha(records.tobytes())
    def encode_job(job):
        raw, quantization, errors = encode_pack(records, ids, node_id=f"probe-{job}")
        return signature(raw, compress(raw), {"quantization": quantization, "errors": errors})
    def compression_job(job):
        raw = contents[job % len(contents)]
        return signature(raw, compress(raw))
    repo = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    status = subprocess.check_output(["git", "status", "--short"], cwd=repo, text=True).strip()
    metadata = {"schema": "gstile-pack-worker-probe-v1", "commit": commit, "dirty": bool(status),
                "status": status, "python": sys.version, "numpy": np.__version__, "zstandard": zstandard.__version__,
                "platform": platform.platform(), "cpuCount": os.cpu_count(), "bundleId": index["bundleId"],
                "fixtures": inventory, "synthetic": {"records": count, "seed": 20260827, "sha256": original_hash},
                "tasksPerTrial": tasks, "workerOrders": [[1, 2, 4], [4, 2, 1], [2, 1, 4]],
                "scope": "Hot in-memory inputs; encode+Zstd+hash and Zstd+hash separately; no tree, V4, disk or fsync. RSS is cumulative process high-water, not per-arm peak."}
    output.mkdir(parents=True, exist_ok=False)
    (output / "protocol.json").write_text(json.dumps(metadata, indent=2))
    trials = []
    with (output / "trials.jsonl").open("x") as log:
        for stage, function in (("real-pack-zstd-hash", compression_job), ("synthetic-encode-zstd-hash", encode_job)):
            control = None
            # Retain every warmup and every predeclared trial. No best-of selection.
            for round_id, order in enumerate([[1, 2, 4], *metadata["workerOrders"]]):
                for workers in order:
                    result, measurement = timed(function, range(tasks), workers)
                    if control is None:
                        control = result
                    if result != control:
                        raise AssertionError("Parallel output/hash/quantization/order differs")
                    trial = {"stage": stage, "warmup": round_id == 0, "round": round_id,
                             **measurement, "outputSignatureSha256": sha(json.dumps(result, sort_keys=True).encode())}
                    trials.append(trial)
                    log.write(json.dumps(trial) + "\n")
                    log.flush()
                    print(json.dumps(trial), flush=True)
            (output / f"{stage}-signatures.json").write_text(json.dumps(control, indent=2))
    if sha(records.tobytes()) != original_hash:
        raise AssertionError("Codec mutated shared input")
    medians = []
    for stage in ("real-pack-zstd-hash", "synthetic-encode-zstd-hash"):
        for workers in (1, 2, 4):
            samples = [t["wallSeconds"] for t in trials if t["stage"] == stage and t["workers"] == workers and not t["warmup"]]
            medians.append({"stage": stage, "workers": workers, "medianSeconds": statistics.median(samples)})
    (output / "summary.json").write_text(json.dumps({"complete": True, "identicalSignatures": True, "medians": medians}, indent=2))
    return medians


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tasks", type=int, default=48)
    parser.add_argument("--records", type=int, default=65536)
    args = parser.parse_args()
    print(json.dumps(run(args.fixtures, args.output, args.tasks, args.records), indent=2))


if __name__ == "__main__":
    main()
