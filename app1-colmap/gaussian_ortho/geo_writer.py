"""
GeoTIFF writer for Gaussian orthophoto output.

Matches the existing ortho_dsm.py output format: LZW-compressed GeoTIFF
with UTM CRS, using rasterio + from_origin transform.
"""
import os
from typing import Any

import numpy as np
from numpy.typing import NDArray


def _geotiff_creation_options(*, photometric: str | None = None) -> dict[str, str]:
    """Return lossless GTiff options that remain valid past the 4 GiB limit."""
    options = {
        "compress": "lzw",
        "BIGTIFF": "IF_SAFER",
    }
    if photometric is not None:
        options["photometric"] = photometric
    return options


def write_geotiff(
    output_path: str,
    rgb: NDArray[np.uint8],
    x_min: float,
    y_max: float,
    gsd: float,
    crs: str = "EPSG:32631",
    height_map: NDArray[np.floating[Any]] | None = None,
    height_output_path: str | None = None,
) -> None:
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
        **_geotiff_creation_options(photometric="rgb"),
    ) as dst:
        for band_idx in range(3):
            dst.write(np.ascontiguousarray(rgb[:, :, band_idx]), band_idx + 1)

    # Embed sRGB ICC profile so viewers don't misinterpret values as linear.
    # The rendered pixels are sRGB (trained against sRGB JPEGs) but an
    # untagged TIFF is often assumed linear by VFX tools → double gamma.
    try:
        from PIL.ImageCms import ImageCmsProfile, createProfile
        srgb_bytes = ImageCmsProfile(createProfile("sRGB")).tobytes()
        # TIFF tag 34675 = ICCProfile. Patch via GDAL's SetMetadataItem.
        from osgeo import gdal
        ds = gdal.Open(output_path, gdal.GA_Update)
        if ds is not None:
            ds.GetRasterBand(1).SetColorInterpretation(gdal.GCI_RedBand)
            ds.GetRasterBand(2).SetColorInterpretation(gdal.GCI_GreenBand)
            ds.GetRasterBand(3).SetColorInterpretation(gdal.GCI_BlueBand)
            # GDAL ≥ 3.x supports writing ICC profiles via SetMetadata
            import base64 as _b64
            ds.SetMetadataItem("SOURCE_ICC_PROFILE",
                               _b64.b64encode(srgb_bytes).decode("ascii"),
                               "COLOR_PROFILE")
            ds.FlushCache()
            ds = None
    except Exception:
        pass  # non-critical: ortho is still valid, just without ICC tag

    # Optional height map
    if height_map is not None and height_output_path:
        os.makedirs(os.path.dirname(height_output_path) or ".", exist_ok=True)
        with rasterio.open(
            height_output_path, 'w', driver='GTiff',
            height=H, width=W, count=1,
            dtype='float32', crs=crs, transform=geo_transform,
            nodata=np.nan,
            **_geotiff_creation_options(),
        ) as dst:
            dst.write(height_map.reshape(1, H, W))
