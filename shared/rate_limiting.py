"""Local and PostgreSQL-backed token buckets for expensive API reads."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError

from shared.database import APIRateLimitBucket


class RateLimiter(Protocol):
    requests_per_minute: int

    def consume(self, key: str) -> float | None: ...


class TokenBucketRateLimiter:
    """Bounded in-process implementation for local development and tests."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        burst: int,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate_configuration(requests_per_minute, burst, max_keys)
        self.requests_per_minute = requests_per_minute
        self.burst = burst
        self.max_keys = max_keys
        self._rate_per_second = requests_per_minute / 60.0
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def consume(self, key: str) -> float | None:
        now = self._clock()
        with self._lock:
            previous = self._buckets.pop(key, None)
            if previous is None:
                if len(self._buckets) >= self.max_keys:
                    self._buckets.popitem(last=False)
                tokens, last_seen = float(self.burst), now
            else:
                tokens, last_seen = previous
            tokens = min(
                float(self.burst),
                tokens + max(0.0, now - last_seen) * self._rate_per_second,
            )
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return None
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) / self._rate_per_second


SessionScope = Callable[[], AbstractContextManager[Any]]


class DatabaseTokenBucketRateLimiter:
    """Transactional token bucket shared by every API replica."""

    def __init__(
        self,
        *,
        session_scope: SessionScope,
        requests_per_minute: int,
        burst: int,
        max_keys: int = 100_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        _validate_configuration(requests_per_minute, burst, max_keys)
        self.session_scope = session_scope
        self.requests_per_minute = requests_per_minute
        self.burst = burst
        self.max_keys = max_keys
        self._rate_per_second = requests_per_minute / 60.0
        self._clock = clock

    @staticmethod
    def _key_hash(key: str) -> str:
        return sha256(key.encode("utf-8")).hexdigest()

    def _trim_oldest(self, session: Any) -> None:
        count = int(session.query(APIRateLimitBucket).count())
        overflow = count - self.max_keys + 1
        if overflow <= 0:
            return
        oldest = (
            session.query(APIRateLimitBucket)
            .order_by(APIRateLimitBucket.updated_at.asc())
            .limit(overflow)
            .all()
        )
        for record in oldest:
            session.delete(record)

    def _consume_once(self, key_hash: str, now: datetime) -> float | None:
        with self.session_scope() as session:
            record = (
                session.query(APIRateLimitBucket)
                .filter(APIRateLimitBucket.key_hash == key_hash)
                .with_for_update()
                .first()
            )
            if record is None:
                self._trim_oldest(session)
                session.add(
                    APIRateLimitBucket(
                        key_hash=key_hash,
                        tokens=float(self.burst - 1),
                        updated_at=now,
                    )
                )
                session.flush()
                return None
            last_seen = record.updated_at
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            tokens: float = min(
                float(self.burst),
                float(record.tokens)
                + max(0.0, (now - last_seen).total_seconds())
                * self._rate_per_second,
            )
            record.updated_at = now
            if tokens >= 1.0:
                record.tokens = tokens - 1.0
                return None
            record.tokens = tokens
            return (1.0 - tokens) / self._rate_per_second

    def consume(self, key: str) -> float | None:
        key_hash = self._key_hash(key)
        now = self._clock()
        try:
            return self._consume_once(key_hash, now)
        except IntegrityError:
            # Concurrent first requests may race on the primary key. Once the
            # winning transaction commits, retry through the locked row.
            return self._consume_once(key_hash, now)


def _validate_configuration(
    requests_per_minute: int,
    burst: int,
    max_keys: int,
) -> None:
    if requests_per_minute <= 0 or burst <= 0 or max_keys <= 0:
        raise ValueError("rate limit, burst, and max_keys must be positive")
