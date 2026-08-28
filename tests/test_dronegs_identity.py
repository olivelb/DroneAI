from __future__ import annotations

import importlib
import sys
from pathlib import Path

from shared.facade_process import (
    FACADE_DRONEGS_PROFILE_ID,
)


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

identity = importlib.import_module("colmap_worker.dronegs_identity")


def test_facade_recipe_versions_preserve_their_capacity_and_initialization() -> None:
    current = identity.expected_profile_identity(FACADE_DRONEGS_PROFILE_ID, {})
    assert current is not None
    assert current["cap_max"] == 6_000_000
    assert current["initial_scale_policy"] == "projected-knn"
    assert current["initial_max_projected_sigma_pixels"] == 8.0
    assert current["capacity_targeted_growth"] is True

    assert identity.expected_profile_identity("DRONEGS_FACADE_HD_V1", {}) is None
    assert identity.expected_profile_identity("DRONEGS_FACADE_HD_V2", {}) is None
