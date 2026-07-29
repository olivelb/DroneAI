"""Read-only Kubernetes pod status adapter."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

POD_NAMES = (
    "kafka-broker",
    "postgres",
    "minio",
    "colmap-worker",
    "ia-worker",
    "processing-worker",
    "dashboard-api",
    "dashboard-frontend",
)


def compose_service_states() -> list[dict]:
    """Describe the services managed by the local Compose deployment.

    Compose itself remains the source of truth for health checks. This adapter
    gives the dashboard a meaningful local worker inventory instead of
    presenting Kubernetes credential errors in Compose mode.
    """
    return [
        {
            "name": name,
            "phase": "running",
            "ready": "1/1",
            "restarts": 0,
            "reason": "docker-compose",
            "last_terminated_reason": None,
            "last_terminated_exit_code": None,
            "oom_killed": False,
            "memory_limit": None,
            "memory_request": None,
        }
        for name in POD_NAMES
    ]


def fallback_pod_states() -> list[dict]:
    return [
        {
            "name": name,
            "phase": "unknown",
            "ready": None,
            "restarts": None,
            "reason": "unavailable",
            "last_terminated_reason": None,
            "last_terminated_exit_code": None,
            "oom_killed": False,
            "memory_limit": None,
            "memory_request": None,
        }
        for name in POD_NAMES
    ]


def _pod_payload(item: dict) -> dict:
    status = item.get("status", {})
    container_statuses = status.get("containerStatuses", [])
    container_specs = item.get("spec", {}).get("containers", [])
    waiting_reason = None
    last_terminated_reason = None
    last_terminated_exit_code = None
    oom_killed = False
    for entry in container_statuses:
        state = entry.get("state", {})
        if "waiting" in state:
            waiting_reason = state["waiting"].get("reason")
        terminated = entry.get("lastState", {}).get("terminated") or state.get(
            "terminated"
        )
        if terminated and last_terminated_reason is None:
            last_terminated_reason = terminated.get("reason")
            last_terminated_exit_code = terminated.get("exitCode")
            oom_killed = (
                last_terminated_reason == "OOMKilled"
                or last_terminated_exit_code == 137
            )
    resources = (
        container_specs[0].get("resources", {}) if container_specs else {}
    )
    ready_count = sum(1 for entry in container_statuses if entry.get("ready"))
    total_count = len(container_statuses)
    return {
        "name": item.get("metadata", {}).get("name", "unknown"),
        "phase": status.get("phase", "unknown").lower(),
        "ready": f"{ready_count}/{total_count}" if total_count else None,
        "restarts": sum(
            entry.get("restartCount", 0) for entry in container_statuses
        ),
        "reason": waiting_reason or status.get("reason"),
        "last_terminated_reason": last_terminated_reason,
        "last_terminated_exit_code": last_terminated_exit_code,
        "oom_killed": oom_killed,
        "memory_limit": resources.get("limits", {}).get("memory"),
        "memory_request": resources.get("requests", {}).get("memory"),
    }


def get_pod_states() -> dict:
    if os.getenv("DRONEAI_RUNTIME_MODE", "").strip().lower() == "compose":
        return {
            "available": True,
            "pods": compose_service_states(),
            "error": None,
        }

    namespace = os.getenv("POD_NAMESPACE", "drone-ai")
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    api_host = os.getenv(
        "KUBERNETES_SERVICE_HOST",
        "kubernetes.default.svc",
    )
    api_port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    if not os.path.exists(token_path) or not os.path.exists(ca_path):
        return {
            "available": False,
            "pods": fallback_pod_states(),
            "error": "service account credentials unavailable",
        }

    try:
        with open(token_path, encoding="utf-8") as handle:
            token = handle.read().strip()
        url = (
            f"https://{api_host}:{api_port}/api/v1/namespaces/"
            f"{namespace}/pods"
        )
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        context = ssl.create_default_context(cafile=ca_path)
        with urllib.request.urlopen(
            request,
            context=context,
            timeout=5,
        ) as response:
            payload = json.load(response)
        pods = sorted(
            (_pod_payload(item) for item in payload.get("items", [])),
            key=lambda pod: pod["name"],
        )
        return {"available": True, "pods": pods, "error": None}
    except urllib.error.HTTPError as error:
        message = f"kubernetes API HTTP {error.code}"
    except Exception as error:
        message = str(error)
    return {
        "available": False,
        "pods": fallback_pod_states(),
        "error": message,
    }
