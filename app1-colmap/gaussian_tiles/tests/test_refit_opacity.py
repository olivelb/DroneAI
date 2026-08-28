"""Bit-exact directional-opacity refit against the original expressions."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle, tiler
from gaussian_tiles.tests.test_adaptive_pairs import working_records
from gaussian_tiles.tests.test_gstile import _records, _write_ply


def reference_refit(ordered, starts, scales, merged_scales):
    """Frozen refit expression from e651905; no owned-buffer reuse."""
    names = tiler._opacity_property_names(ordered)
    design = tiler._opacity_design_matrix(len(names))
    coefficients = np.column_stack((ordered["opacity"], *(ordered[name] for name in names))).astype(
        np.float64, copy=False)
    source_alpha = tiler._sigmoid(coefficients @ design.T)
    source_area = tiler._ellipsoid_area(scales)
    directional_mass = np.add.reduceat(source_alpha * source_area[:, None], starts, axis=0)
    target_alpha = np.clip(
        directional_mass / np.maximum(tiler._ellipsoid_area(merged_scales)[:, None], 1e-30),
        1e-7, 1.0 - 1e-7)
    target_logits = np.log(target_alpha / (1.0 - target_alpha))
    fitted = target_logits @ np.linalg.pinv(design).T
    if not np.all(np.isfinite(fitted)):
        raise RuntimeError("GSTile directional opacity refit produced non-finite values")
    return fitted


def opacity_records(count, width, mode):
    rng = np.random.default_rng(79)
    source = working_records(count, 79)
    names = [name for name in source.dtype.names
             if not name.startswith("opacity_sh_") or int(name.rsplit("_", 1)[1]) < width]
    result = np.empty(count * 2, dtype=[(name, source.dtype[name]) for name in names])[::2]
    for name in names:
        result[name] = source[name]
        if name == "opacity" or name.startswith("opacity_sh_"):
            result[name] = rng.normal(size=count)
            if mode == "saturated":
                result[name] *= 1e6
            elif mode == "signed_zero":
                result[name] = np.copysign(0., rng.normal(size=count))
    return result


@pytest.mark.parametrize("width", [0, 3, 8, 15])
@pytest.mark.parametrize("mode", ["random", "saturated", "signed_zero"])
@pytest.mark.parametrize("area", ["normal", "floor", "clipped", "extreme"])
def test_fitted_coefficients_are_bit_exact_and_inputs_immutable(width, mode, area):
    records = opacity_records(129, width, mode)
    rng = np.random.default_rng(31)
    scales = np.exp(rng.uniform(-10, 5, size=(258, 3)))[::2]
    if area == "extreme":
        scales = np.exp(rng.uniform(-30, 30, size=(129, 3)))
    if area == "floor":
        scales = np.zeros_like(scales)
    for groups in (1, 17, 65, 129):
        starts = np.arange(groups, dtype=np.intp) * len(records) // groups
        merged = scales[starts].copy()
        if area == "clipped":
            merged[::2] = 1e-20
            merged[1::2] = 1e20
        inputs = (records, starts, scales, merged)
        before = [item.tobytes() for item in inputs]
        for item in inputs:
            item.flags.writeable = False
        expected = reference_refit(*inputs)
        actual = tiler._refit_directional_opacity(*inputs)
        assert actual.dtype == expected.dtype == np.float64
        assert actual.shape == expected.shape == (groups, width + 1)
        assert actual.tobytes() == expected.tobytes()
        assert [item.tobytes() for item in inputs] == before


def test_large_refit_array_is_bit_exact():
    records = opacity_records(8193, 15, "random")
    _, _, scales = tiler._positions_and_scales(records)
    starts = np.arange(0, len(records), 2)
    merged = scales[starts] * 1.3
    assert tiler._refit_directional_opacity(records, starts, scales, merged).tobytes() == (
        reference_refit(records, starts, scales, merged).tobytes())


@pytest.mark.parametrize("bad_input", ["opacity", "opacity_sh_14", "scales", "merged_scales"])
def test_nonfinite_failure_is_unchanged(bad_input):
    records = opacity_records(17, 15, "random")
    starts = np.array([0, 8])
    scales = np.ones((17, 3))
    merged = np.ones((2, 3))
    if bad_input == "scales":
        scales[2, 1] = np.nan
    elif bad_input == "merged_scales":
        merged[0, 1] = np.nan
    else:
        records[bad_input][2] = np.nan
    for method in (reference_refit, tiler._refit_directional_opacity):
        with pytest.raises(RuntimeError, match="directional opacity refit produced non-finite values"):
            method(records, starts, scales, merged)


@pytest.mark.parametrize("width", [0, 3, 8, 15])
@pytest.mark.parametrize("mode", ["random", "saturated", "signed_zero"])
def test_multigeneration_proxy_bytes_match_original(monkeypatch, width, mode):
    records = opacity_records(257, width, mode)
    errors = np.linspace(0, 0.25, len(records))
    records.flags.writeable = errors.flags.writeable = False
    actual = tiler._adaptive_moment_lod_proxy(records, errors, 17)
    monkeypatch.setattr(tiler, "_refit_directional_opacity", reference_refit)
    expected = tiler._adaptive_moment_lod_proxy(records, errors, 17)
    assert actual.records.tobytes() == expected.records.tobytes()
    assert actual.errors.tobytes() == expected.errors.tobytes()


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("aggregate", [256 * 1024, 2 * 1024**2])
def test_full_bundle_matches_original_refit(monkeypatch, tmp_path, workers, aggregate):
    source = tmp_path / "source.ply"
    _write_ply(source, _records(8193))
    options = GsTileBuildOptions(leaf_size=2048, chunk_records=2048, lod_proxy_size=1024,
                               lod_proxy_strategy="adaptive-moment", pack_target_bytes=aggregate,
                               pack_workers=workers)
    fast, reference = tmp_path / "fast", tmp_path / "reference"
    actual = build_gstile_bundle(source, fast, options=options)
    monkeypatch.setattr(tiler, "_refit_directional_opacity", reference_refit)
    expected = build_gstile_bundle(source, reference, options=options)
    assert actual.bundle_id == expected.bundle_id
    inventory = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert inventory(fast) == inventory(reference)
