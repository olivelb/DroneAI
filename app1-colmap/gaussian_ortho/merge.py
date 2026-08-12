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


def merge_models(
    cell_models: list[tuple[CellBounds, GaussianModel]],
) -> GaussianModel:
    """
    Merge multiple per-cell GaussianModels into one.

    For each cell, only Gaussians whose XY centre lies within the core
    (non-overlap) region are kept.
    """
    if not cell_models:
        raise ValueError("at least one partitioned model is required")

    all_xyz = []
    all_dc = []
    all_rest = []
    all_scaling = []
    all_rotation = []
    all_opacity = []
    all_opacity_sh = []

    reference_model = cell_models[0][1]
    opacity_sh_widths = {
        int(model.opacity_sh.shape[1])
        for _cell, model in cell_models
        if model.opacity_sh.shape[1] > 0
    }
    if len(opacity_sh_widths) > 1:
        raise ValueError("partitioned models use incompatible opacity-SH degrees")
    opacity_sh_width = next(iter(opacity_sh_widths), 0)

    for cell, model in cell_models:
        xyz = model._xyz
        mask = cell.core_mask(xyz, array_module=cp)
        if not cp.any(mask):
            continue

        all_xyz.append(xyz[mask])
        all_dc.append(model._features_dc[mask])
        all_rest.append(model._features_rest[mask])
        all_scaling.append(model._scaling[mask])
        all_rotation.append(model._rotation[mask])
        all_opacity.append(model._opacity[mask])
        if opacity_sh_width > 0:
            if model.opacity_sh.shape[1] == opacity_sh_width:
                all_opacity_sh.append(model.opacity_sh[mask])
            elif model.opacity_sh.shape[1] == 0:
                all_opacity_sh.append(
                    cp.zeros(
                        (int(cp.count_nonzero(mask).item()), opacity_sh_width),
                        dtype=cp.float32,
                    )
                )
            else:
                raise ValueError(
                    "partitioned models use incompatible opacity-SH degrees"
                )

    if not all_xyz:
        raise RuntimeError("partition cores retained no Gaussians")

    merged = GaussianModel(
        sh_degree=reference_model.max_sh_degree,
        opacity_sh_enabled=reference_model.opacity_sh_enabled,
        opacity_sh_max_degree=reference_model.opacity_sh_max_degree,
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
    merged.active_opacity_sh_degree = reference_model.opacity_sh_max_degree

    return merged
