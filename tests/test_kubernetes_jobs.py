
import importlib

import pytest

jobs = importlib.import_module("app4-dashboard.api.kubernetes_jobs")


def test_stage_job_is_bounded_hardened_and_resource_aware():
    request = jobs.StageJobRequest(
        run_id="A_RUN/with unsafe characters",
        mission_id=42,
        organization_id="acme-survey",
        vol_id="quarry-001",
        workspace_prefix="organizations/acme-survey/missions/quarry-001",
        owner_subject="operator@example.test",
        stage="gaussian_training",
        resource_class="gpu-high-memory",
    )
    job = jobs.build_stage_job(
        request,
        jobs.StageJobConfig(
            namespace="drone-ai",
            image="registry.example/drone-colmap@sha256:" + "a" * 64,
            command=("python", "-m", "stage_executor"),
            runtime_class_name="nvidia",
            node_selector=(("droneai.io/gpu-architecture", "ampere"),),
            tolerations=(
                jobs.StageJobToleration(
                    key="nvidia.com/gpu",
                    operator="Equal",
                    value="present",
                    effect="NoSchedule",
                ),
            ),
            environment=(("S3_ENDPOINT", "https://s3.example"),),
            secret_environment=(
                jobs.SecretEnvironment("DATABASE_URL", "drone-ai-storage", "database-url"),
            ),
        ),
    )

    assert job["metadata"]["name"].startswith("droneai-a-run-with-unsafe-characters-")
    assert len(job["metadata"]["name"]) <= 63
    assert job["metadata"]["labels"]["droneai.run-id-hash"].isalnum()
    assert "unsafe" not in job["metadata"]["labels"]["droneai.run-id-hash"]
    assert job["spec"]["backoffLimit"] == 0
    assert "completionMode" not in job["spec"]
    assert "completions" not in job["spec"]
    assert "parallelism" not in job["spec"]
    assert job["spec"]["ttlSecondsAfterFinished"] == 3600
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["runtimeClassName"] == "nvidia"
    assert pod["nodeSelector"] == {
        "nvidia.com/gpu.present": "true",
        "droneai.io/gpu-vram-at-least-24gb": "true",
        "droneai.io/gpu-architecture": "ampere",
    }
    assert pod["tolerations"] == [
        {
            "key": "nvidia.com/gpu",
            "operator": "Equal",
            "value": "present",
            "effect": "NoSchedule",
        }
    ]
    container = pod["containers"][0]
    assert container["resources"]["requests"]["memory"] == "24Gi"
    assert container["resources"]["limits"]["memory"] == "64Gi"
    assert container["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {
        "/tmp",
        "/work",
        "/cache",
    }
    database = next(item for item in container["env"] if item["name"] == "DATABASE_URL")
    assert database["valueFrom"]["secretKeyRef"] == {
        "name": "drone-ai-storage",
        "key": "database-url",
    }
    environment = {item["name"]: item for item in container["env"]}
    assert environment["DRONEAI_ORGANIZATION_ID"]["value"] == "acme-survey"
    assert environment["DRONEAI_WORKSPACE_PREFIX"]["value"] == (
        "organizations/acme-survey/missions/quarry-001"
    )


