from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


leadership = importlib.import_module("app4-dashboard.api.control_leadership")


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeConnection:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.closed = False

    def execute(self, statement, parameters=None):
        rendered = str(statement)
        self.statements.append((rendered, parameters))
        if "pg_try_advisory_lock" in rendered:
            return FakeResult(self.acquired)
        return FakeResult(True)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, connection, dialect="postgresql"):
        self.connection = connection
        self.dialect = SimpleNamespace(name=dialect)

    def connect(self):
        return self.connection


def test_control_leader_configuration_is_strict_and_protected(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "development")
    monkeypatch.delenv("DRONEAI_CONTROL_LEADER_ELECTION", raising=False)
    assert leadership.control_leader_election_enabled() is False

    monkeypatch.setenv("DRONEAI_CONTROL_LEADER_ELECTION", "true")
    assert leadership.control_leader_election_enabled() is True
    monkeypatch.setenv("DRONEAI_CONTROL_LEADER_ELECTION", "sometimes")
    with pytest.raises(RuntimeError, match="must be true or false"):
        leadership.control_leader_election_enabled()

    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_CONTROL_LEADER_ELECTION", "false")
    with pytest.raises(RuntimeError, match="require control-worker"):
        leadership.control_leader_election_enabled()


@pytest.mark.parametrize("value", ["invalid", "0", "61"])
def test_control_leader_poll_interval_is_bounded(monkeypatch, value):
    monkeypatch.setenv("DRONEAI_CONTROL_LEADER_POLL_SECONDS", value)
    with pytest.raises(RuntimeError, match="POLL_SECONDS"):
        leadership.control_leader_poll_seconds()


def test_control_leadership_holds_checks_and_releases_one_connection():
    connection = FakeConnection()

    lease = leadership.try_acquire_control_leadership(
        FakeEngine(connection)
    )

    assert lease is not None
    lease.raise_if_unhealthy()
    lease.release()
    lease.release()
    statements = [statement for statement, _parameters in connection.statements]
    assert sum("pg_try_advisory_lock" in item for item in statements) == 1
    assert sum("pg_advisory_unlock" in item for item in statements) == 1
    assert "SELECT 1" in statements
    assert connection.commits == 3
    assert connection.closed is True


def test_control_leadership_closes_a_follower_connection():
    connection = FakeConnection(acquired=False)

    lease = leadership.try_acquire_control_leadership(
        FakeEngine(connection)
    )

    assert lease is None
    assert connection.closed is True


def test_control_leadership_requires_postgres():
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        leadership.try_acquire_control_leadership(
            FakeEngine(FakeConnection(), dialect="sqlite")
        )
