from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace


gcp_audit = import_module("app4-dashboard.api.gcp_audit")


class RecordingSession:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)


def test_records_gcp_audit_with_public_targets_preserved_in_snapshots():
    session = RecordingSession()
    gcp_set = SimpleNamespace(id=11, mission_id=7)
    point = SimpleNamespace(id=12)
    observation = SimpleNamespace(id=13)

    gcp_audit.record_gcp_audit(
        session,
        gcp_set,
        actor_subject="operator@example.test",
        action="observation_updated",
        before_state={"observation_id": "obs-public", "status": "candidate"},
        after_state={"observation_id": "obs-public", "status": "marked"},
        point=point,
        observation=observation,
    )

    event = session.added[0]
    assert event.gcp_set_id == 11
    assert event.gcp_point_id == 12
    assert event.gcp_observation_id == 13
    assert event.before_state["status"] == "candidate"
    assert event.after_state["status"] == "marked"


def test_serializes_human_and_machine_readable_audit_event():
    event = SimpleNamespace(
        event_id="event-1",
        action="point_updated",
        actor_subject="operator",
        point=SimpleNamespace(point_id="point-1"),
        observation=None,
        before_state={"version": 1},
        after_state={"version": 2},
        created_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    payload = gcp_audit.audit_event_json(event)

    assert payload["point_id"] == "point-1"
    assert payload["observation_id"] is None
    assert payload["created_at"] == "2026-08-10T12:00:00+00:00"
