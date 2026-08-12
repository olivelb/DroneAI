"""Real PostgreSQL session-lock qualification for control-worker failover."""

from __future__ import annotations

import importlib
import os

import pytest
from sqlalchemy import create_engine


leadership = importlib.import_module("app4-dashboard.api.control_leadership")


@pytest.mark.integration
def test_only_one_control_worker_leads_and_a_follower_takes_over() -> None:
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    first = None
    second = None
    successor = None
    try:
        first = leadership.try_acquire_control_leadership(engine)
        second = leadership.try_acquire_control_leadership(engine)

        assert first is not None
        assert second is None
        first.raise_if_unhealthy()
        first.connection.invalidate()
        first.connection.close()
        with pytest.raises(leadership.ControlLeadershipError):
            first.raise_if_unhealthy()

        successor = leadership.try_acquire_control_leadership(engine)
        assert successor is not None
        successor.raise_if_unhealthy()
    finally:
        if first is not None:
            first.release()
        if second is not None:
            second.release()
        if successor is not None:
            successor.release()
        engine.dispose()
