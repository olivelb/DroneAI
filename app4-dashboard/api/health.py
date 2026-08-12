"""Minimal liveness and dependency readiness checks."""

from __future__ import annotations

import logging
import os

from sqlalchemy import text

from shared.database import get_session


logger = logging.getLogger("droneai.health")


def database_is_ready() -> bool:
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
            if os.getenv("DRONEAI_RLS_REQUIRED", "").strip().lower() == "true":
                active = session.execute(
                    text("SELECT row_security_active('missions'::regclass)")
                ).scalar_one()
                if active is not True:
                    raise RuntimeError(
                        "PostgreSQL RLS is not active for the dashboard API role"
                    )
    except Exception:
        logger.warning("Database readiness check failed", exc_info=True)
        return False
    return True
