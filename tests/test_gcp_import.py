import json

import pytest

from shared.gcp_import import import_gcp_bytes


def test_imports_odm_observations_and_groups_repeated_points():
    payload = b"""EPSG:4326
1.25 44.5 210 100 200 DJI_0001.JPG GCP-1
1.25 44.5 210 120 220 DJI_0002.JPG GCP-1
1.26 44.51 211 130 230 DJI_0002.JPG GCP-2
"""

    imported = import_gcp_bytes(payload, "gcp_list.txt")

    assert imported.source_format == "odm-gcp-list"
    assert imported.source_crs == "EPSG:4326"
    assert len(imported.points) == 2
    assert len(imported.points[0].observations) == 2
    assert imported.points[0].longitude == pytest.approx(1.25)


def test_imports_metashape_style_semicolon_csv_with_accuracy_and_roles():
    payload = b"""label;easting;northing;altitude;role;sigma_xy;sigma_z
P1;548000;6240000;212.4;control;0.015;0.025
P2;548010;6240010;213.0;checkpoint;0.02;0.04
"""

    imported = import_gcp_bytes(payload, "markers.csv", source_crs="EPSG:2154")

    assert imported.source_format == "delimited-text"
    assert imported.points[0].role == "adjustment"
    assert imported.points[1].role == "checkpoint"
    assert imported.points[0].horizontal_accuracy_m == pytest.approx(0.015)
    assert -180 <= imported.points[0].longitude <= 180


def test_imports_geojson_and_preserves_properties():
    payload = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "P1",
                    "properties": {"role": "verify", "survey_code": "A"},
                    "geometry": {"type": "Point", "coordinates": [1.2, 44.4, 205]},
                }
            ],
        }
    ).encode()

    imported = import_gcp_bytes(payload, "points.geojson")

    assert imported.points[0].external_id == "P1"
    assert imported.points[0].role == "checkpoint"
    assert imported.points[0].properties["survey_code"] == "A"


@pytest.mark.parametrize(
    ("line", "external_id"),
    [("P1 1.2 44.4 205", "P1"), ("1.2 44.4 205 P2", "P2")],
)
def test_imports_common_headerless_orders(line, external_id):
    imported = import_gcp_bytes(
        (line + "\n").encode(),
        "points.txt",
        source_crs="EPSG:4326",
    )
    assert imported.points[0].external_id == external_id


def test_csv_requires_explicit_crs_when_not_declared():
    with pytest.raises(ValueError, match="source_crs"):
        import_gcp_bytes(b"id,x,y,z\nP1,1,2,3\n", "points.csv")


def test_rejects_non_finite_coordinates():
    with pytest.raises(ValueError, match="finite"):
        import_gcp_bytes(
            b"id,x,y,z\nP1,nan,2,3\n",
            "points.csv",
            source_crs="EPSG:4326",
        )


def test_rejects_empty_odm_file():
    with pytest.raises(ValueError, match="no points"):
        import_gcp_bytes(b"EPSG:4326\n", "gcp_list.txt")
