import importlib
import json

import pytest

from shared.pipeline_params import PARAMETER_METADATA, merge_pipeline_params
from shared.validation import (
    configured_work_drives,
    configured_work_drive_names,
    safe_child_path,
    validate_dataset_prefix,
    validate_mission_id,
    validate_pipeline_overrides,
    validate_work_drive,
)

MissionParams = importlib.import_module("app4-dashboard.api.schemas").MissionParams


@pytest.mark.parametrize("value", ["../tmp", "/tmp/foo", "foo/bar", "foo\\bar", ".", "", "ab"])
def test_rejects_unsafe_mission_ids(value):
    with pytest.raises(ValueError):
        validate_mission_id(value)


def test_accepts_normalized_mission_id():
    assert validate_mission_id("banyuls-2026_001") == "banyuls-2026_001"


@pytest.mark.parametrize("value", ["images/foo", "datasets/../secret", "/datasets/foo", "datasets//foo"])
def test_rejects_unsafe_dataset_prefixes(value):
    with pytest.raises(ValueError):
        validate_dataset_prefix(value)


def test_normalizes_dataset_trailing_slash():
    assert validate_dataset_prefix("datasets/banyuls/") == "datasets/banyuls"


def test_safe_child_path_stays_under_base(tmp_path):
    result = safe_child_path(tmp_path, "mission-001", field_name="vol_id")
    assert result == tmp_path / "mission-001"


def test_work_drive_must_be_configured():
    raw = json.dumps([{"name": "system"}, {"name": "fast-storage"}])
    names = configured_work_drive_names(raw)
    assert names == {"system", "fast-storage"}
    assert validate_work_drive("fast-storage", configured_names=names) == "fast-storage"
    with pytest.raises(ValueError, match="work_drive must be one of"):
        validate_work_drive("missing-storage", configured_names=names)


def test_work_drive_metadata_rejects_unmounted_or_duplicate_entries():
    raw = json.dumps(
        [
            {"name": "local", "label": "Local", "mount": "/work/local"},
            {"name": "local", "label": "Duplicate", "mount": "/work/local"},
            {"name": "escape", "label": "Unsafe", "mount": "/etc"},
            {"name": "../bad", "label": "Unsafe"},
        ]
    )
    assert configured_work_drives(raw) == [
        {"name": "local", "label": "Local", "mount": "/work/local"}
    ]


def test_pipeline_overrides_reject_unknown_and_out_of_range_values():
    with pytest.raises(ValueError, match="unknown COLMAP parameters"):
        validate_pipeline_overrides({"not_a_parameter": 1})
    with pytest.raises(ValueError, match="gs_iterations must be >="):
        validate_pipeline_overrides({"gs_iterations": 1})


def test_pipeline_overrides_validate_non_rtk_alignment_tolerance():
    assert validate_pipeline_overrides({"alignment_max_error": 10.0}) == {
        "alignment_max_error": 10.0
    }
    with pytest.raises(ValueError, match="alignment_max_error must be >="):
        validate_pipeline_overrides({"alignment_max_error": 0})


def test_projected_crs_policy_requires_safe_explicit_epsg():
    assert validate_pipeline_overrides(
        {
            "projected_crs_mode": "custom",
            "projected_crs": "EPSG:3944",
        }
    ) == {
        "projected_crs_mode": "custom",
        "projected_crs": "EPSG:3944",
    }
    with pytest.raises(ValueError, match="EPSG"):
        validate_pipeline_overrides(
            {
                "projected_crs_mode": "custom",
                "projected_crs": "",
            }
        )
    with pytest.raises(ValueError, match="EPSG"):
        validate_pipeline_overrides({"projected_crs": "+proj=lcc"})


def test_fast_alignment_dashboard_parameters_are_validated_and_merged():
    overrides = {
        "feature_max_image_size": "2400",
        "matching_strategy": "gps_pairs",
        "camera_model": "SIMPLE_RADIAL",
        "alignment_engine": "auto",
        "gps_pair_max_neighbors": "48",
        "global_mapper_ba_iterations": "2",
        "global_mapper_ceres_iterations": "80",
        "global_mapper_skip_retriangulation": False,
        "minimum_registration_ratio": "0.99",
        "mapping_timeout_seconds": "2400",
    }

    assert validate_pipeline_overrides(overrides) == overrides
    params = merge_pipeline_params("modern", overrides)
    assert params["feature_max_image_size"] == "2400"
    assert params["global_mapper_ba_iterations"] == "2"
    assert params["global_mapper_ceres_iterations"] == "80"
    assert params["global_mapper_skip_retriangulation"] is False
    assert params["mapping_timeout_seconds"] == "2400"

    mission = MissionParams(
        vol_id="alignment-ui-001",
        input_dataset="datasets/albagnac",
        pipeline="modern",
        colmap_params=overrides,
    )
    assert mission.colmap_params == overrides

    with pytest.raises(ValueError, match="global_mapper_ba_iterations must be <="):
        validate_pipeline_overrides({"global_mapper_ba_iterations": 11})
    with pytest.raises(ValueError, match="global_mapper_skip_retriangulation must be a boolean"):
        validate_pipeline_overrides({"global_mapper_skip_retriangulation": "sometimes"})
    with pytest.raises(ValueError, match="gps_pair_min_neighbors must be <="):
        validate_pipeline_overrides(
            {
                "gps_pair_min_neighbors": 64,
                "gps_pair_max_neighbors": 32,
            }
        )
    with pytest.raises(ValueError, match="rtk_refinement_loss_scale must be >="):
        validate_pipeline_overrides({"rtk_refinement_loss_scale": 0})
    with pytest.raises(ValueError, match="requires camera_model"):
        validate_pipeline_overrides(
            {
                "alignment_engine": "caspar",
                "camera_model": "OPENCV",
            }
        )


