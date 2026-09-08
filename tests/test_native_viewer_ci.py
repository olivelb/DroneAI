import pytest
from scripts.ci.select_native_viewer import viewer_required
from scripts.ci.check_selected_jobs import validate_results
from tools.enforce_required_checks import required_checks_update


@pytest.mark.parametrize("path", ["native-viewer/src/bundle.cpp", "shared/gstile_streams.py",
    "app1-colmap/gaussian_tiles/format.py", "tests/fixtures/gstile/valid.json",
    ".github/workflows/native-viewer-windows.yml", "scripts/ci/changed_paths.py"])
def test_viewer_inputs_require_windows_qualification(path):
    assert viewer_required([path])


def test_unrelated_paths_skip_windows_but_unknown_events_require_it():
    assert not viewer_required(["README.md", "app4-dashboard/api/http_errors.py"])
    assert viewer_required(None)


def test_selected_windows_build_cannot_be_skipped():
    with pytest.raises(ValueError, match="expected success"):
        validate_results({"changes": {"result": "success", "outputs": {"viewer_required": "true"}},
                          "windows": {"result": "skipped"}}, "changes", {"windows": "viewer_required"})


def test_gate_update_preserves_custom_checks_strictness_and_is_idempotent():
    original = {"strict": True, "checks": [{"context": "Custom review", "app_id": 42}],
                "contexts": ["Custom review", "Legacy context"]}
    update = required_checks_update(original, ["CodeQL gate", "GPU qualification gate"])
    assert update["strict"] is True
    assert original["checks"][0] in update["checks"]
    assert {"context": "Legacy context", "app_id": -1} in update["checks"]
    assert required_checks_update(update, ["CodeQL gate", "GPU qualification gate"]) == update
    assert original["checks"] == [{"context": "Custom review", "app_id": 42}]


def test_any_app_binding_is_preserved_as_github_minus_one_sentinel():
    update = required_checks_update({"strict": False, "checks": [{"context": "External", "app_id": None}]}, [])
    assert update == {"strict": False, "checks": [{"context": "External", "app_id": -1}]}
