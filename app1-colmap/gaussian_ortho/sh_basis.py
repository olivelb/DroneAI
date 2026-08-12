"""Canonical DroneGS real spherical-harmonic basis for array backends."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any


SH_C0 = 0.28209479177387814


def _write_sh_basis(
    degree: int,
    x: Any,
    y: Any,
    z: Any,
    write: Callable[[int, Any], None],
) -> None:
    """Write the canonical basis through a scalar or array-backed sink."""
    write(0, x * 0.0 + SH_C0)
    if degree >= 1:
        write(1, -0.4886025119029199 * y)
        write(2, 0.4886025119029199 * z)
        write(3, -0.4886025119029199 * x)
    if degree >= 2:
        write(4, 1.0925484305920792 * x * y)
        write(5, -1.0925484305920792 * y * z)
        write(6, 0.31539156525252005 * (2.0 * z * z - x * x - y * y))
        write(7, -1.0925484305920792 * x * z)
        write(8, 0.5462742152960396 * (x * x - y * y))
    if degree >= 3:
        write(9, -0.5900435899266435 * y * (3.0 * x * x - y * y))
        write(10, 2.890611442640554 * x * y * z)
        write(11, -0.4570457994644658 * y * (4.0 * z * z - x * x - y * y))
        write(
            12,
            0.3731763325901154
            * z
            * (2.0 * z * z - 3.0 * x * x - 3.0 * y * y),
        )
        write(13, -0.4570457994644658 * x * (4.0 * z * z - x * x - y * y))
        write(14, 1.4453057213202769 * z * (x * x - y * y))
        write(15, -0.5900435899266435 * x * (x * x - 3.0 * y * y))


def evaluate_sh_basis_direction(
    degree: int,
    direction: Sequence[float],
) -> tuple[float, ...]:
    """Evaluate one direction without a NumPy dependency (used by CTest)."""
    if degree not in {0, 1, 2, 3}:
        raise ValueError("spherical harmonic degree must be between zero and three")
    if len(direction) != 3 or not all(math.isfinite(value) for value in direction):
        raise ValueError("spherical harmonic direction must contain three finite values")
    norm = math.sqrt(sum(value * value for value in direction))
    if norm <= 1.0e-10:
        raise ValueError("spherical harmonic direction must be non-zero")
    x, y, z = (value / norm for value in direction)
    result = [0.0] * ((degree + 1) ** 2)

    def store(index: int, value: float) -> None:
        result[index] = value

    _write_sh_basis(degree, x, y, z, store)
    return tuple(result)


def evaluate_sh_basis(
    degree: int,
    directions: Any,
    *,
    array_module: Any,
) -> Any:
    """Evaluate the degree-0..3 DroneGS SH basis for unit directions."""
    if degree not in {0, 1, 2, 3}:
        raise ValueError("spherical harmonic degree must be between zero and three")
    if directions.ndim != 2 or directions.shape[1] != 3:
        raise ValueError("spherical harmonic directions must have shape (N, 3)")
    count = (degree + 1) ** 2
    result = array_module.zeros(
        (directions.shape[0], count), dtype=array_module.float32
    )
    x, y, z = directions[:, 0], directions[:, 1], directions[:, 2]

    def store(index: int, value: Any) -> None:
        result[:, index] = value

    _write_sh_basis(degree, x, y, z, store)
    return result
