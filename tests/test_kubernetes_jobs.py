import importlib

jobs = importlib.import_module("app4-dashboard.api.kubernetes_jobs")


def test_stage_job_is_bounded_hardened_and_resource_aware():
    request = jobs.StageJobRequest(
        run_id="A_RUN/with unsafe characters",
        mission_id=42,
        vol_id="quarry-001",
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
        ),
    )

    assert job["metadata"]["name"].startswith("droneai-a-run-with-unsafe-characters-")
    assert len(job["metadata"]["name"]) <= 63
    assert job["metadata"]["labels"]["droneai.run-id-hash"].isalnum()
    assert "unsafe" not in job["metadata"]["labels"]["droneai.run-id-hash"]
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["ttlSecondsAfterFinished"] == 3600
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    container = pod["containers"][0]
    assert container["resources"]["requests"]["memory"] == "24Gi"
    assert container["resources"]["limits"]["memory"] == "64Gi"
    assert container["resources"]["limits"]["nvidia.com/gpu"] == "1"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


def test_cpu_job_does_not_request_a_gpu():
    job = jobs.build_stage_job(
        jobs.StageJobRequest(
            run_id="run-1",
            mission_id=1,
            vol_id="mission-1",
            owner_subject="owner",
            stage="rasterization",
            resource_class="cpu-standard",
        ),
        jobs.StageJobConfig(namespace="drone-ai", image="image@sha256:" + "b" * 64, command=("run",)),
    )

    limits = job["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert "nvidia.com/gpu" not in limits
