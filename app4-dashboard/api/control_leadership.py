"""PostgreSQL-backed leadership for cross-tenant control-plane loops."""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from shared.database import get_engine
from shared.deployment_mode import is_protected_environment


CONTROL_LEADER_LOCK_NAMESPACE = 0x44524F4E  # "DRON"
CONTROL_LEADER_LOCK_KEY = 0x4354524C  # "CTRL"


class ControlLeadershipError(RuntimeError):
    """Raised when a worker can no longer prove that it owns leadership."""


def control_leader_election_enabled() -> bool:
    raw = os.getenv("DRONEAI_CONTROL_LEADER_ELECTION", "false").strip().lower()
    if raw not in {"true", "false"}:
        raise RuntimeError(
            "DRONEAI_CONTROL_LEADER_ELECTION must be true or false"
        )
    enabled = raw == "true"
    if is_protected_environment() and not enabled:
        raise RuntimeError(
            "Staging and production require control-worker leader election"
        )
    return enabled


def control_leader_poll_seconds() -> float:
    raw = os.getenv("DRONEAI_CONTROL_LEADER_POLL_SECONDS", "2").strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(
            "DRONEAI_CONTROL_LEADER_POLL_SECONDS must be numeric"
        ) from error
    if value < 0.1 or value > 60:
        raise RuntimeError(
            "DRONEAI_CONTROL_LEADER_POLL_SECONDS must be between 0.1 and 60"
        )
    return value


@dataclass
class ControlLeadership:
    """A session-level advisory lock held by one dedicated DB connection."""

    connection: Any
    released: bool = False

    def raise_if_unhealthy(self) -> None:
        if self.released:
            raise ControlLeadershipError("Control leadership is already released")
        try:
            self.connection.execute(text("SELECT 1")).scalar_one()
            self.connection.commit()
        except Exception as error:
            raise ControlLeadershipError(
                "Control leadership database connection was lost"
            ) from error

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            # A broken connection has already released its session lock on the
            # server. Cleanup must not hide the leadership-loss cause.
            with suppress(Exception):
                self.connection.execute(
                    text(
                        "SELECT pg_advisory_unlock(:namespace, :lock_key)"
                    ),
                    {
                        "namespace": CONTROL_LEADER_LOCK_NAMESPACE,
                        "lock_key": CONTROL_LEADER_LOCK_KEY,
                    },
                )
                self.connection.commit()
        finally:
            with suppress(Exception):
                self.connection.close()

    def __enter__(self) -> ControlLeadership:
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        self.release()


def try_acquire_control_leadership(
    engine: Any | None = None,
) -> ControlLeadership | None:
    """Return a held lease, or None while another replica is leader."""

    database_engine = engine or get_engine()
    if database_engine.dialect.name != "postgresql":
        raise RuntimeError("Control-worker leader election requires PostgreSQL")
    connection = database_engine.connect()
    try:
        acquired = bool(
            connection.execute(
                text(
                    "SELECT pg_try_advisory_lock(:namespace, :lock_key)"
                ),
                {
                    "namespace": CONTROL_LEADER_LOCK_NAMESPACE,
                    "lock_key": CONTROL_LEADER_LOCK_KEY,
                },
            ).scalar_one()
        )
        connection.commit()
    except Exception:
        connection.close()
        raise
    if not acquired:
        connection.close()
        return None
    return ControlLeadership(connection)
