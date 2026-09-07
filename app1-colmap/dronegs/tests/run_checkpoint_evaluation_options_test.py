#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run a native evaluation fixture in a temporary, isolated scratch root."""
import argparse
import pathlib
import subprocess
import tempfile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=pathlib.Path)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="dronegs-checkpoint-evaluation-") as parent:
        subprocess.run([str(args.executable.resolve()), str(pathlib.Path(parent) / "case")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
