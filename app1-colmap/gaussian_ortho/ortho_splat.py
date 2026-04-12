"""
TDOM generation orchestrator (CuPy — no PyTorch).

Loads a trained Gaussian model checkpoint, computes scene extent,
and renders the full orthographic TDOM (RGB + optional height map).
"""
import os
from pathlib import Path

import cupy as cp
import numpy as np

from .gaussian_model import GaussianModel
from .ortho_renderer import render_orthophoto, compute_ortho_extent


def generate_tdom(checkpoint_path: str, gsd: float = 0.02,
                  output_rgb_path: str = None,
                  output_height_path: str = None,
                  sh_degree: int = 3, fagk: bool = True,
                  chunk_size: int = 0,
                  device=None,
                  report_fn=None):
    """
    Generate a TDOM from a trained Gaussian model.

    Parameters
    ----------
    checkpoint_path : str
        Path to the .ply checkpoint.
    gsd : float
        Ground sample distance in scene units (metres).
    output_rgb_path : str
        If given, save RGB orthophoto as .npy.
    output_height_path : str
        If given, save height map as .npy.
    sh_degree, fagk : model config.
    chunk_size : max tile pixel dimension.
    device : ignored (kept for API compatibility).
    report_fn : callable for progress logging.

    Returns
    -------
    dict with 'rgb', 'height', 'extent', 'gsd'
    """
    if report_fn:
        report_fn(f"Loading model from {checkpoint_path}")

    model = GaussianModel(sh_degree=sh_degree, fagk_enabled=fagk)
    model.load_ply(checkpoint_path)
    model.active_sh_degree = sh_degree
    if fagk:
        model.active_fagk_degree = model.fagk_max_degree

    if report_fn:
        report_fn(f"Model loaded: {model.num_gaussians} Gaussians")

    extent = compute_ortho_extent(model)
    if report_fn:
        x0, x1, y0, y1, z0, z1 = extent
        report_fn(f"Scene extent: X[{x0:.1f},{x1:.1f}] Y[{y0:.1f},{y1:.1f}] Z[{z0:.1f},{z1:.1f}]")

    result = render_orthophoto(
        model, gsd=gsd, extent=extent,
        chunk_size=chunk_size,
    )

    if report_fn:
        h, w = result["rgb"].shape[:2]
        report_fn(f"Orthophoto rendered: {w}x{h} px at GSD={gsd}")

    if output_rgb_path:
        os.makedirs(os.path.dirname(output_rgb_path) or ".", exist_ok=True)
        np.save(output_rgb_path, result["rgb"])

    if output_height_path:
        os.makedirs(os.path.dirname(output_height_path) or ".", exist_ok=True)
        np.save(output_height_path, result["height"])

    return result
