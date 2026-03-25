from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re

from PIL import Image, ImageColor, ImageDraw


TILE_INDEX_PATTERN = re.compile(r"tile_(\d+)\.txt$")


@dataclass
class Detection:
    tile_index: int
    confidence: float
    polygon: list[tuple[float, float]]

    @property
    def center(self) -> tuple[float, float]:
        point_count = len(self.polygon)
        return (
            sum(point[0] for point in self.polygon) / point_count,
            sum(point[1] for point in self.polygon) / point_count,
        )

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.polygon]
        ys = [point[1] for point in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draw tile-level OBB detections back onto an orthomosaic.")
    parser.add_argument("--ortho", required=True, help="Path to the orthomosaic image.")
    parser.add_argument("--tiles", required=True, help="Directory containing the source tile JPGs.")
    parser.add_argument("--labels", required=True, help="Directory containing Ultralytics OBB prediction labels.")
    parser.add_argument("--output", required=True, help="Output path for the annotated orthomosaic image.")
    parser.add_argument("--tile-size", type=int, default=1024, help="Tile size used to slice the orthomosaic.")
    parser.add_argument("--overlap", type=int, default=256, help="Tile overlap used when slicing the orthomosaic.")
    parser.add_argument("--min-confidence", type=float, default=0.0, help="Minimum detection confidence to keep.")
    parser.add_argument("--dedupe-center-threshold", type=float, default=24.0, help="Maximum center distance in pixels for duplicate suppression.")
    parser.add_argument("--dedupe-iou-threshold", type=float, default=0.15, help="Minimum bbox IoU for duplicate suppression.")
    parser.add_argument("--outline-color", default="#ff2d55", help="Polygon outline color.")
    parser.add_argument("--fill-color", default="#ff2d55", help="Polygon fill color.")
    parser.add_argument("--fill-alpha", type=int, default=70, help="Polygon fill alpha in the range 0-255.")
    parser.add_argument("--width", type=int, default=3, help="Polygon outline width.")
    parser.add_argument("--preview-scale", type=float, default=0.25, help="Optional preview scale factor. Set to 0 to disable preview output.")
    return parser.parse_args()


def build_tile_starts(full_size: int, tile_size: int, overlap: int) -> list[int]:
    if full_size <= tile_size:
        return [0]

    stride = max(1, tile_size - overlap)
    starts = list(range(0, max(full_size - tile_size, 0) + 1, stride))
    last_start = full_size - tile_size
    if starts[-1] != last_start:
        starts.append(last_start)
    return starts


def bbox_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    left_x1, left_y1, left_x2, left_y2 = left
    right_x1, right_y1, right_x2, right_y2 = right

    inter_x1 = max(left_x1, right_x1)
    inter_y1 = max(left_y1, right_y1)
    inter_x2 = min(left_x2, right_x2)
    inter_y2 = min(left_y2, right_y2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0

    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    left_area = max(0.0, left_x2 - left_x1) * max(0.0, left_y2 - left_y1)
    right_area = max(0.0, right_x2 - right_x1) * max(0.0, right_y2 - right_y1)
    union_area = left_area + right_area - inter_area
    if union_area <= 0:
        return 0.0
    return inter_area / union_area


def is_duplicate(candidate: Detection, kept: Detection, center_threshold: float, iou_threshold: float) -> bool:
    candidate_center_x, candidate_center_y = candidate.center
    kept_center_x, kept_center_y = kept.center
    if abs(candidate_center_x - kept_center_x) > center_threshold or abs(candidate_center_y - kept_center_y) > center_threshold:
        return False
    return bbox_iou(candidate.bbox, kept.bbox) >= iou_threshold


def dedupe_detections(detections: list[Detection], center_threshold: float, iou_threshold: float) -> list[Detection]:
    kept: list[Detection] = []
    for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
        if any(is_duplicate(detection, kept_detection, center_threshold, iou_threshold) for kept_detection in kept):
            continue
        kept.append(detection)
    return kept


def parse_tile_index(path: Path) -> int:
    match = TILE_INDEX_PATTERN.search(path.name)
    if match is None:
        raise ValueError(f"Unrecognized tile label file name: {path.name}")
    return int(match.group(1))


def load_detections(
    labels_dir: Path,
    tiles_dir: Path,
    x_starts: list[int],
    y_starts: list[int],
    image_width: int,
    image_height: int,
    min_confidence: float,
) -> list[Detection]:
    detections: list[Detection] = []
    x_count = len(x_starts)

    for label_path in sorted(labels_dir.glob("tile_*.txt")):
        tile_index = parse_tile_index(label_path)
        y_index, x_index = divmod(tile_index, x_count)
        if y_index >= len(y_starts):
            raise ValueError(f"Tile index {tile_index} exceeds reconstructed tile grid")

        tile_path = tiles_dir / f"tile_{tile_index}.jpg"
        if not tile_path.exists():
            raise FileNotFoundError(f"Missing tile image for detections: {tile_path}")

        with Image.open(tile_path) as tile_image:
            tile_width, tile_height = tile_image.size

        x_offset = x_starts[x_index]
        y_offset = y_starts[y_index]

        for raw_line in label_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue

            values = [float(value) for value in line.split()]
            if len(values) < 9:
                raise ValueError(f"Unexpected label format in {label_path}: {line}")

            confidence = values[9] if len(values) >= 10 else 1.0
            if confidence < min_confidence:
                continue
            polygon: list[tuple[float, float]] = []
            for point_index in range(4):
                x_norm = values[1 + point_index * 2]
                y_norm = values[2 + point_index * 2]
                x_pixel = x_offset + x_norm * tile_width
                y_pixel = y_offset + y_norm * tile_height
                x_pixel = min(max(x_pixel, 0.0), float(image_width - 1))
                y_pixel = min(max(y_pixel, 0.0), float(image_height - 1))
                polygon.append((x_pixel, y_pixel))

            detections.append(Detection(tile_index=tile_index, confidence=confidence, polygon=polygon))

    return detections


def draw_detections(
    ortho_path: Path,
    detections: list[Detection],
    output_path: Path,
    outline_color: str,
    fill_color: str,
    fill_alpha: int,
    width: int,
    preview_scale: float,
) -> None:
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(ortho_path) as image:
        base_image = image.convert("RGBA")

    overlay = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    outline_rgba = ImageColor.getrgb(outline_color) + (255,)
    fill_rgba = ImageColor.getrgb(fill_color) + (max(0, min(fill_alpha, 255)),)

    for detection in detections:
        draw.polygon(detection.polygon, fill=fill_rgba, outline=outline_rgba, width=width)
        center_x, center_y = detection.center
        draw.ellipse((center_x - 3, center_y - 3, center_x + 3, center_y + 3), fill=(0, 255, 255, 255))

    annotated = Image.alpha_composite(base_image, overlay).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    annotated.save(output_path, quality=95)

    if preview_scale > 0:
        preview_size = (
            max(1, int(annotated.width * preview_scale)),
            max(1, int(annotated.height * preview_scale)),
        )
        preview = annotated.resize(preview_size)
        preview_path = output_path.with_name(f"{output_path.stem}_preview{output_path.suffix}")
        preview.save(preview_path, quality=90)


def main() -> None:
    args = parse_args()
    ortho_path = Path(args.ortho)
    tiles_dir = Path(args.tiles)
    labels_dir = Path(args.labels)
    output_path = Path(args.output)

    Image.MAX_IMAGE_PIXELS = None
    with Image.open(ortho_path) as image:
        image_width, image_height = image.size

    x_starts = build_tile_starts(image_width, args.tile_size, args.overlap)
    y_starts = build_tile_starts(image_height, args.tile_size, args.overlap)
    detections = load_detections(
        labels_dir,
        tiles_dir,
        x_starts,
        y_starts,
        image_width,
        image_height,
        args.min_confidence,
    )
    deduped_detections = dedupe_detections(detections, args.dedupe_center_threshold, args.dedupe_iou_threshold)

    draw_detections(
        ortho_path=ortho_path,
        detections=deduped_detections,
        output_path=output_path,
        outline_color=args.outline_color,
        fill_color=args.fill_color,
        fill_alpha=args.fill_alpha,
        width=args.width,
        preview_scale=args.preview_scale,
    )

    print(f"raw_detections={len(detections)}")
    print(f"deduped_detections={len(deduped_detections)}")
    print(f"min_confidence={args.min_confidence}")
    print(f"output={output_path}")
    if args.preview_scale > 0:
        print(f"preview={output_path.with_name(f'{output_path.stem}_preview{output_path.suffix}')}")


if __name__ == "__main__":
    main()