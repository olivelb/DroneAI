"""
Merge Gaussian models from partitioned cells into a single model.

After independent per-cell training, this module stitches the results
by retaining only the Gaussians whose centres fall within the *core*
(non-overlap) region of each cell, discarding duplicates in overlapping
borders.
"""
import numpy as np
import torch
import torch.nn as nn

from .gaussian_model import GaussianModel
from .partition import CellBounds


def _core_bounds(cell: CellBounds, dx: float, dy: float, overlap: float):
    """Compute the core (non-overlap) region of a cell."""
    pad_x = dx * overlap
    pad_y = dy * overlap
    return CellBounds(
        x_min=cell.x_min + pad_x,
        x_max=cell.x_max - pad_x,
        y_min=cell.y_min + pad_y,
        y_max=cell.y_max - pad_y,
        row=cell.row, col=cell.col,
    )


def merge_models(cell_models: list[tuple[CellBounds, GaussianModel]],
                 scene_x_range: tuple[float, float],
                 scene_y_range: tuple[float, float],
                 m: int, n: int, overlap: float = 0.20) -> GaussianModel:
    """
    Merge multiple per-cell GaussianModels into one.

    For each cell, only Gaussians whose XY centre lies within the core
    (non-overlap) region are kept.  This avoids duplicates at cell borders.

    Parameters
    ----------
    cell_models : list of (CellBounds, GaussianModel)
    scene_x_range : (x_lo, x_hi) of full scene
    scene_y_range : (y_lo, y_hi) of full scene
    m, n : grid dimensions
    overlap : overlap fraction used during partitioning

    Returns
    -------
    GaussianModel with merged Gaussians
    """
    x_lo, x_hi = scene_x_range
    y_lo, y_hi = scene_y_range
    dx = (x_hi - x_lo) / n
    dy = (y_hi - y_lo) / m

    all_xyz = []
    all_dc = []
    all_rest = []
    all_scaling = []
    all_rotation = []
    all_opacity = []
    all_opacity_sh = []

    reference_model = cell_models[0][1]

    for cell, model in cell_models:
        core = _core_bounds(cell, dx, dy, overlap)
        xyz = model._xyz.detach()
        mask = (
            (xyz[:, 0] >= core.x_min) & (xyz[:, 0] <= core.x_max) &
            (xyz[:, 1] >= core.y_min) & (xyz[:, 1] <= core.y_max)
        )
        if not mask.any():
            continue

        all_xyz.append(xyz[mask])
        all_dc.append(model._features_dc.detach()[mask])
        all_rest.append(model._features_rest.detach()[mask])
        all_scaling.append(model._scaling.detach()[mask])
        all_rotation.append(model._rotation.detach()[mask])
        all_opacity.append(model._opacity.detach()[mask])
        if model.fagk_enabled and model._opacity_sh.shape[1] > 0:
            all_opacity_sh.append(model._opacity_sh.detach()[mask])

    merged = GaussianModel(
        sh_degree=reference_model.max_sh_degree,
        fagk_enabled=reference_model.fagk_enabled,
        fagk_max_degree=reference_model.fagk_max_degree,
    )

    device = all_xyz[0].device
    merged._xyz = nn.Parameter(torch.cat(all_xyz, dim=0))
    merged._features_dc = nn.Parameter(torch.cat(all_dc, dim=0))
    merged._features_rest = nn.Parameter(torch.cat(all_rest, dim=0))
    merged._scaling = nn.Parameter(torch.cat(all_scaling, dim=0))
    merged._rotation = nn.Parameter(torch.cat(all_rotation, dim=0))
    merged._opacity = nn.Parameter(torch.cat(all_opacity, dim=0))

    if all_opacity_sh:
        merged._opacity_sh = nn.Parameter(torch.cat(all_opacity_sh, dim=0))
    else:
        merged._opacity_sh = nn.Parameter(torch.empty(merged._xyz.shape[0], 0, device=device))

    n_pts = merged._xyz.shape[0]
    merged.xyz_gradient_accum = torch.zeros(n_pts, 1, device=device)
    merged.denom = torch.zeros(n_pts, 1, device=device)
    merged.max_radii2D = torch.zeros(n_pts, device=device)

    # Set active SH degrees to max (training is done)
    merged.active_sh_degree = reference_model.max_sh_degree
    merged.active_fagk_degree = reference_model.fagk_max_degree

    return merged
