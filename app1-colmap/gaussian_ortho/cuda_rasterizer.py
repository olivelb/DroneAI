"""
Custom CUDA Gaussian rasterizer for orthographic rendering.

Replaces gsplat with a standalone CuPy-based implementation.
No PyTorch dependency — only CuPy + inline CUDA kernels (JIT-compiled).

Pipeline:
  1. Project 3D Gaussians to 2D (orthographic)
  2. Compute 2D covariance → conic (inverse)
  3. Evaluate SH colours with uniform nadir view direction
  4. Tile-based binning (16×16 pixel tiles)
  5. Per-tile depth sort
  6. Alpha-compositing CUDA kernel (one thread per pixel)
"""
import math

import cupy as cp
import numpy as np

TILE_SIZE = 16

# =====================================================================
#  SH evaluation (vectorised CuPy)
# =====================================================================

SH_C0 = 0.28209479177387814


def eval_sh_basis(degree, dirs):
    """Evaluate SH basis functions.  dirs: (N, 3) CuPy array."""
    N = dirs.shape[0]
    n_coeffs = (degree + 1) ** 2
    result = cp.zeros((N, n_coeffs), dtype=cp.float32)

    x, y, z = dirs[:, 0], dirs[:, 1], dirs[:, 2]
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


def eval_sh(degree, dirs, sh_coeffs):
    """Evaluate SH → RGB.  sh_coeffs: (N, K, 3), dirs: (N, 3)."""
    basis = eval_sh_basis(degree, dirs)     # (N, (deg+1)^2)
    K = sh_coeffs.shape[1]
    basis = basis[:, :K]                     # trim to available coefficients
    return cp.sum(basis[:, :, None] * sh_coeffs, axis=1)   # (N, 3)


# =====================================================================
#  Quaternion → rotation matrix (vectorised)
# =====================================================================

def quat_to_rotmat(quats):
    """(N, 4) quaternions (w, x, y, z) → (N, 3, 3) rotation matrices."""
    w, x, y, z = quats[:, 0], quats[:, 1], quats[:, 2], quats[:, 3]
    xx = x * x; yy = y * y; zz = z * z
    xy = x * y; xz = x * z; yz = y * z
    wx = w * x; wy = w * y; wz = w * z

    N = len(quats)
    R = cp.empty((N, 3, 3), dtype=cp.float32)
    R[:, 0, 0] = 1 - 2 * (yy + zz)
    R[:, 0, 1] = 2 * (xy - wz)
    R[:, 0, 2] = 2 * (xz + wy)
    R[:, 1, 0] = 2 * (xy + wz)
    R[:, 1, 1] = 1 - 2 * (xx + zz)
    R[:, 1, 2] = 2 * (yz - wx)
    R[:, 2, 0] = 2 * (xz - wy)
    R[:, 2, 1] = 2 * (yz + wx)
    R[:, 2, 2] = 1 - 2 * (xx + yy)
    return R


# =====================================================================
#  CUDA Kernels (JIT-compiled by CuPy, cached after first run)
# =====================================================================

_BINNING_KERNEL = cp.RawKernel(r'''
extern "C" __global__ void compute_tile_pairs(
    const float* __restrict__ means2d,
    const float* __restrict__ radii,
    const float* __restrict__ depths,
    const int*   __restrict__ pair_offsets,
    int N,
    int tiles_x, int tiles_y, int tile_size,
    int*   __restrict__ out_tile_ids,
    int*   __restrict__ out_gauss_ids,
    float* __restrict__ out_depths
) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= N) return;

    float mx = means2d[idx * 2];
    float my = means2d[idx * 2 + 1];
    float r  = radii[idx];
    float d  = depths[idx];

    int tx_min = max(0, (int)floorf((mx - r) / (float)tile_size));
    int tx_max = min(tiles_x - 1, (int)floorf((mx + r) / (float)tile_size));
    int ty_min = max(0, (int)floorf((my - r) / (float)tile_size));
    int ty_max = min(tiles_y - 1, (int)floorf((my + r) / (float)tile_size));

    int offset = pair_offsets[idx];
    for (int ty = ty_min; ty <= ty_max; ty++) {
        for (int tx = tx_min; tx <= tx_max; tx++) {
            out_tile_ids[offset]  = ty * tiles_x + tx;
            out_gauss_ids[offset] = idx;
            out_depths[offset]    = d;
            offset++;
        }
    }
}
''', 'compute_tile_pairs')


