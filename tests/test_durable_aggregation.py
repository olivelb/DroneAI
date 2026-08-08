import importlib
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.database import Mission, ProcessedTile, count_received_tiles


PROCESSING_ROOT = Path(__file__).resolve().parents[1] / "app3-processing"
if str(PROCESSING_ROOT) not in sys.path:
    sys.path.insert(0, str(PROCESSING_ROOT))

legacy_module = importlib.import_module("legacy_aggregation")


def test_zero_detection_tiles_count_toward_durable_completion():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    ProcessedTile.__table__.create(engine)

    with Session(engine) as session:
        mission = Mission(vol_id="mission-1", total_tiles=2)
        session.add(mission)
        session.flush()
        session.add_all(
            [
                ProcessedTile(
                    mission_id=mission.id,
                    vol_id=mission.vol_id,
                    tile_index=0,
                    detection_count=0,
                ),
                ProcessedTile(
                    mission_id=mission.id,
                    vol_id=mission.vol_id,
                    tile_index=1,
                    detection_count=3,
                ),
            ]
        )
        session.flush()

        assert count_received_tiles(session, mission.vol_id) == 2


def test_multiple_aggregator_replicas_share_one_durable_completion_counter(
    monkeypatch,
):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    ProcessedTile.__table__.create(engine)

    with Session(engine) as session:
        session.add(
            Mission(
                vol_id="mission-shared",
                total_tiles=4,
                aggregation_status="collecting",
                retry_count=0,
                tiling_metadata={},
            )
        )
        session.commit()

    @contextmanager
    def session_scope():
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(legacy_module, "get_session", session_scope)
    reporters = {
        "tiler": lambda *_args, **_kwargs: None,
        "ia": lambda *_args, **_kwargs: None,
    }
    replica_a = legacy_module.LegacyAggregationWorkflow(
        report_progress=reporters["tiler"],
        report_ia_progress=reporters["ia"],
        logger=importlib.import_module("logging").getLogger("test"),
    )
    replica_b = legacy_module.LegacyAggregationWorkflow(
        report_progress=reporters["tiler"],
        report_ia_progress=reporters["ia"],
        logger=importlib.import_module("logging").getLogger("test"),
    )

    results = [
        worker._store_tile("mission-shared", tile_index, [], 0)
        for tile_index, worker in enumerate(
            (replica_a, replica_b, replica_a, replica_b)
        )
    ]

    assert [result["tiles_received"] for result in results if result] == [1, 2, 3, 4]
    assert all(result["finalize_mission"] is None for result in results[:3] if result)
    assert results[3] is not None
    assert results[3]["finalize_mission"] is not None
    with Session(engine) as session:
        mission = session.query(Mission).filter(Mission.vol_id == "mission-shared").one()
        assert mission.tiles_received == 4
        assert mission.aggregation_status == "finalizing"
