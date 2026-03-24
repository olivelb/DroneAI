from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image
from ultralytics.utils.ops import xywhr2xyxyxyxy


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".JPG", ".JPEG", ".PNG", ".TIF", ".TIFF")


@dataclass
class SplitStats:
    images: int = 0
    tiles: int = 0
    positive_tiles: int = 0
    empty_tiles: int = 0
    kept_objects: int = 0
    dropped_partial_objects: int = 0
    source_duplicates_removed: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tile the EAGLE OBB dataset while preserving labels for fully contained objects.")
    parser.add_argument("--source", default="datasets/eagle_yolo11_obb/EagleDatasetYOLO", help="Path to the extracted EAGLE dataset.")
    parser.add_argument("--output", default="datasets/eagle_yolo11_obb_tiled", help="Output path for the tiled dataset.")
    parser.add_argument("--tile-size", type=int, default=1024, help="Square tile size in pixels.")
    parser.add_argument("--overlap", type=int, default=256, help="Overlap between neighboring tiles in pixels.")
    parser.add_argument("--background-limit", type=int, default=-1, help="Max empty tiles per source image. -1 keeps all empty tiles.")
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


def find_image(image_dir: Path, stem: str) -> Path:
    for suffix in IMAGE_SUFFIXES:
        candidate = image_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Image for label {stem} not found in {image_dir}")


def load_polygons(label_path: Path, image_width: int, image_height: int) -> tuple[list[tuple[int, np.ndarray]], int]:
    polygons: list[tuple[int, np.ndarray]] = []
    seen_lines: set[str] = set()
    duplicates_removed = 0
    if not label_path.exists():
        return polygons, duplicates_removed

    for raw_line in label_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in seen_lines:
            duplicates_removed += 1
            continue
        seen_lines.add(line)
        class_id_text, center_x_text, center_y_text, width_text, height_text, angle_text = line.split()
        xywhr = np.array(
            [[
                float(center_x_text) * image_width,
                float(center_y_text) * image_height,
                float(width_text) * image_width,
                float(height_text) * image_height,
                float(angle_text),
            ]],
            dtype=np.float32,
        )
        polygon = xywhr2xyxyxyxy(xywhr)[0]
        polygons.append((int(class_id_text), polygon))
    return polygons, duplicates_removed


def iter_tiles(image_width: int, image_height: int, tile_size: int, overlap: int) -> Iterable[tuple[int, int, int, int]]:
    for x_start in build_tile_starts(image_width, tile_size, overlap):
        for y_start in build_tile_starts(image_height, tile_size, overlap):
            tile_width = min(tile_size, image_width - x_start)
            tile_height = min(tile_size, image_height - y_start)
            yield x_start, y_start, tile_width, tile_height


def polygon_within_tile(polygon: np.ndarray, x_start: int, y_start: int, tile_width: int, tile_height: int) -> bool:
    x_coords = polygon[:, 0]
    y_coords = polygon[:, 1]
    return (
        np.all(x_coords >= x_start)
        and np.all(x_coords <= x_start + tile_width)
        and np.all(y_coords >= y_start)
        and np.all(y_coords <= y_start + tile_height)
    )


def polygon_intersects_tile(polygon: np.ndarray, x_start: int, y_start: int, tile_width: int, tile_height: int) -> bool:
    x_coords = polygon[:, 0]
    y_coords = polygon[:, 1]
    return not (
        np.max(x_coords) < x_start
        or np.min(x_coords) > x_start + tile_width
        or np.max(y_coords) < y_start
        or np.min(y_coords) > y_start + tile_height
    )


def write_tile_labels(tile_label_path: Path, labels: list[str]) -> None:
    tile_label_path.write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")


def process_split(source_root: Path, output_root: Path, split: str, tile_size: int, overlap: int, background_limit: int) -> SplitStats:
    stats = SplitStats()
    image_dir = source_root / split / "images"
    label_dir = source_root / split / "labels"
    output_image_dir = output_root / split / "images"
    output_label_dir = output_root / split / "labels"
    output_image_dir.mkdir(parents=True, exist_ok=True)
    output_label_dir.mkdir(parents=True, exist_ok=True)

    label_files = sorted(label_dir.glob("*.txt"))
    for label_path in label_files:
        image_path = find_image(image_dir, label_path.stem)
        with Image.open(image_path) as image:
            rgb_image = image.convert("RGB")
            image_width, image_height = rgb_image.size
            polygons, duplicates_removed = load_polygons(label_path, image_width, image_height)
            stats.images += 1
            stats.source_duplicates_removed += duplicates_removed

            empty_tiles_written = 0
            tile_index = 0
            for x_start, y_start, tile_width, tile_height in iter_tiles(image_width, image_height, tile_size, overlap):
                tile_index += 1
                tile_labels: list[str] = []
                dropped_partials = 0

                for class_id, polygon in polygons:
                    if not polygon_within_tile(polygon, x_start, y_start, tile_width, tile_height):
                        if polygon_intersects_tile(polygon, x_start, y_start, tile_width, tile_height):
                            dropped_partials += 1
                        continue
                    local_polygon = polygon.copy()
                    local_polygon[:, 0] = (local_polygon[:, 0] - x_start) / tile_width
                    local_polygon[:, 1] = (local_polygon[:, 1] - y_start) / tile_height
                    flat_points = [f"{value:.6f}" for point in local_polygon for value in point]
                    tile_labels.append(" ".join([str(class_id)] + flat_points))

                is_positive = bool(tile_labels)
                if not is_positive and background_limit >= 0 and empty_tiles_written >= background_limit:
                    continue

                tile_name = f"{image_path.stem}_x{x_start}_y{y_start}"
                tile_image_path = output_image_dir / f"{tile_name}.jpg"
                tile_label_path = output_label_dir / f"{tile_name}.txt"

                tile = rgb_image.crop((x_start, y_start, x_start + tile_width, y_start + tile_height))
                tile.save(tile_image_path, quality=95)
                write_tile_labels(tile_label_path, tile_labels)

                stats.tiles += 1
                stats.dropped_partial_objects += dropped_partials
                if is_positive:
                    stats.positive_tiles += 1
                    stats.kept_objects += len(tile_labels)
                else:
                    stats.empty_tiles += 1
                    empty_tiles_written += 1

    return stats


def write_data_yaml(output_root: Path) -> Path:
    data_yaml = output_root / "data.yaml"
    data_yaml.write_text(
        "\n".join(
            [
                f"path: {output_root.resolve()}",
                "train: train/images",
                "val: val/images",
                "test: test/images",
                "nc: 1",
                "names:",
                "  0: vehicle",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return data_yaml


def main() -> None:
    args = parse_args()
    source_root = Path(args.source)
    output_root = Path(args.output)
    if not source_root.exists():
        raise FileNotFoundError(f"Source dataset not found: {source_root}")

    summaries: dict[str, SplitStats] = {}
    for split in ("train", "val", "test"):
        summaries[split] = process_split(source_root, output_root, split, args.tile_size, args.overlap, args.background_limit)

    data_yaml = write_data_yaml(output_root)
    print(f"Tiled dataset written to {output_root}")
    print(f"data.yaml: {data_yaml}")
    for split, stats in summaries.items():
        print(
            f"{split}: images={stats.images} tiles={stats.tiles} positive_tiles={stats.positive_tiles} "
            f"empty_tiles={stats.empty_tiles} kept_objects={stats.kept_objects} dropped_partial_objects={stats.dropped_partial_objects} "
            f"source_duplicates_removed={stats.source_duplicates_removed}"
        )


if __name__ == "__main__":
    main()