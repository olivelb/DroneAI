import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CUDA_WORKFLOW = ROOT / ".github" / "workflows" / "cuda-containers.yml"
PROMOTION_WORKFLOW = ROOT / ".github" / "workflows" / "promote-images.yml"
PROMOTE_IMAGE_SCRIPT = ROOT / "scripts" / "ci" / "promote_image.sh"
QUALIFICATION_SCRIPT = ROOT / "scripts" / "ci" / "verify_release_qualification.py"
UNFIXED_CVE_SCRIPT = ROOT / "scripts" / "ci" / "verify_unfixed_cves.py"
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
    for marker in ("Record HIGH and CRITICAL image vulnerabilities", "Record frontend HIGH and CRITICAL vulnerabilities"):
        report_step = workflow.split(marker, 1)[1].split("- name:", 1)[0]
        assert "--ignore-unfixed" not in report_step


def test_runtime_image_gate_rejects_fixable_high_and_critical_findings() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Reject fixed HIGH and CRITICAL image vulnerabilities" in workflow
    assert "--severity HIGH,CRITICAL --exit-code 1" in workflow
    assert workflow.count("--ignore-unfixed") >= 2
    assert workflow.count("--image-src docker") >= 2
    assert workflow.count("--scanners vuln") >= 2


def test_cuda_runtime_images_use_the_same_supply_chain_gate() -> None:
    workflow = CUDA_WORKFLOW.read_text(encoding="utf-8")

    assert "run: python3 -m scripts.ci.select_cuda_builds" in workflow
    assert workflow.count("if: needs.changes.outputs.build_required == 'true'") == 2
    assert "dockerfile: app1-colmap/Dockerfile.base" in workflow
    assert "dockerfile: app1-colmap/Dockerfile.local-gaussian" in workflow
    assert "artifact: cuda-colmap-base" in workflow
    assert "artifact: cuda-local-gaussian" in workflow
    assert "run: bash setup_deps.sh" in workflow
    assert "anchore/syft:v1.50.0@sha256:" in workflow
    assert "aquasec/trivy:0.73.0@sha256:" in workflow
    assert "--severity HIGH,CRITICAL --exit-code 1" in workflow
    assert "supply-chain-${{ matrix.artifact }}-${{ github.sha }}" in workflow
    assert "retention-days: 30" in workflow


def test_cuda_gates_use_fail_closed_selection() -> None:
    for name in ("ci.yml", "cuda-containers.yml", "dronegs-gpu-qualification.yml"):
        workflow = (WORKFLOWS / name).read_text()
        assert "python3 -m scripts.ci.check_selected_jobs" in workflow
        assert "NEEDS_JSON: ${{ toJSON(needs) }}" in workflow
        assert "success|skipped" not in workflow
    gpu = (WORKFLOWS / "dronegs-gpu-qualification.yml").read_text()
    assert "name: GPU qualification gate" in gpu
    assert "--job cuda-tests=gpu_required" in gpu


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
    assert "--job frontend-container=frontend_container" in workflow


def test_source_security_covers_python_typescript_and_cpp_without_gpu_or_secrets() -> None:
    workflow = (WORKFLOWS / "codeql.yml").read_text()
    assert "language: python" in workflow
    assert "language: javascript-typescript" in workflow
    assert "language: c-cpp" in workflow
    assert "matrix.selected == 'true'" in workflow
    assert "security-events: write" in workflow
    assert "build-mode: none" in workflow
    assert "queries: security-extended" in workflow
    assert "pull_request_target" not in workflow
    assert "self-hosted" not in workflow
    assert "secrets." not in workflow


def test_codeql_is_language_selected_and_not_repeated_on_push() -> None:
    workflow = (WORKFLOWS / "codeql.yml").read_text()
    trigger = _trigger_block(WORKFLOWS / "codeql.yml")
    assert "pull_request:" in trigger
    assert "merge_group:" in trigger
    assert "\n  push:" not in trigger
    assert "run: python3 -m scripts.ci.select_codeql" in workflow
    assert "if: needs.changes.outputs.any == 'true'" in workflow
    assert "--job analyze=any" in workflow
    assert "name: CodeQL gate" in workflow



def test_signed_promotion_is_tag_bound_qualified_and_environment_gated() -> None:
    workflow = PROMOTION_WORKFLOW.read_text(encoding="utf-8")
    trigger = _trigger_block(PROMOTION_WORKFLOW)

    assert 'tags:\n      - "v*.*.*"' in trigger
    assert "workflow_dispatch:" not in trigger
    assert "pull_request:" not in trigger
    assert "environment: production-promotion" in workflow
    assert "git merge-base --is-ancestor" in workflow
    assert ".verification.verified" in workflow
    for required in (
        "ci.yml",
        "cuda-containers.yml",
        "dronegs-gpu-qualification.yml",
        "codeql.yml",
    ):
        assert f'    "{required}",' in QUALIFICATION_SCRIPT.read_text(encoding="utf-8")
    assert "scripts.ci.verify_release_qualification" in workflow
    qualification = QUALIFICATION_SCRIPT.read_text(encoding="utf-8")
    assert 'run.get("event") != "workflow_dispatch"' in qualification
    assert 'job.get("name") == GPU_JOB' in qualification
    assert 'cuda_job.get("conclusion") != "success"' in qualification
    assert "dronegs-gpu-validation-{commit}" in qualification


def test_signed_promotion_binds_digest_sbom_scan_provenance_and_signature() -> None:
    workflow = PROMOTION_WORKFLOW.read_text(encoding="utf-8")

    assert "packages: write" in workflow
    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert workflow.count("provenance: mode=max") == 3
    assert workflow.count("sbom: true") == 3
    promotion_script = PROMOTE_IMAGE_SCRIPT.read_text(encoding="utf-8")
    assert "anchore/syft:v1.50.0@sha256:" in promotion_script
    assert "aquasec/trivy:0.73.0@sha256:" in promotion_script
    assert "--ignore-unfixed" in promotion_script
    assert "scripts.ci.verify_unfixed_cves" in promotion_script
    assert "security/unfixed-cve-waivers.json" in promotion_script
    assert "cosign sign --yes" in promotion_script
    assert "cosign verify" in promotion_script
    assert workflow.count("scripts/ci/promote_image.sh") == 3
    assert not re.search(r"(?:^|\s)\+\s+(?:--|[A-Za-z])", workflow)
    assert workflow.count("actions/attest-build-provenance@") == 3
    assert "COLMAP_BASE_IMAGE=${{ env.BASE_REF }}@${{ steps.base.outputs.digest }}" in workflow
    assert "tools/promotion_manifest.py assemble" in workflow
    assert "release-manifest.sigstore.json" in workflow


def test_critical_boundaries_have_explicit_code_ownership() -> None:
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    for path in (
        "/.github/workflows/",
        "/scripts/ci/",
        "/alembic/",
        "/app4-dashboard/api/security.py",
        "/charts/",
        "/infra/",
        "/security/",
    ):
        assert path in codeowners
