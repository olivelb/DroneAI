"""
Load COLMAP sparse reconstructions into 3DGS-compatible data structures.

Reads cameras.bin / images.bin / points3D.bin from a COLMAP sparse model
directory and converts them into numpy arrays suitable for Gaussian Splatting
training initialisation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, cast

import numpy as np


type Sim3Transform = dict[str, Any]


# ---------------------------------------------------------------------------
#  COLMAP binary format readers
# ---------------------------------------------------------------------------


def _read_colmap_dense_array(path: str) -> np.ndarray:
    """Read a COLMAP dense array (depth/normal *.bin) with width&height&channels& header."""
    header_tokens: list[str] = []
    current_token: list[bytes] = []
    with open(path, "rb") as fh:
        while len(header_tokens) < 3:
            char = fh.read(1)
            if not char:
                raise ValueError(f"Incomplete COLMAP dense map header: {path}")
            if char == b"&":
                header_tokens.append(b"".join(current_token).decode("ascii"))
                current_token = []
            else:
                current_token.append(char)
        width, height, channels = (int(t) for t in header_tokens)
        payload = fh.read()
    data = np.frombuffer(payload, dtype="<f4")
    expected = width * height * channels
    if data.size < expected:
        raise ValueError(f"COLMAP dense map truncated: {path}")
    dense: np.ndarray = data[:expected].reshape((height, width, channels))
    result: np.ndarray = dense[:, :, 0] if channels == 1 else dense
    return result


def _resolve_depth_map_path(dense_path: str, image_name: str, preferred: str = "geometric") -> str | None:
    """Find a depth map for the given image, preferring the given type."""
    folder = os.path.join(dense_path, "stereo", "depth_maps")
    for dtype in (preferred, "photometric" if preferred == "geometric" else "geometric"):
        p = os.path.join(folder, f"{image_name}.{dtype}.bin")
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------------------
#  Camera info
# ---------------------------------------------------------------------------


@dataclass
class CameraInfo:
    """Intrinsics + extrinsics for a single image."""

    uid: int
    image_name: str
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    R: np.ndarray  # 3x3 rotation (camera-to-world)
    T: np.ndarray  # 3   translation (camera centre in world)
    image_path: str = ""
    sparse_observations: int = 0
    camera_model: str = "PINHOLE"


@dataclass
class PointCloud:
    """Sparse point cloud from COLMAP SfM."""

    points: np.ndarray  # (N, 3) world XYZ
    colors: np.ndarray  # (N, 3) RGB 0-1
    normals: np.ndarray  # (N, 3) or zeros


# ---------------------------------------------------------------------------
#  Loader
# ---------------------------------------------------------------------------


def _camera_intrinsics(cam: Any) -> tuple[float, float, float, float]:
    """Extract (fx, fy, cx, cy) from a pycolmap Camera regardless of model."""
    params = [float(value) for value in cam.params]
    model = cam.model.name if hasattr(cam.model, "name") else str(cam.model)
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        f, cx, cy = params[0], params[1], params[2]
        return f, f, cx, cy
    elif model in ("PINHOLE", "OPENCV"):
        return params[0], params[1], params[2], params[3]
    else:
        # Best-effort: assume first 4 params are fx, fy, cx, cy
        return (
            params[0],
            params[1] if len(params) > 1 else params[0],
            params[2] if len(params) > 2 else 0,
            params[3] if len(params) > 3 else 0,
        )


def load_colmap_reconstruction(
    dense_path: str,
    transform_file: str | None = None,
    llffhold: int = 0,
    *,
    max_reproj_error: float = 1.0,
    min_track_length: int = 3,
) -> tuple[
    list[CameraInfo],
    list[CameraInfo],
    PointCloud,
    Sim3Transform | None,
]:
    """
    Load a COLMAP dense workspace into 3DGS-compatible structures.

    Parameters
    ----------
    dense_path : str
        Path to the COLMAP dense workspace (contains sparse/, images/).
    transform_file : str, optional
        Path to alignment_transform.json for geo-alignment (Sim3).
    llffhold : int
        If > 0, hold out every N-th image for testing.

    Returns
    -------
    train_cameras : list[CameraInfo]
    test_cameras : list[CameraInfo]
    point_cloud : PointCloud
    transform_data : dict or None
    """
    import pycolmap

    sparse_dir = os.path.join(dense_path, "sparse")
    images_dir = os.path.join(dense_path, "images")
    reconstruction = pycolmap.Reconstruction(sparse_dir)

    # Load alignment transform
    transform_data: Sim3Transform | None = None
    if transform_file and os.path.exists(transform_file):
        with open(transform_file, encoding="utf-8") as fh:
            transform_data = cast(Sim3Transform, json.load(fh))

    # Extract cameras
    all_cameras: list[CameraInfo] = []
    for img_id in sorted(reconstruction.images.keys()):
        image = reconstruction.images[img_id]
        cam = reconstruction.cameras[image.camera_id]
        camera_model = cam.model.name if hasattr(cam.model, "name") else str(cam.model)
        if camera_model not in {"SIMPLE_PINHOLE", "PINHOLE"}:
            raise ValueError(
                "Gaussian native-image footprints require the undistorted "
                "COLMAP dense workspace camera model; expected PINHOLE or "
                f"SIMPLE_PINHOLE, got {camera_model!r} for {image.name!r}"
            )
        fx, fy, cx, cy = _camera_intrinsics(cam)

        # COLMAP: R_cw (world→camera), t_cw
        cfw = image.cam_from_world()
        R_cw = cfw.rotation.matrix()
        t_cw = cfw.translation
        # Camera centre in world coords: C = -R_cw^T @ t_cw
        C_world = -R_cw.T @ t_cw
        # R for 3DGS: camera-to-world rotation
        R_c2w = R_cw.T

        all_cameras.append(
            CameraInfo(
                uid=img_id,
                image_name=image.name,
                width=cam.width,
                height=cam.height,
                fx=fx,
                fy=fy,
                cx=cx,
                cy=cy,
                R=R_c2w.astype(np.float32),
                T=C_world.astype(np.float32),
                image_path=os.path.join(images_dir, image.name),
                sparse_observations=int(image.num_points3D),
                camera_model=camera_model,
            )
        )

    # Train/test split
    train_cameras: list[CameraInfo]
    test_cameras: list[CameraInfo]
    if llffhold > 0:
        train_cameras = [c for i, c in enumerate(all_cameras) if (i + 1) % llffhold != 0]
        test_cameras = [c for i, c in enumerate(all_cameras) if (i + 1) % llffhold == 0]
    else:
        train_cameras = all_cameras
        test_cameras = []

    # Extract sparse point cloud with quality metrics
    pts3d = reconstruction.points3D
    n_pts = len(pts3d)
    points: np.ndarray = np.zeros((n_pts, 3), dtype=np.float32)
    colors: np.ndarray = np.zeros((n_pts, 3), dtype=np.float32)
    reproj_errors: np.ndarray = np.zeros(n_pts, dtype=np.float32)
    track_lengths: np.ndarray = np.zeros(n_pts, dtype=np.int32)
    for i, (_pid, pt) in enumerate(pts3d.items()):
        points[i] = pt.xyz
        colors[i] = np.array(pt.color[:3], dtype=np.float32) / 255.0
        reproj_errors[i] = pt.error if hasattr(pt, "error") else 0.0
        track_lengths[i] = pt.track.length() if hasattr(pt, "track") else 0

    # Filter by reprojection error and track length (like Metashape confidence).
    # Facade products can deliberately choose a more coverage-oriented gate:
    # two-view points are useful along thin borders where a third observation
    # is unavailable, while map mode retains the conservative defaults.
    max_reproj_error = float(max_reproj_error)
    min_track_length = int(min_track_length)
    if max_reproj_error <= 0:
        raise ValueError("max_reproj_error must be positive")
    if min_track_length < 2:
        raise ValueError("min_track_length must be at least 2")
    keep = (reproj_errors <= max_reproj_error) & (track_lengths >= min_track_length)
    n_before = len(points)
    n_removed = n_before - int(keep.sum())
    if n_removed > 0:
        points = points[keep]
        colors = colors[keep]
        reproj_errors = reproj_errors[keep]
        track_lengths = track_lengths[keep]
        print(
            f"[colmap_loader] Point quality filter: {n_before} → {keep.sum()} points "
            f"(removed {n_removed}: reproj_error>{max_reproj_error}px or track_len<{min_track_length})"
        )

    # Spatial proximity filter: remove points far from any camera.
    # COLMAP local coordinates are NOT metric — we use the max inter-camera
    # distance (scene diameter) as an adaptive threshold.  No legitimate scene
    # point should be farther from all cameras than the cameras are from each
    # other, so this is a physically conservative bound.
    from scipy.spatial import cKDTree

    cam_positions = np.array([c.T for c in all_cameras], dtype=np.float64)
    cam_tree = cKDTree(cam_positions)
    from scipy.spatial.distance import pdist

    max_inter_cam = float(np.max(pdist(cam_positions)))
    max_dist = max_inter_cam
    pt_dists, _ = cam_tree.query(points, k=1)
    inside = pt_dists <= max_dist
    n_before_bbox = len(points)
    n_outside = n_before_bbox - int(inside.sum())
    if n_outside > 0:
        points = points[inside]
        colors = colors[inside]
        print(
            f"[colmap_loader] Spatial proximity filter: {n_before_bbox} → {inside.sum()} points "
            f"(removed {n_outside} farther than {max_dist:.2f} units from nearest camera)"
        )
    else:
        print(
            f"[colmap_loader] Spatial proximity filter: all {n_before_bbox} points within "
            f"{max_dist:.2f} units of a camera (scene diameter)"
        )

    normals = np.zeros_like(points)
    point_cloud = PointCloud(points=points, colors=colors, normals=normals)

    return train_cameras, test_cameras, point_cloud, transform_data


def apply_sim3_to_points(
    points: np.ndarray,
    transform_data: Sim3Transform,
) -> np.ndarray:
    """Apply Sim3 alignment (scale * R @ p + t) to an (N, 3) point array."""
    R = np.array(transform_data["R"], dtype=np.float64)
    s = float(transform_data["scale"])
    t = np.array(transform_data["t"], dtype=np.float64)
    transformed: np.ndarray = (s * (R @ points.T)).T + t
    return transformed


def apply_sim3_to_camera(
    cam: CameraInfo,
    transform_data: Sim3Transform,
) -> CameraInfo:
    """Transform a CameraInfo's pose through a Sim3 alignment."""
    R_a = np.array(transform_data["R"], dtype=np.float64)
    s = float(transform_data["scale"])
    t_a = np.array(transform_data["t"], dtype=np.float64)
    new_T = s * R_a @ cam.T.astype(np.float64) + t_a
    new_R = R_a @ cam.R.astype(np.float64)
    return CameraInfo(
        uid=cam.uid,
        image_name=cam.image_name,
        width=cam.width,
        height=cam.height,
        fx=cam.fx,
        fy=cam.fy,
        cx=cam.cx,
        cy=cam.cy,
        R=new_R.astype(np.float32),
        T=new_T.astype(np.float32),
        image_path=cam.image_path,
        sparse_observations=cam.sparse_observations,
        camera_model=cam.camera_model,
    )
