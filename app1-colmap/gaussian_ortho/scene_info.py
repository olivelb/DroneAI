"""
Scene information dataclass for 3DGS training.

Wraps camera lists, point cloud, normalisation bounds, and workspace paths
into a single structure consumed by the training loop.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .colmap_loader import CameraInfo, PointCloud


@dataclass
class SceneInfo:
    """All data needed to train a 3DGS model on a scene (or partition)."""
    train_cameras: list[CameraInfo]
    test_cameras: list[CameraInfo]
    point_cloud: PointCloud
    # NeRF-style normalisation: centre + radius of the camera distribution
    scene_centre: np.ndarray   # (3,)
    scene_radius: float
    # Workspace paths
    dense_path: str = ""
    images_dir: str = ""


def compute_scene_extent(cameras: list[CameraInfo]) -> tuple[np.ndarray, float]:
    """
    Compute the centre and radius of the camera distribution.

    Used for NeRF-style scene normalisation: all camera centres are shifted
    so that the distribution is centred at the origin, and scales are relative
    to the radius.
    """
    if not cameras:
        return np.zeros(3, dtype=np.float32), 1.0
    positions = np.stack([c.T for c in cameras], axis=0)
    centre = positions.mean(axis=0)
    dists = np.linalg.norm(positions - centre, axis=1)
    radius = float(np.max(dists)) * 1.1  # 10% padding
    return centre.astype(np.float32), max(radius, 1e-3)


def build_scene_info(train_cameras, test_cameras, point_cloud,
                     dense_path="", images_dir="") -> SceneInfo:
    """Construct a SceneInfo from loader outputs."""
    all_cams = train_cameras + test_cameras
    centre, radius = compute_scene_extent(all_cams)
    return SceneInfo(
        train_cameras=train_cameras,
        test_cameras=test_cameras,
        point_cloud=point_cloud,
        scene_centre=centre,
        scene_radius=radius,
        dense_path=dense_path,
        images_dir=images_dir or str(Path(dense_path) / "images"),
    )
