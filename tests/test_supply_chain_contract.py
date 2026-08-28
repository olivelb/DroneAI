import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CUDA_WORKFLOW = ROOT / ".github" / "workflows" / "cuda-containers.yml"
PINNED_PYTHON_BASE = re.compile(
    r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$",
)
PINNED_NODE_BASE = re.compile(
    r"^FROM node:20-alpine@sha256:[0-9a-f]{64} AS (builder|runner)$",
)
PINNED_ACTION_REF = re.compile(r"[0-9a-f]{40}")
PINNED_CONTAINER_REF = re.compile(r".+@sha256:[0-9a-f]{64}")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^#\s]+)", re.MULTILINE)


def _trigger_block(workflow_path: Path) -> str:
    workflow = workflow_path.read_text(encoding="utf-8")
    return workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]


def test_every_external_workflow_action_is_immutable() -> None:
    for workflow_path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = workflow_path.read_text(encoding="utf-8")
        for reference in USES_LINE.findall(workflow):
            if reference.startswith("./"):
                continue
            if reference.startswith("docker://"):
                assert PINNED_CONTAINER_REF.fullmatch(reference.removeprefix("docker://")), (
                    f"{workflow_path.name}: mutable container action {reference}"
                )
                continue
            action, separator, revision = reference.rpartition("@")
            assert separator and "/" in action and PINNED_ACTION_REF.fullmatch(revision), (
                f"{workflow_path.name}: mutable action {reference}"
            )


def test_pr_workflows_do_not_repeat_checks_after_merge() -> None:
    for workflow_path in (CI_WORKFLOW, CUDA_WORKFLOW):
        triggers = _trigger_block(workflow_path)
        assert "\n  push:" not in triggers
        assert "  pull_request:" in triggers
        assert "  workflow_dispatch:" in triggers

    assert "\n    name: CI gate\n" in CI_WORKFLOW.read_text(encoding="utf-8")
    cuda_workflow = CUDA_WORKFLOW.read_text(encoding="utf-8")
    assert "\n    name: CUDA validation gate\n" in cuda_workflow
    assert "\n    paths:\n" not in _trigger_block(CUDA_WORKFLOW)


def test_runtime_images_publish_pinned_sbom_and_vulnerability_evidence() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "anchore/syft:v1.50.0@sha256:" in workflow
    assert "aquasec/trivy:0.73.0@sha256:" in workflow
    assert "cyclonedx-json=/output/${{ matrix.artifact }}.cdx.json" in workflow
    assert "${{ matrix.artifact }}.trivy.json" in workflow
    assert "supply-chain-${{ matrix.artifact }}-${{ github.sha }}" in workflow
    assert "retention-days: 30" in workflow


def test_runtime_image_gate_rejects_fixable_critical_findings() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Reject fixed CRITICAL image vulnerabilities" in workflow
    assert "--severity CRITICAL --exit-code 1" in workflow
    assert workflow.count("--ignore-unfixed") >= 2
    assert workflow.count("--image-src docker") >= 2
    assert workflow.count("--scanners vuln") >= 2


def test_cuda_runtime_images_use_the_same_supply_chain_gate() -> None:
    workflow = CUDA_WORKFLOW.read_text(encoding="utf-8")

    assert "run: python3 scripts/ci/select_cuda_builds.py" in workflow
    assert workflow.count("if: needs.version-change.outputs.build_required == 'true'") == 2
    assert "dockerfile: app1-colmap/Dockerfile.base" in workflow
    assert "dockerfile: app1-colmap/Dockerfile.local-gaussian" in workflow
    assert "artifact: cuda-colmap-base" in workflow
    assert "artifact: cuda-local-gaussian" in workflow
    assert "run: bash setup_deps.sh" in workflow
    assert "anchore/syft:v1.50.0@sha256:" in workflow
    assert "aquasec/trivy:0.73.0@sha256:" in workflow
    assert "--severity CRITICAL --exit-code 1" in workflow
    assert "supply-chain-${{ matrix.artifact }}-${{ github.sha }}" in workflow
    assert "retention-days: 30" in workflow


def test_cuda_builds_require_manual_dispatch_or_authoritative_version_change() -> None:
    workflow = CUDA_WORKFLOW.read_text(encoding="utf-8")
    selector = (ROOT / "scripts" / "ci" / "select_cuda_builds.py").read_text(encoding="utf-8")

    assert "workflow_dispatch" in workflow
    assert 'CUDA_VERSION_LINE: Final = re.compile(r"^([+-])FROM\\s+nvidia/cuda:' in selector
    assert "COLMAP_VERSION_LINE: Final" in selector
    assert "CUDA_PYTHON_VERSION_LINE: Final" in selector
    assert '"requirements/colmap.txt"' in selector
    assert '"requirements/local-gaussian.txt"' in selector
    assert 'reason = "manual-dispatch"' in selector


def test_cuda_runtimes_refresh_fixable_openssl_packages() -> None:
    dockerfiles = [
        (ROOT / "app1-colmap" / "Dockerfile.base").read_text(encoding="utf-8"),
        (ROOT / "app1-colmap" / "Dockerfile.local-gaussian").read_text(encoding="utf-8"),
    ]

    assert all("libssl3t64 openssl" in dockerfile for dockerfile in dockerfiles)


def test_python_runtime_bases_and_artifacts_are_immutable() -> None:
    runtime_dockerfiles = [
        ROOT / "app4-dashboard" / "api" / "Dockerfile",
    ]
    for path in runtime_dockerfiles:
        dockerfile = path.read_text(encoding="utf-8")
        assert PINNED_PYTHON_BASE.match(dockerfile.splitlines()[0])
        assert "--require-hashes" in dockerfile

    colmap_dockerfile = (ROOT / "app1-colmap" / "Dockerfile.base").read_text(
        encoding="utf-8",
    )
    assert "--require-hashes" in colmap_dockerfile


def test_supported_python_locks_and_installers_require_hashes() -> None:
    for lock_name in ("api.txt", "processing.txt", "colmap.txt", "dev.txt", "ia-extra.txt"):
        lock = (ROOT / "requirements" / lock_name).read_text(encoding="utf-8")
        assert "pip-compile" in lock[:300]
        assert "--generate-hashes" in lock[:300]
        assert "--hash=sha256:" in lock

    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "bootstrap-dev.sh").read_text(
        encoding="utf-8",
    )
    assert workflow.count("pip install --require-hashes") == 3
    assert "--require-hashes" in bootstrap
    assert "--require-hashes" in (ROOT / "app2-ia" / "Dockerfile").read_text(encoding="utf-8")


def test_frontend_runtime_has_immutable_supply_chain_evidence() -> None:
    dockerfile = (ROOT / "app4-dashboard" / "frontend" / "Dockerfile").read_text(
        encoding="utf-8",
    )
    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 2
    assert all(PINNED_NODE_BASE.match(line) for line in from_lines)
    assert "rm -rf /usr/local/lib/node_modules/npm" in dockerfile
    assert 'CMD ["node", "node_modules/next/dist/bin/next", "start"]' in dockerfile

    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "frontend_container: ${{ steps.scopes.outputs.frontend_container }}" in workflow
    assert "if: needs.changes.outputs.frontend_container == 'true'" in workflow
    assert "--read-only" in workflow
    assert "npm in runtime" in workflow
    assert "dashboard-frontend.cdx.json" in workflow
    assert "dashboard-frontend.trivy.json" in workflow
    assert "supply-chain-dashboard-frontend-${{ github.sha }}" in workflow
    assert "${{ needs.frontend-container.result }}" in workflow
