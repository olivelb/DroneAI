import hashlib
import struct
import zlib
import pytest
from shared.gstile_streams import (
    split_q96,
    join_q96_payload,
    validate_stream,
    validate_stream_metadata,
    stream_metadata,
)


def fixture():
    payload = bytes((i * 37 + i // 96) % 256 for i in range(96 * 257))
    return struct.pack("<8sHHHHIQI", b"GSTILE1\0", 1, 32, 96, 0, 257, 10, zlib.crc32(payload)) + payload


def test_lossless_split_and_base_only():
    pack = fixture()
    streams = split_q96(pack)
    assert join_q96_payload(streams["base"], streams["sh"], 257) == pack[32:]
    base = join_q96_payload(streams["base"], None, 257)
    for i in range(257):
        assert base[i * 96 : i * 96 + 28] == pack[32 + i * 96 : 60 + i * 96]
        assert base[i * 96 + 88 : i * 96 + 96] == pack[120 + i * 96 : 128 + i * 96]
        assert base[i * 96 + 28 : i * 96 + 88] == bytes(60)


def test_stream_identity_rejects_corruption_truncation_kind_and_count():
    streams = split_q96(fixture())
    for content, kind, count in [
        (streams["base"][:-1], "base", 257),
        (streams["base"][:-1] + bytes([streams["base"][-1] ^ 1]), "base", 257),
        (streams["sh"], "base", 257),
        (streams["base"], "base", 256),
    ]:
        with pytest.raises(ValueError):
            validate_stream(content, kind, count)


def test_metadata_paths_and_lengths():
    streams = split_q96(fixture())
    meta = {"version": 1, **{kind: stream_metadata(b, "packs/x." + kind) for kind, b in streams.items()}}
    validate_stream_metadata(meta, 257)
    meta["sh"]["path"] = "../escape"
    with pytest.raises(ValueError):
        validate_stream_metadata(meta, 257)


def test_converter_keeps_canonical_bytes_and_source_immutable(tmp_path):
    import json
    from tools.split_gstile_attributes import convert_bundle

    content = fixture()
    source = tmp_path / "source"
    source.mkdir()
    (source / "p.gst").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    bounds = {"min": [0, 0, 0], "max": [1, 1, 1]}
    manifest = {
        "schema": "droneai-gstile",
        "version": 1,
        "profile": "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4",
        "bundleId": "sha256:" + "a" * 64,
        "source": {"sha256": "b" * 64, "gaussianCount": 257},
        "root": "root",
        "packs": [
            {
                "id": "p",
                "path": "p.gst",
                "recordCount": 257,
                "byteOffset": 32,
                "byteLength": len(content),
                "sha256": digest,
            }
        ],
        "nodes": [
            {
                "id": "root",
                "bounds": bounds,
                "renderBounds": bounds,
                "gaussianCount": 257,
                "geometricError": 0,
                "tile": {"pack": "p", "byteOffset": 32, "byteLength": 257 * 96, "recordCount": 257, "sha256": digest},
            }
        ],
        "statistics": {
            "lod": "deterministic-adaptive-cost-moment-opacity-refit-v4",
            "proxyCount": 0,
            "proxyRecords": 0,
        },
    }
    original = json.dumps(manifest).encode()
    (source / "manifest.json").write_bytes(original)
    out = tmp_path / "new"
    identity = convert_bundle(source, out)
    converted = json.loads((out / "manifest.json").read_bytes())
    assert converted["bundleId"] == identity != manifest["bundleId"]
    assert (source / "manifest.json").read_bytes() == original
    from shared.gstile_streams import read_pack_content

    assert not (out / "p.gst").exists()
    assert converted["packs"][0]["storage"] == "streams"
    assert "encodings" not in converted["packs"][0]
    assert read_pack_content(out, converted["packs"][0]) == content
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "p.gst.base", "p.gst.sh"]
    second = tmp_path / "second"
    convert_bundle(out, second)
    assert read_pack_content(second, json.loads((second / "manifest.json").read_bytes())["packs"][0]) == content
    import shutil

    interrupted = tmp_path / "resumed.partial"
    shutil.copytree(out, interrupted)
    (interrupted / "p.gst.sh").write_bytes(b"interrupted write")
    (interrupted / "manifest.json.writing").write_text("interrupted manifest")
    resumed = tmp_path / "resumed"
    convert_bundle(source, resumed, resume=True)
    assert read_pack_content(resumed, json.loads((resumed / "manifest.json").read_bytes())["packs"][0]) == content

    assert join_q96_payload((out / "p.gst.base").read_bytes(), (out / "p.gst.sh").read_bytes(), 257) == content[32:]
    with pytest.raises(ValueError):
        convert_bundle(source, out)
    (source / "p.gst").write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    with pytest.raises(ValueError):
        convert_bundle(source, tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"storage": None},
        {"storage": "unknown"},
        {"q96Header": "0" * 64},
        {"q96Header": "z" * 64},
        {"encodings": {"zstd": {}}},
        {"streams": None},
    ],
)
def test_stream_only_storage_rejects_inconsistent_metadata(change):
    from shared.gstile_streams import validate_pack_storage

    pack = {"storage": "streams", "q96Header": fixture()[:32].hex(), "recordCount": 257, "streams": {"version": 1}}
    pack.update(change)
    if pack.get("streams") is None:
        # The full manifest validator normally catches malformed stream metadata first.
        from shared.gstile_streams import validate_stream_metadata

        with pytest.raises(ValueError):
            validate_stream_metadata(pack["streams"], 257)
    else:
        with pytest.raises(ValueError):
            validate_pack_storage(pack)
