"""PostgreSQL transaction-boundary tests for the distributed scheduler."""

from __future__ import annotations

import importlib
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

orchestrator = importlib.import_module("app4-dashboard.api.stage_orchestrator")


@pytest.mark.integration
def test_advisory_lock_serializes_two_scheduler_transactions() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    first = Session(engine)
    second = Session(engine)
    try:
        assert orchestrator._try_acquire_scheduler_reservation_lock(first)
        assert not orchestrator._try_acquire_scheduler_reservation_lock(second)

        first.commit()
        second.rollback()

        assert orchestrator._try_acquire_scheduler_reservation_lock(second)
        second.commit()
    finally:
        first.close()
        second.close()
        engine.dispose()