_RENDER_KERNEL = cp.RawKernel(r'''
extern "C" __global__ void render_tiles(
    const float* __restrict__ means2d,
    const float* __restrict__ conics,
    const float* __restrict__ colors,
    const float* __restrict__ opacities,
    const float* __restrict__ depths,
    const int*   __restrict__ sorted_gauss_ids,
    const int*   __restrict__ tile_offsets,
    float* __restrict__ out_rgb,
    float* __restrict__ out_depth,
    float bg_r, float bg_g, float bg_b,
    int width, int height, int tile_size
) {
    int px = blockIdx.x * tile_size + threadIdx.x;
    int py = blockIdx.y * tile_size + threadIdx.y;
    if (px >= width || py >= height) return;

    int tiles_per_row = (width + tile_size - 1) / tile_size;
    int tile_id = (int)blockIdx.y * tiles_per_row + (int)blockIdx.x;

    int start = tile_offsets[tile_id];
    int end   = tile_offsets[tile_id + 1];

    float T     = 1.0f;
    float r_acc = 0.0f, g_acc = 0.0f, b_acc = 0.0f;
    float d_acc = 0.0f;

    float pixel_x = (float)px + 0.5f;
    float pixel_y = (float)py + 0.5f;

    for (int i = start; i < end; i++) {
        if (T < 1e-4f) break;

        int gid = sorted_gauss_ids[i];
        float mx = means2d[gid * 2];
        float my = means2d[gid * 2 + 1];
        float dx = pixel_x - mx;
        float dy = pixel_y - my;

        float a = conics[gid * 3];
        float b = conics[gid * 3 + 1];
        float c = conics[gid * 3 + 2];

        float power = -0.5f * (a * dx * dx + 2.0f * b * dx * dy + c * dy * dy);
        if (power > 0.0f) power = 0.0f;

        float alpha_val = opacities[gid] * __expf(power);
        if (alpha_val < 1.0f / 255.0f) continue;
        alpha_val = fminf(alpha_val, 0.99f);

        float w = alpha_val * T;
        r_acc += w * colors[gid * 3];
        g_acc += w * colors[gid * 3 + 1];
        b_acc += w * colors[gid * 3 + 2];
        d_acc += w * depths[gid];

        T *= (1.0f - alpha_val);
    }

    int idx = py * width + px;
    out_rgb[idx * 3]     = r_acc + T * bg_r;
    out_rgb[idx * 3 + 1] = g_acc + T * bg_g;
    out_rgb[idx * 3 + 2] = b_acc + T * bg_b;
    // d_acc uses the same front-to-back alpha weights as RGB.  Unlike RGB,
    // depth has no background value to contribute, so leaving it
    // premultiplied by accumulated opacity biases semi-transparent pixels
    // toward zero and makes z_cam - depth several metres too high.
    float accumulated_opacity = 1.0f - T;
    out_depth[idx] = accumulated_opacity > 1.0e-6f
        ? d_acc / accumulated_opacity
        : 0.0f;
}
''', 'render_tiles')


# =====================================================================
#  Main rasterisation entry point
# =====================================================================

