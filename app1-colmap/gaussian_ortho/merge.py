"""
Merge Gaussian models from partitioned cells into a single model.

After independent per-cell training, this module stitches the results
by retaining only the Gaussians whose centres fall within the *core*
(non-overlap) region of each cell, discarding duplicates in overlapping
borders.
"""
import cupy as cp

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
    (non-overlap) region are kept.
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
        xyz = model._xyz
        mask = (
            (xyz[:, 0] >= core.x_min) & (xyz[:, 0] <= core.x_max) &
            (xyz[:, 1] >= core.y_min) & (xyz[:, 1] <= core.y_max)
        )
        if not cp.any(mask):
            continue

        all_xyz.append(xyz[mask])
        all_dc.append(model._features_dc[mask])
        all_rest.append(model._features_rest[mask])
        all_scaling.append(model._scaling[mask])
        all_rotation.append(model._rotation[mask])
        all_opacity.append(model._opacity[mask])
        if model.fagk_enabled and model._opacity_sh.shape[1] > 0:
            all_opacity_sh.append(model._opacity_sh[mask])

    merged = GaussianModel(
        sh_degree=reference_model.max_sh_degree,
        fagk_enabled=reference_model.fagk_enabled,
        fagk_max_degree=reference_model.fagk_max_degree,
    )

    merged._xyz = cp.concatenate(all_xyz, axis=0)
    merged._features_dc = cp.concatenate(all_dc, axis=0)
    merged._features_rest = cp.concatenate(all_rest, axis=0)
    merged._scaling = cp.concatenate(all_scaling, axis=0)
    merged._rotation = cp.concatenate(all_rotation, axis=0)
    merged._opacity = cp.concatenate(all_opacity, axis=0)

    if all_opacity_sh:
        merged._opacity_sh = cp.concatenate(all_opacity_sh, axis=0)
    else:
        merged._opacity_sh = cp.empty((merged._xyz.shape[0], 0), dtype=cp.float32)

    merged.active_sh_degree = reference_model.max_sh_degree
    merged.active_fagk_degree = reference_model.fagk_max_degree

    return merged
