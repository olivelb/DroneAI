"""Read-only verification of externally provisioned OVH preproduction Secrets."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import subprocess
from collections.abc import Callable
from typing import Any, Final

STAGE_SECRETS: Final = {
    "reconstruction": "drone-ai-stage-reconstruction-preprod",
    "gaussian_training": "drone-ai-stage-gaussian-training-preprod",
    "gaussian_filtering": "drone-ai-stage-gaussian-filtering-preprod",
    "rasterization": "drone-ai-stage-rasterization-preprod",
    "detection": "drone-ai-stage-detection-preprod",
    "gaussian_viewer": "drone-ai-stage-gaussian-viewer-preprod",
}
REQUIRED_SECRET_KEYS: Final = {
    "drone-ai-postgres": ("password",),
    "drone-ai-storage-preprod": (
        "s3-access-key",
        "s3-secret-key",
        "database-url",
        "api-database-url",
    ),
    "drone-ai-backup-preprod": ("s3-access-key", "s3-secret-key"),
    "drone-ai-api-auth": (
        "api-keys.json",
        "session-secret",
        "credential-pepper",
    ),
    **{
        secret_name: ("stage-database-url", "s3-access-key", "s3-secret-key")
        for secret_name in STAGE_SECRETS.values()
    },
}

JsonCommand = Callable[[list[str]], Any]


def _kubectl_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        ["kubectl", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _decode_secret(name: str, payload: Any) -> dict[str, bytes]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ValueError(f"{name}: invalid Kubernetes Secret response")
    decoded: dict[str, bytes] = {}
    data = payload["data"]
    for key in REQUIRED_SECRET_KEYS[name]:
        encoded = data.get(key)
        if not isinstance(encoded, str) or not encoded:
            raise ValueError(f"{name}: missing non-empty key {key}")
        try:
            value = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError(f"{name}: key {key} is not valid base64") from error
        if not value:
            raise ValueError(f"{name}: key {key} decodes to an empty value")
        decoded[key] = value
    return decoded


def _reject_reused_stage_identities(secrets: dict[str, dict[str, bytes]]) -> None:
    for key in ("stage-database-url", "s3-access-key"):
        owners: dict[bytes, str] = {}
        for stage, secret_name in STAGE_SECRETS.items():
            value = secrets[secret_name][key]
            previous = owners.get(value)
            if previous is not None:
                raise ValueError(
                    f"Stage identities {previous} and {stage} reuse {key}"
                )
            owners[value] = stage


def verify_preprod_secrets(
    namespace: str,
    kubeconfig: str,
    *,
    kubectl_json: JsonCommand = _kubectl_json,
) -> list[str]:
    if not namespace or any(character.isspace() for character in namespace):
        raise ValueError("namespace must be non-empty and contain no whitespace")
    prefix = ["--kubeconfig", kubeconfig, "--namespace", namespace]
    decoded: dict[str, dict[str, bytes]] = {}
    for secret_name in REQUIRED_SECRET_KEYS:
        payload = kubectl_json(
            [*prefix, "get", "secret", secret_name, "--output", "json"]
        )
        decoded[secret_name] = _decode_secret(secret_name, payload)

    auth = decoded["drone-ai-api-auth"]
    for key in ("session-secret", "credential-pepper"):
        if len(auth[key]) < 32:
            raise ValueError(f"drone-ai-api-auth: {key} must contain at least 32 bytes")
    try:
        api_keys = json.loads(auth["api-keys.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("drone-ai-api-auth: api-keys.json is invalid JSON") from error
    if not isinstance(api_keys, list):
        raise ValueError("drone-ai-api-auth: api-keys.json must contain a JSON array")

    _reject_reused_stage_identities(decoded)
    return list(decoded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--kubeconfig", required=True)
    args = parser.parse_args()
    verified = verify_preprod_secrets(args.namespace, args.kubeconfig)
    print(f"Verified {len(verified)} externally managed Secrets in {args.namespace}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
