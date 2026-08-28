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

    def join(self, *_criteria: object) -> _Query:
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
        organization_id="tenant-a",
        workspace_prefix=f"organizations/tenant-a/missions/{vol_id}",
    )


def test_raster_product_resolves_content_addressed_ortho(monkeypatch):
    monkeypatch.setattr(
        map_support,
        "_workspace_object_keys",
        lambda _key, _checksum, _organization: {
            "orthomosaic.tif": "organizations/tenant-a/blobs/sha256/aa/ortho",
            "orthomosaic.tif.cog.json": "organizations/tenant-a/blobs/sha256/bb/sidecar",
        },
    )
    monkeypatch.setattr(map_support.storage, "file_exists", lambda _key: True)

    product = map_support.resolve_raster_product(
        _Session(_artifact(ortho_file="orthomosaic.tif")),
        _mission("mission-1", 7),
        "mission-1",
        "ortho",
    )

    assert product.key == "organizations/tenant-a/blobs/sha256/aa/ortho"
    assert product.sidecar_key == "organizations/tenant-a/blobs/sha256/bb/sidecar"
    assert product.artifact_id == "raster-artifact-id"
    assert product.default_colormap == ""


def test_raster_product_resolves_height_layer_without_sidecar(monkeypatch):
    monkeypatch.setattr(
        map_support,
        "_workspace_object_keys",
        lambda _key, _checksum, _organization: {
            "orthomosaic.height.tif": "organizations/tenant-a/blobs/sha256/cc/height",
        },
    )
    monkeypatch.setattr(map_support.storage, "file_exists", lambda _key: True)

    product = map_support.resolve_raster_product(
        _Session(_artifact(height_file="orthomosaic.height.tif")),
        _mission("mission-1", 7),
        "mission-1",
        "depth",
    )

    assert product.key == "organizations/tenant-a/blobs/sha256/cc/height"
    assert product.sidecar_key is None
    assert product.default_colormap == "depth"


def test_raster_product_rejects_historical_root_without_artifact(monkeypatch):
    monkeypatch.setattr(
        map_support.storage,
        "file_exists",
        lambda _key: pytest.fail("historical root object must not be probed"),
    )
    with pytest.raises(HTTPException) as error:
        map_support.resolve_raster_product(
            _Session(None), _mission("mission-1", 8), "mission-1", "ortho"
        )
    assert error.value.status_code == 404


def test_raster_product_fails_closed_for_incomplete_versioned_artifact(
    monkeypatch,
):
    monkeypatch.setattr(
        map_support,
        "_workspace_object_keys",
        lambda _key, _checksum, _organization: {
            "unrelated.txt": "organizations/tenant-a/blobs/sha256/dd/other"
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
            key="organizations/tenant-a/blobs/sha256/ee/detections",
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
    assert features[0]["properties"]["source"] == "pipeline"
    assert features[0]["properties"]["name"] == "car"


def test_pipeline_detection_features_reject_cross_mission_artifact(monkeypatch):
    monkeypatch.setattr(
        map_support,
        "resolve_detection_product",
        lambda _session, _mission: map_support.DetectionProductObject(
            key="organizations/tenant-a/blobs/sha256/ff/detections",
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
                "source": "pipeline",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [3.2, 42.5]},
            "properties": {
                "class_name": "building",
                "name": "building",
                "confidence": 0.7,
                "source": "pipeline",
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


@pytest.mark.parametrize("published", [False, True])
def test_pipeline_vector_and_export_consumers_never_query_retired_rows(monkeypatch, published):
    rasters = importlib.import_module("app4-dashboard.api.routers.map_rasters")
    exports = importlib.import_module("app4-dashboard.api.routers.map_exports")
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [3.1, 42.4]},
        "properties": {"source": "pipeline", "class_name": "car"},
    }]
    result = (features, False) if published else None

    class ArtifactOnlySession:
        def query(self, *_entities):
            pytest.fail("pipeline consumers must not query historical Detection rows")

    session = ArtifactOnlySession()
    for module in (rasters, exports):
        monkeypatch.setattr(module, "pipeline_detection_features", lambda *_args: result)
    expected = features if published else []
    assert rasters._pipeline_features(
        session, _mission("mission-1", 7), "mission-1", None, 10
    ) == (expected, False)
    assert list(exports._pipeline_features(
        session, _mission("mission-1", 7), "mission-1"
    )) == expected


def test_vector_export_all_keeps_pipeline_manual_and_analysis_layers(monkeypatch):
    exports = importlib.import_module("app4-dashboard.api.routers.map_exports")
    calls = []
    monkeypatch.setattr(exports, "_pipeline_features", lambda *_args: iter([{"id": "pipeline"}]))

    def stored(_session, mission_id, sources, runs):
        calls.append((mission_id, sources, runs))
        return iter([{"id": "manual"}, {"id": "ai"}])

    monkeypatch.setattr(exports, "_stored_features", stored)
    result = list(exports._export_features(
        _Session(None), _mission("mission-1", 7), "mission-1", "all", {"run-1"}
    ))
    assert result == [{"id": "pipeline"}, {"id": "manual"}, {"id": "ai"}]
    assert calls == [(7, {"manual", "ai"}, {"run-1"})]


def test_vector_layer_rejects_retired_source_before_storage_access():
    rasters = importlib.import_module("app4-dashboard.api.routers.map_rasters")
    with pytest.raises(HTTPException) as error:
        rasters.vector_layer(
            "mission-1", None, bbox=None, sources="legacy", run_ids=None, limit=10
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    ("vol_id", "layer", "status_code"),
    [("../escape", "ortho", 400), ("mission-1", "missing", 404)],
)
def test_map_layer_validation_preserves_identity_and_layer_guards(vol_id, layer, status_code):
    with pytest.raises(HTTPException) as error:
        map_support.validate_map_layer(vol_id, layer)
    assert error.value.status_code == status_code
