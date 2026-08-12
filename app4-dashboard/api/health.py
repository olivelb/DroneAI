"""Minimal liveness and dependency readiness checks."""

from __future__ import annotations

import logging

from sqlalchemy import text

from shared.database import get_session


logger = logging.getLogger("droneai.health")


def database_is_ready() -> bool:
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Database readiness check failed", exc_info=True)
        return False
    return True
