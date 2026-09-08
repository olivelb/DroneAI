"""Real PostgreSQL quota isolation and concurrent first-request contracts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from shared.database import APIRateLimitBucket, get_session
from shared.rate_limiting import DatabaseTokenBucketRateLimiter


@pytest.mark.integration
def test_scoped_pressure_and_concurrent_consumption():
    prefix = uuid4().hex
    scopes = [f"{prefix}:{kind}" for kind in ("peer", "credential", "tile")]
    now = datetime.now(UTC)
    try:
        with get_session() as session:
            session.bulk_insert_mappings(APIRateLimitBucket, [
                {"scope": scope, "key_hash": f"{i:064x}", "tokens": 0,
                 "updated_at": now - timedelta(hours=1)}
                for scope, count in zip(scopes[:2], (50_000, 20_000))
                for i in range(count)
            ])
        tile = DatabaseTokenBucketRateLimiter(scope=scopes[2], session_scope=get_session,
                                              requests_per_minute=60, burst=5, max_keys=2,
                                              clock=lambda: now)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(tile.consume, ["concurrent"] * 20))
        assert results.count(None) == 5
        assert results.count(1.0) == 15
        for i in range(10):
            tile.consume(str(i))
        assert tile.collect_expired() == 0
        now += timedelta(minutes=6)
        assert tile.collect_expired(batch_size=3) == 3
        with get_session() as session:
            for scope, count in zip(scopes[:2], (50_000, 20_000)):
                rows = session.query(APIRateLimitBucket).filter_by(scope=scope)
                assert rows.count() == count
                assert rows.filter(APIRateLimitBucket.tokens != 0).count() == 0
    finally:
        with get_session() as session:
            session.query(APIRateLimitBucket).filter(APIRateLimitBucket.scope.in_(scopes)).delete()
