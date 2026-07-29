import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

PROCESSING_DIR = (
    Path(__file__).resolve().parents[1] / "app3-processing"
)
if str(PROCESSING_DIR) not in sys.path:
    sys.path.insert(0, str(PROCESSING_DIR))

analysis_workflow = importlib.import_module("analysis_workflow")
orthomosaic_tiler = importlib.import_module("orthomosaic_tiler")


def test_analysis_json_publication_is_atomic_and_verified(
    tmp_path,
    monkeypatch,
):
    uploads = []
    monkeypatch.setattr(
        analysis_workflow.storage,
        "upload_verified_file",
        lambda path, key: uploads.append((Path(path), key)),
    )
    destination = tmp_path / "results" / "detections.geojson"

    analysis_workflow.AnalysisWorkflow._write_verified_json(
        {"type": "FeatureCollection", "features": []},
        "missions/m-1/analyses/a-1/detections.geojson",
        destination,
    )

    assert destination.read_text(encoding="utf-8") == (
        '{"type":"FeatureCollection","features":[]}'
    )
    assert uploads == [
        (
            destination,
            "missions/m-1/analyses/a-1/detections.geojson",
        )
    ]
    assert not destination.with_suffix(".geojson.tmp").exists()


def test_tiling_plan_has_bounded_overlap_and_private_iteration_state(
    monkeypatch,
):
    monkeypatch.setenv("TILE_OVERLAP", "9999")
    source = SimpleNamespace(
        width=2500,
        height=1700,
        transform=SimpleNamespace(
            to_gdal=lambda: (0, 1, 0, 0, 0, -1)
        ),
        crs=SimpleNamespace(to_string=lambda: "EPSG:2154"),
    )

    plan = orthomosaic_tiler.OrthomosaicTiler._build_plan(
        source,
        1024,
    )
    public = orthomosaic_tiler.OrthomosaicTiler._public_metadata(plan)

    assert plan["overlap"] == 512
    assert plan["total_tiles"] == (
        len(plan["x_starts"]) * len(plan["y_starts"])
    )
    assert public["crs"] == "EPSG:2154"
    assert "x_starts" not in public
    assert "y_starts" not in public
