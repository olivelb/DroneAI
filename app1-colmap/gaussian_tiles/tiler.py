"""Out-of-core spatial partitioner for immutable GSTile v1 bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from gaussian_ortho.ply_stream import BinaryPlyLayout, read_binary_ply_layout

from .format import (
    GSTILE_PROFILE,
    GSTILE_SCHEMA,
    GSTILE_VERSION,
    canonical_manifest_bytes,
    validate_manifest,
    write_pack_atomic,
)


@dataclass(frozen=True)
class GsTileBuildOptions:
    leaf_size: int = 65_536
    chunk_records: int = 131_072
    maximum_depth: int = 48
    temporary_root: Path | None = None
    coordinate_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    crs: str | None = None
    cancellation_check: Callable[[], None] | None = None
    progress_callback: Callable[[dict[str, Any]], None] | None = None

    def validate(self) -> None:
        if not 1_024 <= self.leaf_size <= 1_048_576:
            raise ValueError("GSTile leaf_size must be between 1,024 and 1,048,576")
        if not 1_024 <= self.chunk_records <= 1_048_576:
            raise ValueError("GSTile chunk_records must be between 1,024 and 1,048,576")
        if not 1 <= self.maximum_depth <= 64:
            raise ValueError("GSTile maximum_depth must be between 1 and 64")
        if len(self.coordinate_origin) != 3 or not all(
            np.isfinite(value) for value in self.coordinate_origin
        ):
            raise ValueError("GSTile coordinate origin must contain three finite values")


@dataclass(frozen=True)
class GsTileBuildResult:
    output: Path
    manifest_path: Path
    bundle_id: str
    gaussian_count: int
    leaf_count: int
    pack_bytes: int
    source_bytes: int
    maximum_errors: dict[str, float]


@dataclass(frozen=True)
class _WorkFile:
    path: Path
    count: int
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]


def _emit_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    event: str,
    **details: Any,
) -> None:
    if callback is not None:
        callback({"event": event, **details})


def _work_dtype(layout: BinaryPlyLayout) -> np.dtype:
    names = list(layout.dtype.names or ()) + ["source_id"]
    formats = [layout.dtype.fields[name][0] for name in layout.dtype.names or ()] + ["<u8"]
    offsets = [layout.dtype.fields[name][1] for name in layout.dtype.names or ()] + [layout.dtype.itemsize]
    return np.dtype(
        {
            "names": names,
            "formats": formats,
            "offsets": offsets,
            "itemsize": layout.dtype.itemsize + 8,
        }
    )


def _chunks(
    path: Path,
    dtype: np.dtype,
    count: int,
    cancellation_check: Callable[[], None] | None = None,
) -> Iterator[np.ndarray]:
    with path.open("rb") as handle:
        while True:
            if cancellation_check is not None:
                cancellation_check()
            records = np.fromfile(handle, dtype=dtype, count=count)
            if records.size == 0:
                return
            yield records


def _bounds(records: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xyz = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float64, copy=False
    )
    if not np.all(np.isfinite(xyz)):
        raise ValueError("PLY contains non-finite Gaussian positions")
    return xyz.min(axis=0), xyz.max(axis=0)


def _create_root_work_file(
    source: Path,
    layout: BinaryPlyLayout,
    target: Path,
    chunk_records: int,
    cancellation_check: Callable[[], None] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[_WorkFile, str]:
    dtype = _work_dtype(layout)
    minimum = np.full(3, np.inf, dtype=np.float64)
    maximum = np.full(3, -np.inf, dtype=np.float64)
    source_id = 0
    digest = hashlib.sha256()
    with source.open("rb") as input_handle, target.open("xb") as output_handle:
        header = input_handle.read(layout.header_size)
        if len(header) != layout.header_size:
            raise ValueError("PLY header ended before its declared size")
        digest.update(header)
        remaining = layout.vertex_count
        while remaining:
            if cancellation_check is not None:
                cancellation_check()
            count = min(chunk_records, remaining)
            records = np.fromfile(input_handle, dtype=layout.dtype, count=count)
            if records.shape[0] != count:
                raise ValueError("PLY payload ended before its declared vertex count")
            digest.update(memoryview(records).cast("B"))
            working = np.empty(count, dtype=dtype)
            for name in layout.dtype.names or ():
                working[name] = records[name]
            working["source_id"] = np.arange(source_id, source_id + count, dtype="<u8")
            source_id += count
            chunk_min, chunk_max = _bounds(working)
            minimum = np.minimum(minimum, chunk_min)
            maximum = np.maximum(maximum, chunk_max)
            working.tofile(output_handle)
            remaining -= count
            _emit_progress(
                progress_callback,
                "source_copy",
                processed=source_id,
                total=layout.vertex_count,
            )
        for block in iter(lambda: input_handle.read(1024 * 1024), b""):
            digest.update(block)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    return (
        _WorkFile(
            target,
            layout.vertex_count,
            tuple(float(value) for value in minimum),
            tuple(float(value) for value in maximum),
        ),
        digest.hexdigest(),
    )


def _split_work_file(
    item: _WorkFile,
    *,
    node_id: str,
    dtype: np.dtype,
    chunk_records: int,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[_WorkFile, _WorkFile]:
    extent = np.asarray(item.bounds_max) - np.asarray(item.bounds_min)
    axis = int(np.argmax(extent))
    midpoint = (item.bounds_min[axis] + item.bounds_max[axis]) / 2.0
    left_path = item.path.with_name(f"{node_id}0.work")
    right_path = item.path.with_name(f"{node_id}1.work")
    left_count = right_count = 0
    left_minimum = np.full(3, np.inf, dtype=np.float64)
    left_maximum = np.full(3, -np.inf, dtype=np.float64)
    right_minimum = np.full(3, np.inf, dtype=np.float64)
    right_maximum = np.full(3, -np.inf, dtype=np.float64)

    def record_partition_bounds(records: np.ndarray, *, left: bool) -> None:
        nonlocal left_minimum, left_maximum, right_minimum, right_maximum
        if records.size == 0:
            return
        chunk_minimum, chunk_maximum = _bounds(records)
        if left:
            left_minimum = np.minimum(left_minimum, chunk_minimum)
            left_maximum = np.maximum(left_maximum, chunk_maximum)
        else:
            right_minimum = np.minimum(right_minimum, chunk_minimum)
            right_maximum = np.maximum(right_maximum, chunk_maximum)

    with left_path.open("xb") as left, right_path.open("xb") as right:
        for records in _chunks(item.path, dtype, chunk_records, cancellation_check):
            mask = records[("x", "y", "z")[axis]] < midpoint
            left_records, right_records = records[mask], records[~mask]
            left_records.tofile(left)
            right_records.tofile(right)
            record_partition_bounds(left_records, left=True)
            record_partition_bounds(right_records, left=False)
            left_count += left_records.shape[0]
            right_count += right_records.shape[0]

    if left_count == 0 or right_count == 0:
        left_path.unlink(missing_ok=True)
        right_path.unlink(missing_ok=True)
        left_count = item.count // 2
        right_count = item.count - left_count
        left_minimum.fill(np.inf)
        left_maximum.fill(-np.inf)
        right_minimum.fill(np.inf)
        right_maximum.fill(-np.inf)
        written = 0
        with left_path.open("xb") as left, right_path.open("xb") as right:
            for records in _chunks(item.path, dtype, chunk_records, cancellation_check):
                left_remaining = max(0, left_count - written)
                left_part = records[:left_remaining]
                right_part = records[left_remaining:]
                left_part.tofile(left)
                right_part.tofile(right)
                record_partition_bounds(left_part, left=True)
                record_partition_bounds(right_part, left=False)
                written += left_part.shape[0]

    if left_count + right_count != item.count:
        raise RuntimeError("GSTile partition count changed during split")
    item.path.unlink()
    return (
        _WorkFile(
            left_path,
            left_count,
            tuple(left_minimum.tolist()),
            tuple(left_maximum.tolist()),
        ),
        _WorkFile(
            right_path,
            right_count,
            tuple(right_minimum.tolist()),
            tuple(right_maximum.tolist()),
        ),
    )


def _source_degrees(layout: BinaryPlyLayout) -> tuple[int, int]:
    color = len([name for name in layout.property_names if name.startswith("f_rest_")])
    opacity = len([name for name in layout.property_names if name.startswith("opacity_sh_")])
    color_coefficients = color // 3
    color_degree = int(round(np.sqrt(color_coefficients + 1) - 1))
    opacity_degree = int(round(np.sqrt(opacity + 1) - 1))
    return color_degree, opacity_degree


def build_gstile_bundle(
    source_ply: str | Path,
    output_directory: str | Path,
    *,
    options: GsTileBuildOptions | None = None,
) -> GsTileBuildResult:
    """Build a deterministic leaf-streaming bundle without loading the PLY whole."""

    options = options or GsTileBuildOptions()
    options.validate()
    if options.cancellation_check is not None:
        options.cancellation_check()
    source = Path(source_ply).resolve()
    output = Path(output_directory).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists():
        raise FileExistsError(f"GSTile bundles are immutable: {output}")
    layout = read_binary_ply_layout(source)
    if layout.vertex_count < 1:
        raise ValueError("Cannot tile an empty Gaussian PLY")
    color_degree, opacity_degree = _source_degrees(layout)
    if color_degree > 3 or opacity_degree > 3:
        raise ValueError("GSTile v1 supports SH degree at most 3")

    output_parent = output.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = (options.temporary_root or output_parent).resolve()
    temporary_parent.mkdir(parents=True, exist_ok=True)
    source_payload_bytes = layout.vertex_count * layout.dtype.itemsize
    estimated_temporary = layout.vertex_count * (layout.dtype.itemsize + 8) * 2
    estimated_output = layout.vertex_count * 96 + ((layout.vertex_count + options.leaf_size - 1) // options.leaf_size) * 32
    reserve = 1024**3
    shared_filesystem = os.stat(temporary_parent).st_dev == os.stat(output_parent).st_dev
    temporary_required = estimated_temporary + reserve
    output_required = estimated_output + reserve
    if shared_filesystem:
        temporary_required += estimated_output
    _emit_progress(
        options.progress_callback,
        "preflight",
        gaussianCount=layout.vertex_count,
        sourceBytes=source_payload_bytes,
        estimatedTemporaryBytes=temporary_required,
        estimatedOutputBytes=output_required,
        sharedFilesystem=shared_filesystem,
    )
    if shutil.disk_usage(temporary_parent).free < temporary_required:
        raise RuntimeError(
            "Insufficient temporary disk for GSTile atomic build: "
            f"need about {temporary_required / 1024**3:.1f} GiB"
        )
    if not shared_filesystem and shutil.disk_usage(output_parent).free < output_required:
        raise RuntimeError(
            "Insufficient output disk for GSTile atomic build: "
            f"need about {output_required / 1024**3:.1f} GiB"
        )

    bundle_tmp = output_parent / f".{output.name}.partial"
    if bundle_tmp.exists():
        raise FileExistsError(f"Stale GSTile publication path exists: {bundle_tmp}")
    build_root = Path(tempfile.mkdtemp(prefix="gstile-build-", dir=temporary_parent))
    try:
        bundle_tmp.mkdir()
        (bundle_tmp / "packs").mkdir()
        work_dtype = _work_dtype(layout)
        root_work, source_sha256 = _create_root_work_file(
            source,
            layout,
            build_root / "r.work",
            options.chunk_records,
            options.cancellation_check,
            options.progress_callback,
        )
        _emit_progress(
            options.progress_callback,
            "source_ready",
            gaussianCount=root_work.count,
            sha256=source_sha256,
        )
        nodes: list[dict[str, Any]] = []
        packs: list[dict[str, Any]] = []
        maximum_errors: dict[str, float] = {}

        def visit(item: _WorkFile, node_id: str, depth: int) -> None:
            if options.cancellation_check is not None:
                options.cancellation_check()
            node: dict[str, Any] = {
                "id": node_id,
                "bounds": {"min": list(item.bounds_min), "max": list(item.bounds_max)},
                "gaussianCount": item.count,
            }
            nodes.append(node)
            if item.count <= options.leaf_size:
                records = np.fromfile(item.path, dtype=work_dtype, count=item.count)
                if records.shape[0] != item.count:
                    raise RuntimeError("GSTile leaf payload is incomplete")
                ply_records = np.empty(item.count, dtype=layout.dtype)
                for name in layout.dtype.names or ():
                    ply_records[name] = records[name]
                pack_id = node_id
                relative = Path("packs") / f"{pack_id}.gst"
                pack, errors = write_pack_atomic(
                    bundle_tmp / relative,
                    ply_records,
                    records["source_id"],
                    node_id=node_id,
                )
                pack["id"] = pack_id
                pack["path"] = relative.as_posix()
                pack["byteOffset"] = 32
                packs.append(pack)
                node["tile"] = {
                    "pack": pack_id,
                    "byteOffset": 32,
                    "byteLength": pack["byteLength"] - 32,
                    "recordCount": item.count,
                    "sha256": pack["sha256"],
                    "quantization": pack.pop("quantization"),
                }
                for key, value in errors.items():
                    maximum_errors[key] = max(maximum_errors.get(key, 0.0), value)
                item.path.unlink()
                _emit_progress(
                    options.progress_callback,
                    "leaf_written",
                    node=node_id,
                    depth=depth,
                    gaussianCount=item.count,
                    leafCount=len(packs),
                )
                return
            if depth >= options.maximum_depth:
                raise RuntimeError("GSTile partition exceeded maximum depth")
            _emit_progress(
                options.progress_callback,
                "partition_split",
                node=node_id,
                depth=depth,
                gaussianCount=item.count,
            )
            left, right = _split_work_file(
                item,
                node_id=node_id,
                dtype=work_dtype,
                chunk_records=options.chunk_records,
                cancellation_check=options.cancellation_check,
            )
            node["children"] = [node_id + "0", node_id + "1"]
            visit(left, node_id + "0", depth + 1)
            visit(right, node_id + "1", depth + 1)

        visit(root_work, "r", 0)
        if options.cancellation_check is not None:
            options.cancellation_check()
        manifest: dict[str, Any] = {
            "schema": GSTILE_SCHEMA,
            "version": GSTILE_VERSION,
            "profile": GSTILE_PROFILE,
            "bundleId": "sha256:" + "0" * 64,
            "source": {
                "sha256": source_sha256,
                "gaussianCount": layout.vertex_count,
                "colorShDegree": color_degree,
                "opacityShDegree": opacity_degree,
                "recordBytes": layout.dtype.itemsize,
            },
            "coordinateFrame": {
                "kind": "projected" if options.crs else "local",
                "origin": list(options.coordinate_origin),
                "crs": options.crs,
            },
            "root": "r",
            "nodes": nodes,
            "packs": packs,
            "statistics": {
                "leafCount": len(packs),
                "packBytes": sum(pack["byteLength"] for pack in packs),
                "bytesPerGaussian": sum(pack["byteLength"] for pack in packs) / layout.vertex_count,
                "maximumQuantizationError": maximum_errors,
                "lod": "leaf-only",
            },
        }
        validate_manifest(manifest)
        identity_payload = json.loads(json.dumps(manifest))
        identity_payload["bundleId"] = None
        bundle_hash = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest["bundleId"] = f"sha256:{bundle_hash}"
        manifest_bytes = canonical_manifest_bytes(manifest)
        with (bundle_tmp / "manifest.json").open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(bundle_tmp, output)
        _emit_progress(
            options.progress_callback,
            "published",
            bundleId=manifest["bundleId"],
            gaussianCount=layout.vertex_count,
            leafCount=len(packs),
            packBytes=manifest["statistics"]["packBytes"],
        )
        return GsTileBuildResult(
            output=output,
            manifest_path=output / "manifest.json",
            bundle_id=manifest["bundleId"],
            gaussian_count=layout.vertex_count,
            leaf_count=len(packs),
            pack_bytes=manifest["statistics"]["packBytes"],
            source_bytes=source_payload_bytes,
            maximum_errors=maximum_errors,
        )
    except Exception:
        shutil.rmtree(bundle_tmp, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
