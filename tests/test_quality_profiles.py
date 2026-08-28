import pytest

from shared.quality_profiles import (
    DEFAULT_QUALITY_PROFILE_ID,
    QUALITY_PROFILES,
    QUALITY_PROFILE_BY_ID,
    profile_overrides,
    profile_overrides_for_new_mission,
    quality_profile,
    quality_profile_for_new_mission,
    selectable_quality_profiles,
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
        "fast-v2": ("1600", "2048", "7500", "1500000"),
        "normal-v3": ("2400", "4096", "15000", "8000000"),
        "high-quality-v4": ("4096", "16384", "30000", "6000000"),
    }

    assert DEFAULT_QUALITY_PROFILE_ID == "normal-v3"
    assert set(expected) == set(QUALITY_PROFILE_BY_ID)
    for profile_id, values in expected.items():
        parameters = quality_profile(profile_id).parameters
        assert (
            parameters["feature_max_image_size"],
            parameters["feature_max_num_features"],
            parameters["gs_iterations"],
            parameters["gs_cap_max"],
        ) == values
        assert parameters["gs_production_profile"] == profile_id
        candidate = profile_id in {"fast-v2", "normal-v4", "high-quality-v4"}
        assert parameters["gs_initial_scale_policy"] == ("projected-knn" if candidate else "local-knn")
        assert parameters["gs_initial_max_projected_sigma_pixels"] == ("8.0" if candidate else "2.0")
        assert parameters["gs_maximum_scale_growth_factor"] == "54.59815"
        assert parameters["gs_capacity_targeted_growth"] is candidate
    assert quality_profile("fast-v2").parameters["gs_capacity_mode"] == "fixed"
    assert quality_profile("high-quality-v4").parameters["gs_capacity_floor"] == "5000000"
    assert quality_profile("normal-v3").parameters["gs_target_gaussian_spacing_pixels"] == "8.0"
    assert quality_profile("normal-v3").parameters["gs_resident_partitioning"] is True
    assert "high-quality-v3" not in {profile.profile_id for profile in QUALITY_PROFILES}
    assert "high-quality-v2" not in {profile.profile_id for profile in QUALITY_PROFILES}
    assert "normal-v3" in {profile.profile_id for profile in QUALITY_PROFILES}


@pytest.mark.parametrize("profile_id", [
    "fast-v1", "normal-v1", "normal-v2", "normal-v4", "high-quality-v1", "high-quality-v2", "high-quality-v3",
])
def test_retired_quality_profiles_are_rejected(profile_id):
    with pytest.raises(ValueError, match="unknown quality profile"):
        quality_profile(profile_id)


def test_profile_overrides_only_records_changed_envelope_values():
    assert profile_overrides(
        "normal-v3",
        {
            "gs_iterations": "20000",
            "gs_cap_max": "8000000",
            "projected_crs": "EPSG:2154",
        },
    ) == {"gs_iterations": "20000"}


def test_new_mission_override_policy_preserves_qualified_initialization(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED", raising=False)
    assert profile_overrides_for_new_mission(
        "normal-v3",
        {"gs_iterations": "20000"},
    ) == {"gs_iterations": "20000"}
    with pytest.raises(ValueError, match="immutable quality-profile identity"):
        profile_overrides_for_new_mission(
            "normal-v3",
            {"gs_production_profile": "high-quality-v4"},
        )
    with pytest.raises(ValueError, match="qualified profile initialization cannot be overridden"):
        profile_overrides_for_new_mission(
            "normal-v3",
            {"gs_initial_scale_policy": "projected-knn"},
        )



def test_catalog_and_new_missions_use_the_same_qualified_profiles():
    assert tuple(QUALITY_PROFILE_BY_ID) == ("fast-v2", "normal-v3", "high-quality-v4")
    assert selectable_quality_profiles() == QUALITY_PROFILES
    for profile_id in QUALITY_PROFILE_BY_ID:
        assert quality_profile_for_new_mission(profile_id) is quality_profile(profile_id)


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
