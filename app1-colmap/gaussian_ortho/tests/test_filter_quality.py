"""CPU-only tests for the post-training Gaussian retention gate."""

import pytest

from gaussian_ortho.filter_quality import require_minimum_filter_retention


def test_filter_retention_accepts_healthy_cleanup() -> None:
    assert require_minimum_filter_retention(1_500_000, 1_358_194, 0.80) == pytest.approx(
        1_358_194 / 1_500_000
    )


def test_filter_retention_rejects_sparse_map_product() -> None:
    with pytest.raises(ValueError, match=r"410378/1500000.*27.4%.*80.0%"):
        require_minimum_filter_retention(1_500_000, 410_378, 0.80)


@pytest.mark.parametrize(
    ("initial", "retained", "minimum"),
    [(0, 0, 0.8), (10, 11, 0.8), (10, 5, -0.1), (10, 5, 1.1)],
)
def test_filter_retention_rejects_invalid_contract(
    initial: int,
    retained: int,
    minimum: float,
) -> None:
    with pytest.raises(ValueError):
        require_minimum_filter_retention(initial, retained, minimum)
