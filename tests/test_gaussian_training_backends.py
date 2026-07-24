from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training.backends import (  # noqa: E402
    DroneGSBackend,
    LichtFeldBackend,
    TrainingRequest,
    resolve_training_backend,
)


def request(**overrides) -> TrainingRequest:
    values = {
        "data_path": "/data",
        "output_path": "/output",
        "iterations": 500,
        "strategy": "mrnf",
        "sh_degree": 1,
        "max_cap": 100_000,
        "resize_factor": 8,
        "max_width": 1024,
        "tile_mode": 4,
        "seed": 42,
    }
    values.update(overrides)
    return TrainingRequest(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("iterations", 0, "iterations"),
        ("iterations", True, "iterations"),
        ("strategy", "unknown", "strategy"),
        ("sh_degree", 4, "sh_degree"),
        ("max_cap", 0, "max_cap"),
        ("resize_factor", 3, "resize_factor"),
        ("max_width", 4097, "max_width"),
        ("tile_mode", 3, "tile_mode"),
        ("seed", -1, "seed"),
    ],
)
def test_training_request_validation(field, value, message):
    with pytest.raises(ValueError, match=message):
        request(**{field: value})


def test_lichtfeld_adapter_preserves_legacy_command():
    command = LichtFeldBackend("/opt/lichtfeld/LichtFeld-Studio").build_command(request())

    assert command[:3] == [
        "/opt/lichtfeld/LichtFeld-Studio", "--headless", "--train",
    ]
    assert command[command.index("--resize_factor") + 1] == "8"
    assert command[command.index("--max-width") + 1] == "1024"
    assert command[command.index("--tile-mode") + 1] == "4"
    assert "--seed" not in command


def test_dronegs_adapter_uses_canonical_contract():
    command = DroneGSBackend("/opt/dronegs").build_command(request())

    assert command[0] == "/opt/dronegs"
    assert command[command.index("--resize-factor") + 1] == "8"
    assert command[command.index("--seed") + 1] == "42"
    assert command[command.index("--run-manifest") + 1] == "/output/trainer_run.json"
    assert "--resize_factor" not in command


def test_dronegs_adapter_runs_contract_executable(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    executable = tmp_path / "fake_dronegs"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

arguments = dict(zip(sys.argv[1::2], sys.argv[2::2]))
output = Path(arguments["--output-path"])
output.mkdir(parents=True, exist_ok=True)
(output / "point_cloud.ply").write_text(
    "ply\\nformat ascii 1.0\\nelement vertex 2\\nend_header\\n",
    encoding="utf-8",
)
manifest = Path(arguments["--run-manifest"])
manifest.write_text(
    json.dumps({"contract_version": 1, "status": "completed"}),
    encoding="utf-8",
)
print(json.dumps({
    "event": "progress", "iteration": 5, "loss": 0.25, "gaussians": 2,
}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    progress = []
    training_request = request(
        data_path=str(dataset), output_path=str(tmp_path / "output"),
    )

    result = DroneGSBackend(str(executable)).train(
        training_request, report_fn=lambda *event: progress.append(event),
    )

    assert result.ply_path.is_file()
    assert result.manifest_path.is_file()
    assert result.effective_seed == 42
    assert progress == [(5, 0.25, 2)]


def test_dronegs_adapter_rejects_nonempty_output(tmp_path):
    dataset = tmp_path / "dataset"
    output = tmp_path / "output"
    dataset.mkdir()
    output.mkdir()
    (output / "stale.ply").write_text("stale", encoding="utf-8")
    executable = tmp_path / "dronegs"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    with pytest.raises(FileExistsError, match="not empty"):
        DroneGSBackend(str(executable)).train(
            request(data_path=str(dataset), output_path=str(output)),
        )


def test_resolver_defaults_to_lichtfeld():
    backend = resolve_training_backend(environment={})

    assert isinstance(backend, LichtFeldBackend)


def test_resolver_supports_environment_selection():
    backend = resolve_training_backend(
        environment={"DRONEAI_GAUSSIAN_BACKEND": "dronegs", "DRONEGS_BIN": "/bin/dronegs"}
    )

    assert isinstance(backend, DroneGSBackend)
    assert backend.build_command(request())[0] == "/bin/dronegs"


def test_resolver_rejects_unknown_backend():
    with pytest.raises(ValueError, match="unknown Gaussian training backend"):
        resolve_training_backend("other", environment={})
