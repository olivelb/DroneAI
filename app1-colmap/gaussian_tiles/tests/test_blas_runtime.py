"""Standalone tiler BLAS startup policy, without host-process tuning."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from gaussian_tiles.tests.test_gstile import _records, _write_ply

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / "tools/build_gstiles.py"
CONTROLS = (
    "OPENBLAS_THREAD_TIMEOUT", "OPENBLAS_NUM_THREADS", "OPENBLAS_DEFAULT_NUM_THREADS",
    "GOTO_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS",
)


def test_import_and_programmatic_help_do_not_initialize_or_tune_numpy():
    code = """
import os, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
before = dict(os.environ)
module = runpy.run_path(sys.argv[1])
assert 'numpy' not in sys.modules
assert dict(os.environ) == before
try:
    module['main'](['--help'])
except SystemExit as error:
    assert error.code == 0
assert 'numpy' not in sys.modules
assert dict(os.environ) == before
"""
    result = subprocess.run([sys.executable, "-c", code, str(SCRIPT)], cwd=REPOSITORY,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("timeout", [None, "0", "4", "30", "", "invalid"])
def test_cli_sets_only_absent_timeout_and_preserves_explicit_controls(timeout):
    env = dict(os.environ)
    env.pop("OPENBLAS_THREAD_TIMEOUT", None)
    if timeout is not None:
        env["OPENBLAS_THREAD_TIMEOUT"] = timeout
    for key in CONTROLS[1:]:
        env[key] = "2"
    code = """
import contextlib, io, json, os, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
sys.argv = [sys.argv[1], '--help']
before = dict(os.environ)
with contextlib.redirect_stdout(io.StringIO()):
    try:
        runpy.run_path(sys.argv[0], run_name='__main__')
    except SystemExit as error:
        assert error.code == 0
assert 'numpy' not in sys.modules
expected = {**before, 'OPENBLAS_THREAD_TIMEOUT': before.get('OPENBLAS_THREAD_TIMEOUT', '16')}
assert dict(os.environ) == expected
print(json.dumps({'timeout': os.environ['OPENBLAS_THREAD_TIMEOUT']}))
"""
    result = subprocess.run([sys.executable, "-c", code, str(SCRIPT)], cwd=REPOSITORY,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["timeout"] == ("16" if timeout is None else timeout)


@pytest.mark.parametrize("workers", [1, 2])
def test_complete_bundle_matches_backend_default_timeout(tmp_path, workers):
    source = tmp_path / "source.ply"
    _write_ply(source, _records(8193))
    inherited = {k: v for k, v in os.environ.items() if k not in CONTROLS}
    # A fixed thread budget in both children; this test changes ONLY the idle timeout.
    inherited["OPENBLAS_NUM_THREADS"] = "2"
    reports, inventories = [], []
    for label, timeout in (("backend-default", "0"), ("cli-default", None)):
        target = tmp_path / label
        env = dict(inherited)
        if timeout is not None:
            env["OPENBLAS_THREAD_TIMEOUT"] = timeout
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(source), str(target), "--leaf-size", "2048",
             "--chunk-records", "2048", "--lod-proxy-size", "1024", "--lod-proxy-strategy", "adaptive-moment",
             "--pack-workers", str(workers), "--pack-target-bytes", "262144", "--progress-jsonl"],
            cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        reports.append(json.loads(result.stdout))
        assert result.stderr.strip()
        for line in result.stderr.splitlines():
            json.loads(line)
        inventories.append({p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()})
    assert reports[0]["openblas_thread_timeout"] == "0"
    assert reports[1]["openblas_thread_timeout"] == "16"
    assert reports[0]["bundle_id"] == reports[1]["bundle_id"]
    assert inventories[0] == inventories[1]
