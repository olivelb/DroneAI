from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.validation import (
    validate_aerial_class_names,
    validate_class_names,
    validate_dataset_prefix,
    validate_mission_id,
    validate_pipeline_overrides,
    validate_work_drive,
)
from shared.quality_profiles import (
    DEFAULT_QUALITY_PROFILE_ID,
    QualityProfileId,
)
from shared.sam3_capabilities import validate_sam3_tile_size
from shared.stage_contracts import STAGE_ORDER, StageId, validate_stage_selection
from shared.yolo_capabilities import yolo_model_manifest

YOLOModelVariant = Literal[
    "yolo26l",
    "yolo26m",
    "yolo26s",
    "yolo26n",
    "yolo11l",
    "yolo11m",
    "yolo11s",
    "yolo11n",
]


class MissionParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    vol_id: str
    input_dataset: str
    pipeline: Literal["modern", "legacy"] = "modern"
    quality_profile: QualityProfileId = DEFAULT_QUALITY_PROFILE_ID
    tile_size: int = Field(default=1024, ge=256, le=4096)
    ai_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    ai_backend: Literal["yolo", "sam3"] = "yolo"
    ai_model_variant: YOLOModelVariant = "yolo26l"
    sam_prompt: str = Field(default="car", min_length=1, max_length=128)
    classes: list[str] = Field(default_factory=lambda: ["car"], min_length=1, max_length=20)
    colmap_params: dict[str, Any] = Field(default_factory=dict)
    work_drive: str = ""
    phases: list[StageId] = Field(
        default_factory=lambda: list(STAGE_ORDER),
        min_length=1,
        max_length=len(STAGE_ORDER),
    )

    @field_validator("vol_id")
    @classmethod
    def validate_vol_id(cls, value: str) -> str:
        return validate_mission_id(value)

    @field_validator("input_dataset")
    @classmethod
    def validate_input_dataset(cls, value: str) -> str:
        return validate_dataset_prefix(value)

    @field_validator("classes")
    @classmethod
    def validate_classes(cls, values: list[str]) -> list[str]:
        return validate_class_names(values)

    @model_validator(mode="after")
    def validate_ai_configuration(self) -> MissionParams:
        if self.ai_backend == "yolo":
            validate_aerial_class_names(self.classes)
            yolo_model_manifest(self.ai_model_variant)
        else:
            validate_sam3_tile_size(self.tile_size)
        return self

    @model_validator(mode="after")
    def validate_phase_dag(self) -> MissionParams:
        if len(set(self.phases)) != len(self.phases):
            raise ValueError("Mission phases must not contain duplicates")
        validate_stage_selection(self.phases, {})
        return self

    @field_validator("colmap_params")
    @classmethod
    def validate_colmap_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_pipeline_overrides(value)

    @field_validator("work_drive")
    @classmethod
    def validate_drive(cls, value: str) -> str:
        return validate_work_drive(value)
