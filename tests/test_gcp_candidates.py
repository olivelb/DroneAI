import pytest

from shared.gcp_candidates import (
    parse_positioned_images,
    rank_image_candidates,
    rank_new_image_candidates,
)


def test_parses_positioned_images_and_preserves_names_with_spaces():
    images = parse_positioned_images(
        b"DJI image 1.JPG 500000 4800000 125\nDJI_2.JPG 500020 4800010 130\n",
        "EPSG:32631",
    )

    assert images[0].image_name == "DJI image 1.JPG"
    assert images[0].source_x == 500000
    assert -180 <= images[0].longitude <= 180


def test_ranks_nearby_images_and_applies_radius_and_limit():
    images = parse_positioned_images(
        b"near.JPG 500000 4800000 125\nsecond.JPG 500010 4800000 130\nfar.JPG 501000 4800000 130\n",
        "EPSG:32631",
    )

    candidates = rank_image_candidates(
        longitude=images[0].longitude,
        latitude=images[0].latitude,
        images=images,
        projected_crs="EPSG:32631",
        radius_m=100,
        limit=2,
    )

    assert [item.image.image_name for item in candidates] == ["near.JPG", "second.JPG"]
    assert candidates[0].distance_m == pytest.approx(0, abs=0.001)


def test_rejects_duplicate_image_names():
    with pytest.raises(ValueError, match="duplicate"):
        parse_positioned_images(
            b"same.JPG 1 2 3\nsame.JPG 4 5 6\n",
            "EPSG:4326",
        )


def test_refresh_adds_only_missing_candidates_up_to_requested_limit():
    images = parse_positioned_images(
        b"first.JPG 500000 4800000 125\nsecond.JPG 500010 4800000 130\nthird.JPG 500020 4800000 130\n",
        "EPSG:32631",
    )

    candidates = rank_new_image_candidates(
        longitude=images[0].longitude,
        latitude=images[0].latitude,
        images=images,
        projected_crs="EPSG:32631",
        radius_m=100,
        limit=2,
        existing_image_names={"first.JPG"},
    )

    assert [item.image.image_name for item in candidates] == ["second.JPG", "third.JPG"]


def test_refresh_does_nothing_when_only_nearby_photo_was_already_reviewed():
    images = parse_positioned_images(b"first.JPG 1 2 3\n", "EPSG:32631")

    assert rank_new_image_candidates(
        longitude=images[0].longitude,
        latitude=images[0].latitude,
        images=images,
        projected_crs="EPSG:32631",
        radius_m=100,
        limit=1,
        existing_image_names={"first.JPG"},
    ) == ()
