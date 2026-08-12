from __future__ import annotations

import pytest

from shared.tenancy import (
    LEGACY_ORGANIZATION_ID,
    dataset_prefix,
    mission_prefix,
    validate_organization_id,
    current_organization_id,
    organization_context,
)


def test_organization_scoped_storage_uses_the_v2_layout() -> None:
    assert dataset_prefix("acme-survey", "flight-1") == (
        "organizations/acme-survey/datasets/flight-1"
    )
    assert mission_prefix("acme-survey", "mission-1") == (
        "organizations/acme-survey/missions/mission-1"
    )


def test_legacy_resources_keep_the_v1_layout_during_migration() -> None:
    assert dataset_prefix(LEGACY_ORGANIZATION_ID, "flight-1") == (
        "datasets/flight-1"
    )
    assert mission_prefix(LEGACY_ORGANIZATION_ID, "mission-1") == (
        "missions/mission-1"
    )


@pytest.mark.parametrize("value", ["../escape", "Uppercase", "two.parts", ""])
def test_organization_identifiers_are_safe_storage_segments(value: str) -> None:
    with pytest.raises(ValueError):
        validate_organization_id(value)


def test_organization_context_is_nested_and_resets() -> None:
    assert current_organization_id() is None
    with organization_context("acme-survey"):
        assert current_organization_id() == "acme-survey"
        with organization_context("north-region"):
            assert current_organization_id() == "north-region"
        assert current_organization_id() == "acme-survey"
    assert current_organization_id() is None
