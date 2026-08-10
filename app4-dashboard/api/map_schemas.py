"""Validated HTTP payloads for the geospatial workspace."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.validation import validate_aerial_class_names
from shared.sam3_capabilities import validate_sam3_tile_size
from shared.yolo_capabilities import yolo_model_manifest

from shared.geospatial_workspace import (
    normalize_color,
    normalize_tags,
    validate_geometry,
)


class AnalysisCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    color: str = "#f43f5e"
    tags: list[str] = Field(default_factory=list)
    backend: Literal["yolo", "sam3"] = "yolo"
    model_variant: str | None = Field(default=None, max_length=128)
    prompt: str = Field(default="car", max_length=256)
    classes: list[str] = Field(default_factory=lambda: ["car"])
    confidence: float = Field(default=0.3, ge=0.01, le=0.99)
    tile_size: int = Field(default=1024, ge=256, le=4096)
    persist_results: bool = True

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        return normalize_color(value)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)

    @field_validator("classes")
    @classmethod
    def valid_classes(cls, value: list[str]) -> list[str]:
        classes = normalize_tags(value)
        if not classes:
            raise ValueError("at least one class is required")
        return classes

    @model_validator(mode="after")
    def validate_ai_configuration(self) -> AnalysisCreate:
        if self.backend == "yolo":
            validate_aerial_class_names(self.classes)
            self.model_variant = self.model_variant or "yolo26l"
            yolo_model_manifest(self.model_variant)
        else:
            self.model_variant = None
            validate_sam3_tile_size(self.tile_size)
        return self


class MapFeatureCreate(BaseModel):
    geometry: dict[str, Any]
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    color: str = "#10b981"
    tags: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("geometry")
    @classmethod
    def valid_geometry(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_geometry(value)

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        return normalize_color(value)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)


class MapFeatureUpdate(BaseModel):
    geometry: dict[str, Any] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    color: str | None = None
    tags: list[str] | None = None
    properties: dict[str, Any] | None = None
    version: int = Field(ge=1)

    @field_validator("geometry")
    @classmethod
    def valid_geometry(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return validate_geometry(value) if value is not None else value

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str | None) -> str | None:
        return normalize_color(value) if value is not None else value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value: list[str] | None) -> list[str] | None:
        return normalize_tags(value) if value is not None else value


FeatureBulkAction = Literal["review", "unreview", "delete", "restore"]


class MapFeatureBulkMutation(BaseModel):
    action: FeatureBulkAction
    feature_ids: list[str] = Field(min_length=1, max_length=500)
    expected_versions: dict[str, int] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=2000)

    @field_validator("feature_ids")
    @classmethod
    def valid_feature_ids(cls, values: list[str]) -> list[str]:
        normalized = [str(UUID(value)) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("feature identifiers must be unique")
        return normalized

    @field_validator("expected_versions")
    @classmethod
    def valid_expected_versions(cls, values: dict[str, int]) -> dict[str, int]:
        normalized = {str(UUID(key)): value for key, value in values.items()}
        if any(value < 1 for value in normalized.values()):
            raise ValueError("expected versions must be positive")
        return normalized


RasterPalette = Literal["none", "gray", "depth", "terrain", "viridis"]


class RasterStyleRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bands: list[int] = Field(default_factory=lambda: [1, 2, 3])
    display_ranges: list[tuple[float, float] | None] = Field(default_factory=list)
    palette: RasterPalette = "none"
    opacity: float = Field(default=1.0, ge=0.05, le=1.0)
    stretch: Literal["global-percentile", "fixed"] = "global-percentile"

    @model_validator(mode="after")
    def valid_recipe(self) -> RasterStyleRecipe:
        if len(self.bands) not in {1, 3} or len(set(self.bands)) != len(self.bands):
            raise ValueError("bands must contain one grayscale or three unique RGB indexes")
        if any(index < 1 or index > 256 for index in self.bands):
            raise ValueError("band indexes must be between 1 and 256")
        if self.palette != "none" and len(self.bands) != 1:
            raise ValueError("a color palette requires a single band")
        if self.display_ranges and len(self.display_ranges) != len(self.bands):
            raise ValueError("display ranges must match the selected bands")
        for display_range in self.display_ranges:
            if display_range is not None and display_range[1] <= display_range[0]:
                raise ValueError("every display maximum must exceed its minimum")
        return self


class RasterStyleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    artifact_id: str | None = None
    style: RasterStyleRecipe
    is_default: bool = False

    @field_validator("artifact_id")
    @classmethod
    def valid_artifact_id(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None


class RasterStyleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    style: RasterStyleRecipe | None = None
    is_default: bool | None = None
    version: int = Field(ge=1)
