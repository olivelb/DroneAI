from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from PIL import Image, ImageColor, ImageDraw
from transformers import Sam3Model, Sam3Processor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SAM3 promptable segmentation over a directory of tile images.")
    parser.add_argument("--source", required=True, help="Directory containing input tile images.")
    parser.add_argument("--output", required=True, help="Directory to write overlay images and JSON results.")
    parser.add_argument("--prompt", default="car", help="Text prompt for SAM3 promptable concept segmentation.")
    parser.add_argument("--model", default="facebook/sam3", help="Hugging Face model id.")
    parser.add_argument("--threshold", type=float, default=0.5, help="Instance confidence threshold.")
    parser.add_argument("--mask-threshold", type=float, default=0.5, help="Mask binarization threshold.")
    parser.add_argument("--device", default="cuda", help="Device to run inference on, for example 'cuda' or 'cpu'.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of images to process. 0 means all.")
    parser.add_argument("--pattern", default="*.jpg", help="Glob pattern for tile images.")
    return parser.parse_args()


def overlay_masks(image: Image.Image, masks: torch.Tensor) -> Image.Image:
    rgba_image = image.convert("RGBA")
    colors = ["#ff2d55", "#00c2ff", "#ffd60a", "#32d74b", "#ff9f0a", "#bf5af2"]

    for index, mask in enumerate(masks):
        color = ImageColor.getrgb(colors[index % len(colors)])
        mask_image = Image.fromarray((mask.cpu().numpy().astype("uint8") * 255), mode="L")
        overlay = Image.new("RGBA", image.size, color + (0,))
        overlay.putalpha(mask_image.point(lambda value: int(value * 0.35)))
        rgba_image = Image.alpha_composite(rgba_image, overlay)
    return rgba_image.convert("RGB")


def draw_boxes(image: Image.Image, boxes: torch.Tensor, scores: torch.Tensor) -> Image.Image:
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)

    for box, score in zip(boxes, scores):
        x1, y1, x2, y2 = [float(value) for value in box.tolist()]
        draw.rectangle((x1, y1, x2, y2), outline="#00c2ff", width=3)
        label = f"{score:.2f}"
        draw.text((x1 + 4, max(0, y1 - 18)), label, fill="#00c2ff")
    return annotated


def collect_images(source_dir: Path, pattern: str, limit: int) -> list[Path]:
    images = sorted(source_dir.glob(pattern))
    if limit > 0:
        return images[:limit]
    return images


def main() -> None:
    args = parse_args()
    source_dir = Path(args.source)
    output_dir = Path(args.output)
    overlays_dir = output_dir / "overlays"
    results_dir = output_dir / "results"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    device = args.device if args.device != "cuda" or torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32

    model = Sam3Model.from_pretrained(args.model).to(device)
    processor = Sam3Processor.from_pretrained(args.model)

    image_paths = collect_images(source_dir, args.pattern, args.limit)
    summary: list[dict[str, object]] = []

    for index, image_path in enumerate(image_paths, start=1):
        image = Image.open(image_path).convert("RGB")
        inputs = processor(images=image, text=args.prompt, return_tensors="pt").to(device)

        with torch.no_grad():
            with torch.autocast(device_type="cuda", dtype=dtype, enabled=device.startswith("cuda")):
                outputs = model(**inputs)

        result = processor.post_process_instance_segmentation(
            outputs,
            threshold=args.threshold,
            mask_threshold=args.mask_threshold,
            target_sizes=inputs.get("original_sizes").tolist(),
        )[0]

        masks = result.get("masks")
        boxes = result.get("boxes")
        scores = result.get("scores")

        if masks is None or len(masks) == 0:
            item = {
                "image": image_path.name,
                "prompt": args.prompt,
                "detections": 0,
                "boxes": [],
                "scores": [],
            }
            summary.append(item)
            (results_dir / f"{image_path.stem}.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
            print(f"[{index}/{len(image_paths)}] {image_path.name}: 0 detections")
            continue

        overlay = overlay_masks(image, masks)
        overlay = draw_boxes(overlay, boxes, scores)
        overlay.save(overlays_dir / image_path.name, quality=95)

        item = {
            "image": image_path.name,
            "prompt": args.prompt,
            "detections": len(scores),
            "boxes": [[float(value) for value in box.tolist()] for box in boxes],
            "scores": [float(value) for value in scores.tolist()],
        }
        summary.append(item)
        (results_dir / f"{image_path.stem}.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
        print(f"[{index}/{len(image_paths)}] {image_path.name}: {len(scores)} detections")

    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Processed {len(image_paths)} images into {output_dir}")


if __name__ == "__main__":
    main()