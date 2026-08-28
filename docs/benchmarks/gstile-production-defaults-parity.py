"""Whole-bundle integration check, not a statistical performance benchmark."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def main():
    repo, baseline, source, root = map(Path, sys.argv[1:])
    assert not root.exists(), "Evidence is immutable; choose a new directory"
    assert git(repo, "status", "--porcelain") == ""
    assert git(baseline, "status", "--porcelain") == ""
    root.mkdir(parents=True)
    protocol = {
        "schema": "gstile-production-defaults-parity-v1",
        "candidate": git(repo, "rev-parse", "HEAD"),
        "baseline": git(baseline, "rev-parse", "HEAD"),
        "source": str(source), "sourceSha256": digest(source),
        "driverSha256": digest(Path(__file__)),
        "python": sys.version, "platform": platform.platform(),
        "environment": {key: os.environ.get(key) for key in (
            "OPENBLAS_THREAD_TIMEOUT", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")},
        "acceptance": "Same complete inventory and SHA-256 for every file; no timing claim.",
    }
    assert protocol["baseline"] == "785afcfa550feadad82810c3b0985f77feccf03a"
    assert protocol["sourceSha256"] == "c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb"
    (root / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    trials = []
    for label, checkout, flags in [
        ("explicit", baseline, ["--lod-proxy-size", "16384", "--lod-proxy-strategy",
                               "adaptive-moment", "--pack-target-bytes", "2097152",
                               "--pack-workers", "2"]),
        ("default", repo, []),
    ]:
        output = root / label
        command = [sys.executable, str(checkout / "tools/build_gstiles.py"),
                   str(source), str(output), *flags]
        started = time.perf_counter()
        result = subprocess.run(command, cwd=checkout, capture_output=True, text=True, timeout=300)
        (root / f"{label}.stdout").write_text(result.stdout)
        (root / f"{label}.stderr").write_text(result.stderr)
        result.check_returncode()
        inventory = {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}
        trial = {"label": label, "command": command, "wallSeconds": time.perf_counter() - started,
                 "result": json.loads(result.stdout), "inventory": inventory}
        (root / f"{label}.json").write_text(json.dumps(trial, indent=2) + "\n")
        trials.append(trial)
        print(label, trial["wallSeconds"], len(inventory), flush=True)
    assert trials[0]["inventory"] == trials[1]["inventory"]
    config = trials[1]["result"]["build_configuration"]
    assert config == {"defaults_profile": "gstile-qualified-2026-08-28",
                      "lod_proxy_size": 16384, "lod_proxy_strategy": "adaptive-moment",
                      "pack_target_bytes": 2097152, "pack_workers": 2,
                      "pack_pending_bytes": 134217728}
    assert digest(source) == protocol["sourceSha256"]
    assert git(repo, "rev-parse", "HEAD") == protocol["candidate"]
    assert git(repo, "status", "--porcelain") == ""
    summary = {"accepted": True, "allFilesIdentical": True,
               "fileCount": len(trials[0]["inventory"]), "protocol": protocol,
               "bundleId": trials[1]["result"]["bundle_id"]}
    (root / "verified-results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