def test_pipeline_defaults_select_validated_dronegs_profile():
    params = merge_pipeline_params("modern")

    assert params["projected_crs_mode"] == "auto-local"
    assert params["projected_crs"] == ""
    assert params["feature_type"] == "SIFT"
    assert params["feature_max_image_size"] == "2400"
    assert params["feature_max_num_features"] == "4096"
    assert params["sift_first_octave"] == "-1"
    assert params["guided_matching"] is False
    assert params["matcher_type"] == "STANDARD"
    assert params["matching_strategy"] == "gps_pairs"
    assert params["camera_model"] == "SIMPLE_RADIAL"
    assert params["alignment_engine"] == "auto"
    assert params["global_mapper_ba_iterations"] == "2"
    assert params["global_mapper_ceres_iterations"] == "50"
    assert params["global_mapper_skip_retriangulation"] is False
    assert params["global_mapper_random_seed"] == "42"
    assert params["global_mapper_ba_min_track_length"] == "3"
    assert params["global_mapper_tri_complete_max_reproj_error"] == "15.0"
    assert params["global_mapper_tri_merge_max_reproj_error"] == "15.0"
    assert params["global_mapper_tri_min_angle"] == "1.0"
    assert params["mapping_timeout_seconds"] == "2400"
    assert params["rtk_refinement_loss_scale"] == "7.82"
    assert params["imu_gravity_enabled"] is False
    assert params["mvs_max_image_size"] == "2400"
    assert params["gs_backend"] == "dronegs"
    assert params["gs_iterations"] == "15000"
    assert params["gs_cap_max"] == "1500000"
    assert params["gs_data_factor"] == "4"
    assert params["gs_max_width"] == "1600"
    assert params["gs_ortho_mip_filter_variance"] == "0.03"
    assert params["gs_ortho_mip_filter_compensation"] is True
    assert params["gs_tile_mode"] == "4"
    assert params["gs_seed"] == "42"
    assert params["gs_raster_profile"] == "fastgs"
    assert (
        params["gs_production_profile"]
        == "DRONEGS_PRODUCTION_PROFILE_V1"
    )
    assert params["gs_qualification_policy"] == "DRONEGS_QUALIFICATION_POLICY_V1"
    assert params["gs_pruning_policy"] == "spatial-bounds"
    assert params["gs_topology_cooldown"] == "1000"
    assert params["gs_photometric_finish"] == "1000"
    assert params["gs_photometric_mse_percent"] == "100"
    assert params["gs_checkpoint_every"] == "2000"
    assert params["gs_test_every"] == "8"
    assert params["gs_canary_min_psnr"] == "18.0"
    assert params["gs_canary_min_ssim"] == "0.25"


def test_dashboard_exposes_complete_dronegs_quality_configuration():
    keys = {
        "ortho_mesh_resolution",
        "gs_iterations",
        "gs_data_factor",
        "gs_max_width",
        "gs_ortho_mip_filter_variance",
        "gs_ortho_mip_filter_compensation",
        "gs_tile_mode",
        "gs_cap_max",
        "gs_sh_degree",
        "gs_optimizer_profile",
        "gs_production_profile",
        "gs_qualification_policy",
        "gs_pruning_policy",
        "gs_raster_profile",
        "gs_checkpoint_every",
        "gs_test_every",
        "gs_canary_min_psnr",
        "gs_canary_min_ssim",
        "gs_filter_enabled",
        "gs_filter_sor",
        "gs_filter_cc",
        "gs_filter_z_floater",
    }

    assert keys <= PARAMETER_METADATA.keys()
    assert all(PARAMETER_METADATA[key].get("description") for key in keys)


def test_legacy_thread_default_is_allowed():
    assert validate_pipeline_overrides({"feature_num_threads": "-1"}) == {"feature_num_threads": "-1"}


def test_mission_schema_rejects_extra_fields_and_invalid_bounds(monkeypatch):
    monkeypatch.setenv("WORK_DRIVES", '[{"name":"system"}]')
    valid = {
        "vol_id": "mission-001",
        "input_dataset": "datasets/banyuls",
        "work_drive": "system",
    }
    mission = MissionParams(**valid)
    assert mission.input_dataset == "datasets/banyuls"

    with pytest.raises(ValueError):
        MissionParams(**valid, tile_size=128)
    with pytest.raises(ValueError):
        MissionParams(**valid, epsg="EPSG:4326")
