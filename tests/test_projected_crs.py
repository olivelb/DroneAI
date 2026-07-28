import pytest

from shared.projected_crs import (
    france_cc_epsg,
    normalize_epsg,
    select_projected_crs,
    utm_epsg,
)


def test_france_cc_zone_follows_nearest_integer_latitude():
    assert france_cc_epsg(43.0) == "EPSG:3943"
    assert france_cc_epsg(43.7) == "EPSG:3944"
    assert france_cc_epsg(48.86) == "EPSG:3949"


def test_auto_local_uses_cc_zone_for_small_metropolitan_france_mission():
    choice = select_projected_crs(
        [(43.60, 1.43), (43.61, 1.44), (43.59, 1.42)],
    )

    assert choice.crs == "EPSG:3944"
    assert choice.source == "france-cc9"


def test_large_france_footprint_uses_national_lambert93():
    choice = select_projected_crs(
        [(43.5, 2.0), (49.5, 2.0)],
        policy="france-cc",
    )

    assert choice.crs == "EPSG:2154"
    assert choice.source == "france-national"


def test_auto_local_falls_back_to_utm_outside_registered_country_area():
    choice = select_projected_crs([(-33.93, 18.42), (-33.92, 18.43)])

    assert choice.crs == "EPSG:32734"
    assert choice.source == "utm-fallback"


def test_explicit_france_policy_rejects_non_france_footprint():
    with pytest.raises(ValueError, match="metropolitan France"):
        select_projected_crs([(51.50, -0.12)], policy="france-cc")


def test_custom_policy_requires_normalized_epsg():
    choice = select_projected_crs(
        [(50.85, 4.35)],
        policy="custom",
        custom_crs="epsg:3812",
    )

    assert choice.crs == "EPSG:3812"
    assert normalize_epsg(" EPSG:2154 ") == "EPSG:2154"
    with pytest.raises(ValueError, match="EPSG"):
        normalize_epsg("+proj=lcc")


def test_utm_zone_is_clamped_at_antimeridian():
    assert utm_epsg(0.0, 180.0) == "EPSG:32660"
