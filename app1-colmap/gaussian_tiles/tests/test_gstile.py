from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import zstandard

COLMAP_ROOT = Path(__file__).resolve().parents[2]
if str(COLMAP_ROOT) not in sys.path:
    sys.path.insert(0, str(COLMAP_ROOT))

from gaussian_ortho.ply_stream import read_binary_ply_layout
from gaussian_tiles import (
    GsTileBuildOptions,
    build_gstile_bundle,
    decode_pack,
    repack_gstile_bundle,
    validate_manifest,
)
from gaussian_tiles.format import PACK_DTYPE, PACK_HEADER_SIZE, encode_pack
from gaussian_tiles.tiler import (
    _adaptive_moment_lod_proxy,
    _gaussian_render_bounds,
    _invisible_giant_mask,
    _moment_matched_lod_proxy,
    _opacity_design_matrix,
    _proxy_support_error,
)


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


def _records(count: int) -> np.ndarray:
    records = np.zeros(count, dtype=PLY_DTYPE)
    sequence = np.linspace(-2.0, 3.0, count, dtype=np.float32)
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
    return records


def _write_ply(path: Path, records: np.ndarray) -> None:
    header = [
        "ply",
        "format binary_little_endian 1.0",
        f"element vertex {records.shape[0]}",
        *(f"property float {name}" for name in PROPERTY_NAMES),
        "end_header",
        "",
    ]
    with path.open("wb") as handle:
        handle.write("\n".join(header).encode("ascii"))
        records.tofile(handle)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_invisible_giant_filter_is_directionally_conservative() -> None:
    records = _records(4)
    for name in ("scale_0", "scale_1", "scale_2"):
        records[name] = np.log(0.01)
    records["opacity"] = -10.0
    for index in range(15):
        records[f"opacity_sh_{index}"] = 0.0

    records[0]["scale_0"] = np.log(0.10)  # giant and invisible: remove
    records[1]["scale_0"] = np.log(0.10)  # giant but visible: retain
    records[1]["opacity"] = 0.0
    records[3]["scale_0"] = np.log(0.10)  # directional SH may make it visible
    records[3]["opacity_sh_0"] = 20.0

    np.testing.assert_array_equal(
        _invisible_giant_mask(records, 0.05, 0.05),
        np.array([True, False, False, False]),
    )


@pytest.mark.parametrize(
    "options",
    (
        GsTileBuildOptions(invisible_gaussian_scale_threshold=0.0),
        GsTileBuildOptions(invisible_gaussian_scale_threshold=np.inf),
        GsTileBuildOptions(visibility_opacity_threshold=0.0),
        GsTileBuildOptions(visibility_opacity_threshold=1.0),
    ),
)
def test_invisible_giant_filter_rejects_invalid_thresholds(
    options: GsTileBuildOptions,
) -> None:
    with pytest.raises(ValueError, match="threshold"):
        options.validate()


