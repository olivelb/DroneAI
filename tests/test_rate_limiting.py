from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.database import APIRateLimitBucket
from shared.rate_limiting import DatabaseTokenBucketRateLimiter


def _session_scope():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    APIRateLimitBucket.__table__.create(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return scope


def test_database_token_bucket_is_shared_across_api_replicas():
    now = [datetime(2026, 8, 8, tzinfo=UTC)]
    session_scope = _session_scope()
    replica_a = DatabaseTokenBucketRateLimiter(
        session_scope=session_scope,
        requests_per_minute=2,
        burst=2,
        clock=lambda: now[0],
    )
    replica_b = DatabaseTokenBucketRateLimiter(
        session_scope=session_scope,
        requests_per_minute=2,
        burst=2,
        clock=lambda: now[0],
    )

    assert replica_a.consume("203.0.113.5") is None
    assert replica_b.consume("203.0.113.5") is None
    assert replica_a.consume("203.0.113.5") == 30.0

    now[0] += timedelta(seconds=30)
    assert replica_b.consume("203.0.113.5") is None


def test_database_token_bucket_hashes_clients_and_bounds_rows():
    session_scope = _session_scope()
    limiter = DatabaseTokenBucketRateLimiter(
        session_scope=session_scope,
        requests_per_minute=60,
        burst=1,
        max_keys=2,
    )

    limiter.consume("client-a")
    limiter.consume("client-b")
    limiter.consume("client-c")

    with session_scope() as session:
        records = session.query(APIRateLimitBucket).all()
        assert len(records) == 2
        assert all(len(record.key_hash) == 64 for record in records)
        assert all(record.key_hash != "client-c" for record in records)
