"""Canonical DroneGS real spherical-harmonic basis for array backends."""

from __future__ import annotations

from typing import Any


SH_C0 = 0.28209479177387814


def evaluate_sh_basis(
    degree: int,
    directions: Any,
    *,
    array_module: Any,
) -> Any:
    """Evaluate the normalized degree-0..3 DroneGS SH basis."""
    if degree not in {0, 1, 2, 3}:
        raise ValueError("spherical harmonic degree must be between zero and three")
    if directions.ndim != 2 or directions.shape[1] != 3:
        raise ValueError("spherical harmonic directions must have shape (N, 3)")
    norms = array_module.linalg.norm(directions, axis=1, keepdims=True)
    if bool(array_module.any(~array_module.isfinite(norms))) or bool(
        array_module.any(norms <= 1.0e-10)
    ):
        raise ValueError("spherical harmonic directions must be finite and non-zero")
    normalized = directions / norms
    count = (degree + 1) ** 2
    result = array_module.zeros(
        (directions.shape[0], count), dtype=array_module.float32
    )
    x, y, z = normalized[:, 0], normalized[:, 1], normalized[:, 2]
    result[:, 0] = SH_C0
    if degree >= 1:
        result[:, 1] = -0.4886025119029199 * y
        result[:, 2] = 0.4886025119029199 * z
        result[:, 3] = -0.4886025119029199 * x
    if degree >= 2:
        result[:, 4] = 1.0925484305920792 * x * y
        result[:, 5] = -1.0925484305920792 * y * z
        result[:, 6] = 0.31539156525252005 * (2.0 * z * z - x * x - y * y)
        result[:, 7] = -1.0925484305920792 * x * z
        result[:, 8] = 0.5462742152960396 * (x * x - y * y)
    if degree >= 3:
        result[:, 9] = -0.5900435899266435 * y * (3.0 * x * x - y * y)
        result[:, 10] = 2.890611442640554 * x * y * z
        result[:, 11] = -0.4570457994644658 * y * (
            4.0 * z * z - x * x - y * y
        )
        result[:, 12] = 0.3731763325901154 * z * (
            2.0 * z * z - 3.0 * x * x - 3.0 * y * y
        )
        result[:, 13] = -0.4570457994644658 * x * (
            4.0 * z * z - x * x - y * y
        )
        result[:, 14] = 1.4453057213202769 * z * (x * x - y * y)
        result[:, 15] = -0.5900435899266435 * x * (x * x - 3.0 * y * y)
    return result