def rasterize_ortho(
    means_3d,      # (N, 3) cp float32 — world positions
    quats,         # (N, 4) cp float32 — normalised wxyz quaternions
    scales,        # (N, 3) cp float32 — activated scales (exp)
    opacities,     # (N,)   cp float32 — activated opacities [0, 1]
    sh_coeffs,     # (N, K, 3) cp float32 — SH coefficients
    sh_degree,     # int or None
    viewmat,       # (4, 4) cp float32 — world-to-camera
    fx, fy, cx, cy,
    width, height,
    znear=0.01, zfar=1000.0,
    bg_color=(1.0, 1.0, 1.0),
    eps2d=0.03,
    compensate_filter=True,
):
    """
    Orthographic rasterisation of 3D Gaussians.

    Returns
    -------
    rgb : cp.ndarray (H, W, 3) float32   — rendered RGB
    depth : cp.ndarray (H, W) float32     — rendered depth
    """
    if not math.isfinite(float(eps2d)) or float(eps2d) < 0.0:
        raise ValueError("eps2d must be finite and non-negative")
    N = means_3d.shape[0]
    bg = cp.array(bg_color[:3], dtype=cp.float32)
    fx, fy, cx, cy = float(fx), float(fy), float(cx), float(cy)

    if N == 0:
        rgb = cp.empty((height, width, 3), dtype=cp.float32)
        rgb[:] = bg
        return rgb, cp.zeros((height, width), dtype=cp.float32)

    # ---- 1. Camera-space transform (orthographic projection) ----
    R_view = viewmat[:3, :3]
    t_view = viewmat[:3, 3]
    cam_pts = means_3d @ R_view.T + t_view          # (N, 3)
    depths = cp.ascontiguousarray(cam_pts[:, 2], dtype=cp.float32)

    means_2d = cp.empty((N, 2), dtype=cp.float32)
    means_2d[:, 0] = fx * cam_pts[:, 0] + cx
    means_2d[:, 1] = fy * cam_pts[:, 1] + cy

    # ---- 2. Evaluate SH colours (uniform nadir direction) ----
    c2w = cp.linalg.inv(viewmat)
    cam_fwd = c2w[:3, 2]                             # camera +Z in world
    dirs = cp.broadcast_to(cam_fwd[None, :], (N, 3)).copy()

    if sh_degree is not None and sh_degree > 0:
        colors = eval_sh(sh_degree, dirs, sh_coeffs)
        colors = cp.clip(colors + 0.5, 0.0, None)
    else:
        colors = sh_coeffs[:, 0, :] * SH_C0 + 0.5
        colors = cp.clip(colors, 0.0, None)
    colors = cp.ascontiguousarray(colors.astype(cp.float32))

    # ---- 3. 3D covariance → 2D covariance ----
    rotmats = quat_to_rotmat(quats)                   # (N, 3, 3)
    S = cp.zeros((N, 3, 3), dtype=cp.float32)
    S[:, 0, 0] = scales[:, 0]
    S[:, 1, 1] = scales[:, 1]
    S[:, 2, 2] = scales[:, 2]
    M = cp.matmul(rotmats, S)                          # (N, 3, 3)
    cov3d = cp.matmul(M, M.transpose(0, 2, 1))        # (N, 3, 3)

    J = cp.array([[fx, 0, 0], [0, fy, 0]], dtype=cp.float32)     # (2, 3)
    T_mat = J @ R_view                                             # (2, 3)
    temp = cp.matmul(T_mat[None, :, :], cov3d)                    # (N, 2, 3)
    cov2d = cp.matmul(temp, T_mat[None, :, :].swapaxes(-1, -2))  # (N, 2, 2)

    # Mip-Splatting 2D Mip filter.  The determinant ratio preserves the
    # integrated opacity after widening the covariance by the pixel-space
    # low-pass variance.  Omitting it dilates every splat and softens edges.
    det_unfiltered = (
        cov2d[:, 0, 0] * cov2d[:, 1, 1] - cov2d[:, 0, 1] ** 2
    )
    cov2d[:, 0, 0] += eps2d
    cov2d[:, 1, 1] += eps2d

    # ---- 4. Conic (inverse covariance) + radius ----
    det = cov2d[:, 0, 0] * cov2d[:, 1, 1] - cov2d[:, 0, 1] ** 2
    if compensate_filter:
        opacity_compensation = cp.sqrt(
            cp.maximum(det_unfiltered, 0.0) / cp.maximum(det, 1.0e-10)
        ).astype(cp.float32)
    else:
        opacity_compensation = cp.ones_like(det, dtype=cp.float32)
    valid = det > 1e-10
    inv_det = cp.where(valid, 1.0 / cp.maximum(det, 1e-10), 0.0).astype(cp.float32)

    conics = cp.empty((N, 3), dtype=cp.float32)
    conics[:, 0] =  cov2d[:, 1, 1] * inv_det
    conics[:, 1] = -cov2d[:, 0, 1] * inv_det
    conics[:, 2] =  cov2d[:, 0, 0] * inv_det

    trace = cov2d[:, 0, 0] + cov2d[:, 1, 1]
    disc = cp.maximum(trace * trace - 4 * det, 0)
    lambda_max = 0.5 * (trace + cp.sqrt(disc))
    radii = cp.ceil(3.0 * cp.sqrt(cp.maximum(lambda_max, 0.0))).astype(cp.float32)

    # ---- 5. Filter visible Gaussians ----
    in_bounds = (
        valid &
        (means_2d[:, 0] + radii > 0) & (means_2d[:, 0] - radii < width) &
        (means_2d[:, 1] + radii > 0) & (means_2d[:, 1] - radii < height) &
        (depths > znear) & (depths < zfar)
    )
    idx_valid = cp.nonzero(in_bounds)[0]
    if idx_valid.size == 0:
        rgb = cp.empty((height, width, 3), dtype=cp.float32)
        rgb[:] = bg
        return rgb, cp.zeros((height, width), dtype=cp.float32)

    # Compact to valid subset
    means_2d_v = cp.ascontiguousarray(means_2d[idx_valid])
    conics_v   = cp.ascontiguousarray(conics[idx_valid])
    colors_v   = cp.ascontiguousarray(colors[idx_valid])
    opac_v     = cp.ascontiguousarray(
        opacities[idx_valid] * opacity_compensation[idx_valid]
    )
    depths_v   = cp.ascontiguousarray(depths[idx_valid])
    radii_v    = cp.ascontiguousarray(radii[idx_valid])
    N_v = idx_valid.size

    # ---- 6. Tile binning ----
    tiles_x = (width + TILE_SIZE - 1) // TILE_SIZE
    tiles_y = (height + TILE_SIZE - 1) // TILE_SIZE
    num_tiles = tiles_x * tiles_y

    tx_min = cp.maximum(0, cp.floor((means_2d_v[:, 0] - radii_v) / TILE_SIZE).astype(cp.int32))
    tx_max = cp.minimum(tiles_x - 1, cp.floor((means_2d_v[:, 0] + radii_v) / TILE_SIZE).astype(cp.int32))
    ty_min = cp.maximum(0, cp.floor((means_2d_v[:, 1] - radii_v) / TILE_SIZE).astype(cp.int32))
    ty_max = cp.minimum(tiles_y - 1, cp.floor((means_2d_v[:, 1] + radii_v) / TILE_SIZE).astype(cp.int32))

    n_tiles_per = cp.maximum((tx_max - tx_min + 1) * (ty_max - ty_min + 1), 0).astype(cp.int32)
    cumsum = cp.cumsum(n_tiles_per)
    total_pairs = int(cumsum[-1]) if N_v > 0 else 0

    if total_pairs == 0:
        rgb = cp.empty((height, width, 3), dtype=cp.float32)
        rgb[:] = bg
        return rgb, cp.zeros((height, width), dtype=cp.float32)

    pair_offsets = cp.zeros(N_v, dtype=cp.int32)
    if N_v > 1:
        pair_offsets[1:] = cumsum[:-1]

    # Scatter (tile_id, gauss_id, depth) pairs via CUDA kernel
    out_tile_ids  = cp.empty(total_pairs, dtype=cp.int32)
    out_gauss_ids = cp.empty(total_pairs, dtype=cp.int32)
    out_depths    = cp.empty(total_pairs, dtype=cp.float32)

    threads = 256
    blocks = (N_v + threads - 1) // threads
    _BINNING_KERNEL(
        (blocks,), (threads,),
        (means_2d_v, radii_v, depths_v, pair_offsets,
         np.int32(N_v), np.int32(tiles_x), np.int32(tiles_y), np.int32(TILE_SIZE),
         out_tile_ids, out_gauss_ids, out_depths),
    )

    # ---- 7. Sort by (tile_id, depth) ----
    sort_keys = cp.stack((out_depths, out_tile_ids))
    sort_order = cp.lexsort(sort_keys)
    sorted_gauss_ids = cp.ascontiguousarray(out_gauss_ids[sort_order].astype(cp.int32))
    sorted_tile_ids  = out_tile_ids[sort_order]

    tile_offsets = cp.searchsorted(
        sorted_tile_ids,
        cp.arange(num_tiles + 1, dtype=cp.int32),
    ).astype(cp.int32)
    tile_offsets = cp.ascontiguousarray(tile_offsets)

    # Free intermediate buffers
    del out_tile_ids, out_gauss_ids, out_depths, sort_order, sorted_tile_ids

    # ---- 8. Render ----
    out_rgb   = cp.zeros(height * width * 3, dtype=cp.float32)
    out_depth = cp.zeros(height * width, dtype=cp.float32)

    grid  = (tiles_x, tiles_y)
    block = (TILE_SIZE, TILE_SIZE)

    _RENDER_KERNEL(
        grid, block,
        (means_2d_v, conics_v, colors_v, opac_v, depths_v,
         sorted_gauss_ids, tile_offsets,
         out_rgb, out_depth,
         np.float32(float(bg[0])), np.float32(float(bg[1])), np.float32(float(bg[2])),
         np.int32(width), np.int32(height), np.int32(TILE_SIZE)),
    )

    return out_rgb.reshape(height, width, 3), out_depth.reshape(height, width)
