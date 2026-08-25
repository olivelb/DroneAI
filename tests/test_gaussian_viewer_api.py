from __future__ import annotations

import importlib
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from shared.artifact_manifest import ManifestBlob, ManifestFile


viewer = importlib.import_module("app4-dashboard.api.gaussian_viewer")
viewer_router = importlib.import_module(
    "app4-dashboard.api.routers.mission_gaussians"
)


def _manifest(*, digest: str = "b" * 64) -> dict[str, object]:
    return {
        "schema": "droneai-gstile",
        "version": 1,
        "profile": "dronegs-sh3-opacity-sh3-q96",
        "bundleId": "sha256:" + "a" * 64,
        "root": "root",
        "source": {"sha256": "c" * 64, "gaussianCount": 1},
        "packs": [
            {
                "id": "pack-0",
                "path": "packs/pack-0.gstp",
                "byteOffset": 32,
                "byteLength": 128,
                "recordCount": 1,
                "sha256": digest,
            }
        ],
        "nodes": [
            {
                "id": "root",
                "bounds": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
                "gaussianCount": 1,
                "tile": {
                    "pack": "pack-0",
                    "byteOffset": 32,
                    "byteLength": 96,
                    "recordCount": 1,
                    "sha256": digest,
                },
            }
        ],
        "statistics": {"lod": "leaf-only"},
    }


def _artifact(*, recommended_view: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id="artifact-viewer",
        checksum_sha256="d" * 64,
        artifact_metadata={
            "manifest_key": "organizations/org-a/manifests/viewer.json",
            "viewer_manifest_file": "manifest.json",
            "bundle_id": "sha256:" + "a" * 64,
            "source_filtered_sha256": "c" * 64,
            "recommended_view": recommended_view,
        },
    )


def _files(digest: str = "b" * 64) -> dict[str, ManifestFile]:
    return {
        "manifest.json": ManifestFile(
            path="manifest.json",
            role="viewer-manifest",
            blob=ManifestBlob(
                key="organizations/org-a/blobs/manifest",
                size_bytes=100,
                checksum_sha256="e" * 64,
            ),
        ),
        "packs/pack-0.gstp": ManifestFile(
            path="packs/pack-0.gstp",
            role="viewer-pack",
            blob=ManifestBlob(
                key="organizations/org-a/blobs/pack",
                size_bytes=128,
                checksum_sha256=digest,
            ),
        ),
    }


def test_descriptor_validates_tenant_workspace_and_signs_packs(monkeypatch):
    artifact = _artifact()
    mission = SimpleNamespace(id=7, organization_id="org-a")
    observed: dict[str, object] = {}
    monkeypatch.setattr(viewer, "_latest_viewer_artifact", lambda *_args: artifact)

    def resolve(key, checksum, *, expected_organization_id):
        observed.update(key=key, checksum=checksum, organization=expected_organization_id)
        return _files()

    monkeypatch.setattr(viewer, "resolve_workspace_files", resolve)
    monkeypatch.setattr(
        viewer.storage,
        "get_object_bytes",
        lambda key, *, max_bytes: json.dumps(_manifest()).encode(),
    )
    def sign(key, *, expires, response_cache_control):
        observed["response_cache_control"] = response_cache_control
        return f"https://objects.example/{key}?ttl={expires}"

    monkeypatch.setattr(viewer.storage, "get_presigned_url", sign)

    result = viewer.gaussian_viewer_descriptor(SimpleNamespace(), mission)

    assert observed == {
        "key": artifact.artifact_metadata["manifest_key"],
        "checksum": artifact.checksum_sha256,
        "organization": "org-a",
        "response_cache_control": viewer.VIEWER_PACK_CACHE_CONTROL,
    }
    assert result["schema"] == "droneai-gaussian-viewer-descriptor"
    assert result["artifactId"] == "artifact-viewer"
    assert result["packs"] == [
        {
            "id": "pack-0",
            "url": "https://objects.example/organizations/org-a/blobs/pack?ttl=900",
            "byteLength": 128,
            "sha256": "b" * 64,
        }
    ]


