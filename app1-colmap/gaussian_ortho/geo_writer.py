"""
GeoTIFF writer for Gaussian orthophoto output.

Matches the existing ortho_dsm.py output format: LZW-compressed GeoTIFF
with UTM CRS, using rasterio + from_origin transform.
"""
import os

import numpy as np


def write_geotiff(output_path: str, rgb: np.ndarray,
                  x_min: float, y_max: float, gsd: float,
                  crs: str = "EPSG:32631",
                  height_map: np.ndarray = None,
                  height_output_path: str = None):
    """
    Write an orthophoto RGB array to a GeoTIFF file.

    Parameters
    ----------
    output_path : str
        Output file path (.tif).
    rgb : np.ndarray
        (H, W, 3) uint8 RGB image.
    x_min : float
        Western bound in CRS units (metres for UTM).
    y_max : float
        Northern bound in CRS units.
    gsd : float
        Ground sample distance (pixel size in CRS units).
    crs : str
        Coordinate Reference System (e.g. 'EPSG:32631').
    height_map : np.ndarray, optional
        (H, W) float32 height map. Written to separate file if given.
    height_output_path : str, optional
        Path for height map GeoTIFF.
    """
    import rasterio
    from rasterio.transform import from_origin

    H, W = rgb.shape[:2]
    geo_transform = from_origin(x_min, y_max, gsd, gsd)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # RGB bands: write band-by-band to avoid full (3, H, W) copy in RAM
    with rasterio.open(
        output_path, 'w', driver='GTiff',
        height=H, width=W, count=3,
        dtype='uint8', crs=crs, transform=geo_transform,
        compress='lzw',
    ) as dst:
        for band_idx in range(3):
            dst.write(np.ascontiguousarray(rgb[:, :, band_idx]), band_idx + 1)

    # Optional height map
    if height_map is not None and height_output_path:
        os.makedirs(os.path.dirname(height_output_path) or ".", exist_ok=True)
        with rasterio.open(
            height_output_path, 'w', driver='GTiff',
            height=H, width=W, count=1,
            dtype='float32', crs=crs, transform=geo_transform,
            compress='lzw',
        ) as dst:
            dst.write(height_map.reshape(1, H, W))
