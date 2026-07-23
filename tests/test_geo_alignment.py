import numpy as np
import pytest

from shared.geo_alignment import alignment_from_named_centers, estimate_sim3


def test_estimate_sim3_recovers_known_transform():
    source = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 2.0, 0.0],
            [0.0, 0.0, 3.0],
        ]
    )
    rotation = np.array(
        [
            [0.0, -1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    scale = 2.5
    translation = np.array([700_000.0, 4_800_000.0, 120.0])
    target = (scale * (rotation @ source.T)).T + translation

    transform = estimate_sim3(source, target)

    assert transform["scale"] == pytest.approx(scale)
    assert np.asarray(transform["R"]) == pytest.approx(rotation)
    assert np.asarray(transform["t"]) == pytest.approx(translation)
    assert transform["fit"]["rmse"] < 1e-8


def test_named_centers_match_by_filename_not_mapping_order():
    source = {
        "b.jpg": [1.0, 0.0, 0.0],
        "a.jpg": [0.0, 0.0, 0.0],
        "c.jpg": [0.0, 1.0, 0.0],
    }
    target = {
        "c.jpg": [10.0, 22.0, 30.0],
        "a.jpg": [10.0, 20.0, 30.0],
        "b.jpg": [12.0, 20.0, 30.0],
    }

    transform = alignment_from_named_centers(source, target)

    assert transform["scale"] == pytest.approx(2.0)
    assert transform["fit"]["common_images"] == ["a.jpg", "b.jpg", "c.jpg"]


@pytest.mark.parametrize(
    ("source", "target", "message"),
    [
        ([[0, 0, 0], [1, 0, 0]], [[0, 0, 0], [1, 0, 0]], "three"),
        (
            [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
            [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
            "degenerate",
        ),
    ],
)
def test_estimate_sim3_rejects_invalid_geometry(source, target, message):
    with pytest.raises(ValueError, match=message):
        estimate_sim3(source, target)
