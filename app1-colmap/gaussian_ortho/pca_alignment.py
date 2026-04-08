"""
PCA-based geo-alignment for COLMAP reconstructions.

Computes the rotation from COLMAP coordinates to geo-aligned
(East, North, Up) frame using PCA of camera positions.
"""
import numpy as np


def compute_pca_rotation(cameras, point_cloud_points: np.ndarray) -> np.ndarray:
    """Compute PCA rotation (COLMAP → geo-aligned Z=Up).

    Parameters
    ----------
    cameras : list of CameraInfo
        Training cameras with .T (camera position) and .R (rotation) attributes.
    point_cloud_points : np.ndarray (N, 3)
        3D points (used to verify cameras are above the scene).

    Returns
    -------
    R_geo : np.ndarray (3, 3) float64
        Rotation matrix mapping COLMAP coords → geo-aligned frame.
    """
    from numpy.linalg import svd as _svd

    cam_positions = np.array([c.T for c in cameras], dtype=np.float64)
    centroid = cam_positions.mean(axis=0)
    _, _, Vt = _svd(cam_positions - centroid, full_matrices=False)

    up_est = Vt[2].astype(np.float64)  # smallest eigenvalue direction
    up_est = up_est / max(np.linalg.norm(up_est), 1e-9)

    # Build rotation that maps up_est → [0, 0, 1]
    target = np.array([0.0, 0.0, 1.0])
    v = np.cross(up_est, target)
    c_dot = float(np.dot(up_est, target))
    if np.linalg.norm(v) < 1e-8:
        # Already aligned (or exactly anti-parallel)
        R_align = np.eye(3) if c_dot > 0 else np.diag([1.0, -1.0, -1.0])
    else:
        vx = np.array([[0, -v[2], v[1]],
                       [v[2], 0, -v[0]],
                       [-v[1], v[0], 0]])
        R_align = np.eye(3) + vx + vx @ vx / (1.0 + c_dot)

    # Post-rotation check: cameras must be ABOVE the scene (drone data).
    rot_cam_z = (R_align @ cam_positions.T)[2, :].mean()
    rot_scene_z = (R_align @ point_cloud_points.T)[2, :].mean()
    if rot_cam_z < rot_scene_z:
        R_align = np.diag([1.0, -1.0, -1.0]) @ R_align
        c_dot = -c_dot

    angle_deg = float(np.degrees(np.arccos(np.clip(abs(c_dot), -1, 1))))

    return R_align.astype(np.float64), angle_deg
