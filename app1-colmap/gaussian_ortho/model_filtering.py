"""
Gaussian model spatial filtering.

Removes outlier Gaussians after training: out-of-bounds, transparent,
needle-shaped, SOR isolated, disconnected components, and Z-floaters.
"""
import numpy as np
import torch


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
    R_geo: np.ndarray = None,
    report_fn=None,
):
    """Apply the full filtering pipeline to a GaussianModel.

    Parameters
    ----------
    model : GaussianModel
        Model to filter in-place.
    cam_positions : np.ndarray (N, 3)
        Camera positions in the same coordinate frame as the model.
    max_scale : float
        Maximum activated scale for any axis.  Gaussians with any axis
        larger than this are removed.  0 = disabled.
    dist_multiplier : float
        Distance multiplier for the spatial boundary.  The boundary is
        ``dist_multiplier * max_camera_pairwise_distance``.  Lower values
        crop more aggressively.  0 = disabled.
    opacity_threshold : float
        Minimum sigmoid-activated opacity to keep (default 0.005 ≈ nearly
        transparent).  0 = disabled.
    needle_ratio : float
        Max anisotropy ratio (0 = disabled).
    sor_sigma : float
        SOR sigma multiplier.
    sor_enabled, cc_enabled, z_floater_enabled : bool
        Enable/disable individual filters.
    R_geo : np.ndarray (3, 3), optional
        Rotation COLMAP→geo for Z-floater computation (PCA path).
    report_fn : callable, optional
        report_fn(message) for logging.

    Returns
    -------
    model : GaussianModel (same object, filtered in-place)
    """
    from scipy.spatial import cKDTree
    from scipy.spatial.distance import pdist

    device = model._xyz.device

    def _log(msg):
        if report_fn:
            report_fn(msg)
        else:
            print(msg)

    # --- Oversized Gaussian filter ---
    n_before = model.num_gaussians
    if max_scale > 0:
        activated_scales = model.scales.detach()  # (N, 3) — exp of log-scales
        max_per_gauss = activated_scales.max(dim=-1).values
        not_huge = max_per_gauss <= max_scale
        model.filter_by_mask(not_huge)
        if model.num_gaussians < n_before:
            _log(f"Max-scale filter (>{max_scale:.3f}): {n_before} → {model.num_gaussians}")
        del activated_scales, max_per_gauss, not_huge

    # --- Spatial distance filter ---
    n_before = model.num_gaussians
    if dist_multiplier > 0:
        max_cam_dist = float(np.max(pdist(cam_positions)))
        boundary = dist_multiplier * max_cam_dist
        xyz_np = model.positions.detach().cpu().numpy()
        cam_tree = cKDTree(cam_positions)
        gauss_dists, _ = cam_tree.query(xyz_np, k=1)
        in_bounds = torch.tensor(gauss_dists <= boundary, device=device)
        model.filter_by_mask(in_bounds)
        if model.num_gaussians < n_before:
            _log(f"Distance filter (>{boundary:.2f}): {n_before} → {model.num_gaussians}")
        del xyz_np, gauss_dists, in_bounds

    # --- Opacity filter ---
    n_before = model.num_gaussians
    if opacity_threshold > 0:
        visible = model.opacity.squeeze(-1).detach() > opacity_threshold
        model.filter_by_mask(visible)
        if model.num_gaussians < n_before:
            _log(f"Opacity filter (<{opacity_threshold}): {n_before} → {model.num_gaussians}")
        del visible

    # --- Needle (anisotropy) filter ---
    n_before = model.num_gaussians
    if needle_ratio > 0:
        log_scales = model._scaling.detach()
        sorted_log, _ = log_scales.sort(dim=-1)
        aniso_ratio = (sorted_log[:, 2] - sorted_log[:, 0]).exp()
        not_needle = aniso_ratio <= needle_ratio
        model.filter_by_mask(not_needle)
        if model.num_gaussians < n_before:
            _log(f"Needle filter (>{needle_ratio:.0f}): {n_before} → {model.num_gaussians}")
        del log_scales, sorted_log, aniso_ratio, not_needle

    # --- Statistical outlier removal (SOR) ---
    if sor_enabled:
        xyz_np = model.positions.detach().cpu().numpy()
        k_sor = 16
        tree = cKDTree(xyz_np)
        dists, _ = tree.query(xyz_np, k=k_sor + 1)
        mean_dists = dists[:, 1:].mean(axis=1)
        mu, sigma = mean_dists.mean(), mean_dists.std()
        sor_thresh = mu + sor_sigma * sigma
        sor_keep = torch.tensor(mean_dists <= sor_thresh, device=device)
        n_b = model.num_gaussians
        model.filter_by_mask(sor_keep)
        if model.num_gaussians < n_b:
            _log(f"SOR filter: {n_b} → {model.num_gaussians}")
        del xyz_np, tree, dists, mean_dists, sor_keep

    # --- Connected-component filter ---
    if cc_enabled:
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import connected_components

        k_cc = 16
        xyz_np = model.positions.detach().cpu().numpy()
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
            cc_keep = torch.tensor(labels == unique[counts.argmax()], device=device)
            n_b = model.num_gaussians
            model.filter_by_mask(cc_keep)
            if model.num_gaussians < n_b:
                _log(f"CC filter: {n_b} → {model.num_gaussians} "
                     f"({n_components - 1} disconnected clusters removed)")
            del cc_keep
        del xyz_np, tree, idx_k, adj, labels

    # --- Z-floater removal (IQR-based) ---
    if z_floater_enabled:
        with torch.no_grad():
            if R_geo is not None:
                R_geo_t = torch.tensor(R_geo, dtype=torch.float32, device=device)
                z_vals = (R_geo_t[2:3, :] @ model._xyz.T).squeeze(0).detach()
            else:
                z_vals = model._xyz[:, 2].detach()
            q25 = torch.quantile(z_vals, 0.25).item()
            q75 = torch.quantile(z_vals, 0.75).item()
            z_iqr = q75 - q25
            z_lo, z_hi = q25 - 5.0 * z_iqr, q75 + 5.0 * z_iqr
            z_keep = (z_vals >= z_lo) & (z_vals <= z_hi)
            n_b = model.num_gaussians
            model.filter_by_mask(z_keep)
            if model.num_gaussians < n_b:
                _log(f"Z-floater filter: {n_b} → {model.num_gaussians} "
                     f"(Z outside [{z_lo:.2f}, {z_hi:.2f}])")
            del z_vals, z_keep

    return model
