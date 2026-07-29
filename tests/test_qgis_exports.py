from __future__ import annotations

import json
import sqlite3

import pytest
from rasterio.crs import CRS

from shared.qgis_crs import (
    ExportCrsError,
    reproject_features,
    resolve_export_crs,
)
from shared.qgis_exports import GeoPackageCrs, write_vector_export


def _features():
    return [
        {
            "type": "Feature",
            "id": "point-1",
            "geometry": {"type": "Point", "coordinates": [2.25, 48.75]},
            "properties": {
                "feature_id": "point-1",
                "source": "manual",
                "name": "Repère",
                "description": "Point de contrôle visuel",
                "color": "#10b981",
                "tags": ["terrain", "QGIS"],
                "version": 2,
            },
        },
        {
            "type": "Feature",
            "id": "polygon-1",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [2.2, 48.7],
                        [2.3, 48.7],
                        [2.3, 48.8],
                        [2.2, 48.7],
                    ]
                ],
            },
            "properties": {
                "feature_id": "polygon-1",
                "source": "ai",
                "name": "Zone détectée",
                "confidence": 0.91,
                "tags": ["IA"],
            },
        },
    ]


def test_geojson_export_is_qgis_ready(tmp_path):
    destination = tmp_path / "mission.geojson"
    count = write_vector_export(
        destination,
        iter(_features()),
        output_format="geojson",
        mission_id="MISSION_001",
        scope="all",
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert count == 2
    assert payload["type"] == "FeatureCollection"
    assert payload["name"] == "MISSION_001_all"
    assert [feature["geometry"]["type"] for feature in payload["features"]] == [
        "Point",
        "Polygon",
    ]


def test_geopackage_export_has_standard_metadata_and_mixed_geometry(tmp_path):
    destination = tmp_path / "mission.gpkg"
    count = write_vector_export(
        destination,
        iter(_features()),
        output_format="gpkg",
        mission_id="MISSION_001",
        scope="all",
    )

    assert count == 2
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 1196444487
        contents = connection.execute(
            """
            SELECT table_name, data_type, min_x, min_y, max_x, max_y, srs_id
            FROM gpkg_contents
            """
        ).fetchone()
        assert contents == (
            "droneai_features",
            "features",
            2.2,
            48.7,
            2.3,
            48.8,
            4326,
        )
        geometry_metadata = connection.execute(
            """
            SELECT column_name, geometry_type_name, srs_id, z, m
            FROM gpkg_geometry_columns
            """
        ).fetchone()
        assert geometry_metadata == ("geom", "GEOMETRY", 4326, 0, 0)
        rows = connection.execute(
            """
            SELECT geom, feature_id, source, tags, confidence
            FROM droneai_features ORDER BY fid
            """
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0].startswith(b"GP")
        assert json.loads(rows[0][3]) == ["terrain", "QGIS"]
        assert rows[1][4] == pytest.approx(0.91)


def test_geopackage_records_the_selected_projected_crs(tmp_path):
    destination = tmp_path / "lambert93.gpkg"
    raster_crs = CRS.from_epsg(2154)
    projected = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [648_237.3, 6_802_271.2],
            },
            "properties": {"feature_id": "projected-1", "source": "manual"},
        }
    ]
    write_vector_export(
        destination,
        iter(projected),
        output_format="gpkg",
        mission_id="MISSION_001",
        scope="manual",
        crs=GeoPackageCrs(
            srs_id=2154,
            name="EPSG:2154",
            definition=raster_crs.to_wkt(),
        ),
    )

    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT srs_id FROM gpkg_contents").fetchone() == (2154,)
        assert connection.execute("SELECT srs_id FROM gpkg_geometry_columns").fetchone() == (2154,)
        definition = connection.execute("SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id = 2154").fetchone()[0]
        assert "Lambert" in definition
        assert connection.execute("SELECT min_x, min_y, max_x, max_y FROM gpkg_contents").fetchone() == (
            648_237.3,
            6_802_271.2,
            648_237.3,
            6_802_271.2,
        )


def test_raster_crs_is_resolved_and_wgs84_features_are_reprojected():
    resolved = resolve_export_crs(
        "EPSG:2154",
        "gpkg",
        "raster",
    )
    projected = list(reproject_features(iter(_features()), resolved.label))

    assert resolved.geopackage_crs.srs_id == 2154
    assert resolved.label == "EPSG:2154"
    assert resolved.used_fallback is False
    x, y = projected[0]["geometry"]["coordinates"]
    assert 600_000 < x < 800_000
    assert 6_700_000 < y < 7_000_000


def test_geojson_forces_wgs84_and_rejects_another_requested_crs():
    resolved = resolve_export_crs(
        "EPSG:2154",
        "geojson",
        "raster",
    )
    assert resolved.geopackage_crs.srs_id == 4326
    assert resolved.label == "EPSG:4326"
    assert resolved.used_fallback is False

    with pytest.raises(ExportCrsError, match="GeoJSON"):
        resolve_export_crs(
            "EPSG:2154",
            "geojson",
            "EPSG:2154",
        )


def test_missing_raster_crs_falls_back_explicitly_to_wgs84():
    resolved = resolve_export_crs(
        "unknown",
        "gpkg",
        "raster",
    )
    assert resolved.geopackage_crs.srs_id == 4326
    assert resolved.label == "EPSG:4326"
    assert resolved.used_fallback is True


def test_unsupported_vector_format_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsupported vector export format"):
        write_vector_export(
            tmp_path / "mission.bin",
            [],
            output_format="shapefile",
            mission_id="MISSION_001",
            scope="all",
        )


def test_geopackage_opens_with_gdal_when_available(tmp_path):
    fiona = pytest.importorskip("fiona")
    destination = tmp_path / "qgis-validation.gpkg"
    write_vector_export(
        destination,
        iter(_features()),
        output_format="gpkg",
        mission_id="MISSION_001",
        scope="all",
    )

    assert fiona.listlayers(destination) == ["droneai_features"]
    with fiona.open(destination, layer="droneai_features") as layer:
        assert len(layer) == 2
        assert layer.crs.to_epsg() == 4326
        assert layer.schema["geometry"] == "Unknown"


def test_reprojected_geopackage_opens_with_gdal_when_available(tmp_path):
    fiona = pytest.importorskip("fiona")
    destination = tmp_path / "qgis-lambert93.gpkg"
    resolved = resolve_export_crs(
        "EPSG:2154",
        "gpkg",
        "raster",
    )
    write_vector_export(
        destination,
        reproject_features(iter(_features()), resolved.label),
        output_format="gpkg",
        mission_id="MISSION_001",
        scope="all",
        crs=resolved.geopackage_crs,
    )

    with fiona.open(destination, layer="droneai_features") as layer:
        assert len(layer) == 2
        assert layer.crs.to_epsg() == 2154
        x, y = next(iter(layer))["geometry"]["coordinates"]
        assert 600_000 < x < 800_000
        assert 6_700_000 < y < 7_000_000
