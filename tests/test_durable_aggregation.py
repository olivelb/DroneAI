from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.database import Mission, ProcessedTile, count_received_tiles


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
