"""GSTile v1 baseline pack codec and strict manifest validation."""

from __future__ import annotations

import hashlib
import math
import os
import struct
import zlib
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from shared.gstile_manifest import (
    GSTILE_PACK_HEADER_SIZE,
    GSTILE_PACK_RECORD_SIZE,
    GSTILE_VERSION,
    canonical_gstile_manifest_bytes,
    validate_gstile_manifest,
)

PACK_MAGIC = b"GSTILE1\0"
PACK_HEADER_SIZE = GSTILE_PACK_HEADER_SIZE
PACK_RECORD_SIZE = GSTILE_PACK_RECORD_SIZE
_HEADER = struct.Struct("<8sHHHHIQI")

PACK_DTYPE = np.dtype(
    {
        "names": (
            "position",
            "log_scale",
            "rotation",
            "opacity_logit",
            "color_dc",
            "color_sh",
            "opacity_sh",
            "source_id",
        ),
        "formats": (
            ("<u2", (3,)),
            ("<u2", (3,)),
            ("<i2", (4,)),
            "<u2",
            ("<i2", (3,)),
            ("i1", (45,)),
            ("i1", (15,)),
            "<u8",
        ),
        "offsets": (0, 6, 12, 20, 22, 28, 73, 88),
        "itemsize": PACK_RECORD_SIZE,
    }
)


def _finite_matrix(values: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32)
    if result.ndim != 2 or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must be a finite matrix")
    return result


