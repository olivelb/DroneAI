#!/usr/bin/env python3
"""Measure bounded resident PLY load/save and verify a round trip."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import cupy as cp
import numpy as np

from gaussian_ortho.gaussian_model import GaussianModel


def _sample_digest(model: GaussianModel) -> str:
    digest = hashlib.sha256()
    for values in (
        model._xyz,
        model._features_dc,
        model._features_rest,
        model._scaling,
        model._rotation,
        model._opacity,
        model._opacity_sh,
    ):
        if not values.size:
            continue
        indices = np.linspace(
            0,
            values.shape[0] - 1,
            num=min(1_024, values.shape[0]),
            dtype=np.int64,
        )
        digest.update(cp.asnumpy(values[indices]).tobytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()

    before = cp.cuda.Device(0).mem_info
    started = time.perf_counter()
    source_model = GaussianModel()
    source_model.load_ply(str(arguments.source))
    cp.cuda.Stream.null.synchronize()
    loaded = time.perf_counter()
    source_digest = _sample_digest(source_model)
    source_model.save_ply(str(arguments.destination))
    cp.cuda.Stream.null.synchronize()
    saved = time.perf_counter()
    del source_model
    cp.get_default_memory_pool().free_all_blocks()

    restored_model = GaussianModel()
    restored_model.load_ply(str(arguments.destination))
    cp.cuda.Stream.null.synchronize()
    restored = time.perf_counter()
    restored_digest = _sample_digest(restored_model)
    after = cp.cuda.Device(0).mem_info
    report = {
        "gaussians": restored_model.num_gaussians,
        "source_bytes": arguments.source.stat().st_size,
        "destination_bytes": arguments.destination.stat().st_size,
        "load_seconds": loaded - started,
        "save_seconds": saved - loaded,
        "reload_seconds": restored - saved,
        "sample_sha256": source_digest,
        "roundtrip_sample_sha256": restored_digest,
        "roundtrip_equal": source_digest == restored_digest,
        "device_free_before": int(before[0]),
        "device_free_after": int(after[0]),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["roundtrip_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
