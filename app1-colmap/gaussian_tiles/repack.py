"""Lossless bounded-memory repacking of immutable GSTile bundles."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from .format import (
    PACK_HEADER_SIZE,
    PACK_RECORD_SIZE,
    canonical_manifest_bytes,
    validate_manifest,
    validate_pack_content,
    write_bundle_aggregate_pack_atomic,
)

_PackKind = Literal["exact", "proxy"]


@dataclass(frozen=True)
class GsTileRepackResult:
    output: Path
    manifest_path: Path
    bundle_id: str
    source_pack_count: int
    pack_count: int
    representation_count: int
    pack_bytes: int


@dataclass
class _PendingRepresentation:
    payload: bytes
    tile: dict[str, Any]


def _validate_target_bytes(pack_target_bytes: int) -> None:
    if (
        isinstance(pack_target_bytes, bool)
        or not PACK_HEADER_SIZE + PACK_RECORD_SIZE
        <= pack_target_bytes
        <= 1024**3
    ):
        raise ValueError(
            "GSTile pack_target_bytes must be between 128 bytes and 1 GiB"
        )


def _emit(
    callback: Callable[[dict[str, Any]], None] | None,
    event: str,
    **details: Any,
) -> None:
    if callback is not None:
        callback({"event": event, **details})


class _AggregateRepackWriter:
    def __init__(
        self,
        bundle_tmp: Path,
        pack_target_bytes: int,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.bundle_tmp = bundle_tmp
        self.target_payload_bytes = pack_target_bytes - PACK_HEADER_SIZE
        self.callback = callback
        self.pending: dict[_PackKind, list[_PendingRepresentation]] = {
            "exact": [],
            "proxy": [],
        }
        self.pending_bytes: dict[_PackKind, int] = {"exact": 0, "proxy": 0}
        self.pending_depth: dict[_PackKind, int | None] = {"exact": None, "proxy": None}
        self.sequences: dict[_PackKind, int] = {"exact": 0, "proxy": 0}
        self.packs: list[dict[str, Any]] = []
        self.pack_bytes: dict[_PackKind, int] = {"exact": 0, "proxy": 0}

    def add(
        self,
        kind: _PackKind,
        depth: int,
        tile: dict[str, Any],
        payload: bytes,
    ) -> None:
        if self.pending[kind] and (
            self.pending_depth[kind] != depth
            or self.pending_bytes[kind] + len(payload) > self.target_payload_bytes
        ):
            self.flush(kind)
        self.pending_depth[kind] = depth
        self.pending[kind].append(_PendingRepresentation(payload, tile))
        self.pending_bytes[kind] += len(payload)
        if self.pending_bytes[kind] >= self.target_payload_bytes:
            self.flush(kind)

    def flush(self, kind: _PackKind) -> None:
        pending = self.pending[kind]
        if not pending:
            return
        sequence = self.sequences[kind]
        self.sequences[kind] += 1
        pack_id = f"aggregate-{kind}-{sequence:06d}"
        pack = write_bundle_aggregate_pack_atomic(
            self.bundle_tmp,
            [entry.payload for entry in pending],
            pack_id=pack_id,
        )
        offset = PACK_HEADER_SIZE
        for entry in pending:
            entry.tile["pack"] = pack_id
            entry.tile["byteOffset"] = offset
            entry.tile["sha256"] = pack["sha256"]
            offset += len(entry.payload)
        if offset != pack["byteLength"]:
            raise RuntimeError("GSTile repack payload accounting mismatch")
        self.packs.append(pack)
        self.pack_bytes[kind] += pack["byteLength"]
        _emit(
            self.callback,
            "pack_repacked",
            pack=pack_id,
            kind=kind,
            depth=self.pending_depth[kind],
            tileCount=len(pending),
            gaussianCount=pack["recordCount"],
            byteLength=pack["byteLength"],
        )
        self.pending[kind] = []
        self.pending_bytes[kind] = 0
        self.pending_depth[kind] = None

    def finish(self) -> None:
        self.flush("exact")
        self.flush("proxy")


def repack_gstile_bundle(
    source_bundle: str | Path,
    output_directory: str | Path,
    *,
    pack_target_bytes: int,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> GsTileRepackResult:
    """Repack canonical tile payloads without decoding or requantizing them."""

    _validate_target_bytes(pack_target_bytes)
    source = Path(source_bundle).resolve()
    output = Path(output_directory).resolve()
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    if output.exists():
        raise FileExistsError(f"GSTile bundles are immutable: {output}")
    manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    validate_manifest(manifest)
    updated = copy.deepcopy(manifest)
    representations: list[tuple[_PackKind, int, str, dict[str, Any]]] = []
    representation_count = 0
    for node in updated["nodes"]:
        if tile := node.get("tile"):
            representations.append(("exact", len(node["id"]) - 1, node["id"], tile))
            representation_count += 1
        if tile := node.get("lodTile"):
            representations.append(("proxy", len(node["id"]) - 1, node["id"], tile))
            representation_count += 1

    bundle_tmp = output.parent / f".{output.name}.partial"
    if bundle_tmp.exists():
        raise FileExistsError(f"Stale GSTile publication path exists: {bundle_tmp}")
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = _AggregateRepackWriter(
        bundle_tmp,
        pack_target_bytes,
        progress_callback,
    )
    try:
        (bundle_tmp / "packs").mkdir(parents=True)
        source_packs = {pack["id"]: pack for pack in updated["packs"]}
        verified_packs: set[str] = set()
        for kind, depth, _node_id, tile in sorted(representations):
            source_pack = source_packs[tile["pack"]]
            pack_path = source / source_pack["path"]
            start = tile["byteOffset"]
            end = start + tile["byteLength"]
            if source_pack["id"] not in verified_packs:
                content = pack_path.read_bytes()
                if len(content) != source_pack["byteLength"]:
                    raise ValueError(
                        f"GSTile source pack {source_pack['id']} length mismatch"
                    )
                if hashlib.sha256(content).hexdigest() != source_pack["sha256"]:
                    raise ValueError(
                        f"GSTile source pack {source_pack['id']} SHA-256 mismatch"
                    )
                record_count, payload_crc32 = validate_pack_content(content)
                if (
                    record_count != source_pack["recordCount"]
                    or f"{payload_crc32:08x}" != source_pack["payloadCrc32"]
                ):
                    raise ValueError(
                        f"GSTile source pack {source_pack['id']} header mismatch"
                    )
                payload = content[start:end]
                verified_packs.add(source_pack["id"])
                _emit(
                    progress_callback,
                    "source_pack_read",
                    pack=source_pack["id"],
                    byteLength=len(content),
                )
            else:
                with pack_path.open("rb") as handle:
                    handle.seek(start)
                    payload = handle.read(tile["byteLength"])
                if len(payload) != tile["byteLength"]:
                    raise ValueError(
                        f"GSTile source pack {source_pack['id']} payload is incomplete"
                    )
            writer.add(kind, depth, tile, payload)
        writer.finish()
        if len(verified_packs) != len(source_packs):
            raise ValueError("GSTile source bundle contains an unreferenced pack")
        updated["packs"] = writer.packs
        statistics = updated["statistics"]
        pack_bytes = sum(pack["byteLength"] for pack in writer.packs)
        statistics.update(
            {
                "packCount": len(writer.packs),
                "representationCount": representation_count,
                "packTargetBytes": pack_target_bytes,
                "packGrouping": "depth-spatial-v1",
                "packBytes": pack_bytes,
                "bytesPerGaussian": pack_bytes / updated["source"]["gaussianCount"],
            }
        )
        if "exactPackBytes" in statistics:
            statistics["exactPackBytes"] = writer.pack_bytes["exact"]
        if "proxyPackBytes" in statistics:
            statistics["proxyPackBytes"] = writer.pack_bytes["proxy"]
        validate_manifest(updated)
        identity_payload = copy.deepcopy(updated)
        identity_payload["bundleId"] = None
        bundle_hash = hashlib.sha256(
            json.dumps(
                identity_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        updated["bundleId"] = f"sha256:{bundle_hash}"
        manifest_bytes = canonical_manifest_bytes(updated)
        with (bundle_tmp / "manifest.json").open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(bundle_tmp, output)
        _emit(
            progress_callback,
            "published",
            bundleId=updated["bundleId"],
            sourcePackCount=len(manifest["packs"]),
            packCount=len(writer.packs),
            representationCount=representation_count,
            packBytes=pack_bytes,
        )
        return GsTileRepackResult(
            output=output,
            manifest_path=output / "manifest.json",
            bundle_id=updated["bundleId"],
            source_pack_count=len(manifest["packs"]),
            pack_count=len(writer.packs),
            representation_count=representation_count,
            pack_bytes=pack_bytes,
        )
    except Exception:
        shutil.rmtree(bundle_tmp, ignore_errors=True)
        raise