def test_bundle_filters_only_invisible_giants_before_partition(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    records = _records(2_500)
    records["opacity"] = 0.0
    for index in range(15):
        records[f"opacity_sh_{index}"] = 0.0
    records[:10]["scale_0"] = np.log(0.10)
    records[:10]["opacity"] = -10.0
    _write_ply(source, records)

    result = build_gstile_bundle(
        source,
        tmp_path / "filtered",
        options=GsTileBuildOptions(
            leaf_size=1_024,
            chunk_records=1_024,
            invisible_gaussian_scale_threshold=0.05,
            visibility_opacity_threshold=0.05,
        ),
    )
    manifest = json.loads(result.manifest_path.read_text("ascii"))
    validate_manifest(manifest)

    assert result.input_gaussian_count == 2_500
    assert result.gaussian_count == 2_490
    assert result.filtered_gaussian_count == 10
    assert manifest["source"]["inputGaussianCount"] == 2_500
    assert manifest["source"]["gaussianCount"] == 2_490
    assert manifest["statistics"]["filteredGaussianCount"] == 10


def test_baseline_pack_preserves_dronegs_fields_with_reported_error() -> None:
    source = _records(17)
    content, quantization, errors = encode_pack(
        source, np.arange(17, dtype=np.uint64), node_id="r01"
    )
    decoded = decode_pack(content, quantization)

    np.testing.assert_allclose(
        decoded["position"],
        np.column_stack((source["x"], source["y"], source["z"])),
        atol=errors["positionMax"] + 1.0e-6,
    )
    np.testing.assert_allclose(decoded["opacity_logit"], source["opacity"], atol=errors["opacityLogitMax"] + 1.0e-6)
    expected_opacity_sh = np.column_stack(
        [source[f"opacity_sh_{index}"] for index in range(15)]
    )
    np.testing.assert_allclose(
        decoded["opacity_sh"],
        expected_opacity_sh,
        atol=errors["opacityShMax"] + 1.0e-6,
    )
    np.testing.assert_array_equal(decoded["source_id"], np.arange(17, dtype=np.uint64))
    assert len(content) == PACK_HEADER_SIZE + 17 * 96


def test_pack_rejects_corruption() -> None:
    content, quantization, _errors = encode_pack(
        _records(2), np.arange(2, dtype=np.uint64), node_id="r"
    )
    corrupted = bytearray(content)
    corrupted[-1] ^= 1
    with pytest.raises(ValueError, match="CRC"):
        decode_pack(bytes(corrupted), quantization)


def test_tiler_is_deterministic_and_spatially_bounded(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(2_500))
    events: list[dict[str, object]] = []
    options = GsTileBuildOptions(
        leaf_size=1_024,
        chunk_records=1_024,
        progress_callback=events.append,
    )
    first = build_gstile_bundle(source, tmp_path / "first", options=options)
    second = build_gstile_bundle(source, tmp_path / "second", options=options)

    first_manifest = json.loads(first.manifest_path.read_text("ascii"))
    second_manifest = json.loads(second.manifest_path.read_text("ascii"))
    validate_manifest(first_manifest)
    assert first.bundle_id == second.bundle_id
    assert first.gaussian_count == 2_500
    assert first.leaf_count >= 3
    assert first_manifest["source"]["sha256"] == _sha256(source)
    assert sum(pack["recordCount"] for pack in first_manifest["packs"]) == 2_500
    for left, right in zip(first_manifest["packs"], second_manifest["packs"]):
        assert left["sha256"] == right["sha256"]
        assert _sha256(first.output / left["path"]) == _sha256(second.output / right["path"])
        assert left["encodings"]["zstd"] == right["encodings"]["zstd"]
        encoded = left["encodings"]["zstd"]
        compressed_path = first.output / encoded["path"]
        raw_path = first.output / left["path"]
        assert compressed_path.stat().st_size == encoded["byteLength"]
        assert _sha256(compressed_path) == encoded["sha256"]
        assert zstandard.ZstdDecompressor().decompress(
            compressed_path.read_bytes()
        ) == raw_path.read_bytes()
    assert read_binary_ply_layout(source).vertex_count == 2_500
    event_names = {str(event["event"]) for event in events}
    assert {
        "preflight",
        "source_copy",
        "source_ready",
        "partition_split",
        "leaf_written",
        "published",
    } <= event_names


def test_tiler_aggregates_adjacent_tiles_losslessly_and_deterministically(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(5_000))
    options = GsTileBuildOptions(
        leaf_size=1_024,
        chunk_records=1_024,
        pack_target_bytes=220_000,
    )
    first = build_gstile_bundle(source, tmp_path / "first", options=options)
    second = build_gstile_bundle(source, tmp_path / "second", options=options)
    manifest = json.loads(first.manifest_path.read_text("ascii"))
    repeated = json.loads(second.manifest_path.read_text("ascii"))
    validate_manifest(manifest)

    tiles_by_pack: dict[str, list[dict[str, object]]] = {}
    for node in manifest["nodes"]:
        if tile := node.get("tile"):
            tiles_by_pack.setdefault(tile["pack"], []).append(tile)
    assert len(manifest["packs"]) < sum(len(tiles) for tiles in tiles_by_pack.values())
    assert any(len(tiles) > 1 for tiles in tiles_by_pack.values())
    assert manifest["statistics"]["packCount"] == len(manifest["packs"])
    assert manifest["statistics"]["representationCount"] == first.leaf_count
    assert manifest["statistics"]["packTargetBytes"] == 220_000
    assert first.bundle_id == second.bundle_id
    assert [pack["sha256"] for pack in manifest["packs"]] == [
        pack["sha256"] for pack in repeated["packs"]
    ]

    source_ids: list[int] = []
    for pack in manifest["packs"]:
        content = (first.output / pack["path"]).read_bytes()
        assert len(content) == pack["byteLength"]
        assert hashlib.sha256(content).hexdigest() == pack["sha256"]
        if encoded := pack.get("encodings", {}).get("zstd"):
            assert zstandard.ZstdDecompressor().decompress(
                (first.output / encoded["path"]).read_bytes()
            ) == content
        cursor = PACK_HEADER_SIZE
        for tile in sorted(
            tiles_by_pack[pack["id"]], key=lambda value: value["byteOffset"]
        ):
            assert tile["byteOffset"] == cursor
            assert tile["sha256"] == pack["sha256"]
            end = cursor + tile["byteLength"]
            packed = np.frombuffer(content[cursor:end], dtype=PACK_DTYPE)
            source_ids.extend(int(value) for value in packed["source_id"])
            cursor = end
        assert cursor == len(content)
    assert sorted(source_ids) == list(range(5_000))


def test_manifest_rejects_overlapping_aggregate_tile_ranges(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(5_000))
    result = build_gstile_bundle(
        source,
        tmp_path / "bundle",
        options=GsTileBuildOptions(
            leaf_size=1_024,
            chunk_records=1_024,
            pack_target_bytes=220_000,
        ),
    )
    manifest = json.loads(result.manifest_path.read_text("ascii"))
    nodes_by_pack: dict[str, list[dict[str, object]]] = {}
    for node in manifest["nodes"]:
        if tile := node.get("tile"):
            nodes_by_pack.setdefault(tile["pack"], []).append(tile)
    shared = next(tiles for tiles in nodes_by_pack.values() if len(tiles) > 1)
    shared[1]["byteOffset"] = shared[0]["byteOffset"]
    with pytest.raises(ValueError, match="without holes or overlaps"):
        validate_manifest(manifest)


def test_pack_target_rejects_out_of_contract_sizes() -> None:
    with pytest.raises(ValueError, match="pack_target_bytes"):
        GsTileBuildOptions(pack_target_bytes=127).validate()
    with pytest.raises(ValueError, match="pack_target_bytes"):
        GsTileBuildOptions(pack_target_bytes=1024**3 + 1).validate()


def test_aggregate_packs_do_not_mix_exact_tiles_and_lod_proxies(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(5_000))
    result = build_gstile_bundle(
        source,
        tmp_path / "bundle",
        options=GsTileBuildOptions(
            leaf_size=1_024,
            chunk_records=1_024,
            lod_proxy_size=1_024,
            pack_target_bytes=220_000,
        ),
    )
    manifest = json.loads(result.manifest_path.read_text("ascii"))
    validate_manifest(manifest)
    exact_pack_ids = {
        node["tile"]["pack"] for node in manifest["nodes"] if "tile" in node
    }
    proxy_pack_ids = {
        node["lodTile"]["pack"]
        for node in manifest["nodes"]
        if "lodTile" in node
    }
    assert exact_pack_ids
    assert proxy_pack_ids
    assert exact_pack_ids.isdisjoint(proxy_pack_ids)
    assert all(pack_id.startswith("aggregate-exact-") for pack_id in exact_pack_ids)
    assert all(pack_id.startswith("aggregate-proxy-") for pack_id in proxy_pack_ids)
    depths_by_pack: dict[str, set[int]] = {}
    for node in manifest["nodes"]:
        for field in ("tile", "lodTile"):
            if tile := node.get(field):
                depths_by_pack.setdefault(tile["pack"], set()).add(len(node["id"]) - 1)
    assert all(len(depths) == 1 for depths in depths_by_pack.values())
    assert manifest["statistics"]["representationCount"] == sum(
        "tile" in node or "lodTile" in node for node in manifest["nodes"]
    )


def _representation_payloads(
    bundle: Path,
    manifest: dict[str, object],
) -> dict[tuple[str, str], bytes]:
    packs = {
        pack["id"]: (bundle / pack["path"]).read_bytes()
        for pack in manifest["packs"]
    }
    payloads = {}
    for node in manifest["nodes"]:
        for field in ("tile", "lodTile"):
            if tile := node.get(field):
                start = tile["byteOffset"]
                payloads[(node["id"], field)] = packs[tile["pack"]][
                    start : start + tile["byteLength"]
                ]
    return payloads


def test_repack_preserves_every_canonical_tile_payload(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(5_000))
    original = build_gstile_bundle(
        source,
        tmp_path / "original",
        options=GsTileBuildOptions(
            leaf_size=1_024,
            chunk_records=1_024,
            lod_proxy_size=1_024,
        ),
    )
    first = repack_gstile_bundle(
        original.output,
        tmp_path / "first",
        pack_target_bytes=220_000,
    )
    second = repack_gstile_bundle(
        original.output,
        tmp_path / "second",
        pack_target_bytes=220_000,
    )
    original_manifest = json.loads(original.manifest_path.read_text("ascii"))
    first_manifest = json.loads(first.manifest_path.read_text("ascii"))
    second_manifest = json.loads(second.manifest_path.read_text("ascii"))
    validate_manifest(first_manifest)

    assert first.pack_count < first.source_pack_count
    assert first.bundle_id == second.bundle_id
    assert first_manifest["source"] == original_manifest["source"]
    assert first_manifest["statistics"]["packGrouping"] == "depth-spatial-v1"
    depths_by_pack: dict[str, set[int]] = {}
    for node in first_manifest["nodes"]:
        for field in ("tile", "lodTile"):
            if tile := node.get(field):
                depths_by_pack.setdefault(tile["pack"], set()).add(len(node["id"]) - 1)
    assert all(len(depths) == 1 for depths in depths_by_pack.values())
    assert _representation_payloads(original.output, original_manifest) == (
        _representation_payloads(first.output, first_manifest)
    )
    assert [pack["sha256"] for pack in first_manifest["packs"]] == [
        pack["sha256"] for pack in second_manifest["packs"]
    ]


def test_repack_rejects_corrupt_canonical_source_pack(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(1_024))
    original = build_gstile_bundle(source, tmp_path / "original")
    manifest = json.loads(original.manifest_path.read_text("ascii"))
    pack_path = original.output / manifest["packs"][0]["path"]
    corrupted = bytearray(pack_path.read_bytes())
    corrupted[-1] ^= 1
    pack_path.write_bytes(corrupted)

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        repack_gstile_bundle(
            original.output,
            tmp_path / "repacked",
            pack_target_bytes=220_000,
        )


def test_manifest_rejects_pack_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(1_024))
    result = build_gstile_bundle(source, tmp_path / "bundle")
    manifest = json.loads(result.manifest_path.read_text("ascii"))
    manifest["packs"][0]["path"] = "../secret"
    with pytest.raises(ValueError, match="escapes"):
        validate_manifest(manifest)


