"""Ground-control persistence and serialization helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from fastapi import HTTPException, status
from pyproj import Transformer
from sqlalchemy import func

from shared import storage
from shared.database import GcpObservation, GcpPoint, GcpSet
from shared.gcp_candidates import PositionedImage, parse_positioned_images
from shared.gcp_import import ImportedGcpSet

from .map_support import JsonObject, RouteSession

MAX_GCP_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_POSITION_FILE_BYTES = 10 * 1024 * 1024
GCP_FILE_SUFFIXES = {".csv", ".tsv", ".txt", ".xyz", ".geojson", ".json"}


@dataclass(frozen=True)
class MissionImagePositions:
    projected_crs: str
    images: tuple[PositionedImage, ...]


def safe_upload_name(filename: str | None) -> str:
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    if not name or PurePosixPath(name).suffix.lower() not in GCP_FILE_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported GCP file; use CSV, TSV, TXT, XYZ, GeoJSON or ODM gcp_list.txt",
        )
    return name


def source_checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_bounded_object(key: str, max_bytes: int) -> bytes:
    stream, size, _content_type = storage.get_object_stream(key)
    try:
        if size > max_bytes:
            raise ValueError(f"object {key} exceeds {max_bytes} bytes")
        payload = cast(bytes, stream.read(max_bytes + 1))
    finally:
        stream.close()
    if len(payload) > max_bytes:
        raise ValueError(f"object {key} exceeds {max_bytes} bytes")
    return payload


def load_mission_image_positions(vol_id: str) -> MissionImagePositions | None:
    """Load EXIF-derived image positions published by reconstruction preflight."""

    position_key = f"missions/{vol_id}/geo_data.txt"
    crs_key = f"{position_key}.crs"
    if not storage.file_exists(position_key) or not storage.file_exists(crs_key):
        return None
    projected_crs = read_bounded_object(crs_key, 4096).decode("utf-8-sig").strip()
    if not projected_crs:
        raise ValueError("published image position CRS is empty")
    images = parse_positioned_images(
        read_bounded_object(position_key, MAX_POSITION_FILE_BYTES),
        projected_crs,
    )
    return MissionImagePositions(projected_crs=projected_crs, images=images)


def persist_imported_set(
    session: RouteSession,
    *,
    mission_id: int,
    vol_id: str,
    name: str,
    filename: str,
    checksum: str,
    imported: ImportedGcpSet,
    actor_subject: str,
) -> tuple[GcpSet, dict[str, GcpPoint]]:
    gcp_set = GcpSet(
        mission_id=mission_id,
        vol_id=vol_id,
        name=name,
        source_filename=filename,
        source_format=imported.source_format,
        source_crs=imported.source_crs,
        source_sha256=checksum,
        created_by=actor_subject,
    )
    session.add(gcp_set)
    session.flush()
    points: dict[str, GcpPoint] = {}
    for imported_point in imported.points:
        point = GcpPoint(
            gcp_set_id=gcp_set.id,
            mission_id=mission_id,
            external_id=imported_point.external_id,
            geometry=func.ST_SetSRID(
                func.ST_MakePoint(imported_point.longitude, imported_point.latitude),
                4326,
            ),
            source_x=imported_point.source_x,
            source_y=imported_point.source_y,
            source_z=imported_point.source_z,
            altitude_m=imported_point.altitude_m,
            role=imported_point.role,
            horizontal_accuracy_m=imported_point.horizontal_accuracy_m,
            vertical_accuracy_m=imported_point.vertical_accuracy_m,
            image_accuracy_px=imported_point.image_accuracy_px,
            properties=imported_point.properties,
        )
        session.add(point)
        session.flush()
        points[imported_point.external_id] = point
    return gcp_set, points


def point_longitude_latitude(session: RouteSession, point: GcpPoint) -> tuple[float, float]:
    payload = session.scalar(func.ST_AsGeoJSON(point.geometry))
    if not isinstance(payload, str):
        raise RuntimeError("GCP point geometry could not be serialized")
    coordinates = json.loads(payload).get("coordinates", [])
    if len(coordinates) < 2:
        raise RuntimeError("GCP point geometry is invalid")
    return float(coordinates[0]), float(coordinates[1])


def observation_json(observation: GcpObservation) -> JsonObject:
    return {
        "observation_id": observation.observation_id,
        "image_name": observation.image_name,
        "image_s3_key": observation.image_s3_key,
        "status": observation.status,
        "pixel_x": observation.pixel_x,
        "pixel_y": observation.pixel_y,
        "candidate_distance_m": observation.candidate_distance_m,
        "image_longitude": observation.image_longitude,
        "image_latitude": observation.image_latitude,
        "version": observation.version,
        "updated_at": observation.updated_at.isoformat(),
    }


def point_json(session: RouteSession, point: GcpPoint) -> JsonObject:
    longitude, latitude = point_longitude_latitude(session, point)
    observations = cast(list[GcpObservation], point.observations)
    return {
        "type": "Feature",
        "id": point.point_id,
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": {
            "point_id": point.point_id,
            "external_id": point.external_id,
            "altitude_m": point.altitude_m,
            "source_coordinates": [point.source_x, point.source_y, point.source_z],
            "role": point.role,
            "horizontal_accuracy_m": point.horizontal_accuracy_m,
            "vertical_accuracy_m": point.vertical_accuracy_m,
            "image_accuracy_px": point.image_accuracy_px,
            "observation_summary": {
                state: sum(1 for item in observations if item.status == state)
                for state in ("candidate", "marked", "skipped")
            },
            "observations": [observation_json(item) for item in observations],
            "properties": point.properties,
            "version": point.version,
            "updated_at": point.updated_at.isoformat(),
        },
    }


def set_json(session: RouteSession, gcp_set: GcpSet, *, include_points: bool) -> JsonObject:
    points = cast(list[GcpPoint], gcp_set.points)
    payload: JsonObject = {
        "set_id": gcp_set.set_id,
        "name": gcp_set.name,
        "source_filename": gcp_set.source_filename,
        "source_format": gcp_set.source_format,
        "source_crs": gcp_set.source_crs,
        "source_sha256": gcp_set.source_sha256,
        "point_count": len(points),
        "adjustment_count": sum(1 for point in points if point.role == "adjustment"),
        "checkpoint_count": sum(1 for point in points if point.role == "checkpoint"),
        "marked_observation_count": sum(
            1
            for point in points
            for observation in cast(list[GcpObservation], point.observations)
            if observation.status == "marked"
        ),
        "version": gcp_set.version,
        "created_at": gcp_set.created_at.isoformat(),
        "updated_at": gcp_set.updated_at.isoformat(),
    }
    if include_points:
        payload["type"] = "FeatureCollection"
        payload["features"] = [point_json(session, point) for point in points]
    return payload


def update_point_coordinates(
    point: GcpPoint,
    gcp_set: GcpSet,
    *,
    longitude: float,
    latitude: float,
    altitude_m: float,
) -> None:
    transformer = Transformer.from_crs("EPSG:4326", gcp_set.source_crs, always_xy=True)
    source_x, source_y = transformer.transform(longitude, latitude)
    point.geometry = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)
    point.source_x = float(source_x)
    point.source_y = float(source_y)
    point.source_z = altitude_m
    point.altitude_m = altitude_m