def test_cpu_job_does_not_request_a_gpu():
    job = jobs.build_stage_job(
        jobs.StageJobRequest(
            run_id="run-1",
            mission_id=1,
            organization_id="acme-survey",
            vol_id="mission-1",
            workspace_prefix="organizations/acme-survey/missions/mission-1",
            owner_subject="owner",
            stage="rasterization",
            resource_class="cpu-standard",
        ),
        jobs.StageJobConfig(
            namespace="drone-ai",
            image="image@sha256:" + "b" * 64,
            command=("run",),
            runtime_class_name="nvidia",
        ),
    )

    limits = job["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert "nvidia.com/gpu" not in limits
    assert "runtimeClassName" not in job["spec"]["template"]["spec"]
    assert "nodeSelector" not in job["spec"]["template"]["spec"]


@pytest.mark.parametrize(
    ("volume", "expected"),
    (
        (
            jobs.StageJobWorkVolume(
                {"hostPath": {"path": "/mnt/j/.droneai/work", "type": "Directory"}}
            ),
            {"hostPath": {"path": "/mnt/j/.droneai/work", "type": "Directory"}},
        ),
        (
            jobs.StageJobWorkVolume(
                {"persistentVolumeClaim": {"claimName": "droneai-work"}}
            ),
            {"persistentVolumeClaim": {"claimName": "droneai-work"}},
        ),
    ),
)
def test_stage_job_uses_the_configured_work_volume(volume, expected):
    job = jobs.build_stage_job(
            jobs.StageJobRequest(
                run_id="run-work-volume",
                mission_id=1,
                organization_id="acme-survey",
                vol_id="mission-work-volume",
                workspace_prefix=(
                    "organizations/acme-survey/missions/mission-work-volume"
                ),
                owner_subject="owner",
            stage="rasterization",
            resource_class="gpu-high-memory",
        ),
        jobs.StageJobConfig(
            namespace="drone-ai",
            image="image@sha256:" + "1" * 64,
            command=("run",),
            work_volume=volume,
        ),
    )

    work = next(
        item
        for item in job["spec"]["template"]["spec"]["volumes"]
        if item["name"] == "work"
    )
    assert work == {"name": "work", **expected}


@pytest.mark.parametrize(
    "source",
    (
        {"hostPath": {"path": "/", "type": "Directory"}},
        {"hostPath": {"path": "/mnt/../unsafe", "type": "Directory"}},
        {"persistentVolumeClaim": {"claimName": "Not_Safe"}},
        {"emptyDir": {"sizeLimit": "unbounded"}},
    ),
)
def test_stage_job_rejects_unsafe_work_volumes(source):
    with pytest.raises(ValueError, match="Stage Job"):
        jobs.StageJobWorkVolume(source)


def test_job_rejects_resource_selector_conflicts_and_invalid_tolerations():
    request = jobs.StageJobRequest(
        run_id="run-selector-conflict",
        mission_id=1,
        organization_id="acme-survey",
        vol_id="mission-1",
        workspace_prefix="organizations/acme-survey/missions/mission-1",
        owner_subject="owner",
        stage="detection",
        resource_class="gpu-standard",
    )
    config = jobs.StageJobConfig(
        namespace="drone-ai",
        image="image@sha256:" + "c" * 64,
        command=("run",),
        node_selector=(("nvidia.com/gpu.present", "false"),),
    )
    with pytest.raises(ValueError, match="conflicts with resource class"):
        jobs.build_stage_job(request, config)
    with pytest.raises(ValueError, match="Exists tolerations"):
        jobs.StageJobToleration(
            key="nvidia.com/gpu",
            operator="Exists",
            value="present",
        )


def test_indexed_job_injects_a_bounded_completion_index():
    job = jobs.build_stage_job(
        jobs.StageJobRequest(
            run_id="detection-run",
            mission_id=1,
            organization_id="acme-survey",
            vol_id="mission-1",
            workspace_prefix="organizations/acme-survey/missions/mission-1",
            owner_subject="owner",
            stage="detection",
            resource_class="gpu-standard",
        ),
        jobs.StageJobConfig(
            namespace="drone-ai",
            image="image@sha256:" + "c" * 64,
            command=("run-shard",),
            indexed=jobs.IndexedJobConfig(completions=6, parallelism=2),
        ),
    )

    assert job["spec"]["completionMode"] == "Indexed"
    assert job["spec"]["completions"] == 6
    assert job["spec"]["parallelism"] == 2
    environment = {
        item["name"]: item
        for item in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert environment["DRONEAI_DETECTION_SHARD_INDEX"]["valueFrom"] == {
        "fieldRef": {
            "fieldPath": (
                "metadata.annotations['batch.kubernetes.io/job-completion-index']"
            )
        }
    }
    assert environment["DRONEAI_DETECTION_SHARD_COUNT"]["value"] == "6"


def test_indexed_job_rejects_unsafe_parallelism_and_reserved_environment():
    with pytest.raises(ValueError, match="completions"):
        jobs.IndexedJobConfig(completions=1, parallelism=1)
    with pytest.raises(ValueError, match="parallelism"):
        jobs.IndexedJobConfig(completions=4, parallelism=5)
    with pytest.raises(ValueError, match="non-reserved"):
        jobs.StageJobConfig(
            namespace="drone-ai",
            image="image@sha256:" + "d" * 64,
            command=("run",),
            environment=(("DRONEAI_DETECTION_SHARD_INDEX", "0"),),
        )


def test_job_name_suffix_creates_a_distinct_bounded_finalizer_identity():
    request = jobs.StageJobRequest(
        run_id="detection-run",
        mission_id=1,
        organization_id="acme-survey",
        vol_id="mission-1",
        workspace_prefix="organizations/acme-survey/missions/mission-1",
        owner_subject="owner",
        stage="detection",
        resource_class="gpu-standard",
    )
    config = jobs.StageJobConfig(
        namespace="drone-ai",
        image="image@sha256:" + "e" * 64,
        command=("run",),
        name_suffix="finalizer",
    )

    job = jobs.build_stage_job(request, config)

    assert job["metadata"]["name"] == jobs.stage_job_name(
        "detection-run-finalizer"
    )
    environment = job["spec"]["template"]["spec"]["containers"][0]["env"]
    assert next(
        item["value"] for item in environment if item["name"] == "DRONEAI_STAGE_RUN_ID"
    ) == "detection-run"
    with pytest.raises(ValueError, match="canonical DNS label"):
        jobs.StageJobConfig(
            namespace="drone-ai",
            image="image@sha256:" + "f" * 64,
            command=("run",),
            name_suffix="Not Safe",
        )
