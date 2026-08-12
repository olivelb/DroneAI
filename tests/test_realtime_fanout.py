import importlib
from types import SimpleNamespace

import pytest


realtime = importlib.import_module("app4-dashboard.api.realtime")


def test_status_consumer_group_is_unique_per_api_pod():
    assert realtime.status_consumer_group("dashboard-api-a") == (
        "dashboard-api-realtime-dashboard-api-a"
    )


def test_status_audience_rejects_cross_organization_event(monkeypatch):
    observed = []

    def audience(vol_id, organization_id):
        observed.append((vol_id, organization_id))
        return None

    monkeypatch.setattr(realtime, "get_mission_audience", audience)

    with pytest.raises(LookupError, match="does not match"):
        realtime.mission_owner_subject("mission-1", "tenant-b")

    assert observed == [("mission-1", "tenant-b")]
    assert realtime.status_consumer_group("dashboard-api-b") != (
        realtime.status_consumer_group("dashboard-api-a")
    )


def test_status_audience_rejects_unknown_legacy_event(monkeypatch):
    observed = []

    def audience(vol_id, organization_id):
        observed.append((vol_id, organization_id))
        return None

    monkeypatch.setattr(realtime, "get_mission_audience", audience)

    with pytest.raises(LookupError, match="does not match a legacy mission"):
        realtime.mission_owner_subject("unknown-legacy-mission")

    assert observed == [("unknown-legacy-mission", None)]


def test_status_audience_accepts_known_legacy_mission(monkeypatch):
    monkeypatch.setattr(
        realtime,
        "get_mission_audience",
        lambda vol_id, organization_id: (
            "legacy-unassigned",
            "legacy-operator",
        ),
    )

    assert realtime.mission_owner_subject("legacy-mission") == (
        "legacy-unassigned:legacy-operator"
    )


def test_duplicate_state_receipts_are_still_fanned_out_locally(monkeypatch):
    calls = {"inbox_groups": [], "consumer_groups": [], "broadcasts": []}

    def process_inbox(_scope, *, consumer_group, **_kwargs):
        calls["inbox_groups"].append(consumer_group)
        return "duplicate"

    def process_message(*, consumer_group, handler, **_kwargs):
        calls["consumer_groups"].append(consumer_group)
        handler({"event_id": "status-1", "event_type": "status"})
        return True

    class Hub:
        @staticmethod
        def remember(event, _owner_subject):
            return event["event_id"]

        @staticmethod
        async def broadcast(payload, _owner_subject):
            calls["broadcasts"].append(payload)

    def submit(coroutine, _loop):
        try:
            coroutine.send(None)
        except StopIteration:
            pass
        return SimpleNamespace(result=lambda timeout: None)

    monkeypatch.setattr(realtime, "process_inbox_transaction", process_inbox)
    monkeypatch.setattr(realtime, "process_message", process_message)
    monkeypatch.setattr(
        realtime,
        "mission_owner_subject",
        lambda _vol_id, _organization_id=None: "owner-1",
    )
    monkeypatch.setattr(realtime.asyncio, "run_coroutine_threadsafe", submit)

    succeeded = realtime.handle_status_message(
        consumer=SimpleNamespace(),
        producer=SimpleNamespace(),
        message=SimpleNamespace(),
        hub=Hub(),
        loop=SimpleNamespace(),
        consumer_group="dashboard-api-realtime-pod-b",
    )

    assert succeeded is True
    assert calls == {
        "inbox_groups": ["dashboard-api-status-state"],
        "consumer_groups": ["dashboard-api-realtime-pod-b"],
        "broadcasts": ["status-1"],
    }
