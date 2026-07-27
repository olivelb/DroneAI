import importlib
import json

import pytest

from shared.pipeline_params import merge_pipeline_params
from shared.validation import (
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
    raw = json.dumps([{"name": "system"}, {"name": "drive-i"}])
    names = configured_work_drive_names(raw)
    assert names == {"system", "drive-i"}
    assert validate_work_drive("drive-i", configured_names=names) == "drive-i"
    with pytest.raises(ValueError, match="work_drive must be one of"):
        validate_work_drive("drive-j", configured_names=names)


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


def test_pipeline_defaults_select_validated_dronegs_profile():
    params = merge_pipeline_params("modern")

    assert params["gs_backend"] == "dronegs"
    assert params["gs_iterations"] == "15000"
    assert params["gs_cap_max"] == "1500000"
    assert params["gs_data_factor"] == "4"
    assert params["gs_max_width"] == "1600"
    assert params["gs_tile_mode"] == "4"
    assert params["gs_seed"] == "42"
    assert params["gs_raster_profile"] == "fastgs"
    assert params["gs_pruning_policy"] == "lichtfeld-bounds"
    assert params["gs_topology_cooldown"] == "1000"
    assert params["gs_photometric_finish"] == "1000"
    assert params["gs_photometric_mse_percent"] == "100"


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
