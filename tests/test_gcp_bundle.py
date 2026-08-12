from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from shared.gcp_bundle import (
    BundleObservation,
    BundlePoint,
    build_gcp_bundle_files,
    bundle_blob,
    validate_gcp_bundle,
)
from shared.gcp_control import parse_gcp_accuracy_file, parse_gcp_file, prepare_immutable_gcp_bundle


def _point(
    point_id: str,
    x: float,
    *,
    role: str = "adjustment",
    observations: int = 2,
) -> BundlePoint:
    return BundlePoint(
        external_id=point_id,
        source_xyz=(x, 6_240_000 + x, 212.5),
        role=role,
        horizontal_accuracy_m=0.02,
        vertical_accuracy_m=0.03,
        image_accuracy_px=0.8,
        observations=tuple(
            BundleObservation(f"DJI_{index:04d}.JPG", 100 + index, 200 + index)
            for index in range(observations)
        ),
    )


def _bundle_payload(files, organization_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 2 if organization_id is not None else 1,
        "set_id": str(UUID(int=1)),
        "source_sha256": "a" * 64,
        "gcp_list": bundle_blob(files.gcp_list, organization_id),
        "accuracy_csv": bundle_blob(files.accuracy_csv, organization_id),
        "quality": {
            "adjustment_points": files.adjustment_points,
            "checkpoint_points": files.checkpoint_points,
            "marked_observations": files.observation_count,
            "verification": (
                "independent-checkpoints"
                if files.checkpoint_points
                else "adjustment-only-unverified"
            ),
        },
    }
    if organization_id is not None:
        payload["organization_id"] = organization_id
    return payload


def test_builds_deterministic_calculation_files_with_independent_checkpoint(tmp_path):
    files = build_gcp_bundle_files(
        "EPSG:2154",
        [
            _point("P3", 3),
            _point("P1", 1),
            _point("P2", 2),
            _point("CHECK", 4, role="checkpoint"),
        ],
    )
    gcp_path = tmp_path / "gcp_list.txt"
    accuracy_path = tmp_path / "gcp_accuracy.csv"
    gcp_path.write_bytes(files.gcp_list)
    accuracy_path.write_bytes(files.accuracy_csv)

    source_crs, observations = parse_gcp_file(gcp_path)
    accuracies = parse_gcp_accuracy_file(accuracy_path)

    assert source_crs == "EPSG:2154"
    assert len(observations) == 8
    assert observations[0].point_id == "CHECK"
    assert accuracies["CHECK"].role == "checkpoint"
    assert files.adjustment_points == 3
    assert files.checkpoint_points == 1


def test_bundle_rejects_incomplete_control_geometry():
    with pytest.raises(ValueError, match="three marked adjustment"):
        build_gcp_bundle_files("EPSG:4326", [_point("P1", 1), _point("P2", 2)])
    with pytest.raises(ValueError, match="two marked photos"):
        build_gcp_bundle_files(
            "EPSG:4326",
            [_point("P1", 1), _point("P2", 2), _point("P3", 3, observations=1)],
        )


def test_bundle_descriptor_fails_closed_on_non_content_addressed_key():
    files = build_gcp_bundle_files(
        "EPSG:4326",
        [_point("P1", 1), _point("P2", 2), _point("P3", 3)],
    )
    payload = _bundle_payload(files)
    payload["gcp_list"] = {**payload["gcp_list"], "key": "mutable/gcp.txt"}

    with pytest.raises(ValueError, match="not content-addressed"):
        validate_gcp_bundle(payload)


def test_bundle_descriptor_fails_closed_on_inconsistent_quality_summary():
    files = build_gcp_bundle_files(
        "EPSG:4326",
        [_point("P1", 1), _point("P2", 2), _point("P3", 3)],
    )
    payload = _bundle_payload(files)
    payload["quality"] = {
        **payload["quality"],
        "verification": "independent-checkpoints",
    }

    with pytest.raises(ValueError, match="verification status is inconsistent"):
        validate_gcp_bundle(payload)


def test_tenant_bundle_rejects_cross_tenant_and_global_descriptors():
    files = build_gcp_bundle_files(
        "EPSG:4326",
        [_point("P1", 1), _point("P2", 2), _point("P3", 3)],
    )
    payload = _bundle_payload(files, "acme")

    validate_gcp_bundle(payload, expected_organization_id="acme")
    with pytest.raises(ValueError, match="organization does not match"):
        validate_gcp_bundle(payload, expected_organization_id="other")

    payload["gcp_list"] = bundle_blob(files.gcp_list)
    with pytest.raises(ValueError, match="not content-addressed"):
        validate_gcp_bundle(payload, expected_organization_id="acme")


def test_tenant_mission_rejects_legacy_global_bundle():
    files = build_gcp_bundle_files(
        "EPSG:4326",
        [_point("P1", 1), _point("P2", 2), _point("P3", 3)],
    )

    with pytest.raises(ValueError, match="organization does not match"):
        validate_gcp_bundle(
            _bundle_payload(files),
            expected_organization_id="acme",
        )

    validate_gcp_bundle(
        _bundle_payload(files),
        expected_organization_id="acme",
        allow_legacy_global=True,
    )


def test_worker_downloads_and_verifies_immutable_bundle(monkeypatch, tmp_path: Path):
    files = build_gcp_bundle_files(
        "EPSG:4326",
        [_point("P1", 1), _point("P2", 2), _point("P3", 3)],
    )
    payload = _bundle_payload(files)
    objects = {
        payload["gcp_list"]["key"]: files.gcp_list,
        payload["accuracy_csv"]["key"]: files.accuracy_csv,
    }

    def download(key: str, destination: Path) -> Path:
        destination.write_bytes(objects[key])
        return destination

    monkeypatch.setattr("shared.gcp_control.storage.download_file", download)

    result = prepare_immutable_gcp_bundle(payload, tmp_path)
    repeated = prepare_immutable_gcp_bundle(payload, tmp_path)

    assert Path(result["gcp_path"]).read_bytes() == files.gcp_list
    assert result["immutable_bundle"] is True
    assert result["changed"] is True
    assert repeated["changed"] is False


def test_worker_rejects_corrupted_download(monkeypatch, tmp_path: Path):
    files = build_gcp_bundle_files(
        "EPSG:4326",
        [_point("P1", 1), _point("P2", 2), _point("P3", 3)],
    )
    payload = _bundle_payload(files)

    def download(_key: str, destination: Path) -> Path:
        destination.write_bytes(b"corrupt")
        return destination

    monkeypatch.setattr("shared.gcp_control.storage.download_file", download)

    with pytest.raises(OSError, match="size does not match"):
        prepare_immutable_gcp_bundle(payload, tmp_path)
