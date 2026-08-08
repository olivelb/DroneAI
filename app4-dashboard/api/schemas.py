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
    tile_size: int = Field(default=1024, ge=256, le=4096)
    ai_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    ai_backend: Literal["yolo", "sam3"] = "yolo"
    ai_model_variant: YOLOModelVariant = "yolo26l"
    sam_prompt: str = Field(default="car", min_length=1, max_length=128)
    classes: list[str] = Field(default_factory=lambda: ["car"], min_length=1, max_length=20)
    colmap_params: dict[str, Any] = Field(default_factory=dict)
    work_drive: str = ""

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
    def validate_yolo_classes(self) -> MissionParams:
        if self.ai_backend == "yolo":
            validate_aerial_class_names(self.classes)
        return self

    @field_validator("colmap_params")
    @classmethod
    def validate_colmap_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_pipeline_overrides(value)

    @field_validator("work_drive")
    @classmethod
    def validate_drive(cls, value: str) -> str:
        return validate_work_drive(value)
