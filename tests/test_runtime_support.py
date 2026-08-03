import subprocess
import sys
from pathlib import Path

import pytest

APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from runtime_support import run_command, scale_dimensions


def _reporter(events):
    def report(_vol_id, _step, _progress, **payload):
        events.append(payload)

    return report


def test_scale_dimensions_preserves_aspect_ratio_and_small_images():
    assert scale_dimensions(800, 600, 1000) == (800, 600)
    assert scale_dimensions(4000, 2000, 1000) == (1000, 500)


def test_run_command_reports_output_and_completion(monkeypatch):
    monkeypatch.setenv("COLMAP_COMMAND_HEARTBEAT_SECONDS", "0")
    events = []

    run_command(
        [sys.executable, "-c", "print('ready')"],
        "mission-1",
        "TEST",
        10,
        _reporter(events),
    )

    assert any(event.get("log") == "ready" for event in events)
    assert events[-1]["details"]["event"] == "command_finished"


def test_run_command_reports_nonzero_exit(monkeypatch):
    monkeypatch.setenv("COLMAP_COMMAND_HEARTBEAT_SECONDS", "0")
    events = []

    with pytest.raises(subprocess.CalledProcessError):
        run_command(
            [sys.executable, "-c", "raise SystemExit(3)"],
            "mission-2",
            "TEST",
            20,
            _reporter(events),
        )

    assert events[-1]["details"]["event"] == "command_failed"
    assert events[-1]["details"]["return_code"] == 3


def test_run_command_enforces_timeout(monkeypatch):
    monkeypatch.setenv("COLMAP_COMMAND_HEARTBEAT_SECONDS", "0")
    events = []

    with pytest.raises(TimeoutError):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            "mission-3",
            "TEST",
            30,
            _reporter(events),
            timeout_seconds=0.05,
        )

    assert events[-1]["details"]["event"] == "command_timeout"
