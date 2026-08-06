from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CUDA_WORKFLOW = ROOT / ".github" / "workflows" / "cuda-containers.yml"


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
    assert '"scripts/ci/select_cuda_builds.py"' in workflow
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
    assert 'reason = "manual-dispatch"' in selector


def test_cuda_runtimes_refresh_fixable_openssl_packages() -> None:
    dockerfiles = [
        (ROOT / "app1-colmap" / "Dockerfile.base").read_text(encoding="utf-8"),
        (ROOT / "app1-colmap" / "Dockerfile.local-gaussian").read_text(encoding="utf-8"),
    ]

    assert all("libssl3t64 openssl" in dockerfile for dockerfile in dockerfiles)
