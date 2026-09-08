"""Periodic scoped quota maintenance outside HTTP request transactions."""
from __future__ import annotations

import logging
import threading

from shared.rate_limiting import DatabaseTokenBucketRateLimiter
from .security import build_identity_rate_limiter, build_tile_rate_limiter

logger = logging.getLogger(__name__)


def run_rate_limit_maintenance(stop_event: threading.Event) -> None:
    limiters = [build_tile_rate_limiter(), build_identity_rate_limiter(scope="peer"),
                build_identity_rate_limiter(scope="credential")]
    while not stop_event.is_set():
        for limiter in limiters:
            if isinstance(limiter, DatabaseTokenBucketRateLimiter):
                try:
                    limiter.collect_expired()
                except Exception:
                    logger.exception("Rate-limit maintenance failed for scope %s", limiter.scope)
        stop_event.wait(30)
