"""Typed event routing for the processing worker."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, Unpack, cast


JsonObject = dict[str, Any]


class CancellationRegistry(Protocol):
    def clear(
        self,
        vol_id: str,
        run_id: str | None = None,
        attempt: int = 0,
    ) -> None: ...

    def is_cancelled(
        self,
        vol_id: str,
        run_id: str | None = None,
        attempt: int = 0,
    ) -> bool: ...


class OrthomosaicSliceOptions(TypedDict, total=False):
    """Optional controls accepted by the orthomosaic tiling boundary."""

    tile_size: int
    classes: list[str] | None
    ai_confidence: float
    ai_backend: str
    ai_model_variant: str
    sam_prompt: str
    analysis_run_id: str | None
    analysis_attempt: int


class OrthomosaicTiler(Protocol):
    def slice(
        self,
        ortho_s3_key: str,
        vol_id: str,
        **options: Unpack[OrthomosaicSliceOptions],
    ) -> None: ...


class AnalysisWorkflow(Protocol):
    def process_detection(self, data: JsonObject) -> None: ...

    def recover(self) -> None: ...


class LegacyWorkflow(Protocol):
    def process_detection(self, data: JsonObject) -> None: ...

    def recover(self) -> None: ...


class ProcessingDispatcher:
    """Route validated Kafka events to one focused workflow."""

    def __init__(
        self,
        *,
        orthomosaic_topic: str,
        cancellation_registry: CancellationRegistry,
        tiler: OrthomosaicTiler,
        analysis_workflow: AnalysisWorkflow,
        legacy_workflow: LegacyWorkflow,
    ) -> None:
        self.orthomosaic_topic = orthomosaic_topic
        self.cancellation_registry = cancellation_registry
        self.tiler = tiler
        self.analysis_workflow = analysis_workflow
        self.legacy_workflow = legacy_workflow

    def _process_orthomosaic(self, data: JsonObject) -> None:
        vol_id = cast(str, data["vol_id"])
        analysis_run_id = cast(str | None, data.get("analysis_run_id"))
        analysis_attempt = int(data.get("attempt", 0))
        self.cancellation_registry.clear(
            vol_id,
            analysis_run_id,
            analysis_attempt,
        )
        self.tiler.slice(
            str(data.get("ortho_s3_key") or data.get("ortho_path") or ""),
            vol_id,
            tile_size=int(data.get("tile_size", 1024)),
            classes=cast(list[str], data.get("classes") or ["car"]),
            ai_confidence=float(data.get("ai_confidence", 0.3)),
            ai_backend=str(data.get("ai_backend", "yolo")),
            ai_model_variant=str(data.get("ai_model_variant", "yolo26l")),
            sam_prompt=str(data.get("sam_prompt", "car")),
            analysis_run_id=analysis_run_id,
            analysis_attempt=analysis_attempt,
        )

    def process_event(self, data: JsonObject, topic: str) -> None:
        if topic == self.orthomosaic_topic:
            self._process_orthomosaic(data)
            return
        vol_id = cast(str, data["vol_id"])
        analysis_run_id = cast(str | None, data.get("analysis_run_id"))
        if self.cancellation_registry.is_cancelled(
            vol_id,
            analysis_run_id,
            int(data.get("attempt", 0)),
        ):
            return
        if analysis_run_id:
            self.analysis_workflow.process_detection(data)
            return
        self.legacy_workflow.process_detection(data)

    def recover(self) -> None:
        self.legacy_workflow.recover()
        self.analysis_workflow.recover()
