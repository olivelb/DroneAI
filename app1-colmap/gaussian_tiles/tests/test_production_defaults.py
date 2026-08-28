"""Promoted GSTile defaults remain the previously explicit qualified profile."""
from __future__ import annotations

import sys
import runpy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle
from gaussian_tiles.tests.test_gstile import _records, _write_ply


def test_default_options_select_qualified_profile():
    options = GsTileBuildOptions()
    options.validate()
    assert options.lod_proxy_strategy == "adaptive-moment"
    assert options.lod_proxy_size == 16384
    assert options.leaf_size == 65536
    assert options.pack_target_bytes == 2 * 1024**2
    assert options.pack_workers == 2
    assert options.pack_pending_bytes == 128 * 1024**2
    assert options.invisible_gaussian_scale_threshold is None


def test_default_build_matches_explicit_qualified_profile(tmp_path):
    source = tmp_path / "source.ply"
    _write_ply(source, _records(8193))
    default = tmp_path / "default"
    explicit = tmp_path / "explicit"
    first = build_gstile_bundle(source, default)
    second = build_gstile_bundle(source, explicit, options=GsTileBuildOptions(
        lod_proxy_strategy="adaptive-moment", lod_proxy_size=16384,
        pack_target_bytes=2097152, pack_workers=2, pack_pending_bytes=134217728,
    ))
    assert first.bundle_id == second.bundle_id
    inventory = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert inventory(default) == inventory(explicit)


def test_cli_defaults_and_retired_modes_rejected(monkeypatch):
    repository = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repository / "tools"))
    parser = runpy.run_path(str(repository / "tools/build_gstiles.py"))["_parser"]()
    args = parser.parse_args(["input.ply", "bundle"])
    assert (args.lod_proxy_size, args.lod_proxy_strategy, args.pack_target_bytes, args.pack_workers) == (
        16384, "adaptive-moment", 2097152, 2,
    )
    for flags in (["--no-lod"], ["--individual-packs"],
                  ["--lod-proxy-strategy", "moment-matched"],
                  ["--lod-proxy-strategy", "minhash"],
                  ["--lod-proxy-strategy", "spatial-stratified"]):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(["input.ply", "bundle", *flags])
        assert error.value.code == 2


def test_smaller_leaf_needs_an_explicit_compatible_proxy_size():
    with pytest.raises(ValueError, match="lod_proxy_size"):
        GsTileBuildOptions(leaf_size=1024).validate()
    GsTileBuildOptions(leaf_size=1024, lod_proxy_size=1024).validate()
    with pytest.raises(ValueError, match="lod_proxy_size"):
        GsTileBuildOptions(leaf_size=1024, lod_proxy_size=None).validate()
    with pytest.raises(ValueError, match="pack_target_bytes"):
        GsTileBuildOptions(pack_target_bytes=None).validate()
