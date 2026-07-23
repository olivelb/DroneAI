"""Image preview conversion independent from FastAPI and object storage."""

from __future__ import annotations

import array
import io
import struct

from PIL import Image


Image.MAX_IMAGE_PIXELS = 500_000_000


def _depth_color(value: float) -> tuple[int, int, int]:
    if value < 0.25:
        scale = value / 0.25
        return 0, int(scale * 255), 255
    if value < 0.5:
        scale = (value - 0.25) / 0.25
        return 0, 255, int((1 - scale) * 255)
    if value < 0.75:
        scale = (value - 0.5) / 0.25
        return int(scale * 255), 255, 0
    scale = (value - 0.75) / 0.25
    return 255, int((1 - scale) * 255), 0


def _pixels(image: Image.Image) -> list[float | int]:
    if image.mode == "I;16":
        data = image.tobytes()
        return list(struct.unpack(f"<{len(data) // 2}H", data))
    return list(image.getdata())


def _normalize_grayscale(image: Image.Image) -> Image.Image:
    pixels = _pixels(image)
    if not pixels:
        raise ValueError("image contains no pixels")
    low, high = min(pixels), max(pixels)
    value_range = float(high - low) if high != low else 1.0
    normalized = bytes(
        max(0, min(255, int((value - low) / value_range * 255)))
        for value in pixels
    )
    return Image.frombytes("L", image.size, normalized).convert("RGB")


def _colorize_depth(image: Image.Image) -> Image.Image:
    pixels = _pixels(image)
    if not pixels:
        raise ValueError("image contains no pixels")
    low, high = min(pixels), max(pixels)
    value_range = float(high - low) if high != low else 1.0
    rgb = array.array("B", [0] * (len(pixels) * 3))
    for index, value in enumerate(pixels):
        red, green, blue = _depth_color((value - low) / value_range)
        rgb[index * 3 : index * 3 + 3] = array.array(
            "B",
            (red, green, blue),
        )
    return Image.frombytes("RGB", image.size, bytes(rgb))


def render_preview(
    raw: bytes,
    *,
    max_size: int = 4096,
    colormap: str = "",
) -> io.BytesIO:
    max_size = min(max(256, max_size), 8192)
    with Image.open(io.BytesIO(raw)) as source:
        image = source.copy()

    if colormap == "depth" and image.mode in {"I;16", "I", "F", "L"}:
        image = _colorize_depth(image)
    elif image.mode in {"I;16", "I", "F"}:
        image = _normalize_grayscale(image)
    elif image.mode in {"P", "CMYK"}:
        image = image.convert("RGB")
    elif image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGB")

    width, height = image.size
    if max(width, height) > max_size:
        scale = max_size / max(width, height)
        image = image.resize(
            (int(width * scale), int(height * scale)),
            Image.Resampling.LANCZOS,
        )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
