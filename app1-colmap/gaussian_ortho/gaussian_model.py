"""
3D Gaussian Model (CuPy — inference only).

Each Gaussian is parametrised by:
  - position μ (3D mean)
  - covariance via rotation quaternion (4) + log-scale (3)
  - opacity α (logit-space)
  - colour via spherical harmonics (SH) coefficients
  - [FAGK] optional SH coefficients for view-dependent opacity

Based on Kerbl et al. 2023 (3DGS) with FAGK from Tortho-Gaussian.
"""
import math

import cupy as cp
import numpy as np

from .colmap_loader import PointCloud

SH_C0 = 0.28209479177387814


def num_sh_coefficients(degree: int) -> int:
    return (degree + 1) ** 2


class GaussianModel:
    """Set of 3D Gaussians stored as CuPy GPU arrays (no PyTorch)."""

    def __init__(self, sh_degree: int = 3, fagk_enabled: bool = True,
                 fagk_max_degree: int = 3):
        self.max_sh_degree = sh_degree
        self.active_sh_degree = 0
        self.fagk_enabled = fagk_enabled
        self.fagk_max_degree = fagk_max_degree
        self.active_fagk_degree = 0

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
        return self._xyz.shape[0]

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
    def features(self) -> cp.ndarray:
        return cp.concatenate([self._features_dc, self._features_rest], axis=1)

    # ------------------------------------------------------------------
    #  Filtering
    # ------------------------------------------------------------------

    def filter_by_mask(self, mask):
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

    def load_ply(self, path: str):
        """Load Gaussian parameters from a PLY file onto GPU."""
        from plyfile import PlyData

        plydata = PlyData.read(path)
        vertex = plydata['vertex']
        n = vertex.count

        xyz = np.stack([vertex['x'], vertex['y'], vertex['z']], axis=1)
        self._xyz = cp.array(xyz, dtype=cp.float32)

        dc = np.stack([vertex[f'f_dc_{i}'] for i in range(3)], axis=1)
        self._features_dc = cp.array(dc, dtype=cp.float32)[:, None, :]

        rest_names = sorted(
            [p.name for p in vertex.properties if p.name.startswith('f_rest_')],
            key=lambda s: int(s.split('_')[-1]),
        )
        if rest_names:
            rest = np.stack([vertex[rn] for rn in rest_names], axis=1)
            K = rest.shape[1] // 3
            rest_tensor = cp.array(rest, dtype=cp.float32)
            self._features_rest = rest_tensor.reshape(n, 3, K).transpose(0, 2, 1)
        else:
            self._features_rest = cp.zeros((n, 0, 3), dtype=cp.float32)

        scales = np.stack([vertex[f'scale_{i}'] for i in range(3)], axis=1)
        self._scaling = cp.array(scales, dtype=cp.float32)

        rots = np.stack([vertex[f'rot_{i}'] for i in range(4)], axis=1)
        self._rotation = cp.array(rots, dtype=cp.float32)

        opas = vertex['opacity'].reshape(-1, 1)
        self._opacity = cp.array(opas, dtype=cp.float32)

        # FAGK opacity SH
        opa_sh_names = sorted(
            [p.name for p in vertex.properties if p.name.startswith('opacity_sh_')],
            key=lambda s: int(s.split('_')[-1]),
        )
        if opa_sh_names and self.fagk_enabled:
            opa_sh = np.stack([vertex[on] for on in opa_sh_names], axis=1)
            self._opacity_sh = cp.array(opa_sh, dtype=cp.float32)
        else:
            self._opacity_sh = cp.empty((n, 0), dtype=cp.float32)

    def save_ply(self, path: str):
        """Save Gaussian parameters to a PLY file."""
        from plyfile import PlyData, PlyElement

        n = self.num_gaussians
        xyz = cp.asnumpy(self._xyz)
        dc = cp.asnumpy(self._features_dc).reshape(n, 3)
        rest_raw = cp.asnumpy(self._features_rest)
        rest = rest_raw.transpose(0, 2, 1).reshape(n, -1)
        scales = cp.asnumpy(self._scaling)
        rots = cp.asnumpy(self._rotation)
        opas = cp.asnumpy(self._opacity)

        attrs = [('x', 'f4'), ('y', 'f4'), ('z', 'f4')]
        attrs += [(f'f_dc_{i}', 'f4') for i in range(3)]
        attrs += [(f'f_rest_{i}', 'f4') for i in range(rest.shape[1])]
        attrs += [(f'scale_{i}', 'f4') for i in range(3)]
        attrs += [(f'rot_{i}', 'f4') for i in range(4)]
        attrs += [('opacity', 'f4')]

        if self.fagk_enabled and self._opacity_sh.shape[1] > 0:
            opa_sh = cp.asnumpy(self._opacity_sh)
            attrs += [(f'opacity_sh_{i}', 'f4') for i in range(opa_sh.shape[1])]

        dtype = np.dtype(attrs)
        elements = np.empty(n, dtype=dtype)
        elements['x'] = xyz[:, 0]
        elements['y'] = xyz[:, 1]
        elements['z'] = xyz[:, 2]
        for i in range(3):
            elements[f'f_dc_{i}'] = dc[:, i]
        for i in range(rest.shape[1]):
            elements[f'f_rest_{i}'] = rest[:, i]
        for i in range(3):
            elements[f'scale_{i}'] = scales[:, i]
        for i in range(4):
            elements[f'rot_{i}'] = rots[:, i]
        elements['opacity'] = opas.squeeze()
        if self.fagk_enabled and self._opacity_sh.shape[1] > 0:
            for i in range(opa_sh.shape[1]):
                elements[f'opacity_sh_{i}'] = opa_sh[:, i]

        el = PlyElement.describe(elements, 'vertex')
        PlyData([el]).write(path)

    # ------------------------------------------------------------------
    #  Quaternion utilities (used by Sim3 geo-alignment)
    # ------------------------------------------------------------------

    @staticmethod
    def _matrix_to_quaternion(R) -> np.ndarray:
        """3×3 rotation matrix → (4,) numpy quaternion (w, x, y, z).

        Works on numpy arrays (CPU); call with ``cp.asnumpy(R_gpu)``
        if the matrix is on GPU.
        """
        R = np.asarray(R, dtype=np.float64)
        trace = R[0, 0] + R[1, 1] + R[2, 2]
        if trace > 0:
            s = 0.5 / np.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (R[2, 1] - R[1, 2]) * s
            y = (R[0, 2] - R[2, 0]) * s
            z = (R[1, 0] - R[0, 1]) * s
        elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
            s = 2.0 * np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
            w = (R[2, 1] - R[1, 2]) / s
            x = 0.25 * s
            y = (R[0, 1] + R[1, 0]) / s
            z = (R[0, 2] + R[2, 0]) / s
        elif R[1, 1] > R[2, 2]:
            s = 2.0 * np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
            w = (R[0, 2] - R[2, 0]) / s
            x = (R[0, 1] + R[1, 0]) / s
            y = 0.25 * s
            z = (R[1, 2] + R[2, 1]) / s
        else:
            s = 2.0 * np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
            w = (R[1, 0] - R[0, 1]) / s
            x = (R[0, 2] + R[2, 0]) / s
            y = (R[1, 2] + R[2, 1]) / s
            z = 0.25 * s
        return np.array([w, x, y, z], dtype=np.float32)

    @staticmethod
    def _quaternion_multiply(q1, q2):
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
