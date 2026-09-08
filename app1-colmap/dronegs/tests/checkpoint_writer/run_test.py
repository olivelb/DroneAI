#!/usr/bin/env python3
"""Run a Linux CPU writer fixture in a unique directory, retaining evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--fault", choices=("fsync", "rename", "directory-open", "directory-fsync"))
    parser.add_argument("--fault-library", type=Path)
    args = parser.parse_args()
    if bool(args.fault) != bool(args.fault_library):
        parser.error("fault and fault-library must be supplied together")
    args.artifacts_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="writer-", dir=args.artifacts_root))
    output = directory / "cases"  # The C++ fixture requires a path not yet created.
    command = [str(args.binary.resolve()), str(output.resolve())]
    environment = os.environ.copy()
    if args.fault:
        command.append(args.fault)
        # Match the actual test binary's dynamic ASan runtime, when present.
        # Keep its required first-load position without disabling ASan checks.
        dependencies = subprocess.run(["ldd", str(args.binary.resolve())],
            capture_output=True, text=True, timeout=10, check=True).stdout
        preload = []
        for line in dependencies.splitlines():
            if line.lstrip().startswith("libasan.so"):
                runtime = line.split("=>", 1)[1].strip().split(" (", 1)[0]
                if not Path(runtime).is_file():
                    raise RuntimeError("cannot resolve the test binary's ASan runtime")
                preload.append(runtime)
        preload.append(str(args.fault_library.resolve()))
        environment["LD_PRELOAD"] = ":".join(preload)
        environment["DRONEGS_TEST_IO_FAULT"] = args.fault
    start = time.monotonic()
    try:
        with (directory / "test.log").open("wb") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    env=environment, timeout=60, check=False)
        if result.returncode:
            raise RuntimeError(f"writer fixture exited with {result.returncode}")
        parity = []
        if not args.fault:
            for count in (0, 1, 3, 23301, 23302):
                base = output / f"n{count}"
                hashes = {mode: hashlib.sha256((base / f"{mode}.ckpt").read_bytes()).hexdigest()
                          for mode in ("original", "reread", "streaming")}
                if len(set(hashes.values())) != 1:
                    raise RuntimeError(f"SHA parity failed for {count} gaussians")
                parity.append({"gaussians": count, "sha256": hashes})
        record = {"passed": True, "seconds": time.monotonic() - start,
                  "command": command, "fault": args.fault, "parity": parity}
        (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"passed": True, "artifacts": str(directory)}))
    except BaseException as error:
        (directory / "failed.json").write_text(json.dumps({"error": str(error),
            "seconds": time.monotonic() - start, "command": command}, indent=2) + "\n")
        print(f"Writer test evidence retained at {directory}", flush=True)
        raise


if __name__ == "__main__":
    main()
