import importlib
from types import SimpleNamespace


analysis_event = importlib.import_module(
    "app4-dashboard.api.analysis_support"
).analysis_event


def _run(*, backend: str, model_variant: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        vol_id="mission-001",
        ortho_s3_key="blobs/sha256/ab/cd",
        run_id="run-001",
        classes=["car"],
        confidence=0.3,
        backend=backend,
        model_variant=model_variant,
        prompt="car",
        tile_size=1024,
    )


def test_analysis_event_keeps_yolo_model_provenance():
    event = analysis_event(_run(backend="yolo", model_variant="yolo26l"))

    assert event["ai_model_variant"] == "yolo26l"


def test_analysis_event_omits_irrelevant_yolo_model_for_sam3():
    event = analysis_event(_run(backend="sam3", model_variant=None))

    assert "ai_model_variant" not in event
