"""Lossless, independently authenticated attribute streams for canonical Q96 packs.

The base stream retains geometry, DC/logit and stable identifiers (36 B).
SH contains the 45 colour and 15 opacity coefficients (60 B).
"""

from __future__ import annotations
import hashlib
import struct
import zlib
from collections.abc import Mapping
from typing import Any
from pathlib import Path

STREAM_STRIDES = {"base": 36, "sh": 60}
MAGIC = b"GSATTR1\0"


def split_q96(content: bytes) -> dict[str, bytes]:
    if len(content) < 32 or content[:8] != b"GSTILE1\0":
        raise ValueError("Invalid canonical Q96 pack")
    count = struct.unpack_from("<I", content, 16)[0]
    if count < 1 or len(content) != 32 + count * 96:
        raise ValueError("Invalid canonical Q96 length")
    version, header, stride, flags = struct.unpack_from("<HHHH", content, 8)
    if (version, header, stride, flags) != (1, 32, 96, 0) or zlib.crc32(content[32:]) != struct.unpack_from(
        "<I", content, 28
    )[0]:
        raise ValueError("Invalid canonical Q96 layout or CRC")
    # Strided bulk byte copies avoid a Python loop over millions of records.
    payload = memoryview(content)[32:]
    result = {}
    for kind, stride in STREAM_STRIDES.items():
        columns = list(range(28)) + list(range(88, 96)) if kind == "base" else list(range(28, 88))
        body = bytearray(count * stride)
        for dst, src in enumerate(columns):
            body[dst::stride] = payload[src::96]
        header = struct.pack("<8sHHHHIQI", MAGIC, 1, 32, stride, 1 if kind == "base" else 2, count, 0, zlib.crc32(body))
        result[kind] = header + body
    return result


def validate_stream(content: bytes, kind: str, count: int) -> memoryview:
    stride = STREAM_STRIDES[kind]
    if len(content) != 32 + count * stride:
        raise ValueError("Attribute stream length mismatch")
    magic, version, header, actual_stride, flag, actual_count, reserved, crc = struct.unpack_from("<8sHHHHIQI", content)
    if (magic, version, header, actual_stride, flag, actual_count, reserved) != (
        MAGIC,
        1,
        32,
        stride,
        1 if kind == "base" else 2,
        count,
        0,
    ) or zlib.crc32(content[32:]) != crc:
        raise ValueError("Attribute stream header or CRC mismatch")
    return memoryview(content)[32:]


def join_q96_payload(base: bytes, sh: bytes | None, count: int) -> bytes:
    geometry = validate_stream(base, "base", count)
    harmonics = validate_stream(sh, "sh", count) if sh is not None else None
    result = bytearray(count * 96)
    for src, dst in enumerate(list(range(28)) + list(range(88, 96))):
        result[dst::96] = geometry[src::36]
    if harmonics is not None:
        for src in range(60):
            result[28 + src :: 96] = harmonics[src::60]
    return bytes(result)


def stream_metadata(content: bytes, path: str) -> dict[str, Any]:
    return {"path": path, "byteLength": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def validate_stream_metadata(streams: Any, count: int) -> None:
    from shared.gstile_manifest import safe_bundle_path
    import re

    if (
        not isinstance(streams, Mapping)
        or set(streams) != {"version", "base", "sh"}
        or streams["version"] != 1
        or isinstance(streams["version"], bool)
    ):
        raise ValueError("Unsupported GSTile attribute streams")
    for kind, stride in STREAM_STRIDES.items():
        stream = streams[kind]
        if not isinstance(stream, Mapping):
            raise ValueError("Invalid GSTile attribute stream")
        safe_bundle_path(stream.get("path"), "streams." + kind + ".path")
        if (
            type(stream.get("byteLength")) is not int
            or stream["byteLength"] != 32 + count * stride
            or not isinstance(stream.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", stream["sha256"])
        ):
            raise ValueError("Invalid GSTile attribute stream identity")
    if streams["base"]["path"] == streams["sh"]["path"]:
        raise ValueError("Attribute streams must have distinct paths")


def validate_pack_storage(pack: Mapping[str, Any]) -> None:
    """Absent storage means a historical canonical file; streams means virtual Q96."""
    storage = pack.get("storage")
    if "storage" not in pack:
        return
    if storage != "streams" or "streams" not in pack or "encodings" in pack:
        raise ValueError("Invalid GSTile stream-only storage")
    header_hex = pack.get("q96Header")
    if not isinstance(header_hex, str) or len(header_hex) != 64 or any(c not in "0123456789abcdef" for c in header_hex):
        raise ValueError("Missing virtual Q96 header")
    try:
        header = bytes.fromhex(header_hex)
    except ValueError as error:
        raise ValueError("Invalid virtual Q96 header") from error
    if len(header) != 32 or header[:8] != b"GSTILE1\0":
        raise ValueError("Invalid virtual Q96 header")
    if struct.unpack_from("<HHHHI", header, 8) != (1, 32, 96, 0, pack["recordCount"]):
        raise ValueError("Invalid virtual Q96 layout")


def read_pack_content(root: str | Path, pack: Mapping[str, Any]) -> bytes:
    """Read verified canonical bytes, physically or reconstructed from both streams."""
    from shared.gstile_manifest import safe_bundle_path

    root = Path(root).resolve()

    def read(entry: Mapping[str, Any]) -> bytes:
        path = (root / safe_bundle_path(entry["path"], "pack.path")).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Pack link escapes source bundle")
        if path.stat().st_size != entry["byteLength"]:
            raise ValueError("Pack size mismatch")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("Pack SHA256 mismatch")
        return content

    validate_pack_storage(pack)
    if pack.get("storage") != "streams":
        return read(pack)
    streams = pack["streams"]
    payload = join_q96_payload(read(streams["base"]), read(streams["sh"]), pack["recordCount"])
    content = bytes.fromhex(pack["q96Header"]) + payload
    if len(content) != pack["byteLength"] or hashlib.sha256(content).hexdigest() != pack["sha256"]:
        raise ValueError("Reconstructed Q96 SHA256 mismatch")
    if zlib.crc32(payload) != struct.unpack_from("<I", content, 28)[0]:
        raise ValueError("Reconstructed Q96 CRC mismatch")
    return content
