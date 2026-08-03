"""Reusable tiling, deduplication, rendering, and geospatial exports."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cv2
import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.windows import Window



def build_tile_starts(full_size: int, tile_size: int, overlap: int) -> list[int]:
    if full_size <= 0:
        raise ValueError("full_size must be positive")
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap must be between 0 and tile_size - 1")
    if full_size <= tile_size:
        return [0]
    stride = tile_size - overlap
    starts = list(range(0, full_size - tile_size + 1, stride))
    last_start = full_size - tile_size
    if starts[-1] != last_start:
        distance_to_edge = last_start - starts[-1]
        if len(starts) > 1 and overlap > 0 and distance_to_edge < overlap:
            starts[-1] = last_start
        else:
            starts.append(last_start)
    return starts


def _read_rgb_tile(src, window: Window) -> np.ndarray:
    indexes = list(range(1, min(src.count, 3) + 1))
    tile_data = src.read(indexes, window=window)
    if tile_data.shape[0] == 1:
        tile_data = np.repeat(tile_data, 3, axis=0)
    elif tile_data.shape[0] == 2:
        tile_data = np.concatenate([tile_data, tile_data[:1]], axis=0)
    tile_rgb = tile_data[:3].transpose(1, 2, 0)
    if tile_rgb.dtype != np.uint8:
        tile_rgb = np.clip(tile_rgb, 0, 255).astype(np.uint8)
    return tile_rgb


def _write_jpeg_tile(tile_path: Path, tile_rgb: np.ndarray) -> None:
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
) -> tuple[list[dict], dict]:
    """Write overlapping RGB JPEG tiles and return their pixel offsets."""

    if max_tiles is not None and max_tiles <= 0:
        raise ValueError("max_tiles must be positive when provided")
    tiles_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

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


def polygon_area(points: list[list[float]]) -> float:
    if not points or len(points) < 3:
        return 0.0
    contour = np.asarray(points, dtype=np.float32).reshape((-1, 1, 2))
    return float(abs(cv2.contourArea(contour)))


def polygon_bbox(
    points: list[list[float]],
) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def polygon_contains_point(
    points: list[list[float]],
    point_x: float,
    point_y: float,
) -> bool:
    if not points or len(points) < 3:
        return False
    contour = np.asarray(points, dtype=np.float32).reshape((-1, 1, 2))
    return (
        cv2.pointPolygonTest(
            contour,
            (float(point_x), float(point_y)),
            False,
        )
        >= 0
    )


def polygon_centroid(
    points: list[list[float]],
) -> tuple[float, float] | None:
    if not points or len(points) < 3:
        return None
    contour = np.asarray(points, dtype=np.float32).reshape((-1, 1, 2))
    moments = cv2.moments(contour)
    if moments["m00"]:
        return (
            float(moments["m10"] / moments["m00"]),
            float(moments["m01"] / moments["m00"]),
        )
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))


def bbox_iou(
    left_bbox: tuple[float, float, float, float],
    right_bbox: tuple[float, float, float, float],
) -> float:
    left_x1, left_y1, left_x2, left_y2 = left_bbox
    right_x1, right_y1, right_x2, right_y2 = right_bbox

    inter_x1 = max(left_x1, right_x1)
    inter_y1 = max(left_y1, right_y1)
    inter_x2 = min(left_x2, right_x2)
    inter_y2 = min(left_y2, right_y2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0

    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    left_area = max(0.0, left_x2 - left_x1) * max(
        0.0,
        left_y2 - left_y1,
    )
    right_area = max(0.0, right_x2 - right_x1) * max(
        0.0,
        right_y2 - right_y1,
    )
    union_area = left_area + right_area - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


def are_duplicate_detections(
    candidate: dict,
    kept: dict,
    center_threshold: float,
    iou_threshold: float,
) -> bool:
    if candidate.get("class_name") != kept.get("class_name"):
        return False

    candidate_segment = candidate.get("_segment") or []
    kept_segment = kept.get("_segment") or []
    candidate_centroid = polygon_centroid(candidate_segment)
    if candidate_centroid and polygon_contains_point(
        kept_segment,
        candidate_centroid[0],
        candidate_centroid[1],
    ):
        return True
    if any(polygon_contains_point(kept_segment, point_x, point_y) for point_x, point_y in candidate_segment):
        return True

    delta_x = float(candidate["global_pixel_x"]) - float(kept["global_pixel_x"])
    delta_y = float(candidate["global_pixel_y"]) - float(kept["global_pixel_y"])
    if abs(delta_x) > center_threshold or abs(delta_y) > center_threshold:
        return False
    return bbox_iou(candidate["_bbox"], kept["_bbox"]) >= iou_threshold


def dedupe_mission_detections(
    detections: Iterable[dict],
    center_threshold: float = 40.0,
    iou_threshold: float = 0.05,
) -> list[dict]:
    prepared = []
    for detection in detections:
        segment = detection.get("segment") or []
        if len(segment) < 3:
            prepared.append(detection)
            continue
        enriched = dict(detection)
        enriched["_area"] = polygon_area(segment)
        enriched["_bbox"] = polygon_bbox(segment)
        enriched["_segment"] = segment
        prepared.append(enriched)

    kept = []
    for detection in sorted(
        prepared,
        key=lambda item: (
            item.get("_area", 0.0),
            float(item.get("confidence", 0.0)),
        ),
        reverse=True,
    ):
        if "_bbox" not in detection:
            kept.append(detection)
            continue
        if any(
            are_duplicate_detections(
                detection,
                existing,
                center_threshold,
                iou_threshold,
            )
            for existing in kept
            if "_bbox" in existing
        ):
            continue
        kept.append(detection)

    deduped = []
    for detection in kept:
        cleaned = dict(detection)
        for private_key in ("_area", "_bbox", "_segment"):
            cleaned.pop(private_key, None)
        deduped.append(cleaned)
    return deduped


def pixel_to_projected(
    transform: Affine,
    pixel_x: float,
    pixel_y: float,
) -> tuple[float, float]:
    projected_x, projected_y = transform * (pixel_x, pixel_y)
    return float(projected_x), float(projected_y)


def geolocate_detection(
    detection: dict,
    transform: Affine,
    transformer: Transformer,
) -> dict:
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
    detections: Iterable[dict],
    transform: Affine,
    crs: str,
) -> dict:
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    features = []
    for index, detection in enumerate(detections):
        segment = detection.get("segment") or []
        if len(segment) < 3:
            continue
        geographic_polygon = []
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
    image: np.ndarray,
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
    detections: Iterable[dict],
) -> dict:
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
            center_x = int(round(float(detection["global_pixel_x"])))
            center_y = int(round(float(detection["global_pixel_y"])))
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
