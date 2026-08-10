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
class SecretEnvironment:
    name: str
    secret_name: str
    secret_key: str


@dataclass(frozen=True)
class IndexedJobConfig:
    completions: int
    parallelism: int

    def __post_init__(self) -> None:
        if not 2 <= self.completions <= 256:
            raise ValueError("Indexed Job completions must be between 2 and 256")
        if not 1 <= self.parallelism <= self.completions:
            raise ValueError("Indexed Job parallelism must be within its completions")


@dataclass(frozen=True)
class StageJobConfig:
    namespace: str
    image: str
    command: tuple[str, ...]
    service_account_name: str = "stage-job-sa"
    active_deadline_seconds: int = 86_400
    ttl_seconds_after_finished: int = 3_600
    runtime_class_name: str | None = None
    node_selector: tuple[tuple[str, str], ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    secret_environment: tuple[SecretEnvironment, ...] = ()
    indexed: IndexedJobConfig | None = None
    name_suffix: str | None = None

    def __post_init__(self) -> None:
        if not self.image or not self.command:
            raise ValueError("A stage Job requires an image and command")
        if self.active_deadline_seconds < 1 or self.ttl_seconds_after_finished < 0:
            raise ValueError("Stage Job deadlines must be non-negative")
        if self.runtime_class_name is not None and not self.runtime_class_name.strip():
            raise ValueError("Stage Job runtime class must not be blank")
        if self.name_suffix is not None and not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?",
            self.name_suffix,
        ):
            raise ValueError("Stage Job name suffix must be a canonical DNS label")
        reserved = {
            "DRONEAI_STAGE_RUN_ID",
            "DRONEAI_MISSION_ID",
            "DRONEAI_VOL_ID",
            "DRONEAI_STAGE",
            "DRONEAI_OWNER_SUBJECT",
            "DRONEAI_RESOURCE_CLASS",
            "DRONEAI_DETECTION_SHARD_INDEX",
            "DRONEAI_DETECTION_SHARD_COUNT",
        }
        names = [name for name, _value in self.environment]
        names.extend(item.name for item in self.secret_environment)
        if len(names) != len(set(names)) or reserved.intersection(names):
            raise ValueError("Stage Job environment names must be unique and non-reserved")
        if any(
            not item.name or not item.secret_name or not item.secret_key
            for item in self.secret_environment
        ):
            raise ValueError("Stage Job secret references must be complete")


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
    name_identity = (
        f"{request.run_id}-{config.name_suffix}"
        if config.name_suffix is not None
        else request.run_id
    )
    name = stage_job_name(name_identity)
    run_id_hash = hashlib.sha256(request.run_id.encode()).hexdigest()[:16]
    environment: list[JsonObject] = [
        {"name": "DRONEAI_STAGE_RUN_ID", "value": request.run_id},
        {"name": "DRONEAI_MISSION_ID", "value": str(request.mission_id)},
        {"name": "DRONEAI_VOL_ID", "value": request.vol_id},
        {"name": "DRONEAI_STAGE", "value": request.stage},
        {"name": "DRONEAI_OWNER_SUBJECT", "value": request.owner_subject},
        {"name": "DRONEAI_RESOURCE_CLASS", "value": request.resource_class},
    ]
    environment.extend(
        {"name": name, "value": value} for name, value in config.environment
    )
    environment.extend(
        {
            "name": item.name,
            "valueFrom": {
                "secretKeyRef": {
                    "name": item.secret_name,
                    "key": item.secret_key,
                }
            },
        }
        for item in config.secret_environment
    )
    if config.indexed is not None:
        environment.extend(
            (
                {
                    "name": "DRONEAI_DETECTION_SHARD_INDEX",
                    "valueFrom": {
                        "fieldRef": {
                            "fieldPath": (
                                "metadata.annotations['batch.kubernetes.io/"
                                "job-completion-index']"
                            )
                        }
                    },
                },
                {
                    "name": "DRONEAI_DETECTION_SHARD_COUNT",
                    "value": str(config.indexed.completions),
                },
            )
        )
    labels = {
        "app.kubernetes.io/name": "droneai-stage",
        "app.kubernetes.io/part-of": "drone-ai",
        "droneai.stage": request.stage,
        "droneai.resource-class": request.resource_class,
        "droneai.run-id-hash": run_id_hash,
    }
    pod_spec: JsonObject = {
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
                "env": environment,
                "resources": {"requests": requests, "limits": limits},
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
                "volumeMounts": [
                    {"name": "tmp", "mountPath": "/tmp"},
                    {"name": "work", "mountPath": "/work"},
                    {"name": "cache", "mountPath": "/cache"},
                ],
            }
        ],
        "volumes": [
            {"name": "tmp", "emptyDir": {}},
            {
                "name": "work",
                "emptyDir": {"sizeLimit": resources["ephemeral_storage_limit"]},
            },
            {
                "name": "cache",
                "emptyDir": {"sizeLimit": resources["ephemeral_storage_limit"]},
            },
        ],
    }
    if config.node_selector:
        pod_spec["nodeSelector"] = dict(config.node_selector)
    if resources["gpu_count"] and config.runtime_class_name:
        pod_spec["runtimeClassName"] = config.runtime_class_name
    job_spec: JsonObject = {
        "backoffLimit": 0,
        "activeDeadlineSeconds": config.active_deadline_seconds,
        "ttlSecondsAfterFinished": config.ttl_seconds_after_finished,
        "template": {
            "metadata": {"labels": labels},
            "spec": pod_spec,
        },
    }
    if config.indexed is not None:
        job_spec.update(
            {
                "completionMode": "Indexed",
                "completions": config.indexed.completions,
                "parallelism": config.indexed.parallelism,
            }
        )
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": config.namespace, "labels": labels},
        "spec": job_spec,
    }


class KubernetesApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"Kubernetes API HTTP {status_code}: {detail}")


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
            raise KubernetesApiError(error.code, detail) from error

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
