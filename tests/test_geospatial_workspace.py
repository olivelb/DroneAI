import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.database import AIAnalysisRun, AIAnalysisTile, Mission
from shared.event_contracts import deterministic_event_id
from shared.geospatial_workspace import (
    bounds_intersect,
    geometry_bounds,
    normalize_color,
    normalize_tags,
    validate_geometry,
)


def test_geojson_validation_rejects_invalid_or_excessive_coordinates():
    polygon = validate_geometry(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [2.0, 48.0],
                    [2.1, 48.0],
                    [2.1, 48.1],
                    [2.0, 48.0],
                ]
            ],
        }
    )
    assert geometry_bounds(polygon) == [2.0, 48.0, 2.1, 48.1]

    with pytest.raises(ValueError, match="WGS84"):
        validate_geometry(
            {"type": "Point", "coordinates": [250.0, 48.0]}
        )
    with pytest.raises(ValueError, match="unsupported"):
        validate_geometry(
            {"type": "GeometryCollection", "geometries": []}
        )


def test_style_and_tags_are_normalized_for_safe_layer_metadata():
    assert normalize_color("#AABBcc") == "#aabbcc"
    assert normalize_tags([" RTK ", "survey", "RTK", ""]) == [
        "RTK",
        "survey",
    ]
    with pytest.raises(ValueError, match="color"):
        normalize_color("red")


def test_bounds_intersection_supports_object_store_tile_filtering():
    assert bounds_intersect([1, 1, 3, 3], [2, 2, 4, 4])
    assert not bounds_intersect([1, 1, 2, 2], [3, 3, 4, 4])


def test_analysis_tile_receipts_are_unique_per_run_not_per_mission():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    AIAnalysisRun.__table__.create(engine)
    AIAnalysisTile.__table__.create(engine)

    with Session(engine) as session:
        mission = Mission(vol_id="mission-1")
        session.add(mission)
        session.flush()
        first = AIAnalysisRun(
            run_id="11111111-1111-1111-1111-111111111111",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="First",
            ortho_s3_key="missions/mission-1/orthomosaic.tif",
        )
        second = AIAnalysisRun(
            run_id="22222222-2222-2222-2222-222222222222",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="Second",
            ortho_s3_key="missions/mission-1/orthomosaic.tif",
        )
        session.add_all([first, second])
        session.flush()
        session.add_all(
            [
                AIAnalysisTile(
                    analysis_run_id=first.id,
                    tile_index=0,
                    tile_s3_key="first/tile.jpg",
                    offset_x=0,
                    offset_y=0,
                    width=512,
                    height=512,
                ),
                AIAnalysisTile(
                    analysis_run_id=second.id,
                    tile_index=0,
                    tile_s3_key="second/tile.jpg",
                    offset_x=0,
                    offset_y=0,
                    width=512,
                    height=512,
                ),
            ]
        )
        session.commit()

        session.add(
            AIAnalysisTile(
                analysis_run_id=first.id,
                tile_index=0,
                tile_s3_key="duplicate/tile.jpg",
                offset_x=0,
                offset_y=0,
                width=512,
                height=512,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_kafka_tile_identity_is_scoped_to_analysis_run():
    first = deterministic_event_id("image_tile", "mission", "run-a", 4)
    second = deterministic_event_id("image_tile", "mission", "run-b", 4)
    assert first != second
