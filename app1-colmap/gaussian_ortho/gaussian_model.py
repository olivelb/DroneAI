"""
3D Gaussian Model with Fully Anisotropic Gaussian Kernel (FAGK).

Each Gaussian is parametrised by:
  - position μ (3D mean)
  - covariance  via rotation quaternion (4) + log-scale (3)
  - opacity α (logit-space)
  - colour via spherical harmonics (SH) coefficients
  - [FAGK] optional SH coefficients for view-dependent opacity

Based on Kerbl et al. 2023 (3DGS) with extensions from
Tortho-Gaussian (Wang et al. 2024) — FAGK on opacity channel.
"""
import math
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn

from .colmap_loader import PointCloud


# ---------------------------------------------------------------------------
#  Spherical Harmonics helpers
# ---------------------------------------------------------------------------

SH_C0 = 0.28209479177387814  # 1/(2*sqrt(pi))

def num_sh_coefficients(degree: int) -> int:
    """Total SH coefficients for a given maximum degree."""
    return (degree + 1) ** 2


def eval_sh_basis(degree: int, dirs: torch.Tensor) -> torch.Tensor:
    """
    Evaluate real spherical harmonic basis functions up to *degree*.

    Parameters
    ----------
    degree : int  (0..3)
    dirs : (N, 3) unit direction vectors

    Returns
    -------
    (N, (degree+1)^2) basis values
    """
    n = dirs.shape[0]
    n_coeffs = num_sh_coefficients(degree)
    result = torch.zeros(n, n_coeffs, device=dirs.device, dtype=dirs.dtype)

    x, y, z = dirs[:, 0], dirs[:, 1], dirs[:, 2]

    # l=0
    result[:, 0] = SH_C0

    if degree >= 1:
        result[:, 1] = 0.4886025119029199 * y
        result[:, 2] = 0.4886025119029199 * z
        result[:, 3] = 0.4886025119029199 * x

    if degree >= 2:
        result[:, 4] = 1.0925484305920792 * x * y
        result[:, 5] = 1.0925484305920792 * y * z
        result[:, 6] = 0.31539156525252005 * (2.0 * z * z - x * x - y * y)
        result[:, 7] = 1.0925484305920792 * x * z
        result[:, 8] = 0.5462742152960396 * (x * x - y * y)

    if degree >= 3:
        result[:, 9]  = 0.5900435899266435 * y * (3.0 * x * x - y * y)
        result[:, 10] = 2.890611442640554 * x * y * z
        result[:, 11] = 0.4570457994644658 * y * (4.0 * z * z - x * x - y * y)
        result[:, 12] = 0.3731763325901154 * z * (2.0 * z * z - 3.0 * x * x - 3.0 * y * y)
        result[:, 13] = 0.4570457994644658 * x * (4.0 * z * z - x * x - y * y)
        result[:, 14] = 1.4453057213202769 * z * (x * x - y * y)
        result[:, 15] = 0.5900435899266435 * x * (x * x - 3.0 * y * y)

    return result


# ---------------------------------------------------------------------------
#  Gaussian Model
# ---------------------------------------------------------------------------

