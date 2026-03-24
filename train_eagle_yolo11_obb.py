from __future__ import annotations

import argparse
import math
import shutil
from pathlib import Path

from PIL import Image
from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert EAGLE xywhr labels to Ultralytics OBB polygons and train YOLO11n-OBB.")
    parser.add_argument("--source", default="datasets/eagle_yolo11_obb/EagleDatasetYOLO", help="Path to the extracted Kaggle EAGLE dataset.")
    parser.add_argument("--prepared", default="datasets/eagle_yolo11_obb_ultralytics", help="Output path for the converted Ultralytics-ready dataset.")
    parser.add_argument("--model", default="yolo11n-obb.pt", help="Ultralytics OBB checkpoint to fine-tune.")
    parser.add_argument("--epochs", type=int, default=10, help="Number of training epochs.")
    parser.add_argument("--imgsz", type=int, default=416, help="Training image size.")
    parser.add_argument("--batch", type=int, default=8, help="Batch size.")
    parser.add_argument("--project", default="runs/eagle_obb", help="Ultralytics project directory.")
    parser.add_argument("--name", default="yolo11n_obb_eagle", help="Ultralytics run name.")
    parser.add_argument("--device", default="", help="Training device, for example '0' or 'cpu'. Empty lets Ultralytics decide.")
    parser.add_argument("--workers", type=int, default=8, help="Data loader workers.")
    parser.add_argument("--exist-ok", action="store_true", help="Allow reusing an existing run directory.")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="auto",
        default=None,
        help="Resume training from the last checkpoint. Pass a checkpoint path or omit the value to use PROJECT/NAME/weights/last.pt.",
    )
    parser.add_argument(
        "--auto-resume-latest",
        action="store_true",
        help="Resume from the most recently updated weights/last.pt found under --project.",
    )
    parser.add_argument("--prepare-only", action="store_true", help="Only convert the dataset and write data.yaml.")
    args = parser.parse_args()
    if args.resume is not None and args.auto_resume_latest:
        parser.error("use either --resume or --auto-resume-latest, not both")
    return args


def xywhr_to_polygon(center_x: float, center_y: float, width: float, height: float, angle: float) -> list[tuple[float, float]]:
    half_width = width / 2.0
    half_height = height / 2.0
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    corners = [
        (-half_width, -half_height),
        (half_width, -half_height),
        (half_width, half_height),
        (-half_width, half_height),
    ]
    polygon = []
    for delta_x, delta_y in corners:
        x = center_x + delta_x * cos_angle - delta_y * sin_angle
        y = center_y + delta_x * sin_angle + delta_y * cos_angle
        polygon.append((x, y))
    return polygon


def convert_split(source_root: Path, prepared_root: Path, split: str) -> tuple[int, int]:
    image_dir = source_root / split / "images"
    label_dir = source_root / split / "labels"
    prepared_image_dir = prepared_root / split / "images"
    prepared_label_dir = prepared_root / split / "labels"
    prepared_image_dir.mkdir(parents=True, exist_ok=True)
    prepared_label_dir.mkdir(parents=True, exist_ok=True)

    image_count = 0
    object_count = 0
    for image_path in sorted(image_dir.iterdir()):
        if not image_path.is_file():
            continue
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            continue
        image_count += 1
        target_image_path = prepared_image_dir / image_path.name
        if not target_image_path.exists():
            shutil.copy2(image_path, target_image_path)

        label_path = label_dir / f"{image_path.stem}.txt"
        target_label_path = prepared_label_dir / label_path.name
        with Image.open(image_path) as image:
            image_width, image_height = image.size

        converted_lines: list[str] = []
        if label_path.exists():
            for raw_line in label_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                class_id_text, center_x_text, center_y_text, width_text, height_text, angle_text = line.split()
                class_id = int(class_id_text)
                center_x = float(center_x_text) * image_width
                center_y = float(center_y_text) * image_height
                width = float(width_text) * image_width
                height = float(height_text) * image_height
                angle = float(angle_text)
                polygon_pixels = xywhr_to_polygon(center_x, center_y, width, height, angle)
                polygon_normalized = []
                for x_pixel, y_pixel in polygon_pixels:
                    x_norm = min(1.0, max(0.0, x_pixel / image_width))
                    y_norm = min(1.0, max(0.0, y_pixel / image_height))
                    polygon_normalized.extend([x_norm, y_norm])
                converted_lines.append(
                    " ".join([str(class_id)] + [f"{value:.6f}" for value in polygon_normalized])
                )
                object_count += 1
        target_label_path.write_text("\n".join(converted_lines) + ("\n" if converted_lines else ""), encoding="utf-8")

    return image_count, object_count


def prepare_dataset(source_root: Path, prepared_root: Path) -> Path:
    if not source_root.exists():
        raise FileNotFoundError(f"Source dataset not found: {source_root}")

    summary = {}
    for split in ("train", "val", "test"):
        summary[split] = convert_split(source_root, prepared_root, split)

    data_yaml = prepared_root / "data.yaml"
    data_yaml.write_text(
        "\n".join(
            [
                f"path: {prepared_root.resolve()}",
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

    print("Prepared dataset:")
    for split, (image_count, object_count) in summary.items():
        print(f"  {split}: images={image_count} objects={object_count}")
    print(f"  yaml: {data_yaml}")
    return data_yaml


def resolve_resume_checkpoint(resume: str | None, project: str, name: str) -> Path | None:
    if resume is None:
        return None

    if resume == "auto":
        checkpoint_path = Path(project) / name / "weights" / "last.pt"
    else:
        checkpoint_path = Path(resume)

    checkpoint_path = checkpoint_path.expanduser()
    if not checkpoint_path.exists():
        raise FileNotFoundError(
            f"Resume checkpoint not found: {checkpoint_path}. Pass --resume /path/to/last.pt or use matching --project/--name values."
        )
    return checkpoint_path


def find_latest_resume_checkpoint(project: str) -> Path:
    project_path = Path(project).expanduser()
    checkpoint_paths = [path for path in project_path.rglob("last.pt") if path.name == "last.pt" and path.parent.name == "weights"]
    if not checkpoint_paths:
        raise FileNotFoundError(f"No resumable checkpoint found under {project_path}")
    return max(checkpoint_paths, key=lambda path: path.stat().st_mtime)


def main() -> None:
    args = parse_args()
    source_root = Path(args.source)
    prepared_root = Path(args.prepared)
    data_yaml = prepare_dataset(source_root, prepared_root)
    if args.prepare_only:
        return

    resume_checkpoint = find_latest_resume_checkpoint(args.project) if args.auto_resume_latest else resolve_resume_checkpoint(args.resume, args.project, args.name)
    if resume_checkpoint is not None:
        model = YOLO(str(resume_checkpoint))
        model.train(resume=True)
        return

    model = YOLO(args.model)
    model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=args.project,
        name=args.name,
        workers=args.workers,
        device=args.device,
        exist_ok=args.exist_ok,
    )


if __name__ == "__main__":
    main()