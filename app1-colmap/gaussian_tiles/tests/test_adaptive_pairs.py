"""Exact oracle for the V4 pair-only optimization; reference is test-only."""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle, tiler
from gaussian_tiles.tests.test_gstile import PLY_DTYPE, _records, _write_ply


def reference_pair_roots(records, left, right, target):
    """Union-find algorithm frozen from 9269f8e, before the optimization."""
    count = records.shape[0]
    parent = np.arange(count, dtype=np.intp)
    size = np.ones(count, dtype=np.intp)

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    needed, removed = count - target, 0
    for a, b in zip(left, right, strict=True):
        a, b = find(int(a)), find(int(b))
        if a == b or size[a] + size[b] > 2:
            continue
        if size[a] < size[b] or (size[a] == size[b] and a > b):
            a, b = b, a
        parent[b] = a
        size[a] += size[b]
        removed += 1
        if removed == needed:
            break
    if removed != needed:
        order = np.lexsort((records["source_id"], tiler._morton_codes(records)))
        unmatched = [index for index in order if size[find(int(index))] == 1]
        for offset in range(0, len(unmatched) - 1, 2):
            a, b = find(int(unmatched[offset])), find(int(unmatched[offset + 1]))
            if a == b:
                continue
            parent[b] = a
            size[a] = 2
            removed += 1
            if removed == needed:
                break
    if removed != needed:
        raise RuntimeError(f"GSTile adaptive LOD stalled after {removed}/{needed} merges")
    return np.fromiter((find(index) for index in range(count)), dtype=np.intp, count=count)


def working_records(count, seed=0, coincident=False):
    rng = np.random.default_rng(seed)
    records = np.empty(count, dtype=np.dtype([*PLY_DTYPE.descr, ("source_id", "<u8")]))
    source = _records(count)
    for name in PLY_DTYPE.names:
        records[name] = source[name]
    for name in ("x", "y", "z"):
        records[name] = 0 if coincident else rng.normal(size=count)
    records["source_id"] = rng.permutation(count).astype(np.uint64) + np.uint64(2**63)
    return records


def assert_roots(records, left, right, target):
    before = records.tobytes(), left.tobytes(), right.tobytes()
    expected = reference_pair_roots(records, left, right, target)
    actual = tiler._adaptive_pair_roots(records, left, right, target)
    assert actual.dtype == np.dtype(np.intp)
    assert np.array_equal(actual, expected)
    assert np.array_equal(actual[actual], actual)  # flat roots, including fallback
    assert len(np.unique(actual)) == target
    assert max(np.unique(actual, return_counts=True)[1]) <= 2
    assert before == (records.tobytes(), left.tobytes(), right.tobytes())


def test_all_four_vertex_edge_orders_match_reference():
    records = working_records(4, coincident=True)
    edges = list(itertools.combinations(range(4), 2))
    for permutation in itertools.permutations(edges):
        left, right = np.asarray(permutation, dtype=np.intp).T
        for target in (2, 3):
            assert_roots(records, left, right, target)


@pytest.mark.parametrize("seed", range(8))
def test_random_graphs_duplicates_self_edges_and_partial_targets(seed):
    rng = np.random.default_rng(seed)
    for count in (2, 3, 5, 16, 31, 64, 129):
        records = working_records(count, seed, coincident=seed % 2 == 0)
        left, right = rng.integers(0, count, size=(2, count * 8), dtype=np.intp)
        for target in sorted({(count + 1) // 2, count - 1, (3 * count) // 4}):
            assert_roots(records, left, right, target)


def test_completion_keeps_morton_root_not_minimum_index():
    records = working_records(5, coincident=True)
    records["source_id"] = [4, 3, 2, 1, 0]
    empty = np.empty(0, dtype=np.intp)
    assert_roots(records, empty, empty, 3)
    assert tiler._adaptive_pair_roots(records, empty, empty, 3).tolist() == [0, 2, 2, 4, 4]


def test_complete_greedy_matching_does_not_recompute_morton(monkeypatch):
    records = working_records(4)
    def unexpected(*args):
        pytest.fail("Greedy success must not calculate the fallback order")
    monkeypatch.setattr(tiler, "_morton_codes", unexpected)
    roots = tiler._adaptive_pair_roots(records, np.array([3, 1]), np.array([2, 0]), 2)
    assert roots.tolist() == [0, 0, 2, 2]


def test_impossible_generation_target_keeps_stall_error():
    records = working_records(5)
    empty = np.empty(0, dtype=np.intp)
    for matcher in (reference_pair_roots, tiler._adaptive_pair_roots):
        with pytest.raises(RuntimeError, match="stalled after 2/3 merges"):
            matcher(records, empty, empty, 2)


@pytest.mark.parametrize("coincident", [False, True])
def test_generation_preserves_record_and_error_bytes(monkeypatch, coincident):
    records = working_records(129, 7, coincident)
    errors = np.linspace(0, 0.25, 129)
    actual = tiler._adaptive_moment_lod_proxy(records, errors, 17)
    monkeypatch.setattr(tiler, "_adaptive_pair_roots", reference_pair_roots)
    expected = tiler._adaptive_moment_lod_proxy(records, errors, 17)
    assert actual.records.tobytes() == expected.records.tobytes()
    assert actual.errors.tobytes() == expected.errors.tobytes()


@pytest.mark.parametrize("aggregate", [256 * 1024, 2 * 1024**2])
@pytest.mark.parametrize("workers", [1, 2])
def test_complete_bundle_matches_union_find(monkeypatch, tmp_path, aggregate, workers):
    source = tmp_path / "source.ply"
    _write_ply(source, _records(8193))
    options = GsTileBuildOptions(leaf_size=2048, chunk_records=2048, lod_proxy_size=1024,
                               lod_proxy_strategy="adaptive-moment", pack_target_bytes=aggregate,
                               pack_workers=workers)
    fast, reference = tmp_path / "fast", tmp_path / "reference"
    actual = build_gstile_bundle(source, fast, options=options)
    monkeypatch.setattr(tiler, "_adaptive_pair_roots", reference_pair_roots)
    expected = build_gstile_bundle(source, reference, options=options)
    assert actual.bundle_id == expected.bundle_id
    inventory = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert inventory(fast) == inventory(reference)
