import importlib
from types import SimpleNamespace


from shared.tenancy import MissionObjectNamespace


analysis_support = importlib.import_module("app4-dashboard.api.analysis_support")
analysis_event = analysis_support.analysis_event


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


def test_analysis_pipeline_event_uses_tenant_identity_and_correlation():
    event = analysis_support.build_analysis_pipeline_event(
        _run(backend="yolo", model_variant="yolo26l"),
        MissionObjectNamespace.create("tenant-a", "mission-001"),
        attempt=2,
    )

    assert event["organization_id"] == "tenant-a"
    assert event["correlation_id"] == "tenant-a:run-001"
    assert event["attempt"] == 2

    cancel = analysis_support.build_analysis_cancel_event(
        "mission-001",
        "run-001",
        "tenant-a",
        2,
    )
    assert cancel["organization_id"] == "tenant-a"
    assert cancel["correlation_id"] == "tenant-a:run-001"
    assert cancel["analysis_run_id"] == "run-001"
