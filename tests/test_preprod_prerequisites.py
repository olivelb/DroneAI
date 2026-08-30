from __future__ import annotations

import base64
from typing import Any

import pytest

from scripts.deploy.verify_preprod_prerequisites import (
    REQUIRED_SECRET_KEYS,
    STAGE_SECRETS,
    verify_preprod_secrets,
)


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _secret_payloads() -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for secret_name, keys in REQUIRED_SECRET_KEYS.items():
        payloads[secret_name] = {
            "data": {key: _encoded(f"{secret_name}:{key}:value") for key in keys}
        }
    payloads["drone-ai-api-auth"]["data"]["api-keys.json"] = _encoded("[]")
    payloads["drone-ai-api-auth"]["data"]["session-secret"] = _encoded("s" * 32)
    payloads["drone-ai-api-auth"]["data"]["credential-pepper"] = _encoded("p" * 32)
    storage = payloads["drone-ai-storage-preprod"]["data"]
    storage["database-url"] = _encoded(
        "postgresql://operator:operator-pass@database.internal:5432/droneai"
    )
    storage["api-database-url"] = _encoded(
        "postgresql://api:api-pass@database.internal:5432/droneai"
    )
    storage["s3-access-key"] = _encoded("application-s3")
    payloads["drone-ai-backup-preprod"]["data"]["s3-access-key"] = _encoded(
        "backup-s3"
    )
    for index, secret_name in enumerate(STAGE_SECRETS.values()):
        payloads[secret_name]["data"]["stage-database-url"] = _encoded(
            f"postgresql://stage-{index}:pass@database.internal:5432/droneai"
        )
        payloads[secret_name]["data"]["s3-access-key"] = _encoded(f"stage-s3-{index}")
    return payloads


def _fake_kubectl(payloads: dict[str, dict[str, Any]]):
    def run(arguments: list[str]) -> Any:
        assert arguments[:4] == [
            "--kubeconfig",
            "/tmp/kubeconfig",
            "--namespace",
            "drone-ai-preprod",
        ]
        return payloads[arguments[arguments.index("secret") + 1]]

    return run


def test_preflight_verifies_every_required_secret_without_mutating_cluster() -> None:
    payloads = _secret_payloads()
    calls: list[list[str]] = []
    fake = _fake_kubectl(payloads)

    def recording_fake(arguments: list[str]) -> Any:
        calls.append(arguments)
        return fake(arguments)

    verified = verify_preprod_secrets(
        "drone-ai-preprod",
        "/tmp/kubeconfig",
        kubectl_json=recording_fake,
    )

    assert set(verified) == set(REQUIRED_SECRET_KEYS)
    assert len(verified) == 10
    assert all("get" in call and "secret" in call for call in calls)
    assert not any({"apply", "create", "patch", "replace"} & set(call) for call in calls)


def test_preflight_rejects_a_missing_required_key() -> None:
    payloads = _secret_payloads()
    del payloads["drone-ai-storage-preprod"]["data"]["api-database-url"]

    with pytest.raises(ValueError, match="api-database-url"):
        verify_preprod_secrets(
            "drone-ai-preprod",
            "/tmp/kubeconfig",
            kubectl_json=_fake_kubectl(payloads),
        )


def test_preflight_rejects_reused_stage_principals() -> None:
    payloads = _secret_payloads()
    first, second = list(STAGE_SECRETS.values())[:2]
    payloads[second]["data"]["s3-access-key"] = payloads[first]["data"]["s3-access-key"]

    with pytest.raises(ValueError, match="reuses S3 identity"):
        verify_preprod_secrets(
            "drone-ai-preprod",
            "/tmp/kubeconfig",
            kubectl_json=_fake_kubectl(payloads),
        )


@pytest.mark.parametrize(
    ("global_secret", "label"),
    [
        ("drone-ai-storage-preprod", "application"),
        ("drone-ai-backup-preprod", "backup"),
    ],
)
def test_preflight_rejects_stage_s3_identity_reused_from_global_principal(
    global_secret: str,
    label: str,
) -> None:
    payloads = _secret_payloads()
    stage_secret = next(iter(STAGE_SECRETS.values()))
    payloads[stage_secret]["data"]["s3-access-key"] = payloads[global_secret][
        "data"
    ]["s3-access-key"]

    with pytest.raises(ValueError, match=f"reuses S3 identity {label}"):
        verify_preprod_secrets(
            "drone-ai-preprod",
            "/tmp/kubeconfig",
            kubectl_json=_fake_kubectl(payloads),
        )


@pytest.mark.parametrize(("global_role", "label"), [("operator", "operator"), ("api", "api")])
def test_preflight_compares_database_principals_not_raw_urls(
    global_role: str,
    label: str,
) -> None:
    payloads = _secret_payloads()
    stage_secret = next(iter(STAGE_SECRETS.values()))
    payloads[stage_secret]["data"]["stage-database-url"] = _encoded(
        f"postgresql://{global_role}:different-pass@database.internal:5432/droneai"
    )

    with pytest.raises(ValueError, match=f"reuses PostgreSQL identity {label}"):
        verify_preprod_secrets(
            "drone-ai-preprod",
            "/tmp/kubeconfig",
            kubectl_json=_fake_kubectl(payloads),
        )


def test_preflight_rejects_stage_database_on_another_endpoint() -> None:
    payloads = _secret_payloads()
    stage_secret = next(iter(STAGE_SECRETS.values()))
    payloads[stage_secret]["data"]["stage-database-url"] = _encoded(
        "postgresql://stage-isolated:pass@other.internal:5432/droneai"
    )

    with pytest.raises(ValueError, match="must target the preproduction database"):
        verify_preprod_secrets(
            "drone-ai-preprod",
            "/tmp/kubeconfig",
            kubectl_json=_fake_kubectl(payloads),
        )


def test_preflight_rejects_incomplete_database_url() -> None:
    payloads = _secret_payloads()
    stage_secret = next(iter(STAGE_SECRETS.values()))
    payloads[stage_secret]["data"]["stage-database-url"] = _encoded("not-a-url")

    with pytest.raises(ValueError, match="complete PostgreSQL URL"):
        verify_preprod_secrets(
            "drone-ai-preprod",
            "/tmp/kubeconfig",
            kubectl_json=_fake_kubectl(payloads),
        )
