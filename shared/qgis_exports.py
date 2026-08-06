"""Dependency-free GeoJSON and GeoPackage writers for QGIS exports."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
)

PROPERTY_COLUMNS = {
    "feature_id": "TEXT",
    "source": "TEXT",
    "run_id": "TEXT",
    "name": "TEXT",
    "description": "TEXT",
    "color": "TEXT",
    "tags": "TEXT",
    "class_name": "TEXT",
    "confidence": "REAL",
    "version": "INTEGER",
    "created_by": "TEXT",
    "updated_at": "TEXT",
    "properties_json": "TEXT",
}


@dataclass(frozen=True)
class GeoPackageCrs:
    """Authority-backed CRS metadata written into a GeoPackage."""

    srs_id: int
    name: str
    definition: str
    organization: str = "EPSG"
    organization_coordsys_id: int | None = None

    def __post_init__(self) -> None:
        if self.organization_coordsys_id is None:
            object.__setattr__(self, "organization_coordsys_id", self.srs_id)


WGS84_CRS = GeoPackageCrs(
    srs_id=4326,
    name="WGS 84",
    definition=WGS84_WKT,
)


def _export_geometry_bounds(
    geometry: dict[str, Any],
) -> tuple[float, float, float, float]:
    points: list[tuple[float, float]] = []

    def visit(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            x, y = float(node[0]), float(node[1])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("geometry coordinates must be finite")
            points.append((x, y))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                visit(child)

    if geometry.get("type") == "GeometryCollection":
        for child_geometry in geometry.get("geometries", []):
            child_bounds = _export_geometry_bounds(child_geometry)
            points.extend(
                [
                    (child_bounds[0], child_bounds[1]),
                    (child_bounds[2], child_bounds[3]),
                ]
            )
    else:
        visit(geometry.get("coordinates"))
    if not points:
        raise ValueError("geometry has no coordinates")
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _wkb_geometry(geometry: dict[str, Any]) -> bytes:
    geometry_type = geometry["type"]
    coordinates: Any = geometry.get("coordinates")
    type_codes = {
        "Point": 1,
        "LineString": 2,
        "Polygon": 3,
        "MultiPoint": 4,
        "MultiLineString": 5,
        "MultiPolygon": 6,
        "GeometryCollection": 7,
    }
    try:
        type_code = type_codes[geometry_type]
    except KeyError as error:
        raise ValueError(f"Unsupported geometry type: {geometry_type}") from error

    prefix = struct.pack("<BI", 1, type_code)
    if geometry_type == "Point":
        return prefix + struct.pack("<dd", coordinates[0], coordinates[1])
    if geometry_type == "LineString":
        return (
            prefix
            + struct.pack("<I", len(coordinates))
            + b"".join(struct.pack("<dd", point[0], point[1]) for point in coordinates)
        )
    if geometry_type == "Polygon":
        payload = [prefix, struct.pack("<I", len(coordinates))]
        for ring in coordinates:
            payload.extend(
                [
                    struct.pack("<I", len(ring)),
                    b"".join(struct.pack("<dd", point[0], point[1]) for point in ring),
                ]
            )
        return b"".join(payload)
    if geometry_type == "MultiPoint":
        children = [_wkb_geometry({"type": "Point", "coordinates": point}) for point in coordinates]
    elif geometry_type == "MultiLineString":
        children = [_wkb_geometry({"type": "LineString", "coordinates": line}) for line in coordinates]
    elif geometry_type == "MultiPolygon":
        children = [_wkb_geometry({"type": "Polygon", "coordinates": polygon}) for polygon in coordinates]
    else:
        children = [_wkb_geometry(item) for item in geometry.get("geometries", [])]
    return prefix + struct.pack("<I", len(children)) + b"".join(children)


def _gpkg_geometry(
    geometry: dict[str, Any],
    *,
    srs_id: int,
) -> bytes:
    west, south, east, north = _export_geometry_bounds(geometry)
    flags = 1 | (1 << 1)  # little endian + XY envelope
    header = b"GP" + bytes((0, flags)) + struct.pack("<i", srs_id) + struct.pack("<dddd", west, east, south, north)
    return header + _wkb_geometry(geometry)


def _property_row(properties: dict[str, Any]) -> list[Any]:
    known = {key: properties.get(key) for key in PROPERTY_COLUMNS if key != "properties_json"}
    tags = known.get("tags")
    if isinstance(tags, (list, dict)):
        known["tags"] = json.dumps(tags, ensure_ascii=False, separators=(",", ":"))
    known["properties_json"] = json.dumps(
        properties,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return [known.get(key) for key in PROPERTY_COLUMNS]


def write_geojson(
    destination: str | Path,
    features: Iterable[dict[str, Any]],
    *,
    mission_id: str,
    scope: str,
) -> int:
    """Write a bounded-memory RFC 7946 FeatureCollection."""

    count = 0
    path = Path(destination)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write('{"type":"FeatureCollection","name":')
        output.write(json.dumps(f"{mission_id}_{scope}", ensure_ascii=False))
        output.write(',"features":[')
        for feature in features:
            if not feature.get("geometry"):
                continue
            if count:
                output.write(",")
            json.dump(
                feature,
                output,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            count += 1
        output.write("]}")
    return count


def _initialize_geopackage(
    connection: sqlite3.Connection,
    crs: GeoPackageCrs,
) -> None:
    connection.executescript(
        """
        PRAGMA application_id = 1196444487;
        PRAGMA user_version = 10300;
        PRAGMA foreign_keys = ON;
        CREATE TABLE gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT
        );
        CREATE TABLE gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL,
            identifier TEXT UNIQUE,
            description TEXT DEFAULT '',
            last_change DATETIME NOT NULL,
            min_x DOUBLE,
            min_y DOUBLE,
            max_x DOUBLE,
            max_y DOUBLE,
            srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                REFERENCES gpkg_spatial_ref_sys(srs_id)
        );
        CREATE TABLE gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL,
            m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
            CONSTRAINT fk_gc_tn FOREIGN KEY (table_name)
                REFERENCES gpkg_contents(table_name),
            CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id)
                REFERENCES gpkg_spatial_ref_sys(srs_id)
        );
        """
    )
    spatial_references: list[tuple[str, int, str, int | None, str, str]] = [
        ("Undefined Cartesian", -1, "NONE", -1, "undefined", "undefined"),
        ("Undefined Geographic", 0, "NONE", 0, "undefined", "undefined"),
        ("WGS 84", 4326, "EPSG", 4326, WGS84_WKT, "WGS 84 longitude/latitude"),
    ]
    if crs.srs_id != 4326:
        spatial_references.append(
            (
                crs.name,
                crs.srs_id,
                crs.organization,
                crs.organization_coordsys_id,
                crs.definition,
                f"{crs.organization}:{crs.organization_coordsys_id}",
            )
        )
    connection.executemany(
        """
        INSERT INTO gpkg_spatial_ref_sys
            (srs_name, srs_id, organization, organization_coordsys_id,
             definition, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        spatial_references,
    )


