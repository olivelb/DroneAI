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
        scope="tile",
        session_scope=session_scope,
        requests_per_minute=2,
        burst=2,
        clock=lambda: now[0],
    )
    replica_b = DatabaseTokenBucketRateLimiter(
        scope="tile",
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
    now = [datetime(2026, 8, 8, tzinfo=UTC)]
    limiter = DatabaseTokenBucketRateLimiter(
        scope="tile",
        session_scope=session_scope,
        requests_per_minute=60,
        burst=1,
        max_keys=2,
        clock=lambda: now[0],
    )

    limiter.consume("client-a")
    limiter.consume("client-b")
    limiter.consume("client-c")

    assert limiter.collect_expired() == 0  # Active quotas must survive pressure.
    now[0] += timedelta(minutes=6)
    assert limiter.collect_expired(batch_size=1) == 1
    with session_scope() as session:
        records = session.query(APIRateLimitBucket).all()
        assert len(records) == 2
        assert all(len(record.key_hash) == 64 for record in records)
        assert all(record.key_hash != "client-c" for record in records)


def test_tile_pressure_never_evicts_identity_buckets():
    session_scope = _session_scope()
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with session_scope() as session:
        session.bulk_insert_mappings(APIRateLimitBucket, [
            {"scope": scope, "key_hash": f"{i:064x}", "tokens": 0,
             "updated_at": now - timedelta(hours=1)}
            for scope, count in [("identity:peer", 50_000), ("identity:credential", 20_000)]
            for i in range(count)
        ])
    limiter = DatabaseTokenBucketRateLimiter(
        scope="tile", session_scope=session_scope, requests_per_minute=60,
        burst=1, max_keys=2, clock=lambda: now,
    )
    for i in range(10):
        limiter.consume(str(i))
    assert limiter.collect_expired() == 0
    now += timedelta(minutes=6)
    assert limiter.collect_expired(batch_size=3) == 3
    with session_scope() as session:
        for scope, count in [("identity:peer", 50_000), ("identity:credential", 20_000)]:
            query = session.query(APIRateLimitBucket).filter_by(scope=scope)
            assert query.count() == count
            assert query.filter(APIRateLimitBucket.tokens != 0).count() == 0
        assert session.query(APIRateLimitBucket).filter_by(scope="tile").count() == 7


def test_migration_legacy_adoption_preserves_exhausted_quota():
    session_scope = _session_scope()
    now = datetime(2026, 8, 8, tzinfo=UTC)
    with session_scope() as session:
        session.add(APIRateLimitBucket(scope="legacy", key_hash=DatabaseTokenBucketRateLimiter._key_hash("peer"),
                                       tokens=0, updated_at=now))
    limiter = DatabaseTokenBucketRateLimiter(scope="identity:peer", session_scope=session_scope,
                                            requests_per_minute=2, burst=2, clock=lambda: now)
    assert limiter.consume("peer") == 30.0
    with session_scope() as session:
        record = session.query(APIRateLimitBucket).one()
        assert record.scope == "identity:peer"
        assert record.tokens == 0


def test_identical_keys_in_different_scopes_have_independent_quotas():
    session_scope = _session_scope()
    now = datetime(2026, 8, 8, tzinfo=UTC)
    a, b = [DatabaseTokenBucketRateLimiter(scope=scope, session_scope=session_scope,
                                         requests_per_minute=1, burst=1, clock=lambda: now)
            for scope in ("identity:peer", "tile")]
    assert a.consume("same") is None
    assert b.consume("same") is None
    assert a.consume("same") == 60
    assert b.consume("same") == 60


def test_consume_does_not_count_or_delete(monkeypatch):
    from sqlalchemy.orm import Query
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP quota consumption must not count or delete buckets")
    monkeypatch.setattr(Query, "count", forbidden)
    monkeypatch.setattr(Session, "delete", forbidden)
    limiter = DatabaseTokenBucketRateLimiter(scope="tile", session_scope=_session_scope(),
                                            requests_per_minute=60, burst=1, max_keys=1)
    for key in ("one", "two", "three", "one"):
        limiter.consume(key)
