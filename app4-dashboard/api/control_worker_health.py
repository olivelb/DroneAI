"""Cross-process liveness/readiness contract for the standalone control worker."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from sqlalchemy import text

from shared.database import get_session


def health_path() -> Path:
    return Path(os.getenv("DRONEAI_CONTROL_HEALTH_PATH", "/tmp/droneai-control-worker-health.json"))


def maximum_heartbeat_age_seconds() -> float:
    try:
        value = float(os.getenv("DRONEAI_CONTROL_HEALTH_MAX_AGE_SECONDS", "15"))
    except ValueError as error:
        raise RuntimeError("DRONEAI_CONTROL_HEALTH_MAX_AGE_SECONDS must be numeric") from error
    if not 5 <= value <= 300:
        raise RuntimeError("DRONEAI_CONTROL_HEALTH_MAX_AGE_SECONDS must be between 5 and 300")
    return value


def clear_heartbeat() -> None:
    health_path().unlink(missing_ok=True)


def record_heartbeat(mode: str) -> None:
    if mode not in {"leader", "follower", "single"}:
        raise ValueError("Invalid control-worker heartbeat mode")
    destination = health_path()
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"checked_at": time.time(), "mode": mode, "pid": os.getpid()},
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def heartbeat_is_fresh(*, now: float | None = None) -> bool:
    try:
        payload = json.loads(health_path().read_text(encoding="utf-8"))
        checked_at = float(payload["checked_at"])
        mode = payload["mode"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    current_time = time.time() if now is None else now
    return mode in {"leader", "follower", "single"} and (
        0 <= current_time - checked_at <= maximum_heartbeat_age_seconds()
    )


def database_is_available() -> bool:
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def probe_is_healthy(mode: str) -> bool:
    if mode == "live":
        return heartbeat_is_fresh()
    if mode == "ready":
        return heartbeat_is_fresh() and database_is_available()
    raise ValueError("Probe mode must be live or ready")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "ready"), required=True)
    args = parser.parse_args()
    return 0 if probe_is_healthy(args.mode) else 1


if __name__ == "__main__":
    raise SystemExit(main())