def test_manifest_rejects_zstd_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(1_024))
    result = build_gstile_bundle(source, tmp_path / "bundle")
    manifest = json.loads(result.manifest_path.read_text("ascii"))
    manifest["packs"][0]["encodings"]["zstd"]["path"] = "../secret.zst"
    with pytest.raises(ValueError, match="escapes"):
        validate_manifest(manifest)


def test_stratified_lod_profile_is_deterministic_and_preserves_exact_leaves(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(5_000))
    options = GsTileBuildOptions(
        leaf_size=1_024,
        chunk_records=1_024,
        lod_proxy_size=1_024,
        lod_proxy_strategy="spatial-stratified",
    )
    first = build_gstile_bundle(source, tmp_path / "first", options=options)
    second = build_gstile_bundle(source, tmp_path / "second", options=options)
    legacy = build_gstile_bundle(
        source,
        tmp_path / "legacy",
        options=GsTileBuildOptions(
            leaf_size=1_024,
            chunk_records=1_024,
            lod_proxy_size=1_024,
            lod_proxy_strategy="minhash",
        ),
    )
    first_manifest = json.loads(first.manifest_path.read_text("ascii"))
    second_manifest = json.loads(second.manifest_path.read_text("ascii"))
    legacy_manifest = json.loads(legacy.manifest_path.read_text("ascii"))

    validate_manifest(first_manifest)
    validate_manifest(legacy_manifest)
    assert first.bundle_id == second.bundle_id
    assert first_manifest["profile"].endswith("stratified-lod-v2")
    assert first_manifest["statistics"]["lod"] == (
        "deterministic-morton-stratified-replacement-v2"
    )
    assert legacy_manifest["profile"].endswith("minhash-lod-v1")
    assert legacy_manifest["statistics"]["lod"] == "deterministic-minhash-replacement-v1"
    assert first_manifest["statistics"]["proxyCount"] > 0
    assert sum(
        node["tile"]["recordCount"]
        for node in first_manifest["nodes"]
        if "tile" in node
    ) == 5_000

    root = next(node for node in first_manifest["nodes"] if node["id"] == "r")
    assert root["geometricError"] > 0
    proxy_pack = next(
        pack
        for pack in first_manifest["packs"]
        if pack["id"] == root["lodTile"]["pack"]
    )
    decoded = decode_pack(
        (first.output / proxy_pack["path"]).read_bytes(),

        root["lodTile"]["quantization"],
    )
    assert decoded["source_id"].shape == (1_024,)
    assert np.unique(decoded["source_id"]).shape == (1_024,)
    assert decoded["source_id"].min() < 100
    assert decoded["source_id"].max() > 4_900
    assert np.all(decoded["source_id"] < 5_000)

    first_hashes = {pack["id"]: pack["sha256"] for pack in first_manifest["packs"]}
    second_hashes = {
        pack["id"]: pack["sha256"] for pack in second_manifest["packs"]
    }
    assert first_hashes == second_hashes
    legacy_hashes = {
        pack["id"]: pack["sha256"]
        for pack in legacy_manifest["packs"]
        if not pack["id"].startswith("lod-")
    }
    exact_hashes = {key: value for key, value in first_hashes.items() if not key.startswith("lod-")}
    assert exact_hashes == legacy_hashes


