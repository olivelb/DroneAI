"""Pure GSTile v1 manifest contract shared by producers and HTTP readers."""

from __future__ import annotations

import json
import math
import re
from pathlib import PurePosixPath
from typing import Any, Mapping, cast

GSTILE_SCHEMA = "droneai-gstile"
GSTILE_VERSION = 1
GSTILE_PROFILE = "dronegs-sh3-opacity-sh3-q96"
GSTILE_PACK_HEADER_SIZE = 32
GSTILE_PACK_RECORD_SIZE = 96


def safe_bundle_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError(f"{field} escapes the GSTile bundle")
    return value


def validate_gstile_manifest(payload: Mapping[str, Any]) -> None:
    """Fail closed on incompatible, inconsistent or dangerous manifests."""

    if payload.get("schema") != GSTILE_SCHEMA or payload.get("version") != GSTILE_VERSION:
        raise ValueError("Unsupported GSTile manifest")
    if payload.get("profile") != GSTILE_PROFILE:
        raise ValueError("Unsupported GSTile profile")
    bundle_id = payload.get("bundleId")
    if not isinstance(bundle_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", bundle_id):
        raise ValueError("GSTile bundle identity is invalid")
    nodes = payload.get("nodes")
    packs = payload.get("packs")
    source = payload.get("source")
    if (
        not isinstance(nodes, list)
        or not isinstance(packs, list)
        or not nodes
        or not packs
        or not isinstance(source, Mapping)
    ):
        raise ValueError("GSTile manifest must contain source, nodes and packs")
    source_count = source.get("gaussianCount")
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count < 1:
        raise ValueError("GSTile source Gaussian count is invalid")
    if not isinstance(source.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", cast(str, source.get("sha256"))):
        raise ValueError("GSTile source SHA-256 is invalid")

    pack_ids: set[str] = set()
    pack_counts: dict[str, int] = {}
    for index, raw_pack in enumerate(packs):
        if not isinstance(raw_pack, Mapping):
            raise ValueError("GSTile pack entry must be an object")
        pack = cast(Mapping[str, Any], raw_pack)
        pack_id = pack.get("id")
        if not isinstance(pack_id, str) or not pack_id or pack_id in pack_ids:
            raise ValueError("GSTile pack ids must be unique strings")
        pack_ids.add(pack_id)
        safe_bundle_path(pack.get("path"), f"packs[{index}].path")
        byte_length = pack.get("byteLength")
        record_count = pack.get("recordCount")
        if (
            isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or isinstance(record_count, bool)
            or not isinstance(record_count, int)
            or record_count < 1
            or byte_length != GSTILE_PACK_HEADER_SIZE + record_count * GSTILE_PACK_RECORD_SIZE
        ):
            raise ValueError("GSTile pack length or record count is invalid")
        if pack.get("byteOffset") != GSTILE_PACK_HEADER_SIZE:
            raise ValueError("GSTile pack payload offset is invalid")
        digest = pack.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("GSTile pack SHA-256 is invalid")
        pack_counts[pack_id] = record_count

    node_ids: set[str] = set()
    tile_count = 0
    tile_records = 0
    children_by_node: dict[str, tuple[str, ...]] = {}
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping) or not isinstance(raw_node.get("id"), str):
            raise ValueError("GSTile node entry is invalid")
        node = cast(Mapping[str, Any], raw_node)
        node_id = cast(str, node["id"])
        if not node_id or node_id in node_ids:
            raise ValueError("GSTile node ids must be unique")
        node_ids.add(node_id)
        children, tile = node.get("children"), node.get("tile")
        if (children is None) == (tile is None):
            raise ValueError("GSTile node must contain exactly children or tile")
        bounds = node.get("bounds")
        if not isinstance(bounds, Mapping):
            raise ValueError("GSTile node bounds are missing")
        minimum, maximum = bounds.get("min"), bounds.get("max")
        if not (
            isinstance(minimum, list)
            and isinstance(maximum, list)
            and len(minimum) == len(maximum) == 3
            and all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in minimum + maximum
            )
            and all(left <= right for left, right in zip(minimum, maximum, strict=True))
        ):
            raise ValueError("GSTile node bounds are invalid")
        gaussian_count = node.get("gaussianCount")
        if isinstance(gaussian_count, bool) or not isinstance(gaussian_count, int) or gaussian_count < 1:
            raise ValueError("GSTile node Gaussian count is invalid")
        if children is not None:
            if (
                not isinstance(children, list)
                or len(children) != 2
                or not all(isinstance(child, str) and child for child in children)
            ):
                raise ValueError("GSTile children must contain two node ids")
            children_by_node[node_id] = tuple(cast(list[str], children))
        else:
            if not isinstance(tile, Mapping) or tile.get("pack") not in pack_ids:
                raise ValueError("GSTile tile references an unknown pack")
            pack_id = cast(str, tile["pack"])
            record_count = tile.get("recordCount")
            if record_count != pack_counts[pack_id] or gaussian_count != record_count:
                raise ValueError("GSTile leaf counts do not match their pack")
            tile_count += 1
            tile_records += cast(int, record_count)

    root = payload.get("root")
    if root not in node_ids:
        raise ValueError("GSTile root node is unknown")
    for children in children_by_node.values():
        if any(child not in node_ids for child in children):
            raise ValueError("GSTile node references an unknown child")
    if tile_count != len(packs) or tile_records != source_count:
        raise ValueError("GSTile leaf population does not match the source")

    reachable: set[str] = set()
    pending = [cast(str, root)]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            raise ValueError("GSTile hierarchy contains a cycle or shared child")
        reachable.add(node_id)
        pending.extend(children_by_node.get(node_id, ()))
    if reachable != node_ids:
        raise ValueError("GSTile hierarchy contains unreachable nodes")


def canonical_gstile_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    validate_gstile_manifest(payload)
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
