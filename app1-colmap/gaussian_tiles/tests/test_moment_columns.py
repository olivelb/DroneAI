"""Exact attribute and proxy contracts for bounded-column moment matching."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle, tiler
from gaussian_tiles.tests.test_adaptive_pairs import working_records
from gaussian_tiles.tests.test_gstile import _records, _write_ply


def reference_average(ordered, mass, starts, group_mass):
    """Original expression from 1df7b16, including finite checks on all fields."""
    output = ordered[starts].copy()
    names = [name for name in ordered.dtype.names or ()
             if np.issubdtype(ordered.dtype.fields[name][0], np.floating)]
    values = np.column_stack([ordered[name] for name in names]).astype(np.float64, copy=False)
    averaged = np.add.reduceat(values * mass[:, None], starts, axis=0) / group_mass[:, None]
    for index, name in enumerate(names):
        output[name] = averaged[:, index]
    return output, bool(np.all(np.isfinite(averaged)))


@pytest.mark.parametrize("fields", [1, 7, 8, 9, 16, 74, 79])
@pytest.mark.parametrize("dtype", ["<f4", "<f8"])
@pytest.mark.parametrize("groups", [1, 17, 129])
def test_average_bytes_and_immutable_strided_inputs(fields, dtype, groups):
    rng = np.random.default_rng(53)
    spec = [(f"value_{i}", dtype) for i in range(fields)] + [("source_id", "<u8")]
    records = np.zeros(258, dtype=spec)[::2]
    for name in records.dtype.names[:-1]:
        records[name] = rng.normal(size=129)
        records[name][0] = -0.0
    records["source_id"] = rng.permutation(129).astype(np.uint64) + np.uint64(2**63)
    mass = np.exp(rng.uniform(-60, 60, size=258))[::2]
    starts = np.arange(groups, dtype=np.intp) * len(records) // groups
    group_mass = np.add.reduceat(mass, starts)
    inputs = (records, mass, starts, group_mass)
    before = [a.tobytes() for a in inputs]
    for a in inputs:
        a.flags.writeable = False
    expected, expected_finite = reference_average(*inputs)
    actual, finite = tiler._average_group_attributes(*inputs)
    assert actual.dtype == expected.dtype
    assert actual.tobytes() == expected.tobytes()
    assert finite is expected_finite
    assert not np.shares_memory(actual, records)
    assert [a.tobytes() for a in inputs] == before


@pytest.mark.parametrize("field", [0, 8, 18])
@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_check_includes_every_block(field, bad):
    records = np.ones(3, dtype=[(f"v{i}", "<f8") for i in range(19)])
    records[f"v{field}"][1] = bad
    starts = np.array([0, 2])
    expected, expected_finite = reference_average(records, np.ones(3), starts, np.array([2., 1.]))
    actual, finite = tiler._average_group_attributes(records, np.ones(3), starts, np.array([2., 1.]))
    assert actual.tobytes() == expected.tobytes()
    assert finite is expected_finite is False


def test_scratch_column_width_is_bounded(monkeypatch):
    records = working_records(33)
    starts = np.arange(0, 33, 2)
    mass = np.ones(33)
    group_mass = np.add.reduceat(mass, starts)
    stack = np.column_stack
    widths = []

    def checked_stack(arrays):
        widths.append(len(arrays))
        return stack(arrays)

    monkeypatch.setattr(tiler.np, "column_stack", checked_stack)
    tiler._average_group_attributes(records, mass, starts, group_mass)
    assert widths == [8] * 9 + [2]


@pytest.mark.parametrize("refit", [False, True])
@pytest.mark.parametrize("width", [0, 3, 8, 15])
@pytest.mark.parametrize("mode", ["random", "coincident", "extreme", "signed_zero"])
@pytest.mark.parametrize("layout", ["packed", "padded"])
def test_complete_proxy_and_error_bytes_match_original(monkeypatch, refit, width, mode, layout):
    rng = np.random.default_rng(19)
    records = working_records(257, 19, coincident=mode == "coincident")
    names = [name for name in records.dtype.names
             if not name.startswith("opacity_sh_") or int(name.rsplit("_", 1)[1]) < width]
    records = records[names]
    if layout == "packed":
        packed = np.empty(len(records), dtype=[(name, records.dtype[name]) for name in names])
        for name in names:
            packed[name] = records[name]
        records = packed
    for name in names:
        if name.startswith(("f_dc_", "f_rest_", "opacity_sh_")):
            records[name] = rng.normal(size=len(records)) * (1e12 if mode == "extreme" else 1)
    if mode == "extreme":
        for name in ("x", "y", "z"):
            records[name] *= 1e10
        for name in ("opacity", "scale_0", "scale_1", "scale_2"):
            records[name] = rng.uniform(-80, 80, size=len(records))
    elif mode == "signed_zero":
        for name in names:
            if name != "source_id":
                records[name] = np.copysign(0., rng.normal(size=len(records)))
    errors = np.linspace(0, 0.25, len(records))
    records.flags.writeable = errors.flags.writeable = False
    before = records.tobytes(), errors.tobytes()
    original = tiler._average_group_attributes
    for groups in (1, 17, 129, 257):
        starts = np.arange(groups, dtype=np.intp) * len(records) // groups
        ends = np.r_[starts[1:], len(records)]
        actual = tiler._moment_match_ordered_groups(records, errors, starts, ends,
                                                   refit_directional_opacity=refit)
        monkeypatch.setattr(tiler, "_average_group_attributes", reference_average)
        expected = tiler._moment_match_ordered_groups(records, errors, starts, ends,
                                                     refit_directional_opacity=refit)
        monkeypatch.setattr(tiler, "_average_group_attributes", original)
        assert actual.records.dtype == expected.records.dtype
        for name in names:
            assert actual.records[name].tobytes() == expected.records[name].tobytes(), name
        if layout == "packed":
            assert actual.records.tobytes() == expected.records.tobytes()
        # A field-selection view has holes where omitted SH fields used to be.
        # NumPy copies field values, not deterministic bytes into that padding.
        assert actual.errors.tobytes() == expected.errors.tobytes()
    assert (records.tobytes(), errors.tobytes()) == before


@pytest.mark.parametrize("field", ["f_dc_0", "f_rest_16", "opacity_sh_14"])
def test_nonfinite_proxy_failure_is_preserved(monkeypatch, field):
    records = working_records(17)
    records[field][2] = np.nan
    starts = np.array([0, 8])
    for average in (tiler._average_group_attributes, reference_average):
        monkeypatch.setattr(tiler, "_average_group_attributes", average)
        with pytest.raises(RuntimeError, match="moment matching produced non-finite proxy values"):
            tiler._moment_match_ordered_groups(records, np.zeros(17), starts, np.array([8, 17]))


@pytest.mark.parametrize("strategy", ["moment-matched", "adaptive-moment"])
@pytest.mark.parametrize("aggregate", [None, 256 * 1024])
@pytest.mark.parametrize("workers", [1, 2])
def test_full_bundle_bytes_match_original(monkeypatch, tmp_path, strategy, aggregate, workers):
    source = tmp_path / "source.ply"
    _write_ply(source, _records(8193))
    options = GsTileBuildOptions(leaf_size=2048, chunk_records=2048, lod_proxy_size=1024,
                               lod_proxy_strategy=strategy, pack_target_bytes=aggregate, pack_workers=workers)
    fast, reference = tmp_path / "fast", tmp_path / "reference"
    actual = build_gstile_bundle(source, fast, options=options)
    monkeypatch.setattr(tiler, "_average_group_attributes", reference_average)
    expected = build_gstile_bundle(source, reference, options=options)
    assert actual.bundle_id == expected.bundle_id
    inventory = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert inventory(fast) == inventory(reference)