def write_geopackage(
    destination: str | Path,
    features: Iterable[dict[str, Any]],
    *,
    mission_id: str,
    scope: str,
    crs: GeoPackageCrs = WGS84_CRS,
) -> int:
    """Write one mixed-geometry GeoPackage layer in the supplied CRS."""

    path = Path(destination)
    path.unlink(missing_ok=True)
    layer_name = "annotations" if scope == "manual" else "droneai_features"
    connection = sqlite3.connect(path)
    try:
        _initialize_geopackage(connection, crs)
        columns_sql = ",\n".join(f'"{name}" {column_type}' for name, column_type in PROPERTY_COLUMNS.items())
        connection.execute(
            f"""
            CREATE TABLE "{layer_name}" (
                fid INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                geom BLOB,
                {columns_sql}
            )
            """
        )
        now = datetime.now(UTC).isoformat(timespec="seconds")
        connection.execute(
            """
            INSERT INTO gpkg_contents
                (table_name, data_type, identifier, description, last_change, srs_id)
            VALUES (?, 'features', ?, ?, ?, ?)
            """,
            (
                layer_name,
                f"{mission_id} — {scope}",
                "DroneAI QGIS-compatible export",
                now,
                crs.srs_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO gpkg_geometry_columns
                (table_name, column_name, geometry_type_name, srs_id, z, m)
            VALUES (?, 'geom', 'GEOMETRY', ?, 0, 0)
            """,
            (layer_name, crs.srs_id),
        )
        placeholders = ",".join("?" for _ in range(len(PROPERTY_COLUMNS) + 1))
        column_names = ",".join(f'"{name}"' for name in PROPERTY_COLUMNS)
        insert_sql = f'INSERT INTO "{layer_name}" (geom,{column_names}) VALUES ({placeholders})'
        count = 0
        extent: list[float] | None = None
        for feature in features:
            geometry = feature.get("geometry")
            if not geometry:
                continue
            bounds = _export_geometry_bounds(geometry)
            if extent is None:
                extent = list(bounds)
            else:
                extent = [
                    min(extent[0], bounds[0]),
                    min(extent[1], bounds[1]),
                    max(extent[2], bounds[2]),
                    max(extent[3], bounds[3]),
                ]
            connection.execute(
                insert_sql,
                [
                    _gpkg_geometry(geometry, srs_id=crs.srs_id),
                    *_property_row(feature.get("properties") or {}),
                ],
            )
            count += 1
        if extent:
            connection.execute(
                """
                UPDATE gpkg_contents
                SET min_x = ?, min_y = ?, max_x = ?, max_y = ?
                WHERE table_name = ?
                """,
                (*extent, layer_name),
            )
        connection.commit()
        return count
    finally:
        connection.close()


def write_vector_export(
    destination: str | Path,
    features: Iterable[dict[str, Any]],
    *,
    output_format: str,
    mission_id: str,
    scope: str,
    crs: GeoPackageCrs = WGS84_CRS,
) -> int:
    if output_format == "geojson":
        return write_geojson(
            destination,
            features,
            mission_id=mission_id,
            scope=scope,
        )
    if output_format == "gpkg":
        return write_geopackage(
            destination,
            features,
            mission_id=mission_id,
            scope=scope,
            crs=crs,
        )
    raise ValueError(f"Unsupported vector export format: {output_format}")
