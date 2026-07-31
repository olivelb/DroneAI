"""Image preview conversion independent from FastAPI and object storage."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

# Reject decompression bombs before copying pixels into another full-size
# buffer. Orthomosaics use the COG preview/tile endpoints instead.
MAX_PREVIEW_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_PREVIEW_PIXELS


class PreviewTooLargeError(ValueError):
    """Raised before decoding a preview that exceeds the pixel budget."""


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


def _normalized_luminance(image: Image.Image) -> Image.Image:
    values = np.array(image, dtype=np.float32, copy=True)
    if not values.size:
        raise ValueError("image contains no pixels")
    finite = np.isfinite(values)
    if not finite.any():
        raise ValueError("image contains no finite pixels")
    low = float(values[finite].min())
    high = float(values[finite].max())
    values[~finite] = low
    values -= low
    if high != low:
        values *= 255.0 / (high - low)
    np.clip(values, 0, 255, out=values)
    normalized = values.astype(np.uint8)
    return Image.fromarray(normalized, mode="L")


def _normalize_grayscale(image: Image.Image) -> Image.Image:
    return _normalized_luminance(image).convert("RGB")


def _colorize_depth(image: Image.Image) -> Image.Image:
    indexed = _normalized_luminance(image).convert("P")
    palette = [
        channel
        for value in range(256)
        for channel in _depth_color(value / 255.0)
    ]
    indexed.putpalette(palette)
    return indexed.convert("RGB")


def render_preview(
    raw: bytes,
    *,
    max_size: int = 4096,
    colormap: str = "",
) -> io.BytesIO:
    max_size = min(max(256, max_size), 8192)
    with Image.open(io.BytesIO(raw)) as source:
        if source.width * source.height > MAX_PREVIEW_PIXELS:
            raise PreviewTooLargeError(
                "Preview exceeds the "
                f"{MAX_PREVIEW_PIXELS:,}-pixel safety limit"
            )
        image = source.copy()

    if colormap == "depth" and image.mode in {"I;16", "I", "F", "L"}:
        image = _colorize_depth(image)
    elif image.mode in {"I;16", "I", "F"}:
        image = _normalize_grayscale(image)
    elif image.mode in {"P", "CMYK"} or image.mode not in {"RGB", "RGBA", "L"}:
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
