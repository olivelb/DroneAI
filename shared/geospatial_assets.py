"""Cloud-optimized raster and vector-ready geospatial artifacts.

Large orthomosaics are published as tiled COGs with internal overviews. API
rendering always reads a bounded raster window, so zooming never downloads or
decodes the complete image.
"""

from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.transform import Affine, from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform as warp_coordinates
from rasterio.warp import transform_bounds

WEB_MERCATOR_LIMIT = 20_037_508.342789244
WEB_MERCATOR_INITIAL_RESOLUTION = 156_543.03392804097
DEFAULT_TILE_SIZE = 256


def metadata_path(raster_path: str | Path) -> Path:
    path = Path(raster_path)
    return path.with_suffix(path.suffix + ".cog.json")


def preview_path(raster_path: str | Path) -> Path:
    path = Path(raster_path)
    return path.with_name(f"{path.stem}.preview.webp")


def _wgs84_bounds(dataset: rasterio.DatasetReader) -> list[float]:
    if dataset.crs is None:
        raise ValueError("A georeferenced raster CRS is required")
    bounds = transform_bounds(
        dataset.crs,
        "EPSG:4326",
        *dataset.bounds,
        densify_pts=21,
    )
    return [float(value) for value in bounds]


def _native_max_zoom(dataset: rasterio.DatasetReader) -> int:
    if dataset.crs is None:
        return 0
    mercator_bounds = transform_bounds(
        dataset.crs,
        "EPSG:3857",
        *dataset.bounds,
        densify_pts=21,
    )
    resolution = max(
        abs(mercator_bounds[2] - mercator_bounds[0])
        / max(dataset.width, 1),
        abs(mercator_bounds[3] - mercator_bounds[1])
        / max(dataset.height, 1),
    )
    if not math.isfinite(resolution) or resolution <= 0:
        return 0
    return max(
        0,
        min(
            24,
            int(
                math.ceil(
                    math.log2(
                        WEB_MERCATOR_INITIAL_RESOLUTION / resolution
                    )
                )
            ),
        ),
    )


def raster_metadata(
    dataset: rasterio.DatasetReader,
    *,
    s3_key: str | None = None,
) -> dict[str, Any]:
    overviews = dataset.overviews(1) if dataset.count else []
    max_zoom = _native_max_zoom(dataset)
    return {
        "schema_version": 1,
        "format": "COG",
        "s3_key": s3_key,
        "crs": dataset.crs.to_string() if dataset.crs else None,
        "bounds": {
            "native": [float(value) for value in dataset.bounds],
            "wgs84": _wgs84_bounds(dataset) if dataset.crs else None,
        },
        "coordinate_space": "projected" if dataset.crs else "local",
        "width": dataset.width,
        "height": dataset.height,
        "bands": dataset.count,
        "dtypes": list(dataset.dtypes),
        "nodata": dataset.nodata,
        "tiled": bool(dataset.profile.get("tiled")),
        "block_shapes": [list(shape) for shape in dataset.block_shapes],
        "overviews": list(overviews),
        "tile_size": DEFAULT_TILE_SIZE,
        "min_zoom": max(0, max_zoom - len(overviews) - 2),
        "max_zoom": max_zoom,
    }


def _display_bands(dataset: rasterio.DatasetReader) -> list[int]:
    if dataset.count >= 3:
        return [1, 2, 3]
    return [1]


def _to_uint8(data: np.ma.MaskedArray) -> np.ndarray:
    values = np.ma.asarray(data)
    if values.dtype == np.uint8:
        return np.asarray(values.filled(0), dtype=np.uint8)
    output = np.zeros(values.shape, dtype=np.uint8)
    for index in range(values.shape[0]):
        band = values[index]
        compressed = band.compressed()
        if not compressed.size:
            continue
        low, high = np.percentile(compressed, (2, 98))
        if not math.isfinite(float(low)) or not math.isfinite(float(high)):
            continue
        if high <= low:
            high = low + 1
        scaled = (band.astype(np.float32) - low) * (255.0 / (high - low))
        output[index] = np.asarray(
            np.ma.clip(scaled, 0, 255).filled(0),
            dtype=np.uint8,
        )
    return output


