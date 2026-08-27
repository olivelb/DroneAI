from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/dronegs/contracts/trainer-run-v1.schema.json").read_text()
)
TOPOLOGY_SCHEMA = SCHEMA["properties"]["topology_telemetry"]


def _telemetry() -> dict:
    return {
        name: definition.get("const", 0)
        for name, definition in TOPOLOGY_SCHEMA["properties"].items()
    }


@pytest.mark.parametrize("opacity_sh", [False, True])
@pytest.mark.parametrize("include_telemetry", [False, True])
def test_manifest_schema_accepts_optional_diagnostics_and_current_opacity_modes(opacity_sh, include_telemetry):
    manifest = {
        "contract_version": 1, "backend": "dronegs-native-mrnf-fastgs",
        "trainer_version": "0.5.0-dev.67", "status": "completed",
        "started_at": "2026-08-27T00:00:00Z", "finished_at": "2026-08-27T00:01:00Z",
        "dataset": {"path": "fixture", "fingerprint": "fixed", "training_image_count": 1,
                    "held_out_image_count": 0, "ignored_image_count": 0},
        "parameters": {"iterations": 400, "strategy": "mrnf", "sh_degree": 3,
                       "max_cap": 100, "resize_factor": 1, "max_width": 32,
                       "tile_mode": 1, "seed": 42, "opacity_sh_enabled": opacity_sh,
                       "appearance_model": "color-sh-plus-opacity-sh-v1" if opacity_sh
                       else "color-sh-plus-scalar-opacity-v1",
                       "opacity_sh_learning_rate_ratio": 0.05 if opacity_sh else 0.0},
        "timings": {"wall_seconds": 1.0}, "metrics": {}, "artifacts": {},
    }
    if include_telemetry:
        manifest["topology_telemetry"] = _telemetry()
    jsonschema.Draft202012Validator(SCHEMA).validate(manifest)


@pytest.mark.parametrize("field,value", [
    ("cpu_prune_seconds", -0.1), ("snapshot_download_bytes", 0.5),
    ("measured_calls", -1), ("version", 2), ("scope", "checkpoint_lifetime"),
    ("timing_basis", "gpu_kernel_time"), ("unknown_phase_seconds", 1),
])
def test_topology_schema_rejects_invalid_diagnostics(field, value):
    telemetry = _telemetry()
    telemetry[field] = value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(TOPOLOGY_SCHEMA).validate(telemetry)


def test_topology_schema_requires_all_phase_counters():
    telemetry = _telemetry()
    del telemetry["compaction_download_bytes"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(TOPOLOGY_SCHEMA).validate(telemetry)
