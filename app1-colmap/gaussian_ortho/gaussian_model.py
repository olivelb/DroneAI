"""
3D Gaussian Model (CuPy — inference only).

Each Gaussian is parametrised by:
  - position (3D mean)
  - covariance via rotation quaternion (4) + log-scale (3)
  - opacity (logit-space)
  - colour via spherical harmonics (SH) coefficients
  - optional SH coefficients for view-dependent opacity (`opacity-SH-v1`)

Based on Kerbl et al. 2023 (3DGS), with DroneAI's directional opacity-logit
extension. Scale and rotation remain view-independent; this is not FAGK.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import cupy as cp
import numpy as np

SH_C0 = 0.28209479177387814
PLY_TRANSFER_ROWS = 250_000


def num_sh_coefficients(degree: int) -> int:
    return (degree + 1) ** 2


class GaussianModel:
    """Set of 3D Gaussians stored as CuPy GPU arrays (no PyTorch)."""

    def __init__(self, sh_degree: int = 3, opacity_sh_enabled: bool = True,
                 opacity_sh_max_degree: int = 3):
        self.max_sh_degree = sh_degree
        self.active_sh_degree = 0
        self.opacity_sh_enabled = opacity_sh_enabled
        self.opacity_sh_max_degree = opacity_sh_max_degree
        self.active_opacity_sh_degree = 0

        # Data arrays (CuPy, on current GPU device)
        self._xyz = cp.empty((0, 3), dtype=cp.float32)
        self._features_dc = cp.empty((0, 1, 3), dtype=cp.float32)
        self._features_rest = cp.empty((0, 0, 3), dtype=cp.float32)
        self._scaling = cp.empty((0, 3), dtype=cp.float32)
        self._rotation = cp.empty((0, 4), dtype=cp.float32)
        self._opacity = cp.empty((0, 1), dtype=cp.float32)
        self._opacity_sh = cp.empty((0, 0), dtype=cp.float32)

    # ------------------------------------------------------------------
    #  Properties (same interface as the old torch version)
    # ------------------------------------------------------------------

    @property
    def num_gaussians(self) -> int:
        return int(self._xyz.shape[0])

    @property
    def positions(self) -> cp.ndarray:
        return self._xyz

    @property
    def scales(self) -> cp.ndarray:
        return cp.exp(self._scaling)

    @property
    def rotations(self) -> cp.ndarray:
        norms = cp.linalg.norm(self._rotation, axis=-1, keepdims=True)
        return self._rotation / cp.maximum(norms, 1e-10)

    @property
    def opacity(self) -> cp.ndarray:
        return 1.0 / (1.0 + cp.exp(-self._opacity))   # sigmoid

    @property
    def opacity_sh(self) -> cp.ndarray:
        """Directional opacity-logit residuals in native DroneGS SH order."""
        return self._opacity_sh

    @property
    def features(self) -> cp.ndarray:
        return cp.concatenate([self._features_dc, self._features_rest], axis=1)

    # ------------------------------------------------------------------
    #  Filtering
    # ------------------------------------------------------------------

    def filter_by_mask(self, mask: cp.ndarray) -> None:
        """Keep only Gaussians where *mask* is True (in-place)."""
        self._xyz = self._xyz[mask]
        self._features_dc = self._features_dc[mask]
        self._features_rest = self._features_rest[mask]
        self._scaling = self._scaling[mask]
        self._rotation = self._rotation[mask]
        self._opacity = self._opacity[mask]
        if self._opacity_sh.shape[0] > 0 and self._opacity_sh.shape[1] > 0:
            self._opacity_sh = self._opacity_sh[mask]

    # ------------------------------------------------------------------
    #  Serialisation (PLY)
    # ------------------------------------------------------------------

    def load_ply(self, path: str) -> None:
        """Load one resident PLY with bounded host staging memory."""
        from plyfile import PlyData

        plydata = PlyData.read(path, mmap="r")
        vertex = plydata['vertex']
        n = vertex.count

        def load_properties(names: list[str]) -> cp.ndarray:
            result = cp.empty((n, len(names)), dtype=cp.float32)
            for start in range(0, n, PLY_TRANSFER_ROWS):
                stop = min(n, start + PLY_TRANSFER_ROWS)
                host = np.empty((stop - start, len(names)), dtype=np.float32)
                for column, name in enumerate(names):
                    host[:, column] = vertex[name][start:stop]
                result[start:stop] = cp.asarray(host)
            return result

        self._xyz = load_properties(['x', 'y', 'z'])

        self._features_dc = load_properties(
            [f'f_dc_{i}' for i in range(3)]
        )[:, None, :]

        rest_names = sorted(
            [p.name for p in vertex.properties if p.name.startswith('f_rest_')],
            key=lambda s: int(s.split('_')[-1]),
        )
        if rest_names:
            K = len(rest_names) // 3
            rest_tensor = load_properties(rest_names)
            self._features_rest = rest_tensor.reshape(n, 3, K).transpose(0, 2, 1)
        else:
            self._features_rest = cp.zeros((n, 0, 3), dtype=cp.float32)

        self._scaling = load_properties([f'scale_{i}' for i in range(3)])

        self._rotation = load_properties([f'rot_{i}' for i in range(4)])

        self._opacity = load_properties(['opacity'])

        # View-dependent opacity-logit SH residuals.
        opa_sh_names = sorted(
            [p.name for p in vertex.properties if p.name.startswith('opacity_sh_')],
            key=lambda s: int(s.split('_')[-1]),
        )
        if opa_sh_names and self.opacity_sh_enabled:
            expected_counts = {3, 8, 15}
            if len(opa_sh_names) not in expected_counts:
                raise ValueError(
                    "opacity SH properties must encode degree 1, 2, or 3"
                )
            if len(opa_sh_names) != self._features_rest.shape[1]:
                raise ValueError(
                    "opacity SH degree must match the color SH degree"
                )
            self._opacity_sh = load_properties(opa_sh_names)
            self.active_opacity_sh_degree = int(np.sqrt(len(opa_sh_names) + 1)) - 1
        else:
            self._opacity_sh = cp.empty((n, 0), dtype=cp.float32)
            self.active_opacity_sh_degree = 0

    def save_ply(self, path: str) -> None:
        """Atomically stream a binary PLY without a full host-side copy."""
        n = self.num_gaussians
        rest_count = int(self._features_rest.shape[1] * 3)
        opacity_sh_count = (
            int(self._opacity_sh.shape[1])
            if self.opacity_sh_enabled and self._opacity_sh.shape[1] > 0
            else 0
        )

        attrs = [('x', 'f4'), ('y', 'f4'), ('z', 'f4')]
        attrs += [(f'f_dc_{i}', 'f4') for i in range(3)]
        attrs += [(f'f_rest_{i}', 'f4') for i in range(rest_count)]
        attrs += [(f'scale_{i}', 'f4') for i in range(3)]
        attrs += [(f'rot_{i}', 'f4') for i in range(4)]
        attrs += [('opacity', 'f4')]

        attrs += [(f'opacity_sh_{i}', 'f4') for i in range(opacity_sh_count)]

        dtype = np.dtype(attrs).newbyteorder("<")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        properties = "".join(
            f"property float {name}\n" for name in dtype.names or ()
        )
        header = (
            "ply\n"
            "format binary_little_endian 1.0\n"
            f"element vertex {n}\n"
            f"{properties}"
            "end_header\n"
        ).encode("ascii")
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                handle.write(header)
                for start in range(0, n, PLY_TRANSFER_ROWS):
                    stop = min(n, start + PLY_TRANSFER_ROWS)
                    count = stop - start
                    elements: np.ndarray = np.empty(count, dtype=dtype)
                    xyz = cp.asnumpy(self._xyz[start:stop])
                    dc = cp.asnumpy(self._features_dc[start:stop]).reshape(count, 3)
                    rest = cp.asnumpy(self._features_rest[start:stop])
                    rest = rest.transpose(0, 2, 1).reshape(count, rest_count)
                    scales = cp.asnumpy(self._scaling[start:stop])
                    rotations = cp.asnumpy(self._rotation[start:stop])
                    opacities = cp.asnumpy(self._opacity[start:stop]).reshape(count)
                    for index, name in enumerate(('x', 'y', 'z')):
                        elements[name] = xyz[:, index]
                    for index in range(3):
                        elements[f'f_dc_{index}'] = dc[:, index]
                    for index in range(rest_count):
                        elements[f'f_rest_{index}'] = rest[:, index]
                    for index in range(3):
                        elements[f'scale_{index}'] = scales[:, index]
                    for index in range(4):
                        elements[f'rot_{index}'] = rotations[:, index]
                    elements['opacity'] = opacities
                    if opacity_sh_count:
                        opacity_sh = cp.asnumpy(self._opacity_sh[start:stop])
                        for index in range(opacity_sh_count):
                            elements[f'opacity_sh_{index}'] = opacity_sh[:, index]
                    handle.write(elements.tobytes(order="C"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                Path(temporary_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    #  Quaternion utilities (used by Sim3 geo-alignment)
    # ------------------------------------------------------------------

    @staticmethod
    def _matrix_to_quaternion(rotation: object) -> np.ndarray:
        """3x3 rotation matrix to (4,) numpy quaternion (w, x, y, z).

        Works on numpy arrays (CPU); call with ``cp.asnumpy(R_gpu)``
        if the matrix is on GPU.
        """
        matrix: np.ndarray = np.asarray(rotation, dtype=np.float64)
        trace = matrix[0, 0] + matrix[1, 1] + matrix[2, 2]
        if trace > 0:
            s = 0.5 / np.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (matrix[2, 1] - matrix[1, 2]) * s
            y = (matrix[0, 2] - matrix[2, 0]) * s
            z = (matrix[1, 0] - matrix[0, 1]) * s
        elif matrix[0, 0] > matrix[1, 1] and matrix[0, 0] > matrix[2, 2]:
            s = 2.0 * np.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2])
            w = (matrix[2, 1] - matrix[1, 2]) / s
            x = 0.25 * s
            y = (matrix[0, 1] + matrix[1, 0]) / s
            z = (matrix[0, 2] + matrix[2, 0]) / s
        elif matrix[1, 1] > matrix[2, 2]:
            s = 2.0 * np.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2])
            w = (matrix[0, 2] - matrix[2, 0]) / s
            x = (matrix[0, 1] + matrix[1, 0]) / s
            y = 0.25 * s
            z = (matrix[1, 2] + matrix[2, 1]) / s
        else:
            s = 2.0 * np.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1])
            w = (matrix[1, 0] - matrix[0, 1]) / s
            x = (matrix[0, 2] + matrix[2, 0]) / s
            y = (matrix[1, 2] + matrix[2, 1]) / s
            z = 0.25 * s
        quaternion: np.ndarray = np.array([w, x, y, z], dtype=np.float32)
        return quaternion

    @staticmethod
    def _quaternion_multiply(q1: Any, q2: Any) -> Any:
        """Hamilton product.  Broadcasts over leading dimensions.

        Accepts both CuPy and numpy arrays.
        """
        xp = cp if isinstance(q1, cp.ndarray) else np
        w1, x1, y1, z1 = q1[..., 0], q1[..., 1], q1[..., 2], q1[..., 3]
        w2, x2, y2, z2 = q2[..., 0], q2[..., 1], q2[..., 2], q2[..., 3]
        return xp.stack([
            w1*w2 - x1*x2 - y1*y2 - z1*z2,
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2,
        ], axis=-1)
