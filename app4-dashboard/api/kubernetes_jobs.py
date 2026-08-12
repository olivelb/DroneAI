"""Deterministic Kubernetes Job contract for bounded stage executors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, cast

from shared.stage_contracts import (
    RESOURCE_CLASSES,
    ResourceClassId,
    StageId,
    resource_class_node_selector,
)
from shared.tenancy import mission_prefix, validate_organization_id

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class StageJobRequest:
    run_id: str
    mission_id: int
    organization_id: str
    vol_id: str
    workspace_prefix: str
    owner_subject: str
    stage: StageId
    resource_class: ResourceClassId

    def __post_init__(self) -> None:
        organization_id = validate_organization_id(self.organization_id)
        if self.mission_id < 1 or not self.run_id or not self.owner_subject:
            raise ValueError("Stage Job mission binding is incomplete")
        if self.workspace_prefix != mission_prefix(organization_id, self.vol_id):
            raise ValueError(
                "Stage Job workspace prefix must match its durable tenant mission"
            )


@dataclass(frozen=True)
class SecretEnvironment:
    name: str
    secret_name: str
    secret_key: str


@dataclass(frozen=True)
class StageJobToleration:
    key: str
    operator: str = "Equal"
    value: str | None = None
    effect: str | None = None
    toleration_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("Stage Job toleration key must not be blank")
        if not isinstance(self.operator, str) or self.operator not in {
            "Equal",
            "Exists",
        }:
            raise ValueError("Stage Job toleration operator must be Equal or Exists")
        if self.value is not None and not isinstance(self.value, str):
            raise ValueError("Stage Job toleration value must be a string")
        if self.operator == "Exists" and self.value is not None:
            raise ValueError("Exists tolerations must not declare a value")
        if self.effect not in {None, "NoSchedule", "PreferNoSchedule", "NoExecute"}:
            raise ValueError("Stage Job toleration effect is invalid")
        if self.toleration_seconds is not None and (
            isinstance(self.toleration_seconds, bool)
            or not isinstance(self.toleration_seconds, int)
            or self.effect != "NoExecute"
            or self.toleration_seconds < 0
        ):
            raise ValueError(
                "Stage Job toleration seconds require a non-negative NoExecute toleration"
            )

    def manifest(self) -> JsonObject:
        result: JsonObject = {"key": self.key, "operator": self.operator}
        if self.value is not None:
            result["value"] = self.value
        if self.effect is not None:
            result["effect"] = self.effect
        if self.toleration_seconds is not None:
            result["tolerationSeconds"] = self.toleration_seconds
        return result


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
class StageJobWorkVolume:
    """Validated Kubernetes volume source selected from deployment config."""

    source: JsonObject

    def __post_init__(self) -> None:
        if len(self.source) != 1:
            raise ValueError("A Stage Job work volume requires exactly one source")
        kind, value = next(iter(self.source.items()))
        if kind == "hostPath":
            if not isinstance(value, dict) or set(value) != {"path", "type"}:
                raise ValueError("Stage Job hostPath work volumes require path and type")
            path = value.get("path")
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or path == "/"
                or ".." in PurePosixPath(path).parts
                or value.get("type") != "Directory"
            ):
                raise ValueError("Stage Job hostPath work volume is unsafe")
        elif kind == "persistentVolumeClaim":
            if not isinstance(value, dict) or set(value) != {"claimName"}:
                raise ValueError("Stage Job PVC work volumes require claimName")
            claim_name = value.get("claimName")
            if not isinstance(claim_name, str) or not re.fullmatch(
                r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?",
                claim_name,
            ):
                raise ValueError("Stage Job PVC claim name is invalid")
        elif kind == "emptyDir":
            if not isinstance(value, dict) or set(value) - {"sizeLimit"}:
                raise ValueError("Stage Job emptyDir work volume is invalid")
            size_limit = value.get("sizeLimit")
            if size_limit is not None and (
                not isinstance(size_limit, str)
                or not re.fullmatch(r"[1-9][0-9]*(?:Mi|Gi|Ti)", size_limit)
            ):
                raise ValueError("Stage Job emptyDir size limit is invalid")
        else:
            raise ValueError(f"Unsupported Stage Job work volume source: {kind}")

    def manifest(self) -> JsonObject:
        return deepcopy(self.source)


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
    tolerations: tuple[StageJobToleration, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    secret_environment: tuple[SecretEnvironment, ...] = ()
    indexed: IndexedJobConfig | None = None
    name_suffix: str | None = None
    work_volume: StageJobWorkVolume | None = None

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
            "DRONEAI_ORGANIZATION_ID",
            "DRONEAI_VOL_ID",
            "DRONEAI_WORKSPACE_PREFIX",
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
        {"name": "DRONEAI_ORGANIZATION_ID", "value": request.organization_id},
        {"name": "DRONEAI_VOL_ID", "value": request.vol_id},
        {"name": "DRONEAI_WORKSPACE_PREFIX", "value": request.workspace_prefix},
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
    work_volume = (
        {
            "name": "work",
            **config.work_volume.manifest(),
        }
        if config.work_volume is not None
        else {
            "name": "work",
            "emptyDir": {"sizeLimit": resources["ephemeral_storage_limit"]},
        }
    )
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
            work_volume,
            {
                "name": "cache",
                "emptyDir": {"sizeLimit": resources["ephemeral_storage_limit"]},
            },
        ],
    }
    node_selector = resource_class_node_selector(request.resource_class)
    for key, value in config.node_selector:
        existing = node_selector.get(key)
        if existing is not None and existing != value:
            raise ValueError(
                f"Stage Job node selector conflicts with resource class for {key}"
            )
        node_selector[key] = value
    if node_selector:
        pod_spec["nodeSelector"] = node_selector
    if config.tolerations:
        pod_spec["tolerations"] = [item.manifest() for item in config.tolerations]
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
