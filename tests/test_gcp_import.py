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


@pytest.mark.parametrize(
    ("pixel_x", "pixel_y", "message"),
    [
        ("nan", "20", "finite"),
        ("10", "inf", "finite"),
        ("-0.1", "20", "non-negative"),
        ("10", "-1", "non-negative"),
    ],
)
def test_rejects_unsafe_odm_observation_pixels(pixel_x, pixel_y, message):
    payload = (
        "EPSG:4326\n"
        f"1.25 44.5 210 {pixel_x} {pixel_y} DJI_0001.JPG GCP-1\n"
    ).encode()

    with pytest.raises(ValueError, match=message):
        import_gcp_bytes(payload, "gcp_list.txt")


def test_imports_kml_point_placemarks_as_wgs84():
    payload = b"""<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>GCP-K1</name><Point><coordinates>1.25,44.5,210</coordinates></Point></Placemark>
</Document></kml>"""

    imported = import_gcp_bytes(payload, "controls.kml")

    assert imported.source_format == "kml"
    assert imported.source_crs == "EPSG:4326"
    assert imported.points[0].external_id == "GCP-K1"
    assert imported.points[0].altitude_m == pytest.approx(210)


def test_imports_metashape_xml_positions_and_photo_projections():
    payload = b"""<document>
  <coordinate_system><wkt>EPSG:4326</wkt></coordinate_system>
  <cameras><camera id="4" label="DJI_0004.JPG" /></cameras>
  <markers><marker id="1" label="GCP-M1"><reference x="1.25" y="44.5" z="211" /></marker></markers>
  <frames><frame><markers><marker marker_id="1"><location camera_id="4" x="1200.5" y="900.25" /></marker></markers></frame></frames>
</document>"""

    imported = import_gcp_bytes(payload, "markers.xml")

    assert imported.source_format == "metashape-xml"
    point = imported.points[0]
    assert point.external_id == "GCP-M1"
    assert point.observations[0].image_name == "DJI_0004.JPG"
    assert point.observations[0].pixel_x == pytest.approx(1200.5)


def test_rejects_unsafe_metashape_projection_pixels():
    payload = b"""<document>
  <coordinate_system><wkt>EPSG:4326</wkt></coordinate_system>
  <cameras><camera id="4" label="DJI_0004.JPG" /></cameras>
  <markers><marker id="1" label="GCP-M1"><reference x="1.25" y="44.5" z="211" /></marker></markers>
  <frames><frame><markers><marker marker_id="1"><location camera_id="4" x="nan" y="900.25" /></marker></markers></frame></frames>
</document>"""

    with pytest.raises(ValueError, match="finite"):
        import_gcp_bytes(payload, "markers.xml")


def test_applies_trimble_and_custom_column_profiles():
    trimble = import_gcp_bytes(
        b"Point Name,Northing,Easting,Elevation\nT1,44.5,1.25,210\n",
        "trimble.csv",
        source_crs="EPSG:4326",
        column_profile="trimble",
    )
    custom = import_gcp_bytes(
        b"Station,Grid E,Grid N,Level\nC1,1.26,44.51,212\n",
        "custom.csv",
        source_crs="EPSG:4326",
        column_mapping={
            "point_id": "Station",
            "x": "Grid E",
            "y": "Grid N",
            "z": "Level",
        },
    )

    assert trimble.points[0].external_id == "T1"
    assert trimble.points[0].longitude == pytest.approx(1.25)
    assert custom.points[0].external_id == "C1"
    assert custom.points[0].altitude_m == pytest.approx(212)


def test_rejects_xml_entity_declarations():
    with pytest.raises(ValueError, match="entity declarations"):
        import_gcp_bytes(
            b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
            "markers.xml",
            source_crs="EPSG:4326",
        )
