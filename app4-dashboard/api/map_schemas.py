"""Validated HTTP payloads for the geospatial workspace."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.validation import validate_aerial_class_names

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
    model_variant: str = Field(default="yolo26l", max_length=128)
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
    def validate_yolo_classes(self):
        if self.backend == "yolo":
            validate_aerial_class_names(self.classes)
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
    def valid_geometry(cls, value: dict[str, Any] | None):
        return validate_geometry(value) if value is not None else value

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str | None):
        return normalize_color(value) if value is not None else value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value: list[str] | None):
        return normalize_tags(value) if value is not None else value
