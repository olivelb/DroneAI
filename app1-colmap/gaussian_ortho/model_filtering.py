"""
Gaussian model spatial filtering (CuPy).

Removes outlier Gaussians after training: out-of-bounds, transparent,
needle-shaped, SOR isolated, disconnected components, and Z-floaters.
"""
import cupy as cp
import numpy as np


def filter_gaussians(
    model,
    cam_positions: np.ndarray,
    *,
    max_scale: float = 1.0,
    dist_multiplier: float = 1.0,
    opacity_threshold: float = 0.005,
    needle_ratio: float = 0.0,
    sor_sigma: float = 4.0,
    sor_enabled: bool = False,
    cc_enabled: bool = False,
    z_floater_enabled: bool = False,
    minimum_retained_ratio: float = 0.0,
    R_geo: np.ndarray = None,
    report_fn=None,
):
    """Apply the full filtering pipeline to a GaussianModel (in-place)."""
    from .filter_quality import require_minimum_filter_retention

    from scipy.spatial import cKDTree
    from scipy.spatial.distance import pdist

    initial_count = model.num_gaussians

    def _log(msg):
        if report_fn:
            report_fn(msg)
        else:
            print(msg)

    # --- Oversized Gaussian filter ---
    n_before = model.num_gaussians
    if max_scale > 0:
        activated_scales = model.scales          # (N, 3)
        max_per_gauss = activated_scales.max(axis=-1)
        not_huge = max_per_gauss <= max_scale
        model.filter_by_mask(not_huge)
        if model.num_gaussians < n_before:
            _log(f"Max-scale filter (>{max_scale:.3f}): {n_before} → {model.num_gaussians}")

    # --- Spatial distance filter ---
    n_before = model.num_gaussians
    if dist_multiplier > 0:
        max_cam_dist = float(np.max(pdist(cam_positions)))
        boundary = dist_multiplier * max_cam_dist
        xyz_np = cp.asnumpy(model.positions)
        cam_tree = cKDTree(cam_positions)
        gauss_dists, _ = cam_tree.query(xyz_np, k=1)
        in_bounds = cp.array(gauss_dists <= boundary)
        model.filter_by_mask(in_bounds)
        if model.num_gaussians < n_before:
            _log(f"Distance filter (>{boundary:.2f}): {n_before} → {model.num_gaussians}")

    # --- Opacity filter ---
    n_before = model.num_gaussians
    if opacity_threshold > 0:
        visible = model.opacity.squeeze(-1) > opacity_threshold
        model.filter_by_mask(visible)
        if model.num_gaussians < n_before:
            _log(f"Opacity filter (<{opacity_threshold}): {n_before} → {model.num_gaussians}")

    # --- Needle (anisotropy) filter ---
    n_before = model.num_gaussians
    if needle_ratio > 0:
        log_scales = model._scaling.copy()
        sorted_log = cp.sort(log_scales, axis=-1)
        aniso_ratio = cp.exp(sorted_log[:, 2] - sorted_log[:, 0])
        not_needle = aniso_ratio <= needle_ratio
        model.filter_by_mask(not_needle)
        if model.num_gaussians < n_before:
            _log(f"Needle filter (>{needle_ratio:.0f}): {n_before} → {model.num_gaussians}")

    # --- Statistical outlier removal (SOR) ---
    if sor_enabled:
        xyz_np = cp.asnumpy(model.positions)
        k_sor = 16
        tree = cKDTree(xyz_np)
        dists, _ = tree.query(xyz_np, k=k_sor + 1)
        mean_dists = dists[:, 1:].mean(axis=1)
        mu, sigma = mean_dists.mean(), mean_dists.std()
        sor_thresh = mu + sor_sigma * sigma
        sor_keep = cp.array(mean_dists <= sor_thresh)
        n_b = model.num_gaussians
        model.filter_by_mask(sor_keep)
        if model.num_gaussians < n_b:
            _log(f"SOR filter: {n_b} → {model.num_gaussians}")

    # --- Connected-component filter ---
    if cc_enabled:
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import connected_components

        k_cc = 16
        xyz_np = cp.asnumpy(model.positions)
        N_cc = len(xyz_np)
        tree = cKDTree(xyz_np)
        _, idx_k = tree.query(xyz_np, k=k_cc + 1)
        rows = np.repeat(np.arange(N_cc), k_cc)
        cols = idx_k[:, 1:].ravel()
        adj = csr_matrix(
            (np.ones(len(rows), dtype=np.float32), (rows, cols)),
            shape=(N_cc, N_cc),
        )
        n_components, labels = connected_components(adj, directed=False)
        if n_components > 1:
            unique, counts = np.unique(labels, return_counts=True)
            cc_keep = cp.array(labels == unique[counts.argmax()])
            n_b = model.num_gaussians
            model.filter_by_mask(cc_keep)
            if model.num_gaussians < n_b:
                _log(f"CC filter: {n_b} → {model.num_gaussians} "
                     f"({n_components - 1} disconnected clusters removed)")

    # --- Z-floater removal (IQR-based) ---
    if z_floater_enabled:
        if R_geo is not None:
            R_geo_cp = cp.array(R_geo, dtype=cp.float32)
            z_vals = (R_geo_cp[2:3, :] @ model._xyz.T).squeeze(0)
        else:
            z_vals = model._xyz[:, 2]
        q25 = float(cp.quantile(z_vals, 0.25))
        q75 = float(cp.quantile(z_vals, 0.75))
        z_iqr = q75 - q25
        z_lo, z_hi = q25 - 5.0 * z_iqr, q75 + 5.0 * z_iqr
        z_keep = (z_vals >= z_lo) & (z_vals <= z_hi)
        n_b = model.num_gaussians
        model.filter_by_mask(z_keep)
        if model.num_gaussians < n_b:
            _log(f"Z-floater filter: {n_b} → {model.num_gaussians} "
                 f"(Z outside [{z_lo:.2f}, {z_hi:.2f}])")

    retained_ratio = require_minimum_filter_retention(
        initial_count,
        model.num_gaussians,
        minimum_retained_ratio,
    )
    _log(
        "Filter retention: "
        f"{model.num_gaussians}/{initial_count} ({retained_ratio:.1%})"
    )

    return model