def _rgba_image(
    data: np.ma.MaskedArray,
    *,
    colormap: str = "",
) -> Image.Image:
    values = np.ma.asarray(data)
    mask = np.ma.getmaskarray(values)
    alpha = np.where(np.all(mask, axis=0), 0, 255).astype(np.uint8)
    normalized = _to_uint8(values)
    if colormap == "depth":
        gray = normalized[0].astype(np.float32) / 255.0
        red = np.clip(1.5 - np.abs(4 * gray - 3), 0, 1)
        green = np.clip(1.5 - np.abs(4 * gray - 2), 0, 1)
        blue = np.clip(1.5 - np.abs(4 * gray - 1), 0, 1)
        rgb = (np.stack((red, green, blue), axis=-1) * 255).astype(
            np.uint8
        )
    elif normalized.shape[0] >= 3:
        rgb = np.moveaxis(normalized[:3], 0, -1)
    else:
        rgb = np.repeat(normalized[0, :, :, None], 3, axis=2)
    return Image.fromarray(np.dstack((rgb, alpha)), mode="RGBA")


def create_bounded_preview(
    raster_path: str | Path,
    *,
    maximum_size: int = 2048,
) -> Path:
    path = Path(raster_path)
    maximum_size = max(256, min(int(maximum_size), 4096))
    with rasterio.open(path) as dataset:
        scale = min(
            maximum_size / max(dataset.width, 1),
            maximum_size / max(dataset.height, 1),
            1.0,
        )
        width = max(1, int(dataset.width * scale))
        height = max(1, int(dataset.height * scale))
        data = dataset.read(
            _display_bands(dataset),
            out_shape=(len(_display_bands(dataset)), height, width),
            masked=True,
            resampling=Resampling.bilinear,
        )
    output = preview_path(path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    image = _rgba_image(data).convert("RGB")
    image.save(temporary, format="WEBP", quality=85, method=4)
    os.replace(temporary, output)
    return output


def convert_to_cog(
    raster_path: str | Path,
    *,
    block_size: int = 512,
    preview_maximum_size: int = 2048,
) -> dict[str, Any]:
    """Atomically replace a GeoTIFF with a validated tiled COG."""

    path = Path(raster_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    temporary = path.with_suffix(path.suffix + ".cog.tmp")
    temporary.unlink(missing_ok=True)
    try:
        raster_copy(
            path,
            temporary,
            driver="COG",
            BLOCKSIZE=max(128, min(int(block_size), 1024)),
            COMPRESS="DEFLATE",
            BIGTIFF="IF_SAFER",
            OVERVIEW_RESAMPLING="AVERAGE",
            NUM_THREADS="ALL_CPUS",
        )
        with rasterio.open(temporary) as dataset:
            metadata = raster_metadata(dataset)
            if (
                not dataset.profile.get("tiled")
                or not dataset.overviews(1)
            ):
                raise RuntimeError(
                    f"COG validation failed for {path}: "
                    "missing tiles/overviews"
                )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    metadata_file = metadata_path(path)
    metadata_temporary = metadata_file.with_suffix(
        metadata_file.suffix + ".tmp"
    )
    metadata_temporary.write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(metadata_temporary, metadata_file)
    preview = create_bounded_preview(
        path,
        maximum_size=preview_maximum_size,
    )
    metadata["metadata_path"] = str(metadata_file)
    metadata["preview_path"] = str(preview)
    return metadata


def _tile_bounds_mercator(z: int, x: int, y: int) -> tuple[float, ...]:
    if z < 0 or z > 24:
        raise ValueError("zoom must be between 0 and 24")
    limit = 1 << z
    if x < 0 or y < 0 or x >= limit or y >= limit:
        raise ValueError("tile coordinate is outside the zoom grid")
    span = 2 * WEB_MERCATOR_LIMIT / limit
    left = -WEB_MERCATOR_LIMIT + x * span
    right = left + span
    top = WEB_MERCATOR_LIMIT - y * span
    bottom = top - span
    return left, bottom, right, top


def render_cog_tile(
    source: str | Path,
    *,
    z: int,
    x: int,
    y: int,
    tile_size: int = DEFAULT_TILE_SIZE,
    colormap: str = "",
) -> io.BytesIO:
    tile_size = max(128, min(int(tile_size), 512))
    with (
        rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"),
        rasterio.open(source) as dataset,
    ):
        if dataset.crs is None:
            raise ValueError("Raster does not have a CRS")
        mercator_bounds = _tile_bounds_mercator(z, x, y)
        tile_transform = from_bounds(
            *mercator_bounds,
            tile_size,
            tile_size,
        )
        indexes = _display_bands(dataset)
        with WarpedVRT(
            dataset,
            crs="EPSG:3857",
            transform=tile_transform,
            width=tile_size,
            height=tile_size,
            resampling=Resampling.bilinear,
        ) as tile_dataset:
            data = tile_dataset.read(indexes, masked=True)
    output = io.BytesIO()
    _rgba_image(data, colormap=colormap).save(
        output,
        format="PNG",
        optimize=True,
    )
    output.seek(0)
    return output


def inspect_remote_cog(
    source: str | Path,
    *,
    s3_key: str,
) -> dict[str, Any]:
    with (
        rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"),
        rasterio.open(source) as dataset,
    ):
        return raster_metadata(dataset, s3_key=s3_key)


def pixel_segment_to_wgs84(
    segment: list[list[float]],
    *,
    geotransform: list[float],
    source_crs: str,
) -> list[list[float]]:
    """Project a raster pixel polygon to a closed WGS84 ring."""

    if len(geotransform) != 6:
        raise ValueError("A six-value GDAL geotransform is required")
    if len(segment) < 3:
        raise ValueError("A polygon needs at least three vertices")
    affine = Affine.from_gdal(*[float(value) for value in geotransform])
    projected = [
        affine * (float(point[0]), float(point[1])) for point in segment
    ]
    longitudes, latitudes = warp_coordinates(
        source_crs,
        "EPSG:4326",
        [point[0] for point in projected],
        [point[1] for point in projected],
    )
    ring = [
        [float(longitude), float(latitude)]
        for longitude, latitude in zip(
            longitudes,
            latitudes,
            strict=True,
        )
    ]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def detections_feature_collection(
    detections: list[Any],
    *,
    geotransform: list[float] | None,
    source_crs: str | None,
    vol_id: str,
) -> dict[str, Any]:
    """Build a GeoJSON layer without rasterizing AI results."""

    features: list[dict[str, Any]] = []
    for detection in detections:
        get = (
            detection.get
            if isinstance(detection, dict)
            else vars(detection).get
        )
        segment = get("segment") or []
        geometry: dict[str, Any] | None = None
        if segment and geotransform and source_crs:
            try:
                geometry = {
                    "type": "Polygon",
                    "coordinates": [
                        pixel_segment_to_wgs84(
                            segment,
                            geotransform=geotransform,
                            source_crs=source_crs,
                        )
                    ],
                }
            except (TypeError, ValueError, rasterio.errors.CRSError):
                geometry = None
        if geometry is None:
            longitude = get("geo_lon")
            latitude = get("geo_lat")
            if longitude is not None and latitude is not None:
                geometry = {
                    "type": "Point",
                    "coordinates": [float(longitude), float(latitude)],
                }
        if geometry is None:
            continue
        features.append(
            {
                "type": "Feature",
                "id": get("id"),
                "geometry": geometry,
                "properties": {
                    "vol_id": vol_id,
                    "class_name": get("class_name", "unknown"),
                    "class_id": get("class_id"),
                    "confidence": float(get("confidence", 0.0)),
                    "tile_index": get("tile_index"),
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "vol_id": vol_id,
            "feature_count": len(features),
        },
    }
