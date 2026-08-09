from __future__ import annotations

import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

feature_audit = importlib.import_module("app4-dashboard.api.feature_audit")
map_schemas = importlib.import_module("app4-dashboard.api.map_schemas")
raster_contract = importlib.import_module("app4-dashboard.api.raster_style_contract")


def _feature(**overrides):
    values = {
        "reviewed_at": None,
        "reviewed_by": None,
        "deleted_at": None,
        "deleted_by": None,
        "deletion_reason": None,
        "version": 1,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_feature_lifecycle_is_reversible_versioned_and_idempotent():
    feature = _feature()

    assert feature_audit.feature_lifecycle_change_needed(feature, "review")
    assert feature_audit.apply_feature_lifecycle_action(
        feature,
        action="review",
        actor_subject="operator-1",
        reason="",
    ) == "reviewed"
    assert feature.reviewed_by == "operator-1"
    assert feature.version == 2
    assert not feature_audit.feature_lifecycle_change_needed(feature, "review")

    assert feature_audit.apply_feature_lifecycle_action(
        feature,
        action="delete",
        actor_subject="operator-1",
        reason="false positive",
    ) == "tombstoned"
    assert feature.deletion_reason == "false positive"
    assert feature.version == 3
    assert not feature_audit.feature_lifecycle_change_needed(feature, "unreview")

    assert feature_audit.apply_feature_lifecycle_action(
        feature,
        action="restore",
        actor_subject="operator-2",
        reason="",
    ) == "restored"
    assert feature.deleted_at is None
    assert feature.deleted_by is None
    assert feature.deletion_reason is None
    assert feature.version == 4


def test_bulk_mutation_requires_unique_uuid_identifiers():
    feature_id = str(uuid4())
    request = map_schemas.MapFeatureBulkMutation(
        action="review",
        feature_ids=[feature_id],
        expected_versions={feature_id: 2},
    )

    assert request.feature_ids == [feature_id]
    assert request.expected_versions == {feature_id: 2}
    with pytest.raises(ValidationError):
        map_schemas.MapFeatureBulkMutation(
            action="delete",
            feature_ids=[feature_id, feature_id],
        )


def test_raster_style_contract_accepts_single_band_or_rgb_only():
    rgb = map_schemas.RasterStyleRecipe(
        bands=[3, 2, 1],
        display_ranges=[(0, 255), (10, 240), (20, 230)],
        palette="none",
        stretch="fixed",
    )
    assert rgb.bands == [3, 2, 1]
    assert raster_contract.parse_band_indexes("3,2,1") == [3, 2, 1]
    assert raster_contract.parse_display_ranges(
        "0:255,10:240,auto",
        expected_count=3,
    ) == [[0.0, 255.0], [10.0, 240.0], None]

    with pytest.raises(ValidationError):
        map_schemas.RasterStyleRecipe(bands=[1, 2], palette="none")
    with pytest.raises(ValidationError):
        map_schemas.RasterStyleRecipe(bands=[1, 2, 3], palette="terrain")
    with pytest.raises(ValueError):
        raster_contract.parse_display_ranges("0:1", expected_count=3)
