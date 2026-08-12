"""Cross-language parity gate for the native and Python SH implementations."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_sh_python_parity.py PROBE GAUSSIAN_ORTHO_ROOT")
    probe = Path(sys.argv[1])
    package_root = Path(sys.argv[2])
    sys.path.insert(0, str(package_root.parent))
    from gaussian_ortho.sh_basis import evaluate_sh_basis_direction

    directions = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, -2.0, 3.0),
        (-4.5, 0.25, 2.0),
    )
    for degree in range(4):
        for direction in directions:
            python_basis = evaluate_sh_basis_direction(degree, direction)
            completed = subprocess.run(
                [
                    str(probe),
                    str(degree),
                    *(format(float(value), ".9g") for value in direction),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            native_basis = tuple(
                float(value) for value in completed.stdout.strip().split(",")
            )
            if len(native_basis) != len(python_basis):
                raise AssertionError("native/Python SH coefficient count differs")
            for native_value, python_value in zip(native_basis, python_basis):
                if not math.isclose(
                    native_value,
                    python_value,
                    rel_tol=2.0e-6,
                    abs_tol=2.0e-7,
                ):
                    raise AssertionError(
                        "native/Python SH mismatch: "
                        f"{native_value} != {python_value}"
                    )
    print("DroneGS native/Python SH parity passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
