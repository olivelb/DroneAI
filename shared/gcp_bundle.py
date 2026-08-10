"""Immutable calculation bundle contract for operator-marked GCPs."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from shared.artifact_manifest import content_addressed_blob_key


@dataclass(frozen=True)
class BundleObservation:
    image_name: str
    pixel_x: float
    pixel_y: float


@dataclass(frozen=True)
class BundlePoint:
    external_id: str
    source_xyz: tuple[float, float, float]
    role: str
    horizontal_accuracy_m: float
    vertical_accuracy_m: float
    image_accuracy_px: float
    observations: tuple[BundleObservation, ...]


@dataclass(frozen=True)
class GcpBundleFiles:
    gcp_list: bytes
    accuracy_csv: bytes
    adjustment_points: int
    checkpoint_points: int
    observation_count: int


def build_gcp_bundle_files(
    source_crs: str,
    points: list[BundlePoint],
) -> GcpBundleFiles:
    """Build deterministic ODM coordinates and per-point covariance files."""

    active = sorted(
        (point for point in points if point.role != "disabled"),
        key=lambda point: point.external_id,
    )
    invalid = [point.external_id for point in active if len(point.observations) < 2]
    if invalid:
        raise ValueError(
            "Every active GCP requires at least two marked photos: " + ", ".join(invalid)
        )
    adjustment_count = sum(point.role == "adjustment" for point in active)
    if adjustment_count < 3:
        raise ValueError("At least three marked adjustment GCPs are required")
    lines = [source_crs]
    accuracy_buffer = io.StringIO(newline="")
    writer = csv.writer(accuracy_buffer, lineterminator="\n")
    writer.writerow(
        (
            "point_id",
            "horizontal_accuracy_m",
            "vertical_accuracy_m",
            "image_accuracy_px",
            "role",
        )
    )
    observation_count = 0
    for point in active:
        if any(character.isspace() for character in point.external_id):
            raise ValueError(f"GCP identifier contains whitespace: {point.external_id!r}")
        writer.writerow(
            (
                point.external_id,
                format(point.horizontal_accuracy_m, ".12g"),
                format(point.vertical_accuracy_m, ".12g"),
                format(point.image_accuracy_px, ".12g"),
                point.role,
            )
        )
        for observation in sorted(point.observations, key=lambda item: item.image_name):
            if any(character.isspace() for character in observation.image_name):
                raise ValueError(
                    f"Image name contains whitespace and cannot be exported to ODM: {observation.image_name!r}"
                )
            x, y, z = point.source_xyz
            lines.append(
                " ".join(
                    (
                        format(x, ".12g"),
                        format(y, ".12g"),
                        format(z, ".12g"),
                        format(observation.pixel_x, ".12g"),
                        format(observation.pixel_y, ".12g"),
                        observation.image_name,
                        point.external_id,
                    )
                )
            )
            observation_count += 1
    return GcpBundleFiles(
        gcp_list=("\n".join(lines) + "\n").encode(),
        accuracy_csv=accuracy_buffer.getvalue().encode(),
        adjustment_points=adjustment_count,
        checkpoint_points=sum(point.role == "checkpoint" for point in active),
        observation_count=observation_count,
    )


def bundle_blob(data: bytes) -> dict[str, Any]:
    checksum = hashlib.sha256(data).hexdigest()
    return {
        "key": content_addressed_blob_key(checksum),
        "size": len(data),
        "sha256": checksum,
    }


def validate_gcp_bundle(payload: object) -> dict[str, dict[str, Any]]:
    """Fail closed on a stage-supplied immutable GCP bundle descriptor."""

    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "set_id",
        "source_sha256",
        "gcp_list",
        "accuracy_csv",
        "quality",
    }:
        raise ValueError("GCP bundle fields are invalid")
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported GCP bundle schema version")
    try:
        UUID(str(payload.get("set_id")))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("GCP bundle set_id is invalid") from error
    source_checksum = payload.get("source_sha256")
    if not isinstance(source_checksum, str) or re.fullmatch(
        r"[0-9a-f]{64}", source_checksum
    ) is None:
        raise ValueError("GCP bundle source checksum is invalid")
    quality = payload.get("quality")
    if not isinstance(quality, dict) or set(quality) != {
        "adjustment_points",
        "checkpoint_points",
        "marked_observations",
        "verification",
    }:
        raise ValueError("GCP bundle quality summary is invalid")
    counts: dict[str, int] = {}
    for name in ("adjustment_points", "checkpoint_points", "marked_observations"):
        value = quality.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"GCP bundle quality field {name} is invalid")
        counts[name] = value
    if counts["adjustment_points"] < 3:
        raise ValueError("GCP bundle requires at least three adjustment points")
    if counts["marked_observations"] < 2 * (
        counts["adjustment_points"] + counts["checkpoint_points"]
    ):
        raise ValueError("GCP bundle observation count is inconsistent")
    expected_verification = (
        "independent-checkpoints"
        if counts["checkpoint_points"] > 0
        else "adjustment-only-unverified"
    )
    if quality.get("verification") != expected_verification:
        raise ValueError("GCP bundle verification status is inconsistent")
    result: dict[str, dict[str, Any]] = {}
    for name in ("gcp_list", "accuracy_csv"):
        descriptor = payload.get(name)
        if not isinstance(descriptor, dict) or set(descriptor) != {"key", "size", "sha256"}:
            raise ValueError(f"GCP bundle {name} descriptor is invalid")
        checksum = descriptor.get("sha256")
        size = descriptor.get("size")
        key = descriptor.get("key")
        if not isinstance(checksum, str) or re.fullmatch(
            r"[0-9a-f]{64}", checksum
        ) is None:
            raise ValueError(f"GCP bundle {name} checksum is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError(f"GCP bundle {name} size is invalid")
        if key != content_addressed_blob_key(checksum):
            raise ValueError(f"GCP bundle {name} key is not content-addressed")
        result[name] = descriptor
    return result
