import sys
from pathlib import Path


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
sys.path.insert(0, str(APP1_ROOT))

from pipeline_support import choose_dronegs_data_factor  # noqa: E402


def test_auto_factor_never_downscales_below_training_width() -> None:
    assert choose_dronegs_data_factor(5472, 3200) == 1
    assert choose_dronegs_data_factor(5472, 2400) == 2
    assert choose_dronegs_data_factor(5472, 1600) == 2
    assert choose_dronegs_data_factor(12000, 1500) == 8


def test_image_count_does_not_affect_spatial_resolution() -> None:
    # Count is intentionally absent from the API: tile mode and Gaussian caps
    # handle memory without throwing source pixels away.
    assert choose_dronegs_data_factor(4000, 3200) == 1
