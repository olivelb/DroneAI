from __future__ import annotations

from typing import Any

import pytest

from scripts.ci.verify_release_qualification import (
    REQUIRED_WORKFLOWS,
    collect_qualification_runs,
)

COMMIT = "a" * 40
REPOSITORY = "olivelb/DroneAI"


class FakeGh:
    def __init__(
        self,
        *,
        gpu_event: str = "workflow_dispatch",
        gpu_conclusion: str = "success",
        artifact_present: bool = True,
        fallback_gpu_run: bool = False,
    ) -> None:
        self.gpu_event = gpu_event
        self.gpu_conclusion = gpu_conclusion
        self.artifact_present = artifact_present
        self.fallback_gpu_run = fallback_gpu_run

    def __call__(self, arguments: list[str]) -> Any:
        if arguments[:2] == ["run", "list"]:
            workflow = arguments[arguments.index("--workflow") + 1]
            run_id = REQUIRED_WORKFLOWS.index(workflow) + 10
            event = self.gpu_event if workflow == "dronegs-gpu-qualification.yml" else "workflow_dispatch"
            runs = [
                {
                    "databaseId": run_id,
                    "conclusion": "success",
                    "event": event,
                    "headSha": COMMIT,
                    "url": f"https://example.invalid/runs/{run_id}",
                    "workflowName": workflow,
                }
            ]
            if workflow == "dronegs-gpu-qualification.yml" and self.fallback_gpu_run:
                runs[0]["event"] = "pull_request"
                runs.append(
                    {
                        "databaseId": 99,
                        "conclusion": "success",
                        "event": "workflow_dispatch",
                        "headSha": COMMIT,
                        "url": "https://example.invalid/runs/99",
                        "workflowName": workflow,
                    }
                )
            return runs
        if arguments[:2] == ["run", "view"]:
            return {
                "jobs": [
                    {
                        "databaseId": 123,
                        "name": "cuda-tests",
                        "conclusion": self.gpu_conclusion,
                    }
                ]
            }
        if arguments[0] == "api":
            artifacts = []
            if self.artifact_present:
                artifacts.append(
                    {
                        "id": 456,
                        "name": f"dronegs-gpu-validation-{COMMIT}",
                        "expired": False,
                    }
                )
            return {"artifacts": artifacts}
        raise AssertionError(f"Unexpected gh arguments: {arguments}")


def test_collects_exact_commit_and_physical_gpu_evidence() -> None:
    records = collect_qualification_runs(COMMIT, REPOSITORY, gh_json=FakeGh())

    assert [record["workflow_file"] for record in records] == list(REQUIRED_WORKFLOWS)
    gpu_record = next(
        record
        for record in records
        if record["workflow_file"] == "dronegs-gpu-qualification.yml"
    )
    assert gpu_record["event"] == "workflow_dispatch"
    assert gpu_record["physical_gpu_evidence"]["job"]["conclusion"] == "success"
    assert gpu_record["physical_gpu_evidence"]["artifact"]["expired"] is False


def test_rejects_globally_successful_gpu_workflow_with_skipped_cuda_job() -> None:
    with pytest.raises(ValueError, match="cuda-tests did not succeed"):
        collect_qualification_runs(
            COMMIT,
            REPOSITORY,
            gh_json=FakeGh(gpu_conclusion="skipped"),
        )


def test_rejects_gpu_qualification_not_manually_dispatched() -> None:
    with pytest.raises(ValueError, match="workflow_dispatch"):
        collect_qualification_runs(
            COMMIT,
            REPOSITORY,
            gh_json=FakeGh(gpu_event="pull_request"),
        )


def test_rejects_gpu_qualification_without_retained_artifact() -> None:
    with pytest.raises(ValueError, match="missing non-expired GPU artifact"):
        collect_qualification_runs(
            COMMIT,
            REPOSITORY,
            gh_json=FakeGh(artifact_present=False),
        )


def test_selects_a_valid_manual_gpu_run_after_rejecting_a_pr_exemption() -> None:
    records = collect_qualification_runs(
        COMMIT,
        REPOSITORY,
        gh_json=FakeGh(fallback_gpu_run=True),
    )

    gpu_record = next(
        record
        for record in records
        if record["workflow_file"] == "dronegs-gpu-qualification.yml"
    )
    assert gpu_record["databaseId"] == 99