def _affine_parameters(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    minimum = values.min(axis=0).astype(np.float32)
    maximum = values.max(axis=0).astype(np.float32)
    return minimum, maximum


def _quantize_u16(values: np.ndarray, minimum: np.ndarray, maximum: np.ndarray) -> np.ndarray:
    extent = maximum - minimum
    safe_extent = np.where(extent > 0.0, extent, 1.0)
    normalized = np.clip((values - minimum) / safe_extent, 0.0, 1.0)
    return np.rint(normalized * 65535.0).astype("<u2")


def _dequantize_u16(values: np.ndarray, minimum: np.ndarray, maximum: np.ndarray) -> np.ndarray:
    return minimum + values.astype(np.float32) * ((maximum - minimum) / 65535.0)


def _symmetric_scales(values: np.ndarray, maximum_integer: int) -> np.ndarray:
    maxima = np.max(np.abs(values), axis=0).astype(np.float32)
    return np.where(maxima > 0.0, maxima / float(maximum_integer), 1.0).astype(np.float32)


def _matrix_from_properties(records: np.ndarray, names: list[str], width: int) -> np.ndarray:
    result = np.zeros((records.shape[0], width), dtype=np.float32)
    for index, name in enumerate(names):
        result[:, index] = records[name]
    return result


def _ordered_properties(records: np.ndarray, prefix: str) -> list[str]:
    names = records.dtype.names or ()
    selected = [name for name in names if name.startswith(prefix)]
    try:
        return sorted(selected, key=lambda name: int(name.removeprefix(prefix)))
    except ValueError as error:
        raise ValueError(f"Malformed {prefix} PLY property") from error


def _sh_degree(color_rest_count: int, opacity_sh_count: int) -> tuple[int, int]:
    color_coefficients = color_rest_count // 3
    if color_rest_count % 3 or color_coefficients not in (0, 3, 8, 15):
        raise ValueError("Color SH residual count must encode degree 0 through 3")
    if opacity_sh_count not in (0, 3, 8, 15):
        raise ValueError("Opacity SH residual count must encode degree 0 through 3")
    color_degree = int(round(math.sqrt(color_coefficients + 1) - 1))
    opacity_degree = int(round(math.sqrt(opacity_sh_count + 1) - 1))
    return color_degree, opacity_degree


def encode_pack(
    records: np.ndarray,
    source_ids: np.ndarray,
    *,
    node_id: str,
) -> tuple[bytes, dict[str, Any], dict[str, float]]:
    """Encode one bounded leaf and return bytes, quantization, error bounds."""

    if records.shape[0] == 0:
        raise ValueError("GSTile packs cannot be empty")
    required = {
        "x",
        "y",
        "z",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
        "opacity",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
    }
    missing = sorted(required.difference(records.dtype.names or ()))
    if missing:
        raise ValueError("PLY is missing GSTile properties: " + ", ".join(missing))

    color_names = _ordered_properties(records, "f_rest_")
    opacity_names = _ordered_properties(records, "opacity_sh_")
    color_degree, opacity_degree = _sh_degree(len(color_names), len(opacity_names))

    position = _finite_matrix(
        np.column_stack((records["x"], records["y"], records["z"])),
        "position",
    )
    log_scale = _finite_matrix(
        np.column_stack((records["scale_0"], records["scale_1"], records["scale_2"])),
        "log_scale",
    )
    rotation = _finite_matrix(
        np.column_stack((records["rot_0"], records["rot_1"], records["rot_2"], records["rot_3"])),
        "rotation",
    )
    rotation_norm = np.linalg.norm(rotation, axis=1)
    if np.any(rotation_norm <= 1.0e-12):
        raise ValueError("PLY contains a zero-length quaternion")
    rotation = rotation / rotation_norm[:, None]
    opacity = _finite_matrix(records["opacity"].reshape(-1, 1), "opacity")
    color_dc = _finite_matrix(
        np.column_stack((records["f_dc_0"], records["f_dc_1"], records["f_dc_2"])),
        "color_dc",
    )
    color_sh = _finite_matrix(_matrix_from_properties(records, color_names, 45), "color_sh")
    opacity_sh = _finite_matrix(_matrix_from_properties(records, opacity_names, 15), "opacity_sh")

    position_min, position_max = _affine_parameters(position)
    scale_min, scale_max = _affine_parameters(log_scale)
    opacity_min, opacity_max = _affine_parameters(opacity)
    dc_scale = _symmetric_scales(color_dc, 32767)
    color_scale = _symmetric_scales(color_sh, 127)
    opacity_scale = _symmetric_scales(opacity_sh, 127)

    packed = np.zeros(records.shape[0], dtype=PACK_DTYPE)
    packed["position"] = _quantize_u16(position, position_min, position_max)
    packed["log_scale"] = _quantize_u16(log_scale, scale_min, scale_max)
    packed["rotation"] = np.rint(np.clip(rotation, -1.0, 1.0) * 32767.0).astype("<i2")
    packed["opacity_logit"] = _quantize_u16(opacity, opacity_min, opacity_max)[:, 0]
    packed["color_dc"] = np.rint(color_dc / dc_scale).clip(-32767, 32767).astype("<i2")
    packed["color_sh"] = np.rint(color_sh / color_scale).clip(-127, 127).astype("i1")
    packed["opacity_sh"] = np.rint(opacity_sh / opacity_scale).clip(-127, 127).astype("i1")
    packed["source_id"] = np.asarray(source_ids, dtype="<u8")

    payload = packed.tobytes(order="C")
    node_hash = int.from_bytes(hashlib.sha256(node_id.encode("utf-8")).digest()[:8], "little")
    header = _HEADER.pack(
        PACK_MAGIC,
        GSTILE_VERSION,
        PACK_HEADER_SIZE,
        PACK_RECORD_SIZE,
        0,
        records.shape[0],
        node_hash,
        zlib.crc32(payload),
    )
    quantization = {
        "position": {"min": position_min.tolist(), "max": position_max.tolist()},
        "logScale": {"min": scale_min.tolist(), "max": scale_max.tolist()},
        "rotation": {"encoding": "snorm16x4"},
        "opacityLogit": {"min": float(opacity_min[0]), "max": float(opacity_max[0])},
        "colorDcScale": dc_scale.tolist(),
        "colorShScale": color_scale.tolist(),
        "opacityShScale": opacity_scale.tolist(),
        "sourceColorShDegree": color_degree,
        "sourceOpacityShDegree": opacity_degree,
    }
    errors = {
        "positionMax": float(np.max((position_max - position_min) / 65535.0 / 2.0)),
        "logScaleMax": float(np.max((scale_max - scale_min) / 65535.0 / 2.0)),
        "rotationComponentMax": 0.5 / 32767.0,
        "opacityLogitMax": float((opacity_max[0] - opacity_min[0]) / 65535.0 / 2.0),
        "colorDcMax": float(np.max(dc_scale) / 2.0),
        "colorShMax": float(np.max(color_scale) / 2.0),
        "opacityShMax": float(np.max(opacity_scale) / 2.0),
    }
    return header + payload, quantization, errors


def write_pack_atomic(
    path: Path,
    records: np.ndarray,
    source_ids: np.ndarray,
    *,
    node_id: str,
) -> tuple[dict[str, Any], dict[str, float]]:
    content, quantization, errors = encode_pack(records, source_ids, node_id=node_id)
    content_sha256 = hashlib.sha256(content).hexdigest()
    payload_crc32 = _HEADER.unpack_from(content)[-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return (
        {
            "path": path.as_posix(),
            "byteLength": len(content),
            "recordCount": records.shape[0],
            # The immutable bytes are already resident here. Hashing them before
            # the write avoids reading every completed pack back from disk.
            "sha256": content_sha256,
            "payloadCrc32": f"{payload_crc32:08x}",
            "quantization": quantization,
        },
        errors,
    )


def decode_pack(content: bytes, quantization: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """Decode a baseline pack for validation/reference tests."""

    if len(content) < PACK_HEADER_SIZE:
        raise ValueError("GSTile pack is truncated")
    magic, version, header_size, record_size, flags, count, _node_hash, crc = _HEADER.unpack_from(content)
    if magic != PACK_MAGIC or version != GSTILE_VERSION:
        raise ValueError("Unsupported GSTile pack")
    if header_size != PACK_HEADER_SIZE or record_size != PACK_RECORD_SIZE or flags != 0:
        raise ValueError("Unsupported GSTile pack layout")
    payload = content[header_size:]
    if len(payload) != count * record_size:
        raise ValueError("GSTile pack length does not match its header")
    if zlib.crc32(payload) != crc:
        raise ValueError("GSTile payload CRC mismatch")
    packed = np.frombuffer(payload, dtype=PACK_DTYPE, count=count)

    def array(path: str, key: str) -> np.ndarray:
        return np.asarray(quantization[path][key], dtype=np.float32)

    position = _dequantize_u16(packed["position"], array("position", "min"), array("position", "max"))
    log_scale = _dequantize_u16(packed["log_scale"], array("logScale", "min"), array("logScale", "max"))
    opacity_min = float(quantization["opacityLogit"]["min"])
    opacity_max = float(quantization["opacityLogit"]["max"])
    opacity = _dequantize_u16(
        packed["opacity_logit"].reshape(-1, 1),
        np.asarray([opacity_min], dtype=np.float32),
        np.asarray([opacity_max], dtype=np.float32),
    )[:, 0]
    rotation = packed["rotation"].astype(np.float32) / 32767.0
    rotation /= np.maximum(np.linalg.norm(rotation, axis=1, keepdims=True), 1.0e-12)
    return {
        "position": position,
        "log_scale": log_scale,
        "rotation": rotation,
        "opacity_logit": opacity,
        "color_dc": packed["color_dc"].astype(np.float32) * np.asarray(quantization["colorDcScale"], dtype=np.float32),
        "color_sh": packed["color_sh"].astype(np.float32) * np.asarray(quantization["colorShScale"], dtype=np.float32),
        "opacity_sh": packed["opacity_sh"].astype(np.float32)
        * np.asarray(quantization["opacityShScale"], dtype=np.float32),
        "source_id": packed["source_id"].copy(),
    }


validate_manifest = validate_gstile_manifest


def canonical_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    return canonical_gstile_manifest_bytes(payload)