def test_moment_matched_proxy_conserves_mass_covariance_and_coefficients() -> None:
    records = _records(2)
    records["x"] = (-1.0, 1.0)
    records["y"] = 0.0
    records["z"] = 0.0
    records["f_dc_0"] = (1.0, 3.0)
    records["scale_0"] = np.log(0.1)
    records["scale_1"] = np.log(0.1)
    records["scale_2"] = np.log(0.1)
    records["opacity"] = 0.0
    records["rot_0"] = 1.0
    records["rot_1"] = records["rot_2"] = records["rot_3"] = 0.0
    working = np.empty(
        2,
        dtype=np.dtype(
            {
                "names": [*PROPERTY_NAMES, "source_id"],
                "formats": [*("<f4" for _ in PROPERTY_NAMES), "<u8"],
                "offsets": [
                    *(PLY_DTYPE.fields[name][1] for name in PROPERTY_NAMES),
                    PLY_DTYPE.itemsize,
                ],
                "itemsize": PLY_DTYPE.itemsize + 8,
            }
        ),
    )
    for name in PROPERTY_NAMES:
        working[name] = records[name]
    working["source_id"] = (0, 1)

    proxy = _moment_matched_lod_proxy(working, np.zeros(2), 1)
    merged = proxy.records[0]

    assert merged["x"] == pytest.approx(0.0, abs=1e-6)
    assert merged["f_dc_0"] == pytest.approx(2.0, abs=1e-6)
    assert np.exp(merged["scale_0"]) > 0.9
    assert proxy.errors[0] >= 1.0
    assert merged["source_id"] == 0
    assert all(np.all(np.isfinite(proxy.records[name])) for name in PROPERTY_NAMES)


