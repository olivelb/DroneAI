"""Verify that a release commit has complete, physical qualification evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Final

REQUIRED_WORKFLOWS: Final = (
    "ci.yml",
    "cuda-containers.yml",
    "dronegs-gpu-qualification.yml",
    "codeql.yml",
)
GPU_WORKFLOW: Final = "dronegs-gpu-qualification.yml"
GPU_JOB: Final = "cuda-tests"

GhJson = Callable[[list[str]], Any]


def _gh_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _successful_candidates(runs: Any, commit: str) -> list[dict[str, Any]]:
    if not isinstance(runs, list):
        raise ValueError("gh run list returned a non-list response")
    return [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get("headSha") == commit
        and run.get("conclusion") == "success"
    ]


def _physical_gpu_evidence(
    run: dict[str, Any],
    *,
    commit: str,
    repository: str,
    gh_json: GhJson,
) -> dict[str, Any]:
    if run.get("event") != "workflow_dispatch":
        raise ValueError("GPU qualification was not triggered by workflow_dispatch")
    run_id = run.get("databaseId")
    if not isinstance(run_id, int):
        raise ValueError("GPU qualification run has no numeric databaseId")

    details = gh_json(["run", "view", str(run_id), "--json", "jobs"])
    jobs = details.get("jobs", []) if isinstance(details, dict) else []
    cuda_jobs = [job for job in jobs if isinstance(job, dict) and job.get("name") == GPU_JOB]
    if len(cuda_jobs) != 1:
        raise ValueError(f"GPU qualification must contain exactly one {GPU_JOB} job")
    cuda_job = cuda_jobs[0]
    if cuda_job.get("conclusion") != "success":
        raise ValueError(f"{GPU_JOB} did not succeed")

    artifact_name = f"dronegs-gpu-validation-{commit}"
    artifact_response = gh_json(
        [
            "api",
            f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
        ]
    )
    artifacts = (
        artifact_response.get("artifacts", [])
        if isinstance(artifact_response, dict)
        else []
    )
    matching_artifacts = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("name") == artifact_name
        and artifact.get("expired") is False
    ]
    if len(matching_artifacts) != 1:
        raise ValueError(f"missing non-expired GPU artifact {artifact_name}")
    artifact = matching_artifacts[0]
    return {
        "job": {
            "databaseId": cuda_job.get("databaseId"),
            "name": GPU_JOB,
            "conclusion": "success",
        },
        "artifact": {
            "id": artifact.get("id"),
            "name": artifact_name,
            "expired": False,
        },
    }


def collect_qualification_runs(
    commit: str,
    repository: str,
    *,
    gh_json: GhJson = _gh_json,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for workflow in REQUIRED_WORKFLOWS:
        runs = gh_json(
            [
                "run",
                "list",
                "--workflow",
                workflow,
                "--commit",
                commit,
                "--limit",
                "20",
                "--json",
                "databaseId,conclusion,event,headSha,url,workflowName",
            ]
        )
        candidates = _successful_candidates(runs, commit)
        if not candidates:
            raise ValueError(f"{workflow}: no successful run for {commit}")

        selected: dict[str, Any] | None = None
        rejection_reasons: list[str] = []
        for candidate in candidates:
            record = {"workflow_file": workflow, **candidate}
            if workflow == GPU_WORKFLOW:
                try:
                    record["physical_gpu_evidence"] = _physical_gpu_evidence(
                        candidate,
                        commit=commit,
                        repository=repository,
                        gh_json=gh_json,
                    )
                except ValueError as error:
                    rejection_reasons.append(str(error))
                    continue
            selected = record
            break
        if selected is None:
            detail = "; ".join(rejection_reasons) or "no acceptable run"
            raise ValueError(f"{workflow}: {detail}")
        records.append(selected)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = collect_qualification_runs(args.commit, args.repository)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(records, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
