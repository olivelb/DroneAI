from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from tools.production_qualification import (
    REQUIRED_DRILLS,
    REQUIRED_IMAGE_NAMES,
    gate_failures,
    new_draft,
    render_markdown,
    validate_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


def _draft() -> dict[str, object]:
    return new_draft(
        qualification_id="bigzen-release-2026-08-10",
        environment="bigzen-preproduction",
        cluster="bigzen-k3s",
        namespace="drone-ai-preprod",
        operator="operator@example.test",
        git_commit="1" * 40,
    )


def _passing_evidence() -> dict[str, object]:
    document = _draft()
    environment = document["environment"]
    assert isinstance(environment, dict)
    environment["kubernetes_version"] = "v1.33.1+k3s1"
    environment["node_type"] = "BIGZEN WSL2"
    environment["gpu"] = {
        "model": "NVIDIA GeForce RTX 3090",
        "vram_mb": 24576,
        "driver": "580.82.09",
        "cuda_runtime": "12.9",
    }
    release = document["release"]
    assert isinstance(release, dict)
    release["chart_version"] = "0.1.0"
    release["values_sha256"] = "2" * 64
    release["images"] = {
        name: f"sha256:{index:x}".ljust(71, str(index))
        for index, name in enumerate(REQUIRED_IMAGE_NAMES, start=1)
    }
    drills = document["drills"]
    assert isinstance(drills, list)
    for drill in drills:
        assert isinstance(drill, dict)
        drill.update(
            {
                "status": "passed",
                "started_at": "2026-08-10T08:00:00Z",
                "completed_at": "2026-08-10T08:05:00Z",
                "observed_rto_seconds": 300,
                "observed_rpo_seconds": 0,
                "evidence_refs": [f"cluster-capture/{drill['id']}.txt"],
            }
        )
    attestation = document["attestation"]
    assert isinstance(attestation, dict)
    attestation["reviewed_at"] = "2026-08-10T09:00:00+02:00"
    return document


def test_generated_draft_is_valid_but_cannot_pass_the_gate() -> None:
    document = _draft()

    assert validate_evidence(document) == []
    failures = gate_failures(document)

    assert any("placeholder" in failure for failure in failures)
    assert any("stage_cancellation" in failure for failure in failures)
    assert any("review timestamp" in failure for failure in failures)


def test_complete_bounded_evidence_passes_and_renders_human_report() -> None:
    document = _passing_evidence()

    assert validate_evidence(document) == []
    assert gate_failures(document) == []

    report = render_markdown(document, source_name="release.qualification.json")
    assert "Gate: **PASSED**" in report
    assert "Cancellation of a running stage" in report
    assert "NVIDIA GeForce RTX 3090" in report
    assert document["release"]["git_commit"] in report  # type: ignore[index]


def test_gate_rejects_objective_breach_even_when_drill_says_passed() -> None:
    document = _passing_evidence()
    drills = document["drills"]
    assert isinstance(drills, list)
    drills[0]["observed_rto_seconds"] = 901

    assert "drills.five_stage_chain: observed RTO exceeds the objective" in gate_failures(document)


def test_validator_rejects_missing_duplicate_drills_and_secret_material() -> None:
    document = _draft()
    drills = document["drills"]
    assert isinstance(drills, list)
    drills[-1] = deepcopy(drills[0])
    drills[0]["notes"] = "Authorization: Bearer very-sensitive-example-token"

    errors = validate_evidence(document)

    assert any("duplicate drill" in error for error in errors)
    assert any("missing drills helm_rollback" in error for error in errors)
    assert any("possible credential" in error for error in errors)


def test_json_schema_and_runtime_validator_define_the_same_required_drills() -> None:
    schema = json.loads(
        (ROOT / "docs/contracts/production-qualification-evidence-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    schema_ids = set(schema["$defs"]["drill"]["properties"]["id"]["enum"])

    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema_ids == {drill_id for drill_id, _title in REQUIRED_DRILLS}
