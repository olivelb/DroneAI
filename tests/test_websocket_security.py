from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

security = importlib.import_module("app4-dashboard.api.security")
realtime = importlib.import_module("app4-dashboard.api.realtime")


class FakeLimiter:
    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        self.keys: list[str] = []

    def consume(self, key: str) -> float | None:
        self.keys.append(key)
        return self.retry_after


class FakeWebSocket:
    def __init__(
        self,
        *,
        origin: str = "https://app.example",
        token: str = "credential-token",
        peer: str = "203.0.113.10",
        received: list[str] | None = None,
    ):
        self.headers = {"origin": origin} if origin else {}
        self.cookies = {security.SESSION_COOKIE_NAME: token} if token else {}
        self.query_params: dict[str, str] = {}
        self.client = SimpleNamespace(host=peer)
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.messages: list[str] = []
        self.received = list(received or [])

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)

    async def send_text(self, message: str) -> None:
        self.messages.append(message)

    async def receive_text(self) -> str:
        if self.received:
            return self.received.pop(0)
        return "client-ping"


def _principal(*, role: str = "viewer", auth_version: int = 1):
    return security.Principal(
        subject="member@example.com",
        role=role,
        organization_id="tenant-a",
        member_id="member-a",
        credential_id="credential-a",
        auth_version=auth_version,
        authentication_method="database",
    )


def test_websocket_rejects_untrusted_origin_before_authentication(monkeypatch):
    socket = FakeWebSocket(origin="https://attacker.example")
    monkeypatch.setattr(security, "is_production", lambda: True)
    monkeypatch.setattr(
        security,
        "configured_cors_origins",
        lambda: ["https://app.example"],
    )
    monkeypatch.setattr(
        security,
        "authenticate_token",
        lambda _token: (_ for _ in ()).throw(AssertionError("must not authenticate")),
    )

    authorization = asyncio.run(security.authorize_websocket(socket))

    assert authorization is None
    assert socket.closed == (4403, "Untrusted request origin")


def test_websocket_rate_limits_before_database_authentication(monkeypatch):
    socket = FakeWebSocket()
    peer_limiter = FakeLimiter(retry_after=2.0)
    monkeypatch.setattr(security, "is_production", lambda: True)
    monkeypatch.setattr(
        security,
        "configured_cors_origins",
        lambda: ["https://app.example"],
    )
    monkeypatch.setattr(security, "identity_peer_rate_limiter", peer_limiter)
    monkeypatch.setattr(
        security,
        "authenticate_token",
        lambda _token: (_ for _ in ()).throw(AssertionError("must not authenticate")),
    )

    authorization = asyncio.run(security.authorize_websocket(socket))

    assert authorization is None
    assert peer_limiter.keys == ["identity:peer:203.0.113.10"]
    assert socket.closed == (4429, "Identity rate limit exceeded")


def test_websocket_session_uses_public_credential_bucket_before_auth(monkeypatch):
    monkeypatch.setenv("DRONEAI_SESSION_SECRET", "s" * 32)
    token = security.issue_session_token(_principal(), max_age_seconds=300)
    socket = FakeWebSocket(token=token)
    peer_limiter = FakeLimiter()
    credential_limiter = FakeLimiter()
    monkeypatch.setattr(security, "is_production", lambda: True)
    monkeypatch.setattr(
        security,
        "configured_cors_origins",
        lambda: ["https://app.example"],
    )
    monkeypatch.setattr(security, "identity_peer_rate_limiter", peer_limiter)
    monkeypatch.setattr(
        security,
        "identity_credential_rate_limiter",
        credential_limiter,
    )
    monkeypatch.setattr(security, "authenticate_token", lambda _token: _principal())

    authorization = asyncio.run(security.authorize_websocket(socket))

    assert authorization is not None
    assert credential_limiter.keys == [
        "identity:credential:tenant:credential-a"
    ]


def test_websocket_authorization_detects_role_and_revocation_changes(monkeypatch):
    authorization = security.WebSocketAuthorization(
        principal=_principal(),
        token="credential-token",
        peer="203.0.113.10",
    )
    monkeypatch.setattr(security, "authenticate_token", lambda _token: _principal())
    assert security.websocket_authorization_status(authorization) == "valid"

    monkeypatch.setattr(
        security,
        "authenticate_token",
        lambda _token: _principal(role="operator", auth_version=2),
    )
    assert security.websocket_authorization_status(authorization) == "forbidden"

    monkeypatch.setattr(security, "authenticate_token", lambda _token: None)
    assert security.websocket_authorization_status(authorization) == "unauthenticated"


def test_status_hub_enforces_credential_quota_and_partitions_history():
    async def exercise():
        hub = realtime.StatusHub(
            history_size=2,
            max_history_audiences=1,
            max_history_messages=2,
            max_connections=10,
            max_connections_per_organization=10,
            max_connections_per_credential=1,
            max_connections_per_peer=10,
        )
        first = FakeWebSocket(peer="peer-a")
        duplicate = FakeWebSocket(peer="peer-b")
        assert await hub.connect(
            first,
            "tenant-a:alice",
            organization_id="tenant-a",
            credential_id="credential-a",
            peer="peer-a",
        )
        assert not await hub.connect(
            duplicate,
            "tenant-a:alice",
            organization_id="tenant-a",
            credential_id="credential-a",
            peer="peer-b",
        )
        hub.remember({"sequence": 1}, "tenant-a:alice")
        hub.remember({"sequence": 2}, "tenant-a:bob")
        replay = FakeWebSocket(peer="peer-c")
        assert await hub.connect(
            replay,
            "tenant-a:alice",
            organization_id="tenant-a",
            credential_id="credential-b",
            peer="peer-c",
        )
        return duplicate, replay

    duplicate, replay = asyncio.run(exercise())
    assert duplicate.closed == (4429, "WebSocket connection quota exceeded")
    assert replay.messages == []


def test_status_connection_closes_when_authorization_changes(monkeypatch):
    socket = FakeWebSocket(received=["client-ping"])
    authorization = security.WebSocketAuthorization(
        principal=_principal(),
        token="credential-token",
        peer="203.0.113.10",
    )
    hub = realtime.StatusHub(
        max_history_audiences=10,
        max_history_messages=10,
        max_connections=10,
        max_connections_per_organization=10,
        max_connections_per_credential=10,
        max_connections_per_peer=10,
    )
    ticks = iter([0.0, 0.0, 2.0, 2.0, 2.0])
    monkeypatch.setattr(realtime, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        realtime,
        "_positive_setting",
        lambda name, default: 1 if name == "DRONEAI_WS_REVALIDATE_SECONDS" else default,
    )
    monkeypatch.setattr(
        realtime,
        "websocket_authorization_status",
        lambda _authorization: "forbidden",
    )

    asyncio.run(
        realtime.serve_status_connection(
            socket,
            authorization,
            hub=hub,
        )
    )

    assert socket.closed == (4403, "Authorization changed")
    assert hub.connections == {}
