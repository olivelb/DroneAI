from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import CheckConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.database import (
    AIAnalysisRun,
    AIAnalysisTile,
    DatasetUploadFile,
    DatasetUploadSession,
    InboxEvent,
    MapFeature,
    MapFeatureAuditEvent,
    Mission,
    MissionStageRun,
    MissionLog,
    OutboxEvent,
    RasterLayerStyle,
)


EXPECTED_CHECKS = {
    Mission: {
        "ck_missions_status",
        "ck_missions_aggregation_status",
    },
    MissionStageRun: {
        "ck_mission_stage_runs_status",
        "ck_mission_stage_runs_stage",
        "ck_mission_stage_runs_attempt",
        "ck_mission_stage_runs_resource_class",
        "ck_mission_stage_runs_dispatch_attempts",
        "ck_mission_stage_runs_idempotency_length",
    },
    AIAnalysisRun: {
        "ck_ai_analysis_runs_status",
        "ck_ai_analysis_runs_phase",
    },
    AIAnalysisTile: {"ck_ai_analysis_tiles_status"},
    MapFeature: {"ck_map_features_source"},
    MapFeatureAuditEvent: {"ck_map_feature_audit_action"},
    RasterLayerStyle: {"ck_raster_layer_styles_version"},
    MissionLog: {"ck_mission_logs_status"},
    InboxEvent: {"ck_inbox_events_status"},
    OutboxEvent: {"ck_outbox_events_status"},
    DatasetUploadSession: {"ck_dataset_upload_sessions_status"},
    DatasetUploadFile: {"ck_dataset_upload_files_status"},
}


def test_every_durable_state_column_has_a_named_check_constraint():
    for model, expected_names in EXPECTED_CHECKS.items():
        actual_names = {
            constraint.name
            for constraint in model.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert expected_names <= actual_names


@contextmanager
def _expect_rejected(session: Session) -> Iterator[None]:
    with pytest.raises(IntegrityError):
        yield
        session.flush()
    session.rollback()


def test_sqlite_enforces_core_workflow_state_constraints():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        Mission.__table__,
        AIAnalysisRun.__table__,
        AIAnalysisTile.__table__,
        MissionLog.__table__,
        InboxEvent.__table__,
        OutboxEvent.__table__,
        DatasetUploadSession.__table__,
        DatasetUploadFile.__table__,
    ):
        table.create(engine)

    with Session(engine) as session:
        with _expect_rejected(session):
            session.add(Mission(vol_id="invalid-mission", status="unknown"))

        mission = Mission(vol_id="mission-1")
        session.add(mission)
        session.commit()

        with _expect_rejected(session):
            session.add(
                Mission(
                    vol_id="invalid-aggregation",
                    aggregation_status="unknown",
                )
            )

        with _expect_rejected(session):
            session.add(
                AIAnalysisRun(
                    run_id="invalid-status",
                    mission_id=mission.id,
                    vol_id=mission.vol_id,
                    name="Invalid status",
                    ortho_s3_key="missions/mission-1/orthomosaic.tif",
                    status="unknown",
                )
            )

        with _expect_rejected(session):
            session.add(
                AIAnalysisRun(
                    run_id="invalid-phase",
                    mission_id=mission.id,
                    vol_id=mission.vol_id,
                    name="Invalid phase",
                    ortho_s3_key="missions/mission-1/orthomosaic.tif",
                    phase="unknown",
                )
            )

        run = AIAnalysisRun(
            run_id="run-1",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="Valid run",
            ortho_s3_key="missions/mission-1/orthomosaic.tif",
        )
        session.add(run)
        session.commit()

        with _expect_rejected(session):
            session.add(
                AIAnalysisTile(
                    analysis_run_id=run.id,
                    tile_index=0,
                    tile_s3_key="tile.jpg",
                    offset_x=0,
                    offset_y=0,
                    width=10,
                    height=10,
                    status="unknown",
                )
            )

        with _expect_rejected(session):
            session.add(
                MissionLog(
                    mission_id=mission.id,
                    vol_id=mission.vol_id,
                    status="unknown",
                )
            )

        with _expect_rejected(session):
            session.add(
                InboxEvent(
                    consumer_group="worker",
                    event_id="event-1",
                    event_type="status",
                    payload={},
                    status="unknown",
                )
            )

        with _expect_rejected(session):
            session.add(
                OutboxEvent(
                    event_id="event-2",
                    event_type="mission",
                    topic="missions",
                    payload={},
                    status="unknown",
                )
            )
