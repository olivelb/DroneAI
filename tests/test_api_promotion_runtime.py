"""The exact release image must pass native checks before waiver/signing."""
import json
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci/promote_image.sh"


@pytest.mark.parametrize("reject_runtime", [False, True])
def test_api_native_check_precedes_waivers_and_signing(tmp_path, reject_runtime):
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "stub"
    stub.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, sys\n"
        "with open(os.environ['CALL_LOG'], 'a') as log:\n"
        "    log.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + '\\n')\n"
        "if '--entrypoint' in sys.argv and os.environ['REJECT_RUNTIME'] == '1':\n"
        "    sys.exit(42)\n"
    )
    stub.chmod(0o755)
    for name in ("docker", "python3", "cosign"):
        (binary / name).symlink_to(stub)
    call_log = tmp_path / "calls.jsonl"
    digest = "sha256:" + "a" * 64
    target = "registry.example/api@" + digest
    result = subprocess.run(
        ["bash", str(SCRIPT), "--name", "drone-dashboard-api", "--image",
         "registry.example/api", "--digest", digest, "--source-commit", "b" * 40],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
             "CALL_LOG": str(call_log), "REJECT_RUNTIME": str(int(reject_runtime)),
             "HOME": str(tmp_path), "PROMOTION_EVIDENCE_DIR": "evidence",
             "PROMOTION_TRIVY_CACHE_DIR": "cache",
             "GITHUB_REPOSITORY": "olivelb/DroneAI", "GITHUB_REF": "refs/tags/v0.0.0"},
    )
    calls = [json.loads(line) for line in call_log.read_text().splitlines()]
    runtime_index = next(i for i, args in enumerate(calls) if "--entrypoint" in args)
    runtime = calls[runtime_index]
    assert target in runtime
    assert "--read-only" in runtime and "no-new-privileges" in runtime
    assert runtime[runtime.index("--user") + 1] == "10001:10001"
    assert runtime[runtime.index("--network") + 1] == "none"
    assert any(arg.endswith("verify_api_runtime.py:/opt/verify-api-runtime.py:ro") for arg in runtime)
    if reject_runtime:
        assert result.returncode == 42
        assert runtime_index == len(calls) - 1
    else:
        assert result.returncode == 0, result.stderr
        waiver_index = next(i for i, args in enumerate(calls) if "scripts.ci.verify_unfixed_cves" in args)
        sign_index = next(i for i, args in enumerate(calls) if args[:2] == ["cosign", "sign"])
        assert runtime_index < waiver_index < sign_index
