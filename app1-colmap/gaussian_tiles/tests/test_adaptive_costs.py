"""Bit-exact edge costs against the expression used before scratch reuse."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle, tiler
from gaussian_tiles.tests.test_adaptive_pairs import working_records
from gaussian_tiles.tests.test_gstile import _records, _write_ply


def reference_candidate_edges(records, neighbors=8):
    """Frozen scoring expression from acf9e6f; no scratch/precompute helper."""
    count = records.shape[0]
    order = np.lexsort((records["source_id"], tiler._morton_codes(records)))
    left = np.concatenate([order[:-offset] for offset in range(1, min(neighbors, count - 1) + 1)])
    right = np.concatenate([order[offset:] for offset in range(1, min(neighbors, count - 1) + 1)])
    positions, log_scales, scales = tiler._positions_and_scales(records)
    spatial = np.sum(np.square(positions[left] - positions[right]), axis=1) / np.maximum(
        np.sum(np.square(scales[left]), axis=1) + np.sum(np.square(scales[right]), axis=1), 1e-12)
    shape = np.mean(np.square(log_scales[left] - log_scales[right]), axis=1)
    color = np.column_stack((records["f_dc_0"], records["f_dc_1"], records["f_dc_2"])).astype(np.float64, copy=False)
    appearance = np.sum(np.square(color[left] - color[right]), axis=1)
    alpha = tiler._sigmoid(records["opacity"].astype(np.float64, copy=False))
    opacity = np.square(alpha[left] - alpha[right])
    names = tiler._opacity_property_names(records)
    if names:
        coefficients = np.column_stack([records[name] for name in names]).astype(np.float64, copy=False)
        directional = np.mean(np.square(coefficients[left] - coefficients[right]), axis=1)
    else:
        directional = np.zeros(left.shape[0], dtype=np.float64)
    cost = (
        np.log1p(spatial / tiler._robust_cost_scale(spatial))
        + 0.35 * np.log1p(shape / tiler._robust_cost_scale(shape))
        + 2.0 * np.log1p(appearance / tiler._robust_cost_scale(appearance))
        + 0.5 * np.log1p(opacity / tiler._robust_cost_scale(opacity))
        + 0.5 * np.log1p(directional / tiler._robust_cost_scale(directional))
    )
    order = np.lexsort((right, left, cost))
    return left[order], right[order], cost[order]


@pytest.mark.parametrize("columns", [None, 1, 3, 15])
@pytest.mark.parametrize("layout", ["c", "f", "strided"])
def test_squared_edge_scratch_is_exact_owned_and_input_readonly(columns, layout):
    rng = np.random.default_rng(91)
    shape = (64,) if columns is None else (64, columns)
    values = rng.normal(size=shape)
    if layout == "f":
        values = np.asfortranarray(values)
    elif layout == "strided":
        values = values[::-2]
    values.flags.writeable = False
    before = values.tobytes()
    left = np.array([0, 3, 2, 0, 1, 1], dtype=np.intp)
    right = np.array([1, 2, 3, 0, 1, 2], dtype=np.intp)
    expected = np.square(values[left] - values[right])
    actual = tiler._squared_edge_deltas(values, left, right)
    assert actual.dtype == expected.dtype
    assert actual.tobytes() == expected.tobytes()
    assert actual.flags.owndata
    assert not np.shares_memory(actual, values)
    actual.fill(123)
    assert values.tobytes() == before


@pytest.mark.parametrize("width", [0, 3, 8, 15])
@pytest.mark.parametrize("neighbors", [1, 8, 32])
@pytest.mark.parametrize("mode", ["random", "coincident", "extreme", "signed_zero"])
def test_cost_and_candidate_order_are_bit_exact(width, neighbors, mode):
    rng = np.random.default_rng(17)
    for count in (2, 17, 129):
        records = working_records(count, 17)
        fields = [name for name in records.dtype.names
                  if not name.startswith("opacity_sh_") or int(name.rsplit("_", 1)[1]) < width]
        records = records[fields]
        if mode == "coincident":
            ids = records["source_id"].copy()
            records[:] = records[0]
            records["source_id"] = ids
        elif mode == "signed_zero":
            for name in ("x", "y", "z", "opacity"):
                records[name] = np.copysign(0.0, rng.normal(size=count))
        else:
            for name in fields:
                if name.startswith(("opacity_sh_", "f_dc_")):
                    records[name] = rng.normal(size=count) * (1e4 if mode == "extreme" else 1)
            if mode == "extreme":
                for name in ("scale_0", "scale_1", "scale_2", "opacity"):
                    records[name] = rng.uniform(-100, 100, size=count)
                for name in ("x", "y", "z"):
                    records[name] *= 1e12
        records.flags.writeable = False
        before = records.tobytes()
        expected = reference_candidate_edges(records, neighbors)
        actual = tiler._adaptive_candidate_edges(records, neighbors)
        for a, b in zip(actual, expected, strict=True):
            assert a.dtype == b.dtype
            assert a.shape == b.shape
            assert a.tobytes() == b.tobytes()
        assert records.tobytes() == before


def test_large_cost_arrays_preserve_exact_bytes():
    records = working_records(8193, 73)
    expected = reference_candidate_edges(records)
    actual = tiler._adaptive_candidate_edges(records)
    assert all(a.tobytes() == b.tobytes() for a, b in zip(actual, expected, strict=True))


@pytest.mark.parametrize("coincident", [False, True])
def test_proxy_record_and_error_bytes_are_unchanged(monkeypatch, coincident):
    records = working_records(257, 9, coincident)
    errors = np.linspace(0, 0.25, 257)
    actual = tiler._adaptive_moment_lod_proxy(records, errors, 17)
    monkeypatch.setattr(tiler, "_adaptive_candidate_edges", reference_candidate_edges)
    expected = tiler._adaptive_moment_lod_proxy(records, errors, 17)
    assert actual.records.tobytes() == expected.records.tobytes()
    assert actual.errors.tobytes() == expected.errors.tobytes()


@pytest.mark.parametrize("aggregate", [None, 256 * 1024])
@pytest.mark.parametrize("workers", [1, 2])
def test_complete_bundle_matches_original_costs(monkeypatch, tmp_path, aggregate, workers):
    source = tmp_path / "source.ply"
    _write_ply(source, _records(8193))
    options = GsTileBuildOptions(leaf_size=2048, chunk_records=2048, lod_proxy_size=1024,
                               lod_proxy_strategy="adaptive-moment", pack_target_bytes=aggregate,
                               pack_workers=workers)
    fast, reference = tmp_path / "fast", tmp_path / "reference"
    actual = build_gstile_bundle(source, fast, options=options)
    monkeypatch.setattr(tiler, "_adaptive_candidate_edges", reference_candidate_edges)
    expected = build_gstile_bundle(source, reference, options=options)
    assert actual.bundle_id == expected.bundle_id
    inventory = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert inventory(fast) == inventory(reference)
