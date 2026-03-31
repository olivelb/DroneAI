"""
VastGaussian-style divide-and-conquer partitioning.

Splits a large scene into an m×n grid of overlapping cells, assigns
cameras via visibility, and creates per-cell SceneInfo objects for
independent training.
"""
import math
from dataclasses import dataclass

import numpy as np

from .colmap_loader import CameraInfo, PointCloud
from .scene_info import SceneInfo, build_scene_info


@dataclass
class CellBounds:
    """Axis-aligned bounding box for a partition cell (X-Y plane)."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    row: int
    col: int


def compute_partition_grid(scene: SceneInfo, m: int = 2, n: int = 2,
                           overlap: float = 0.20) -> list[CellBounds]:
    """
    Compute an m×n partition grid over the scene extent.

    The grid is laid out in the X-Y plane of the geo-aligned coordinate
    system.  Each cell is expanded by *overlap* fraction on each side
    for seamless merging.

    Parameters
    ----------
    scene : SceneInfo
    m : int  – rows (Y partitions)
    n : int  – cols (X partitions)
    overlap : float – fractional overlap on each side (default 20%)

    Returns
    -------
    list[CellBounds]
    """
    pts = scene.point_cloud.points  # (N, 3)
    cam_pos = np.stack([c.T for c in scene.train_cameras], axis=0)
    all_xy = np.concatenate([pts[:, :2], cam_pos[:, :2]], axis=0)

    x_lo, y_lo = all_xy.min(axis=0)
    x_hi, y_hi = all_xy.max(axis=0)

    dx = (x_hi - x_lo) / n
    dy = (y_hi - y_lo) / m
    pad_x = dx * overlap
    pad_y = dy * overlap

    cells = []
    for row in range(m):
        for col in range(n):
            cells.append(CellBounds(
                x_min=x_lo + col * dx - pad_x,
                x_max=x_lo + (col + 1) * dx + pad_x,
                y_min=y_lo + row * dy - pad_y,
                y_max=y_lo + (row + 1) * dy + pad_y,
                row=row, col=col,
            ))
    return cells


def _camera_in_cell(cam: CameraInfo, cell: CellBounds) -> bool:
    """Check if camera centre (X-Y) lies within the cell bounds."""
    x, y = cam.T[0], cam.T[1]
    return cell.x_min <= x <= cell.x_max and cell.y_min <= y <= cell.y_max


def _filter_points_in_cell(pc: PointCloud, cell: CellBounds) -> PointCloud:
    """Keep only points whose X-Y lies within the cell."""
    mask = (
        (pc.points[:, 0] >= cell.x_min) & (pc.points[:, 0] <= cell.x_max) &
        (pc.points[:, 1] >= cell.y_min) & (pc.points[:, 1] <= cell.y_max)
    )
    return PointCloud(
        points=pc.points[mask],
        colors=pc.colors[mask],
        normals=pc.normals[mask],
    )


def partition_scene(scene: SceneInfo, m: int = 2, n: int = 2,
                    overlap: float = 0.20, min_cameras: int = 5
                    ) -> list[tuple[CellBounds, SceneInfo]]:
    """
    Partition a scene into m×n cells.

    For each cell:
      1. Select cameras whose centre is within the (padded) cell.
      2. Filter points within the cell.
      3. Build a SceneInfo for independent training.

    Cells with fewer than *min_cameras* are merged into their neighbour.

    Returns list of (CellBounds, SceneInfo) pairs.
    """
    cells = compute_partition_grid(scene, m, n, overlap)
    result = []

    for cell in cells:
        cams = [c for c in scene.train_cameras if _camera_in_cell(c, cell)]
        if len(cams) < min_cameras:
            # Skip tiny cells — points will be covered by overlapping neighbours
            continue
        pc = _filter_points_in_cell(scene.point_cloud, cell)
        if pc.points.shape[0] < 100:
            continue
        cell_scene = build_scene_info(
            train_cameras=cams,
            test_cameras=[],  # test only on full scene
            point_cloud=pc,
            dense_path=scene.dense_path,
            images_dir=scene.images_dir,
        )
        result.append((cell, cell_scene))

    return result
