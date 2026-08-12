"""Cross-language parity gate for the native and Python SH implementations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: test_sh_python_parity.py PROBE GAUSSIAN_ORTHO_ROOT")
    probe = Path(sys.argv[1])
    package_root = Path(sys.argv[2])
    sys.path.insert(0, str(package_root.parent))
    from gaussian_ortho.sh_basis import evaluate_sh_basis

    directions = np.asarray(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, -2.0, 3.0],
            [-4.5, 0.25, 2.0],
        ],
        dtype=np.float32,
    )
    for degree in range(4):
        python_basis = evaluate_sh_basis(
            degree,
            directions,
            array_module=np,
        )
        for row, direction in enumerate(directions):
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
            native_basis = np.fromstring(completed.stdout.strip(), sep=",")
            np.testing.assert_allclose(
                native_basis,
                python_basis[row],
                rtol=2.0e-6,
                atol=2.0e-7,
            )
    print("DroneGS native/Python SH parity passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
