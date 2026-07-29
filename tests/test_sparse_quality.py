import sys
from pathlib import Path
from types import SimpleNamespace

APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
sys.path.insert(0, str(APP1_ROOT))

from pipeline_support import inspect_sparse_quality  # noqa: E402


class _Track:
    def __init__(self, length):
        self._length = length

    def length(self):
        return self._length


def test_sparse_quality_reports_reprojection_and_track_metrics(
    tmp_path,
    monkeypatch,
):
    points = {
        1: SimpleNamespace(error=0.5, track=_Track(4)),
        2: SimpleNamespace(error=1.5, track=_Track(6)),
        3: SimpleNamespace(error=2.5, track=_Track(8)),
    }

    class Reconstruction:
        def __init__(self, _model_path):
            self.points3D = points

        def reg_image_ids(self):
            return [1, 2, 3, 4]

    monkeypatch.setitem(
        sys.modules,
        "pycolmap",
        SimpleNamespace(Reconstruction=Reconstruction),
    )

    metrics = inspect_sparse_quality(tmp_path)

    assert metrics["registered_images"] == 4
    assert metrics["points3D"] == 3
    assert metrics["mean_reprojection_error_px"] == 1.5
    assert metrics["median_reprojection_error_px"] == 1.5
    assert metrics["median_track_length"] == 6
