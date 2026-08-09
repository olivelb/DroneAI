"""Deterministic Kubernetes Job contract for bounded stage executors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, cast

from shared.stage_contracts import RESOURCE_CLASSES, ResourceClassId, StageId

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class StageJobRequest:
    run_id: str
    mission_id: int
    vol_id: str
    owner_subject: str
    stage: StageId
    resource_class: ResourceClassId


@dataclass(frozen=True)
class StageJobConfig:
    namespace: str
    image: str
    command: tuple[str, ...]
    service_account_name: str = "stage-job-sa"
    active_deadline_seconds: int = 86_400
    ttl_seconds_after_finished: int = 3_600

    def __post_init__(self) -> None:
        if not self.image or not self.command:
            raise ValueError("A stage Job requires an image and command")
        if self.active_deadline_seconds < 1 or self.ttl_seconds_after_finished < 0:
            raise ValueError("Stage Job deadlines must be non-negative")


def stage_job_name(run_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", run_id.lower()).strip("-")
    digest = hashlib.sha256(run_id.encode()).hexdigest()[:10]
    prefix = normalized[:41].rstrip("-") or "run"
    return f"droneai-{prefix}-{digest}"


def build_stage_job(request: StageJobRequest, config: StageJobConfig) -> JsonObject:
    resources = RESOURCE_CLASSES[request.resource_class]
    requests = {
        "cpu": resources["cpu_request"],
        "memory": resources["memory_request"],
        "ephemeral-storage": resources["ephemeral_storage_request"],
    }
    limits = {
        "cpu": resources["cpu_limit"],
        "memory": resources["memory_limit"],
        "ephemeral-storage": resources["ephemeral_storage_limit"],
    }
    if resources["gpu_count"]:
        limits["nvidia.com/gpu"] = str(resources["gpu_count"])
    name = stage_job_name(request.run_id)
    run_id_hash = hashlib.sha256(request.run_id.encode()).hexdigest()[:16]
    labels = {
        "app.kubernetes.io/name": "droneai-stage",
        "app.kubernetes.io/part-of": "drone-ai",
        "droneai.stage": request.stage,
        "droneai.resource-class": request.resource_class,
        "droneai.run-id-hash": run_id_hash,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": config.namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": config.active_deadline_seconds,
            "ttlSecondsAfterFinished": config.ttl_seconds_after_finished,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": config.service_account_name,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "stage",
                            "image": config.image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": list(config.command),
                            "env": [
                                {"name": "DRONEAI_STAGE_RUN_ID", "value": request.run_id},
                                {"name": "DRONEAI_MISSION_ID", "value": str(request.mission_id)},
                                {"name": "DRONEAI_VOL_ID", "value": request.vol_id},
                                {"name": "DRONEAI_STAGE", "value": request.stage},
                                {"name": "DRONEAI_OWNER_SUBJECT", "value": request.owner_subject},
                                {"name": "DRONEAI_RESOURCE_CLASS", "value": request.resource_class},
                            ],
                            "resources": {"requests": requests, "limits": limits},
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        },
    }


class KubernetesJobClient:
    """Minimal in-cluster batch/v1 client with no third-party dependency."""

    def __init__(self, namespace: str, timeout_seconds: float = 5.0) -> None:
        self.namespace = namespace
        self.timeout_seconds = timeout_seconds
        self._token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        self._ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self._base_url = f"https://{host}:{port}/apis/batch/v1/namespaces/{namespace}/jobs"

    def _call(self, method: str, url: str, payload: JsonObject | None = None) -> JsonObject:
        with open(self._token_path, encoding="utf-8") as handle:
            token = handle.read().strip()
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        context = ssl.create_default_context(cafile=self._ca_path)
        try:
            with urllib.request.urlopen(
                request,
                context=context,
                timeout=self.timeout_seconds,
            ) as response:
                return cast(JsonObject, json.load(response))
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise RuntimeError(f"Kubernetes API HTTP {error.code}: {detail}") from error

    def create(self, job: JsonObject) -> JsonObject:
        return self._call("POST", self._base_url, job)

    def get(self, name: str) -> JsonObject:
        return self._call("GET", f"{self._base_url}/{name}")

    def delete(self, name: str) -> JsonObject:
        return self._call(
            "DELETE",
            f"{self._base_url}/{name}",
            {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Background"},
        )
