from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

map_support = importlib.import_module("app4-dashboard.api.map_support")
map_features = importlib.import_module("app4-dashboard.api.routers.map_features")


class _Query:
    def __init__(self, artifact: object | None) -> None:
        self.artifact = artifact

    def filter(self, *_criteria: object) -> _Query:
        return self

    def order_by(self, *_criteria: object) -> _Query:
        return self

    def first(self) -> object | None:
        return self.artifact


class _Session:
    def __init__(self, artifact: object | None) -> None:
        self.artifact = artifact

    def query(self, *_entities: object) -> _Query:
        return _Query(self.artifact)


def _artifact(**metadata: Any) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id="raster-artifact-id",
        uri="s3://drone-ai/mission/raster/manifest.json",
        checksum_sha256="a" * 64,
        artifact_metadata={
            "manifest_key": "mission/raster/manifest.json",
            **metadata,
        },
    )


def _mission(vol_id: str, mission_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=mission_id,
        vol_id=vol_id,
        organization_id="legacy-unassigned",
        workspace_prefix=f"missions/{vol_id}",
    )


def test_raster_product_resolves_content_addressed_ortho(monkeypatch):
    monkeypatch.setattr(
        map_support,
        "_workspace_object_keys",
        lambda _key, _checksum, _organization: {
            "orthomosaic.tif": "blobs/sha256/aa/ortho",
            "orthomosaic.tif.cog.json": "blobs/sha256/bb/sidecar",
        },
    )
    monkeypatch.setattr(map_support.storage, "file_exists", lambda _key: True)

    product = map_support.resolve_raster_product(
        _Session(_artifact(ortho_file="orthomosaic.tif")),
        _mission("mission-1", 7),
        "mission-1",
        "ortho",
    )

    assert product.key == "blobs/sha256/aa/ortho"
    assert product.sidecar_key == "blobs/sha256/bb/sidecar"
    assert product.artifact_id == "raster-artifact-id"
    assert product.default_colormap == ""


def test_raster_product_resolves_height_layer_without_sidecar(monkeypatch):
    monkeypatch.setattr(
        map_support,
        "_workspace_object_keys",
        lambda _key, _checksum, _organization: {
            "orthomosaic.height.tif": "blobs/sha256/cc/height",
        },
    )
    monkeypatch.setattr(map_support.storage, "file_exists", lambda _key: True)

    product = map_support.resolve_raster_product(
        _Session(_artifact(height_file="orthomosaic.height.tif")),
        _mission("mission-1", 7),
        "mission-1",
        "depth",
    )

    assert product.key == "blobs/sha256/cc/height"
    assert product.sidecar_key is None
    assert product.default_colormap == "depth"


def test_raster_product_keeps_legacy_mission_compatibility(monkeypatch):
    existing = {
        "missions/legacy/orthomosaic.tif",
        "missions/legacy/orthomosaic.tif.cog.json",
    }
    monkeypatch.setattr(
        map_support.storage,
        "file_exists",
        lambda key: key in existing,
    )

    product = map_support.resolve_raster_product(
        _Session(None),
        _mission("legacy", 8),
        "legacy",
        "ortho",
    )

    assert product.key == "missions/legacy/orthomosaic.tif"
    assert product.sidecar_key == "missions/legacy/orthomosaic.tif.cog.json"
    assert product.artifact_id is None


def test_raster_product_fails_closed_for_incomplete_versioned_artifact(
    monkeypatch,
):
    monkeypatch.setattr(
        map_support,
        "_workspace_object_keys",
        lambda _key, _checksum, _organization: {
            "unrelated.txt": "blobs/sha256/dd/other"
        },
    )

    with pytest.raises(HTTPException) as error:
        map_support.resolve_raster_product(
            _Session(_artifact()),
            _mission("mission-1", 7),
            "mission-1",
            "ortho",
        )

    assert error.value.status_code == 502
    assert "orthomosaic.tif" in str(error.value.detail)


def test_pipeline_detection_features_are_scoped_and_decorated(monkeypatch):
    monkeypatch.setattr(
        map_support,
        "resolve_detection_product",
        lambda _session, _mission: map_support.DetectionProductObject(
            key="blobs/sha256/ee/detections",
            artifact_id="detections-artifact-id",
        ),
    )
    monkeypatch.setattr(
        map_support,
        "load_json_object",
        lambda _key: {
            "type": "FeatureCollection",
            "properties": {"vol_id": "mission-1"},
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [3.1, 42.4]},
                    "properties": {
                        "vol_id": "mission-1",
                        "class_name": "car",
                        "confidence": 0.9,
                    },
                }
            ],
        },
    )

    result = map_support.pipeline_detection_features(
        _Session(None),
        SimpleNamespace(id=7),
        "mission-1",
        (3.0, 42.0, 4.0, 43.0),
        10,
    )

    assert result is not None
    features, truncated = result
    assert truncated is False
    assert len(features) == 1
    assert features[0]["properties"]["source"] == "legacy"
    assert features[0]["properties"]["name"] == "car"


def test_pipeline_detection_features_reject_cross_mission_artifact(monkeypatch):
    monkeypatch.setattr(
        map_support,
        "resolve_detection_product",
        lambda _session, _mission: map_support.DetectionProductObject(
            key="blobs/sha256/ff/detections",
            artifact_id="detections-artifact-id",
        ),
    )
    monkeypatch.setattr(
        map_support,
        "load_json_object",
        lambda _key: {
            "type": "FeatureCollection",
            "properties": {"vol_id": "another-mission"},
            "features": [],
        },
    )

    with pytest.raises(HTTPException) as error:
        map_support.pipeline_detection_features(
            _Session(None),
            SimpleNamespace(id=7),
            "mission-1",
            None,
            10,
        )

    assert error.value.status_code == 502
    assert "identity" in str(error.value.detail)


def test_pipeline_artifact_search_applies_operator_filters(monkeypatch):
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [3.1, 42.4]},
            "properties": {
                "class_name": "car",
                "name": "car",
                "confidence": 0.91,
                "source": "legacy",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [3.2, 42.5]},
            "properties": {
                "class_name": "building",
                "name": "building",
                "confidence": 0.7,
                "source": "legacy",
            },
        },
    ]
    monkeypatch.setattr(
        map_features,
        "pipeline_detection_features",
        lambda *_args: (features, False),
    )

    selected = map_features._search_pipeline_artifact(
        _Session(None),
        SimpleNamespace(id=7),
        vol_id="mission-1",
        text="CAR",
        class_name="car",
        min_confidence=0.8,
        bounds=None,
        reviewed=False,
        deleted=False,
        limit=10,
    )

    assert selected == ([features[0]], False)
