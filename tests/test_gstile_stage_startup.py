"""BLAS policy is installed before imports only in dedicated viewer Jobs."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "app1-colmap/stage_executor.py"
STAGES = ("reconstruction", "gaussian_training", "gaussian_filtering", "rasterization", "gaussian_viewer")


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("explicit", [None, "0", "24"])
def test_one_shot_runtime_policy_precedes_executor_import(stage, explicit):
    env = dict(os.environ)
    env.pop("OPENBLAS_THREAD_TIMEOUT", None)
    env["OPENBLAS_NUM_THREADS"] = "3"
    if explicit is not None:
        env["OPENBLAS_THREAD_TIMEOUT"] = explicit
    code = r"""
import importlib.abc, importlib.util, os, runpy, sys, types
from pathlib import Path
script, stage = sys.argv[1:]
sys.path.insert(0, str(Path(script).parent))
before = dict(os.environ)
expected = dict(before)
if stage == 'gaussian_viewer':
    expected.setdefault('OPENBLAS_THREAD_TIMEOUT', '16')
selected = []
class Loader(importlib.abc.Loader):
    def create_module(self, spec): return None
    def exec_module(self, module):
        assert dict(os.environ) == expected
        for name in ('reconstruction','gaussian_training','gaussian_filtering','rasterization','gaussian_viewer'):
            setattr(module, 'run_' + name + '_stage', lambda *args: None)
class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'colmap_worker.stage_executor':
            return importlib.util.spec_from_loader(fullname, Loader())
sys.meta_path.insert(0, Finder())
execution = types.ModuleType('shared.stage_execution')
execution.execute_one_shot_stage = lambda name, callback: selected.append(name)
sys.modules['shared.stage_execution'] = execution
sys.argv = [script, stage]
try:
    runpy.run_path(script, run_name='__main__')
except SystemExit as error:
    assert error.code == 0
assert selected == [stage]
assert dict(os.environ) == expected
assert 'numpy' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code, str(SCRIPT), stage],
                            cwd=REPOSITORY, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_importing_stage_cli_leaves_host_untouched():
    code = """
import os, runpy, sys
before = dict(os.environ)
runpy.run_path(sys.argv[1])
assert dict(os.environ) == before
assert 'numpy' not in sys.modules
assert 'colmap_worker.stage_executor' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code, str(SCRIPT)], cwd=REPOSITORY,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
