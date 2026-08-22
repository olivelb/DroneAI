from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

COLMAP_ROOT = Path(__file__).resolve().parents[2]
if str(COLMAP_ROOT) not in sys.path:
    sys.path.insert(0, str(COLMAP_ROOT))

from gaussian_ortho.ply_stream import read_binary_ply_layout
from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle, decode_pack, validate_manifest
from gaussian_tiles.format import PACK_HEADER_SIZE, encode_pack


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


def test_manifest_rejects_pack_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(1_024))
    result = build_gstile_bundle(source, tmp_path / "bundle")
    manifest = json.loads(result.manifest_path.read_text("ascii"))
    manifest["packs"][0]["path"] = "../secret"
    with pytest.raises(ValueError, match="escapes"):
        validate_manifest(manifest)


def test_lod_profile_adds_deterministic_proxies_without_changing_leaves(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(5_000))
    options = GsTileBuildOptions(
        leaf_size=1_024,
        chunk_records=1_024,
        lod_proxy_size=1_024,
    )
    first = build_gstile_bundle(source, tmp_path / "first", options=options)
    second = build_gstile_bundle(source, tmp_path / "second", options=options)
    first_manifest = json.loads(first.manifest_path.read_text("ascii"))
    second_manifest = json.loads(second.manifest_path.read_text("ascii"))

    validate_manifest(first_manifest)
    assert first.bundle_id == second.bundle_id
    assert first_manifest["profile"].endswith("minhash-lod-v1")
    assert first_manifest["statistics"]["lod"] == (
        "deterministic-minhash-replacement-v1"
    )
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
    assert np.all(decoded["source_id"] < 5_000)

    first_hashes = {pack["id"]: pack["sha256"] for pack in first_manifest["packs"]}
    second_hashes = {
        pack["id"]: pack["sha256"] for pack in second_manifest["packs"]
    }
    assert first_hashes == second_hashes


def test_bundle_publication_is_immutable(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _write_ply(source, _records(1_024))
    output = tmp_path / "bundle"
    build_gstile_bundle(source, output)
    with pytest.raises(FileExistsError, match="immutable"):
        build_gstile_bundle(source, output)
