from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


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
