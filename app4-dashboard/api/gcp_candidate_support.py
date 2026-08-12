"""Photo-candidate ranking and observation construction for GCP routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.camera_projection import (
    CameraProjectionIndex,
    rank_projected_image_candidates,
)
from shared.database import GcpObservation, GcpPoint
from shared.gcp_candidates import (
    PositionedImage,
    rank_image_candidates,
    rank_new_image_candidates,
)


@dataclass(frozen=True)
class CandidateSpec:
    image_name: str
    method: str
    distance_m: float | None = None
    projected_pixel_x: float | None = None
    projected_pixel_y: float | None = None
    image_width_px: int | None = None
    image_height_px: int | None = None
    positioned: PositionedImage | None = None


def rank_candidate_specs(
    *,
    longitude: float,
    latitude: float,
    altitude_m: float,
    positions: Any,
    camera_index: CameraProjectionIndex | None,
    radius_m: float,
    limit: int,
    existing_image_names: set[str] | None = None,
) -> tuple[CandidateSpec, ...]:
    """Prefer registered-camera visibility and fill remaining slots by EXIF distance."""

    excluded = set(existing_image_names or set())
    positioned_by_name = (
        {item.image_name: item for item in positions.images} if positions else {}
    )
    specs: list[CandidateSpec] = []
    if camera_index is not None:
        for candidate in rank_projected_image_candidates(
            longitude=longitude,
            latitude=latitude,
            altitude_m=altitude_m,
            camera_index=camera_index,
            limit=limit,
            existing_image_names=excluded,
            border_margin_ratio=0.01,
        ):
            positioned = positioned_by_name.get(candidate.image_name)
            distance = None
            if positioned is not None:
                transformer_candidates = rank_image_candidates(
                    longitude=longitude,
                    latitude=latitude,
                    images=(positioned,),
                    projected_crs=positions.projected_crs,
                    radius_m=max(radius_m, 1.0e-6),
                    limit=1,
                )
                if transformer_candidates:
                    distance = transformer_candidates[0].distance_m
            specs.append(
                CandidateSpec(
                    image_name=candidate.image_name,
                    method="camera-projection",
                    distance_m=distance,
                    projected_pixel_x=candidate.pixel_x,
                    projected_pixel_y=candidate.pixel_y,
                    image_width_px=candidate.image_width_px,
                    image_height_px=candidate.image_height_px,
                    positioned=positioned,
                )
            )
            excluded.add(candidate.image_name)
    if positions is not None and len(specs) < limit:
        for exif_candidate in rank_new_image_candidates(
            longitude=longitude,
            latitude=latitude,
            images=positions.images,
            projected_crs=positions.projected_crs,
            radius_m=radius_m,
            limit=limit - len(specs),
            existing_image_names=excluded,
        ):
            specs.append(
                CandidateSpec(
                    image_name=exif_candidate.image.image_name,
                    method="exif-distance",
                    distance_m=exif_candidate.distance_m,
                    positioned=exif_candidate.image,
                )
            )
    return tuple(specs)


def candidate_generation_method(
    positions: Any,
    camera_index: CameraProjectionIndex | None,
) -> str | None:
    if camera_index is not None and positions is not None:
        return "camera-projection+exif-distance"
    if camera_index is not None:
        return "camera-projection"
    if positions is not None:
        return "exif-distance"
    return None


def image_key(dataset_prefix: str | None, image_name: str) -> str | None:
    if not dataset_prefix:
        return None
    return f"{dataset_prefix.rstrip('/')}/{image_name.lstrip('/')}"


def candidate_observation(
    point: GcpPoint,
    candidate: CandidateSpec,
    *,
    dataset_prefix: str | None,
    actor_subject: str,
    status: str = "candidate",
    pixel_x: float | None = None,
    pixel_y: float | None = None,
) -> GcpObservation:
    """Build one consistently-provenanced photo observation."""

    positioned = candidate.positioned
    return GcpObservation(
        gcp_point_id=point.id,
        image_name=candidate.image_name,
        image_s3_key=image_key(dataset_prefix, candidate.image_name),
        status=status,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        candidate_distance_m=candidate.distance_m,
        candidate_method=candidate.method,
        projected_pixel_x=candidate.projected_pixel_x,
        projected_pixel_y=candidate.projected_pixel_y,
        image_width_px=candidate.image_width_px,
        image_height_px=candidate.image_height_px,
        image_longitude=(positioned.longitude if positioned else None),
        image_latitude=(positioned.latitude if positioned else None),
        created_by=actor_subject,
        updated_by=actor_subject,
    )