def test_viewer_route_keeps_signed_descriptor_stable_in_private_browser_cache(
    monkeypatch,
):
    session = SimpleNamespace()
    mission = SimpleNamespace(id=7, organization_id="org-a")
    descriptor = {"schema": "droneai-gaussian-viewer-descriptor"}
    monkeypatch.setattr(viewer_router, "get_session", lambda: nullcontext(session))
    monkeypatch.setattr(
        viewer_router,
        "get_owned_mission",
        lambda *_args, **_kwargs: mission,
    )
    monkeypatch.setattr(
        viewer_router,
        "gaussian_viewer_descriptor",
        lambda observed_session, observed_mission: descriptor,
    )
    response = Response()

    result = viewer_router.mission_gaussian_viewer(
        "mission-a",
        response,
        SimpleNamespace(),
    )

    assert result == descriptor
    assert response.headers["cache-control"] == (
        f"private, max-age={viewer.VIEWER_DESCRIPTOR_CACHE_SECONDS}"
    )
    assert response.headers["vary"] == "Authorization, Cookie"


def test_descriptor_publishes_validated_recommended_view(monkeypatch):
    recommended_view = {
        "kind": "facade",
        "right": [0.0, 1.0, 0.0],
        "up": [0.0, 0.0, 1.0],
        "outward": [1.0, 0.0, 0.0],
    }
    monkeypatch.setattr(
        viewer,
        "_latest_viewer_artifact",
        lambda *_args: _artifact(recommended_view=recommended_view),
    )
    monkeypatch.setattr(viewer, "resolve_workspace_files", lambda *_args, **_kwargs: _files())
    monkeypatch.setattr(
        viewer.storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: json.dumps(_manifest()).encode(),
    )
    monkeypatch.setattr(viewer.storage, "get_presigned_url", lambda *_args, **_kwargs: "https://objects.example/pack")

    result = viewer.gaussian_viewer_descriptor(
        SimpleNamespace(), SimpleNamespace(id=7, organization_id="org-a")
    )

    assert result["recommendedView"] == recommended_view


def test_descriptor_rejects_invalid_recommended_view(monkeypatch):
    artifact = _artifact(
        recommended_view={"kind": "facade", "right": [1, 0, 0], "up": [1, 0, 0], "outward": [0, 0, 1]}
    )
    monkeypatch.setattr(viewer, "_latest_viewer_artifact", lambda *_args: artifact)
    monkeypatch.setattr(viewer, "resolve_workspace_files", lambda *_args, **_kwargs: _files())
    monkeypatch.setattr(viewer.storage, "get_object_bytes", lambda *_args, **_kwargs: json.dumps(_manifest()).encode())

    with pytest.raises(HTTPException) as error:
        viewer.gaussian_viewer_descriptor(SimpleNamespace(), SimpleNamespace(id=7, organization_id="org-a"))

    assert error.value.status_code == 502
    assert "not orthonormal" in error.value.detail


def test_descriptor_fails_closed_on_pack_integrity_mismatch(monkeypatch):
    monkeypatch.setattr(viewer, "_latest_viewer_artifact", lambda *_args: _artifact())
    monkeypatch.setattr(viewer, "resolve_workspace_files", lambda *_args, **_kwargs: _files("f" * 64))
    monkeypatch.setattr(
        viewer.storage,
        "get_object_bytes",
        lambda *_args, **_kwargs: json.dumps(_manifest()).encode(),
    )

    with pytest.raises(HTTPException) as error:
        viewer.gaussian_viewer_descriptor(
            SimpleNamespace(),
            SimpleNamespace(id=7, organization_id="org-a"),
        )

    assert error.value.status_code == 502
    assert "integrity differs" in error.value.detail


def test_descriptor_rejects_unsafe_manifest_paths(monkeypatch):
    artifact = _artifact()
    artifact.artifact_metadata["viewer_manifest_file"] = "../manifest.json"
    monkeypatch.setattr(viewer, "_latest_viewer_artifact", lambda *_args: artifact)

    with pytest.raises(HTTPException) as error:
        viewer.gaussian_viewer_descriptor(
            SimpleNamespace(),
            SimpleNamespace(id=7, organization_id="org-a"),
        )

    assert error.value.status_code == 502
    assert "escapes" in error.value.detail
