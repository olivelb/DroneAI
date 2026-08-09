"""Reusable tiling, deduplication, rendering, and geospatial exports."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.windows import Window

from shared.detection_geometry import (
    build_tile_starts,
    dedupe_mission_detections,
)


DetectionRecord = dict[str, Any]
JsonObject = dict[str, Any]


class RasterTileReader(Protocol):
    count: int

    def read(
        self,
        indexes: list[int],
        *,
        window: Window,
    ) -> NDArray[Any]: ...


def _read_rgb_tile(src: RasterTileReader, window: Window) -> NDArray[Any]:
    indexes = list(range(1, min(src.count, 3) + 1))
    tile_data = src.read(indexes, window=window)
    if tile_data.shape[0] == 1:
        tile_data = np.repeat(tile_data, 3, axis=0)
    elif tile_data.shape[0] == 2:
        tile_data = np.concatenate([tile_data, tile_data[:1]], axis=0)
    tile_rgb = tile_data[:3].transpose(1, 2, 0)
    if tile_rgb.dtype != np.uint8:
        tile_rgb = np.clip(tile_rgb, 0, 255).astype(np.uint8)
    return cast(NDArray[Any], tile_rgb)


def _write_jpeg_tile(tile_path: Path, tile_rgb: NDArray[Any]) -> None:
    written = cv2.imwrite(
        str(tile_path),
        cv2.cvtColor(tile_rgb, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, 95],
    )
    if not written:
        raise RuntimeError(f"failed to write tile: {tile_path}")


def write_orthomosaic_tiles(
    ortho_path: Path,
    tiles_dir: Path,
    tile_size: int,
    overlap: int,
    max_tiles: int | None = None,
) -> tuple[list[JsonObject], JsonObject]:
    """Write overlapping RGB JPEG tiles and return their pixel offsets."""

    if max_tiles is not None and max_tiles <= 0:
        raise ValueError("max_tiles must be positive when provided")
    tiles_dir.mkdir(parents=True, exist_ok=True)
    records: list[JsonObject] = []

    with rasterio.open(ortho_path) as src:
        if src.count < 1:
            raise ValueError("orthomosaic has no raster bands")
        x_starts = build_tile_starts(src.width, tile_size, overlap)
        y_starts = build_tile_starts(src.height, tile_size, overlap)
        total_available = len(x_starts) * len(y_starts)

        for y in y_starts:
            for x in x_starts:
                if max_tiles is not None and len(records) >= max_tiles:
                    break
                width = min(tile_size, src.width - x)
                height = min(tile_size, src.height - y)
                window = Window(x, y, width, height)
                index = len(records)
                tile_path = tiles_dir / f"tile_{index:04d}.jpg"
                _write_jpeg_tile(tile_path, _read_rgb_tile(src, window))
                records.append(
                    {
                        "tile_index": index,
                        "path": str(tile_path),
                        "offset_x": int(x),
                        "offset_y": int(y),
                        "width": int(width),
                        "height": int(height),
                    }
                )
            if max_tiles is not None and len(records) >= max_tiles:
                break

        metadata = {
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": src.dtypes[0],
            "crs": src.crs.to_string() if src.crs else None,
            "transform": list(src.transform.to_gdal()),
            "tile_size": tile_size,
            "overlap": overlap,
            "tiles_available": total_available,
            "tiles_written": len(records),
        }
    return records, metadata


def pixel_to_projected(
    transform: Affine,
    pixel_x: float,
    pixel_y: float,
) -> tuple[float, float]:
    projected_x, projected_y = transform * (pixel_x, pixel_y)
    return float(projected_x), float(projected_y)


def geolocate_detection(
    detection: DetectionRecord,
    transform: Affine,
    transformer: Transformer,
) -> DetectionRecord:
    result = dict(detection)
    projected_x, projected_y = pixel_to_projected(
        transform,
        float(detection["global_pixel_x"]),
        float(detection["global_pixel_y"]),
    )
    longitude, latitude = transformer.transform(projected_x, projected_y)
    result.update(
        projected_x=projected_x,
        projected_y=projected_y,
        geo_lon=float(longitude),
        geo_lat=float(latitude),
    )
    return result


def detections_to_geojson(
    detections: Iterable[DetectionRecord],
    transform: Affine,
    crs: str,
) -> JsonObject:
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    features: list[JsonObject] = []
    for index, detection in enumerate(detections):
        segment = detection.get("segment") or []
        if len(segment) < 3:
            continue
        geographic_polygon: list[list[float]] = []
        for pixel_x, pixel_y in segment:
            projected_x, projected_y = pixel_to_projected(
                transform,
                float(pixel_x),
                float(pixel_y),
            )
            longitude, latitude = transformer.transform(
                projected_x,
                projected_y,
            )
            geographic_polygon.append([float(longitude), float(latitude)])
        geographic_polygon.append(geographic_polygon[0])

        properties = {key: value for key, value in detection.items() if key not in {"segment", "polygon"}}
        properties["detection_id"] = index
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [geographic_polygon],
                },
                "properties": properties,
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "droneai_detections",
        "features": features,
    }


def _draw_label(
    image: NDArray[Any],
    anchor_x: int,
    anchor_y: int,
    lines: list[str],
) -> None:
    if not lines:
        return
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.42
    font_thickness = 1
    line_height = 14
    padding = 4
    sizes = [cv2.getTextSize(line, font, font_scale, font_thickness)[0] for line in lines]
    box_width = max(size[0] for size in sizes) + 2 * padding
    box_height = len(lines) * line_height + 2 * padding
    box_x = min(max(0, anchor_x + 6), max(0, image.shape[1] - box_width))
    box_y = min(
        max(0, anchor_y - box_height - 6),
        max(0, image.shape[0] - box_height),
    )
    overlay = image.copy()
    cv2.rectangle(
        overlay,
        (box_x, box_y),
        (box_x + box_width, box_y + box_height),
        (0, 0, 0),
        -1,
    )
    cv2.addWeighted(overlay, 0.6, image, 0.4, 0, image)
    for index, line in enumerate(lines):
        cv2.putText(
            image,
            line,
            (box_x + padding, box_y + padding + (index + 1) * line_height - 3),
            font,
            font_scale,
            (255, 255, 0),
            font_thickness,
            cv2.LINE_AA,
        )


def render_annotated_orthomosaic(
    source_path: Path,
    output_path: Path,
    detections: Iterable[DetectionRecord],
) -> JsonObject:
    detections = list(detections)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source_path) as src:
        meta = src.meta.copy()
        data = src.read(list(range(1, min(src.count, 3) + 1)))
        if data.shape[0] == 1:
            data = np.repeat(data, 3, axis=0)
        elif data.shape[0] == 2:
            data = np.concatenate([data, data[:1]], axis=0)
        image = data[:3].transpose(1, 2, 0).copy()
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)

        for detection in detections:
            segment = detection.get("segment") or []
            if len(segment) < 3:
                continue
            points = np.asarray(segment, dtype=np.int32).reshape((-1, 1, 2))
            box_x, box_y, box_width, box_height = cv2.boundingRect(points)
            margin = 3
            left = max(0, box_x - margin)
            top = max(0, box_y - margin)
            right = min(image.shape[1], box_x + box_width + margin)
            bottom = min(image.shape[0], box_y + box_height + margin)
            region = image[top:bottom, left:right]
            overlay = region.copy()
            local_points = points - np.array([left, top], dtype=np.int32)
            cv2.fillPoly(overlay, [local_points], (255, 0, 0))
            cv2.addWeighted(overlay, 0.35, region, 0.65, 0, region)
            cv2.polylines(image, [points], True, (255, 255, 0), 2)
            center_x = round(float(detection["global_pixel_x"]))
            center_y = round(float(detection["global_pixel_y"]))
            cv2.circle(image, (center_x, center_y), 4, (0, 255, 0), -1)
            _draw_label(
                image,
                center_x,
                center_y,
                [
                    str(detection.get("class_name", "object")),
                    f"{float(detection.get('confidence', 0.0)):.2f}",
                ],
            )

        meta.update(
            count=3,
            dtype="uint8",
            compress="deflate",
            predictor=2,
        )
        with rasterio.open(output_path, "w", **meta) as dst:
            dst.write(image.transpose(2, 0, 1))
        return {
            "width": src.width,
            "height": src.height,
            "crs": src.crs.to_string() if src.crs else None,
            "detections_rendered": len(detections),
        }
