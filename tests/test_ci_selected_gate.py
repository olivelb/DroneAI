import pytest

from scripts.ci.check_selected_jobs import validate_results


def needs(required: str, result: str, selector: str = "success") -> dict:
    return {
        "changes": {"result": selector, "outputs": {"gpu_required": required}},
        "gpu": {"result": result},
    }


@pytest.mark.parametrize("result", ["skipped", "cancelled", "failure", ""])
def test_selected_job_must_succeed(result: str) -> None:
    with pytest.raises(ValueError):
        validate_results(needs("true", result), "changes", {"gpu": "gpu_required"})


@pytest.mark.parametrize("selector", ["skipped", "failure", "cancelled", ""])
def test_selector_itself_must_succeed(selector: str) -> None:
    with pytest.raises(ValueError):
        validate_results(needs("false", "skipped", selector), "changes", {"gpu": "gpu_required"})


@pytest.mark.parametrize("output", ["", "maybe", "True"])
def test_missing_or_invalid_selection_is_not_an_exemption(output: str) -> None:
    with pytest.raises(ValueError):
        validate_results(needs(output, "skipped"), "changes", {"gpu": "gpu_required"})


def test_explicit_skip_and_success_are_valid() -> None:
    validate_results(needs("false", "skipped"), "changes", {"gpu": "gpu_required"})
    validate_results(needs("true", "success"), "changes", {"gpu": "gpu_required"})


def test_missing_job_fails_closed() -> None:
    with pytest.raises(ValueError):
        validate_results({"changes": {"result": "success", "outputs": {"gpu_required": "true"}}},
                         "changes", {"gpu": "gpu_required"})
