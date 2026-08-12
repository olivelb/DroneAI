"""Bounded result loading, styling and publication for AI analysis runs."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

from geoalchemy2.elements import WKTElement

from shared import storage
from shared.database import MapFeature
from shared.geospatial_assets import detections_feature_collection
from shared.tile_results import validate_tile_result_bytes
from shared.tenancy import MissionObjectNamespace
from shared.validation import safe_child_path

DetectionRecord = dict[str, Any]
JsonObject = dict[str, Any]
RunDescriptor = dict[str, Any]


class TileResultReference(TypedDict):
    key: str
    sha256: str
    size_bytes: int
    tile_index: int
    attempt: int
    detection_count: int


def styled_collection(
    detections: Iterable[DetectionRecord],
    *,
    vol_id: str,
    run: Any,
    tile_index: int | None = None,
) -> JsonObject:
    records: list[DetectionRecord] = []
    for detection in detections:
        record = dict(detection)
        if tile_index is not None:
            record["tile_index"] = tile_index
        records.append(record)
    metadata = run.tiling_metadata or {}
    collection = cast(
        JsonObject,
        detections_feature_collection(
            records,
            geotransform=metadata.get("transform"),
            source_crs=metadata.get("crs"),
            vol_id=vol_id,
        ),
    )
    for feature in collection["features"]:
        feature["properties"].update(
            {
                "source": "ai",
                "run_id": run.run_id,
                "name": run.name,
                "description": run.description or "",
                "color": run.color,
                "tags": run.tags or [],
            }
        )
    collection["properties"].update(
        {
            "run_id": run.run_id,
            "name": run.name,
            "color": run.color,
            "model_manifest": run.model_manifest,
        }
    )
    return collection


def workspace(vol_id: str, run_id: str) -> Path:
    mission_workspace = safe_child_path(
        "/tmp/processing",
        vol_id,
        field_name="vol_id",
    )
    return cast(
        Path,
        safe_child_path(
            mission_workspace,
            run_id,
            field_name="analysis_run_id",
        ),
    )


def write_verified_json(
    payload: JsonObject,
    key: str,
    local_path: str | Path,
) -> None:
    path = Path(local_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    storage.upload_verified_file(path, key)


def feature_wkt(geometry: JsonObject) -> WKTElement:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        point = cast(list[float], coordinates)
        return WKTElement(f"POINT({point[0]} {point[1]})", srid=4326)
    if geometry_type == "Polygon":
        polygon = cast(list[list[list[float]]], coordinates)
        rings = [
            "(" + ", ".join(f"{point[0]} {point[1]}" for point in ring) + ")"
            for ring in polygon
        ]
        return WKTElement(f"POLYGON({', '.join(rings)})", srid=4326)
    raise ValueError(f"Unsupported AI geometry: {geometry_type}")


def run_descriptor(
    run: Any,
    namespace: MissionObjectNamespace | None = None,
) -> RunDescriptor:
    descriptor: RunDescriptor = {
        "id": run.id,
        "run_id": run.run_id,
        "vol_id": run.vol_id,
        "persist_results": bool(run.persist_results),
        "name": run.name,
        "description": run.description or "",
        "color": run.color,
        "tags": run.tags or [],
        "tiling_metadata": run.tiling_metadata or {},
        "model_manifest": run.model_manifest,
    }
    if namespace is not None:
        descriptor["organization_id"] = namespace.organization_id
        descriptor["workspace_prefix"] = namespace.root
    return descriptor


def descriptor_proxy(descriptor: RunDescriptor) -> Any:
    return type(
        "AnalysisDescriptor",
        (),
        {
            "run_id": descriptor["run_id"],
            "name": descriptor["name"],
            "description": descriptor["description"],
            "color": descriptor["color"],
            "tags": descriptor["tags"],
            "tiling_metadata": descriptor["tiling_metadata"],
            "model_manifest": descriptor["model_manifest"],
        },
    )()


def read_tile_payload(
    tile_key: str,
    total_payload_bytes: int,
    expected_size: int,
    *,
    maximum_tile_result_bytes: int,
    maximum_aggregate_result_bytes: int,
) -> tuple[bytes, int]:
    stream, content_length, _ = storage.get_object_stream(tile_key)
    content_length = int(content_length or 0)
    if content_length != expected_size:
        stream.close()
        raise RuntimeError(
            "AI tile result size differs from its reference: "
            f"{content_length}/{expected_size} bytes for {tile_key}"
        )
    if content_length > maximum_tile_result_bytes:
        stream.close()
        raise RuntimeError(
            f"AI tile result exceeds the {maximum_tile_result_bytes}-byte limit: "
            f"{tile_key}"
        )
    if total_payload_bytes + content_length > maximum_aggregate_result_bytes:
        stream.close()
        raise RuntimeError(
            "AI analysis exceeds the aggregate result size limit "
            f"({maximum_aggregate_result_bytes} bytes)"
        )
    try:
        raw_payload = stream.read(maximum_tile_result_bytes + 1)
    finally:
        stream.close()
    if len(raw_payload) > maximum_tile_result_bytes:
        raise RuntimeError(
            f"AI tile result exceeds the {maximum_tile_result_bytes}-byte limit: "
            f"{tile_key}"
        )
    return cast(bytes, raw_payload), content_length


def load_tile_payloads(
    references: Iterable[TileResultReference],
    descriptor: RunDescriptor,
    *,
    maximum_tile_result_bytes: int,
    maximum_aggregate_result_bytes: int,
    maximum_raw_detections: int,
    renew_finalization: Callable[[str], None] | None = None,
) -> list[DetectionRecord]:
    detections: list[DetectionRecord] = []
    total_payload_bytes = 0
    model_manifest = cast(JsonObject, descriptor["model_manifest"])
    for reference in references:
        tile_key = reference["key"]
        raw_payload, _ = read_tile_payload(
            tile_key,
            total_payload_bytes,
            reference["size_bytes"],
            maximum_tile_result_bytes=maximum_tile_result_bytes,
            maximum_aggregate_result_bytes=maximum_aggregate_result_bytes,
        )
        total_payload_bytes += len(raw_payload)
        artifact = validate_tile_result_bytes(
            raw_payload,
            expected_sha256=reference["sha256"],
            expected_size=reference["size_bytes"],
            vol_id=cast(str, descriptor["vol_id"]),
            analysis_run_id=cast(str, descriptor["run_id"]),
            tile_index=reference["tile_index"],
            attempt=reference["attempt"],
            detection_count=reference["detection_count"],
            model_manifest=model_manifest,
        )
        tile_detections = artifact.raw_detections
        if len(detections) + len(tile_detections) > maximum_raw_detections:
            raise RuntimeError(
                "AI analysis exceeds the raw detection safety limit "
                f"({maximum_raw_detections})"
            )
        detections.extend(tile_detections)
        if renew_finalization is not None:
            renew_finalization(cast(str, descriptor["run_id"]))
    return detections


def replace_persisted_features(
    session: Any,
    run: Any,
    collection: JsonObject,
) -> None:
    now = datetime.now(UTC)
    session.query(MapFeature).filter(
        MapFeature.analysis_run_id == run.id,
        MapFeature.deleted_at.is_(None),
    ).update(
        {
            MapFeature.deleted_at: now,
            MapFeature.deleted_by: "system:analysis-workflow",
            MapFeature.deletion_reason: "superseded by analysis retry",
            MapFeature.updated_at: now,
            MapFeature.version: MapFeature.version + 1,
        },
        synchronize_session=False,
    )
    for feature in collection["features"]:
        properties = feature.get("properties") or {}
        session.add(
            MapFeature(
                mission_id=run.mission_id,
                analysis_run_id=run.id,
                vol_id=run.vol_id,
                source="ai",
                geometry=feature_wkt(feature["geometry"]),
                name=run.name,
                description=run.description,
                color=run.color,
                tags=run.tags or [],
                properties={
                    "backend": run.backend,
                    "model_variant": run.model_variant,
                },
                class_name=properties.get("class_name"),
                confidence=properties.get("confidence"),
                tile_index=properties.get("tile_index"),
                created_by=run.created_by,
            )
        )
