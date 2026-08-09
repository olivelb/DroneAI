import pytest

from shared.quality_profiles import (
    DEFAULT_QUALITY_PROFILE_ID,
    QUALITY_PROFILE_BY_ID,
    profile_overrides,
    quality_profile,
)
from shared.yolo_capabilities import (
    SUPPORTED_AERIAL_CLASSES,
    YOLO_NATIVE_CLASSES,
    YOLO_MODEL_REGISTRY,
    yolo_model_catalog,
    yolo_model_manifest,
)


def test_versioned_quality_profiles_preserve_confirmed_resource_envelopes():
    expected = {
        "fast-v1": ("1600", "2048", "7500", "1500000"),
        "normal-v1": ("2400", "4096", "15000", "3000000"),
        "high-quality-v1": ("4096", "16384", "30000", "5000000"),
    }

    assert DEFAULT_QUALITY_PROFILE_ID == "normal-v1"
    assert set(QUALITY_PROFILE_BY_ID) == set(expected)
    for profile_id, values in expected.items():
        parameters = quality_profile(profile_id).parameters
        assert (
            parameters["feature_max_image_size"],
            parameters["feature_max_num_features"],
            parameters["gs_iterations"],
            parameters["gs_cap_max"],
        ) == values
        assert parameters["gs_production_profile"] == profile_id


def test_profile_overrides_only_records_changed_envelope_values():
    assert profile_overrides(
        "normal-v1",
        {
            "gs_iterations": "20000",
            "gs_cap_max": "3000000",
            "projected_crs": "EPSG:2154",
        },
    ) == {"gs_iterations": "20000"}


def test_yolo_catalog_exposes_all_approved_models_and_native_classes():
    catalog = yolo_model_catalog()

    assert {entry["id"] for entry in catalog} == set(YOLO_MODEL_REGISTRY)
    assert len(YOLO_NATIVE_CLASSES) == 15
    assert set(YOLO_NATIVE_CLASSES) <= SUPPORTED_AERIAL_CLASSES
    assert all(entry["classes"] == list(YOLO_NATIVE_CLASSES) for entry in catalog)
    assert all(entry["available"] is True for entry in catalog)


def test_yolo_manifest_rejects_a_model_disabled_by_deployment():
    assert yolo_model_manifest("yolo26n", "yolo26n")["available"] is True
    with pytest.raises(ValueError, match="not available"):
        yolo_model_manifest("yolo26l", "yolo26n")
