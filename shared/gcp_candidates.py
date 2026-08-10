"""Rank source photographs near imported ground-control points."""

from __future__ import annotations

import math
from dataclasses import dataclass

from pyproj import Transformer


@dataclass(frozen=True)
class PositionedImage:
    image_name: str
    source_x: float
    source_y: float
    source_z: float
    longitude: float
    latitude: float


@dataclass(frozen=True)
class GcpImageCandidate:
    image: PositionedImage
    distance_m: float


def parse_positioned_images(payload: bytes, source_crs: str) -> tuple[PositionedImage, ...]:
    """Parse DroneAI ``geo_data.txt`` into projected and WGS84 positions."""

    transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    images: list[PositionedImage] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(
        payload.decode("utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            raise ValueError(f"geo_data line {line_number}: expected image X Y Z")
        image_name = " ".join(fields[:-3]).strip()
        if not image_name or image_name in seen:
            raise ValueError(f"geo_data line {line_number}: invalid or duplicate image name")
        try:
            source_x, source_y, source_z = (float(value) for value in fields[-3:])
        except ValueError as error:
            raise ValueError(
                f"geo_data line {line_number}: invalid image coordinates"
            ) from error
        if not all(math.isfinite(value) for value in (source_x, source_y, source_z)):
            raise ValueError(f"geo_data line {line_number}: coordinates must be finite")
        longitude, latitude = transformer.transform(source_x, source_y)
        images.append(
            PositionedImage(
                image_name=image_name,
                source_x=source_x,
                source_y=source_y,
                source_z=source_z,
                longitude=float(longitude),
                latitude=float(latitude),
            )
        )
        seen.add(image_name)
    return tuple(images)


def rank_image_candidates(
    *,
    longitude: float,
    latitude: float,
    images: tuple[PositionedImage, ...],
    projected_crs: str,
    radius_m: float,
    limit: int,
) -> tuple[GcpImageCandidate, ...]:
    """Return nearest image positions in a metric projected CRS."""

    if not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("candidate radius must be positive and finite")
    if limit < 1:
        raise ValueError("candidate limit must be positive")
    transformer = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
    point_x, point_y = transformer.transform(longitude, latitude)
    candidates = [
        GcpImageCandidate(
            image=image,
            distance_m=math.hypot(image.source_x - point_x, image.source_y - point_y),
        )
        for image in images
    ]
    return tuple(
        candidate
        for candidate in sorted(candidates, key=lambda item: item.distance_m)
        if candidate.distance_m <= radius_m
    )[:limit]


def rank_new_image_candidates(
    *,
    longitude: float,
    latitude: float,
    images: tuple[PositionedImage, ...],
    projected_crs: str,
    radius_m: float,
    limit: int,
    existing_image_names: set[str],
) -> tuple[GcpImageCandidate, ...]:
    """Return unseen candidates while preserving prior operator decisions."""

    ranked = rank_image_candidates(
        longitude=longitude,
        latitude=latitude,
        images=images,
        projected_crs=projected_crs,
        radius_m=radius_m,
        limit=limit + len(existing_image_names),
    )
    return tuple(
        candidate
        for candidate in ranked
        if candidate.image.image_name not in existing_image_names
    )[:limit]