def test_proxy_support_error_detects_covariance_blur_without_center_motion() -> None:
    records = _records(2)
    records["x"] = records["y"] = records["z"] = 0.0
    records["scale_0"] = records["scale_1"] = records["scale_2"] = np.log(0.75)
    records["rot_0"] = 1.0
    records["rot_1"] = records["rot_2"] = records["rot_3"] = 0.0

    assert _proxy_support_error(records) == pytest.approx(0.75, rel=1e-6)


def test_moment_matched_lod_profile_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(2_500))
    result = build_gstile_bundle(
        source,
        tmp_path / "moment",
        options=GsTileBuildOptions(leaf_size=1_024, chunk_records=1_024, lod_proxy_size=1_024),
    )
    manifest = json.loads(result.manifest_path.read_text("ascii"))
    assert manifest["profile"].endswith("moment-lod-v3")
    assert manifest["statistics"]["lod"] == "deterministic-morton-moment-matched-v3"
    validate_manifest(manifest)


def test_adaptive_v4_is_deterministic_and_has_conservative_render_bounds(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ply"
    records = _records(2_500)
    records["scale_0"] = np.log(0.25)
    records["scale_1"] = np.log(0.5)
    records["scale_2"] = np.log(1.0)
    _write_ply(source, records)
    options = GsTileBuildOptions(
        leaf_size=1_024,
        chunk_records=1_024,
        lod_proxy_size=1_024,
        lod_proxy_strategy="adaptive-moment",
    )
    first = build_gstile_bundle(source, tmp_path / "adaptive-first", options=options)
    second = build_gstile_bundle(source, tmp_path / "adaptive-second", options=options)
    first_manifest = json.loads(first.manifest_path.read_text("ascii"))
    second_manifest = json.loads(second.manifest_path.read_text("ascii"))

    validate_manifest(first_manifest)
    assert first.bundle_id == second.bundle_id
    assert first_manifest["profile"].endswith("adaptive-lod-v4")
    assert first_manifest["statistics"]["lod"] == (
        "deterministic-adaptive-cost-moment-opacity-refit-v4"
    )
    nodes = {node["id"]: node for node in first_manifest["nodes"]}
    packs = {pack["id"]: pack for pack in first_manifest["packs"]}
    for node in nodes.values():
        assert all(
            node["renderBounds"]["min"][axis] <= node["bounds"]["min"][axis]
            and node["renderBounds"]["max"][axis] >= node["bounds"]["max"][axis]
            for axis in range(3)
        )
        for child_id in node.get("children", []):
            child = nodes[child_id]
            assert all(
                node["renderBounds"]["min"][axis]
                <= child["renderBounds"]["min"][axis]
                and node["renderBounds"]["max"][axis]
                >= child["renderBounds"]["max"][axis]
                for axis in range(3)
            )
        if "lodTile" in node:
            representation = node["lodTile"]
            pack = packs[representation["pack"]]
            decoded = decode_pack(
                (first.output / pack["path"]).read_bytes(),
                representation["quantization"],
            )
            assert node["geometricError"] >= float(
                np.max(np.exp(decoded["log_scale"]), initial=0.0)
            ) - 1.0e-5
            proxy_records = np.zeros(decoded["position"].shape[0], dtype=PLY_DTYPE)
            proxy_records["x"] = decoded["position"][:, 0]
            proxy_records["y"] = decoded["position"][:, 1]
            proxy_records["z"] = decoded["position"][:, 2]
            proxy_records["scale_0"] = decoded["log_scale"][:, 0]
            proxy_records["scale_1"] = decoded["log_scale"][:, 1]
            proxy_records["scale_2"] = decoded["log_scale"][:, 2]
            proxy_records["rot_0"] = decoded["rotation"][:, 0]
            proxy_records["rot_1"] = decoded["rotation"][:, 1]
            proxy_records["rot_2"] = decoded["rotation"][:, 2]
            proxy_records["rot_3"] = decoded["rotation"][:, 3]
            proxy_minimum, proxy_maximum = _gaussian_render_bounds(proxy_records)
            assert all(
                node["renderBounds"]["min"][axis] <= proxy_minimum[axis] + 1.0e-5
                and node["renderBounds"]["max"][axis] >= proxy_maximum[axis] - 1.0e-5
                for axis in range(3)
            )

    first_hashes = {
        pack["id"]: pack["sha256"]
        for pack in first_manifest["packs"]
        if not pack["id"].startswith("lod-")
    }
    second_hashes = {
        pack["id"]: pack["sha256"]
        for pack in second_manifest["packs"]
        if not pack["id"].startswith("lod-")
    }
    assert first_hashes == second_hashes


def test_adaptive_proxy_preserves_isolated_detail() -> None:
    records = _records(17)
    working = np.empty(
        17,
        dtype=np.dtype(
            {
                "names": [*PROPERTY_NAMES, "source_id"],
                "formats": [*("<f4" for _ in PROPERTY_NAMES), "<u8"],
                "offsets": [
                    *(PLY_DTYPE.fields[name][1] for name in PROPERTY_NAMES),
                    PLY_DTYPE.itemsize,
                ],
                "itemsize": PLY_DTYPE.itemsize + 8,
            }
        ),
    )
    for name in PROPERTY_NAMES:
        working[name] = records[name]
    working["source_id"] = np.arange(17, dtype=np.uint64)
    working["x"][:16] = np.linspace(0.0, 0.15, 16)
    working["x"][16] = 20.0
    working["f_dc_0"][:16] = 0.0
    working["f_dc_0"][16] = 10.0

    proxy = _adaptive_moment_lod_proxy(working, np.zeros(17), 5)

    assert proxy.records.shape == (5,)
    assert np.max(proxy.records["x"]) > 19.0
    assert np.max(proxy.records["f_dc_0"]) > 9.0


def test_adaptive_proxy_refits_directional_opacity_after_sigmoid() -> None:
    records = _records(2)
    working = np.empty(
        2,
        dtype=np.dtype(
            {
                "names": [*PROPERTY_NAMES, "source_id"],
                "formats": [*("<f4" for _ in PROPERTY_NAMES), "<u8"],
                "offsets": [
                    *(PLY_DTYPE.fields[name][1] for name in PROPERTY_NAMES),
                    PLY_DTYPE.itemsize,
                ],
                "itemsize": PLY_DTYPE.itemsize + 8,
            }
        ),
    )
    for name in PROPERTY_NAMES:
        working[name] = records[name]
    working["source_id"] = (0, 1)
    working["x"] = working["y"] = working["z"] = 0.0
    working["scale_0"] = working["scale_1"] = working["scale_2"] = np.log(0.1)
    working["opacity"] = (2.0, -2.0)
    for index in range(15):
        working[f"opacity_sh_{index}"] = 0.0
    working["opacity_sh_2"] = (5.0, 0.5)

    adaptive = _adaptive_moment_lod_proxy(working, np.zeros(2), 1).records[0]
    linear = _moment_matched_lod_proxy(working, np.zeros(2), 1).records[0]
    design = _opacity_design_matrix(15)
    source_coefficients = np.column_stack(
        (working["opacity"], *(working[f"opacity_sh_{index}"] for index in range(15)))
    )
    target = np.clip(
        np.sum(1.0 / (1.0 + np.exp(-(source_coefficients @ design.T))), axis=0),
        1e-7,
        1.0 - 1e-7,
    )

    def prediction(record: np.void) -> np.ndarray:
        coefficients = np.asarray(
            [record["opacity"], *(record[f"opacity_sh_{index}"] for index in range(15))]
        )
        return 1.0 / (1.0 + np.exp(-(coefficients @ design.T)))

    adaptive_error = np.mean(np.square(prediction(adaptive) - target))
    linear_error = np.mean(np.square(prediction(linear) - target))
    assert adaptive_error < linear_error * 0.6


def test_gaussian_render_bounds_include_rotated_three_sigma_support() -> None:
    records = _records(1)
    working = np.empty(
        1,
        dtype=np.dtype(
            {
                "names": [*PROPERTY_NAMES, "source_id"],
                "formats": [*("<f4" for _ in PROPERTY_NAMES), "<u8"],
                "offsets": [
                    *(PLY_DTYPE.fields[name][1] for name in PROPERTY_NAMES),
                    PLY_DTYPE.itemsize,
                ],
                "itemsize": PLY_DTYPE.itemsize + 8,
            }
        ),
    )
    for name in PROPERTY_NAMES:
        working[name] = records[name]
    working["source_id"] = 0
    working["x"] = 10.0
    working["y"] = -2.0
    working["z"] = 3.0
    working["scale_0"] = np.log(2.0)
    working["scale_1"] = np.log(1.0)
    working["scale_2"] = np.log(0.5)
    working["rot_0"] = 1.0
    working["rot_1"] = working["rot_2"] = working["rot_3"] = 0.0

    minimum, maximum = _gaussian_render_bounds(working)

    assert minimum == pytest.approx((4.0, -5.0, 1.5), abs=1e-5)
    assert maximum == pytest.approx((16.0, 1.0, 4.5), abs=1e-5)


def test_bundle_publication_is_immutable(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(1_024))
    output = tmp_path / "bundle"
    build_gstile_bundle(source, output)
    with pytest.raises(FileExistsError, match="immutable"):
        build_gstile_bundle(source, output)
