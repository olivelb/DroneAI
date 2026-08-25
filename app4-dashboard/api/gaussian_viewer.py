"""Tenant-safe resolution of immutable GSTile viewer artifacts."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Protocol, cast

from fastapi import HTTPException, status

from shared import storage
from shared.database import MissionArtifact
from shared.gstile_manifest import safe_bundle_path, validate_gstile_manifest
from shared.stage_workspace import resolve_workspace_files

MAX_VIEWER_MANIFEST_BYTES = 8 * 1024 * 1024
VIEWER_URL_TTL_SECONDS = 900


class ViewerMission(Protocol):
    id: int
    organization_id: str


class ViewerSession(Protocol):
    def query(self, *entities: Any) -> Any: ...


def _latest_viewer_artifact(
    session: ViewerSession,
    mission: ViewerMission,
) -> MissionArtifact:
    artifact = cast(
        MissionArtifact | None,
        session.query(MissionArtifact)
        .filter(
            MissionArtifact.mission_id == mission.id,
            MissionArtifact.kind == "gaussian_viewer_bundle",
        )
        .order_by(MissionArtifact.created_at.desc(), MissionArtifact.id.desc())
        .first(),
    )
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gaussian viewer is not available",
        )
    return artifact


def _recommended_view(metadata: dict[str, Any]) -> dict[str, Any] | None:
    raw = metadata.get("recommended_view")
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("kind") != "facade":
        raise ValueError("Gaussian viewer recommended view is invalid")

    def vector(name: str) -> list[float]:
        value = raw.get(name)
        if (
            not isinstance(value, list)
            or len(value) != 3
            or any(
                isinstance(entry, bool)
                or not isinstance(entry, (int, float))
                or not math.isfinite(float(entry))
                for entry in value
            )
        ):
            raise ValueError(f"Gaussian viewer recommended {name} is invalid")
        return [float(entry) for entry in value]

    right = vector("right")
    up = vector("up")
    outward = vector("outward")

    def dot(left: list[float], right_vector: list[float]) -> float:
        return sum(left[index] * right_vector[index] for index in range(3))

    cross = [
        right[1] * up[2] - right[2] * up[1],
        right[2] * up[0] - right[0] * up[2],
        right[0] * up[1] - right[1] * up[0],
    ]
    tolerance = 1e-3
    if (
        any(
            abs(dot(vector_value, vector_value) - 1.0) > tolerance
            for vector_value in (right, up, outward)
        )
        or abs(dot(right, up)) > tolerance
        or abs(dot(right, outward)) > tolerance
        or abs(dot(up, outward)) > tolerance
        or dot(cross, outward) < 1.0 - tolerance
    ):
        raise ValueError("Gaussian viewer recommended view is not orthonormal")
    return {
        "kind": "facade",
        "right": right,
        "up": up,
        "outward": outward,
    }


def gaussian_viewer_descriptor(
    session: ViewerSession,
    mission: ViewerMission,
    *,
    expires_seconds: int = VIEWER_URL_TTL_SECONDS,
    accept_zstd: bool = False,
) -> dict[str, Any]:
    """Return a validated manifest plus browser-reachable pack URLs."""

    if not 60 <= expires_seconds <= 3600:
        raise ValueError("Viewer URL lifetime must be between 60 and 3600 seconds")
    artifact = _latest_viewer_artifact(session, mission)
    metadata = cast(dict[str, Any], artifact.artifact_metadata or {})
    manifest_key = metadata.get("manifest_key")
    manifest_file = metadata.get("viewer_manifest_file", "manifest.json")
    if not isinstance(manifest_key, str) or not manifest_key:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gaussian viewer artifact has no workspace manifest",
        )
    try:
        manifest_relative = safe_bundle_path(
            manifest_file,
            "viewer_manifest_file",
        )
        files = resolve_workspace_files(
            manifest_key,
            cast(str, artifact.checksum_sha256),
            expected_organization_id=mission.organization_id,
        )
        manifest_entry = files.get(manifest_relative)
        if manifest_entry is None:
            raise ValueError("Viewer workspace does not publish its manifest")
        payload = json.loads(
            storage.get_object_bytes(
                manifest_entry.blob.key,
                max_bytes=MAX_VIEWER_MANIFEST_BYTES,
            )
        )
        if not isinstance(payload, dict):
            raise ValueError("GSTile manifest must be a JSON object")
        validate_gstile_manifest(payload)
        if payload.get("bundleId") != metadata.get("bundle_id"):
            raise ValueError("GSTile bundle identity differs from artifact metadata")
        if payload.get("source", {}).get("sha256") != metadata.get("source_filtered_sha256"):
            raise ValueError("GSTile source identity differs from artifact metadata")
        recommended_view = _recommended_view(metadata)
        base = PurePosixPath(manifest_relative).parent
        signed_packs: list[dict[str, Any]] = []
        for pack in cast(list[dict[str, Any]], payload["packs"]):
            relative = (base / safe_bundle_path(pack["path"], "pack.path")).as_posix()
            entry = files.get(relative)
            if entry is None:
                raise ValueError(f"Viewer workspace does not publish {relative}")
            if entry.blob.size_bytes != pack["byteLength"] or entry.blob.checksum_sha256 != pack["sha256"]:
                raise ValueError(f"GSTile pack integrity differs for {pack['id']}")
            signed_pack: dict[str, Any] = {
                "id": pack["id"],
                "url": storage.get_presigned_url(
                    entry.blob.key,
                    expires=expires_seconds,
                ),
                "byteLength": pack["byteLength"],
                "sha256": pack["sha256"],
            }
            zstd = pack.get("encodings", {}).get("zstd")
            if zstd is not None:
                encoded_relative = (
                    base / safe_bundle_path(zstd["path"], "pack.encodings.zstd.path")
                ).as_posix()
                encoded_entry = files.get(encoded_relative)
                if encoded_entry is None:
                    raise ValueError(
                        f"Viewer workspace does not publish {encoded_relative}"
                    )
                if (
                    encoded_entry.blob.size_bytes != zstd["byteLength"]
                    or encoded_entry.blob.checksum_sha256 != zstd["sha256"]
                ):
                    raise ValueError(
                        f"GSTile zstd integrity differs for {pack['id']}"
                    )
                if accept_zstd:
                    signed_pack["encodings"] = {
                        "zstd": {
                            "url": storage.get_presigned_url(
                                encoded_entry.blob.key,
                                expires=expires_seconds,
                                response_content_encoding="zstd",
                            ),
                            "byteLength": zstd["byteLength"],
                            "sha256": zstd["sha256"],
                        }
                    }
            signed_packs.append(signed_pack)
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to resolve Gaussian viewer artifact: {error}",
        ) from error

    descriptor = {
        "schema": "droneai-gaussian-viewer-descriptor",
        "version": 1,
        "artifactId": cast(str, artifact.artifact_id),
        "bundleId": payload["bundleId"],
        "expiresAt": (datetime.now(UTC) + timedelta(seconds=expires_seconds)).isoformat(),
        "manifest": payload,
        "packs": signed_packs,
    }
    if recommended_view is not None:
        descriptor["recommendedView"] = recommended_view
    return descriptor