class GaussianModel(nn.Module):
    """
    A set of 3D Gaussians representing a scene.

    Supports the Fully Anisotropic Gaussian Kernel (FAGK) from
    Tortho-Gaussian: SH-based view-dependent opacity that progressively
    activates higher orders during training (L=0→3).
    """

    def __init__(self, sh_degree: int = 3, fagk_enabled: bool = True,
                 fagk_max_degree: int = 3):
        super().__init__()
        self.max_sh_degree = sh_degree
        self.active_sh_degree = 0

        # FAGK settings
        self.fagk_enabled = fagk_enabled
        self.fagk_max_degree = fagk_max_degree
        self.active_fagk_degree = 0

        # Parameters (initialised via init_from_point_cloud)
        self._xyz = nn.Parameter(torch.empty(0, 3))
        self._features_dc = nn.Parameter(torch.empty(0, 1, 3))
        self._features_rest = nn.Parameter(torch.empty(0, 0, 3))
        self._scaling = nn.Parameter(torch.empty(0, 3))
        self._rotation = nn.Parameter(torch.empty(0, 4))
        self._opacity = nn.Parameter(torch.empty(0, 1))

        # FAGK: SH coefficients for view-dependent opacity
        # Shape: (N, n_sh_coeffs)  where n_sh_coeffs = (fagk_max_degree+1)^2
        self._opacity_sh = nn.Parameter(torch.empty(0, 0))

        # Densification bookkeeping (not nn parameters)
        self.register_buffer("xyz_gradient_accum", torch.empty(0))
        self.register_buffer("denom", torch.empty(0))
        self.register_buffer("max_radii2D", torch.empty(0))

    @property
    def num_gaussians(self) -> int:
        return self._xyz.shape[0]

    def filter_by_mask(self, mask: torch.Tensor):
        """Keep only Gaussians where mask is True (in-place)."""
        import torch.nn as nn
        self._xyz = nn.Parameter(self._xyz.data[mask])
        self._features_dc = nn.Parameter(self._features_dc.data[mask])
        self._features_rest = nn.Parameter(self._features_rest.data[mask])
        self._scaling = nn.Parameter(self._scaling.data[mask])
        self._rotation = nn.Parameter(self._rotation.data[mask])
        self._opacity = nn.Parameter(self._opacity.data[mask])
        if self._opacity_sh.shape[0] > 0:
            self._opacity_sh = nn.Parameter(self._opacity_sh.data[mask])

    @property
    def positions(self) -> torch.Tensor:
        return self._xyz

    @property
    def scales(self) -> torch.Tensor:
        """Activated scales (exp of log-scales)."""
        return torch.exp(self._scaling)

    @property
    def rotations(self) -> torch.Tensor:
        """Normalised quaternions."""
        return torch.nn.functional.normalize(self._rotation, dim=-1)

    @property
    def opacity(self) -> torch.Tensor:
        """Base opacity in [0, 1] via sigmoid."""
        return torch.sigmoid(self._opacity)

    @property
    def features(self) -> torch.Tensor:
        """SH colour features: (N, total_sh, 3)."""
        return torch.cat([self._features_dc, self._features_rest], dim=1)

    # ------------------------------------------------------------------
    #  Initialisation
    # ------------------------------------------------------------------

    def init_from_point_cloud(self, pc: PointCloud, spatial_lr_scale: float = 1.0,
                              init_opa: float = 0.1):
        """
        Initialise Gaussian parameters from a COLMAP sparse point cloud.

        Each 3D point becomes one Gaussian with:
          - position = point XYZ
          - colour = SH DC from point RGB
          - scale = estimated from local point density
          - rotation = identity quaternion
          - opacity = inverse_sigmoid(init_opa)
        """
        device = next(self.parameters()).device if self.num_gaussians > 0 else torch.device("cuda" if torch.cuda.is_available() else "cpu")

        points = torch.tensor(pc.points, dtype=torch.float32, device=device)
        colors = torch.tensor(pc.colors, dtype=torch.float32, device=device)
        n = points.shape[0]

        # Position
        self._xyz = nn.Parameter(points)

        # Colour SH: DC term from RGB, rest zeroed
        # DC = (color - 0.5) / SH_C0
        dc = (colors - 0.5) / SH_C0
        self._features_dc = nn.Parameter(dc.unsqueeze(1))  # (N, 1, 3)
        n_rest = num_sh_coefficients(self.max_sh_degree) - 1
        self._features_rest = nn.Parameter(torch.zeros(n, n_rest, 3, device=device))

        # Scale: log of mean distance to 3 nearest neighbours
        dists = self._knn_distances(points, k=3)
        log_scales = torch.log(dists.clamp(min=1e-7)).unsqueeze(-1).expand(-1, 3).clone()
        self._scaling = nn.Parameter(log_scales)

        # Rotation: unit quaternion (w=1, x=y=z=0)
        rots = torch.zeros(n, 4, device=device)
        rots[:, 0] = 1.0
        self._rotation = nn.Parameter(rots)

        # Opacity: inverse_sigmoid(init_opa)
        opacity_init = torch.full((n, 1), self._inverse_sigmoid(init_opa), device=device)
        self._opacity = nn.Parameter(opacity_init)

        # FAGK opacity SH
        if self.fagk_enabled:
            n_fagk = num_sh_coefficients(self.fagk_max_degree)
            self._opacity_sh = nn.Parameter(torch.zeros(n, n_fagk, device=device))
        else:
            self._opacity_sh = nn.Parameter(torch.empty(n, 0, device=device))

        # Densification buffers
        self.xyz_gradient_accum = torch.zeros(n, 1, device=device)
        self.denom = torch.zeros(n, 1, device=device)
        self.max_radii2D = torch.zeros(n, device=device)

    @staticmethod
    def _knn_distances(points: torch.Tensor, k: int = 3) -> torch.Tensor:
        """Compute mean distance to k nearest neighbours using CPU cKDTree.

        Previous implementation used torch.cdist which allocates an (N, 50K)
        distance matrix on GPU — e.g. 200K × 50K × 4 bytes = 40 GB, causing
        instant OOM.  cKDTree runs on CPU in O(N log N) with negligible memory.
        """
        from scipy.spatial import cKDTree

        pts_np = points.detach().cpu().numpy()
        tree = cKDTree(pts_np)
        dists, _ = tree.query(pts_np, k=k + 1)  # +1 because first neighbour is self
        mean_dists = dists[:, 1:].mean(axis=1)   # skip self-distance
        return torch.tensor(mean_dists, dtype=points.dtype, device=points.device)

    @staticmethod
    def _inverse_sigmoid(x: float) -> float:
        return math.log(x / (1.0 - x))

    # ------------------------------------------------------------------
    #  Quaternion utilities (used by Sim3 geo-alignment)
    # ------------------------------------------------------------------

    @staticmethod
    def _matrix_to_quaternion(R: torch.Tensor) -> torch.Tensor:
        """Convert a 3x3 rotation matrix to a unit quaternion (w, x, y, z)."""
        trace = R[0, 0] + R[1, 1] + R[2, 2]
        if trace > 0:
            s = 0.5 / torch.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (R[2, 1] - R[1, 2]) * s
            y = (R[0, 2] - R[2, 0]) * s
            z = (R[1, 0] - R[0, 1]) * s
        elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
            s = 2.0 * torch.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
            w = (R[2, 1] - R[1, 2]) / s
            x = 0.25 * s
            y = (R[0, 1] + R[1, 0]) / s
            z = (R[0, 2] + R[2, 0]) / s
        elif R[1, 1] > R[2, 2]:
            s = 2.0 * torch.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
            w = (R[0, 2] - R[2, 0]) / s
            x = (R[0, 1] + R[1, 0]) / s
            y = 0.25 * s
            z = (R[1, 2] + R[2, 1]) / s
        else:
            s = 2.0 * torch.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
            w = (R[1, 0] - R[0, 1]) / s
            x = (R[0, 2] + R[2, 0]) / s
            y = (R[1, 2] + R[2, 1]) / s
            z = 0.25 * s
        return torch.stack([w, x, y, z])

    @staticmethod
    def _quaternion_multiply(q1: torch.Tensor, q2: torch.Tensor) -> torch.Tensor:
        """Hamilton product of two quaternions (w, x, y, z). Broadcasts."""
        w1, x1, y1, z1 = q1[..., 0], q1[..., 1], q1[..., 2], q1[..., 3]
        w2, x2, y2, z2 = q2[..., 0], q2[..., 1], q2[..., 2], q2[..., 3]
        return torch.stack([
            w1*w2 - x1*x2 - y1*y2 - z1*z2,
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2,
        ], dim=-1)

    # ------------------------------------------------------------------
    #  Serialisation
    # ------------------------------------------------------------------

    def save_ply(self, path: str):
        """Save Gaussian parameters to a PLY file."""
        from plyfile import PlyData, PlyElement

        n = self.num_gaussians
        xyz = self._xyz.detach().cpu().numpy()
        dc = self._features_dc.detach().cpu().numpy().reshape(n, 3)
        rest = self._features_rest.detach().cpu().numpy().reshape(n, -1)
        scales = self._scaling.detach().cpu().numpy()
        rots = self._rotation.detach().cpu().numpy()
        opas = self._opacity.detach().cpu().numpy()

        attrs = [('x', 'f4'), ('y', 'f4'), ('z', 'f4')]
        attrs += [(f'f_dc_{i}', 'f4') for i in range(3)]
        attrs += [(f'f_rest_{i}', 'f4') for i in range(rest.shape[1])]
        attrs += [(f'scale_{i}', 'f4') for i in range(3)]
        attrs += [(f'rot_{i}', 'f4') for i in range(4)]
        attrs += [('opacity', 'f4')]

        if self.fagk_enabled and self._opacity_sh.shape[1] > 0:
            opa_sh = self._opacity_sh.detach().cpu().numpy()
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

    def load_ply(self, path: str):
        """Load Gaussian parameters from a PLY file."""
        from plyfile import PlyData

        plydata = PlyData.read(path)
        vertex = plydata['vertex']
        n = vertex.count

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        xyz = np.stack([vertex['x'], vertex['y'], vertex['z']], axis=1)
        self._xyz = nn.Parameter(torch.tensor(xyz, dtype=torch.float32, device=device))

        dc = np.stack([vertex[f'f_dc_{i}'] for i in range(3)], axis=1)
        self._features_dc = nn.Parameter(torch.tensor(dc, dtype=torch.float32, device=device).unsqueeze(1))

        rest_names = sorted([p.name for p in vertex.properties if p.name.startswith('f_rest_')])
        if rest_names:
            rest = np.stack([vertex[n] for n in rest_names], axis=1)
            n_rest = rest.shape[1]
            self._features_rest = nn.Parameter(
                torch.tensor(rest, dtype=torch.float32, device=device).reshape(n, n_rest // 3, 3)
            )
        else:
            self._features_rest = nn.Parameter(torch.zeros(n, 0, 3, device=device))

        scales = np.stack([vertex[f'scale_{i}'] for i in range(3)], axis=1)
        self._scaling = nn.Parameter(torch.tensor(scales, dtype=torch.float32, device=device))

        rots = np.stack([vertex[f'rot_{i}'] for i in range(4)], axis=1)
        self._rotation = nn.Parameter(torch.tensor(rots, dtype=torch.float32, device=device))

        opas = vertex['opacity'].reshape(-1, 1)
        self._opacity = nn.Parameter(torch.tensor(opas, dtype=torch.float32, device=device))

        # FAGK
        opa_sh_names = sorted([p.name for p in vertex.properties if p.name.startswith('opacity_sh_')])
        if opa_sh_names and self.fagk_enabled:
            opa_sh = np.stack([vertex[n] for n in opa_sh_names], axis=1)
            self._opacity_sh = nn.Parameter(torch.tensor(opa_sh, dtype=torch.float32, device=device))
        else:
            self._opacity_sh = nn.Parameter(torch.empty(n, 0, device=device))

        self.xyz_gradient_accum = torch.zeros(n, 1, device=device)
        self.denom = torch.zeros(n, 1, device=device)
        self.max_radii2D = torch.zeros(n, device=device)
