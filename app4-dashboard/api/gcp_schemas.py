"""Validated request payloads for the GCP workspace."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class GcpPointUpdate(BaseModel):
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    altitude_m: float | None = None
    role: Literal["adjustment", "checkpoint", "disabled"] | None = None
    horizontal_accuracy_m: float | None = Field(default=None, gt=0)
    vertical_accuracy_m: float | None = Field(default=None, gt=0)
    image_accuracy_px: float | None = Field(default=None, gt=0)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def complete_coordinates(self) -> GcpPointUpdate:
        supplied = (
            self.longitude is not None,
            self.latitude is not None,
            self.altitude_m is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError("longitude, latitude and altitude_m must be updated together")
        return self


class GcpObservationUpdate(BaseModel):
    status: Literal["candidate", "marked", "skipped"]
    pixel_x: float | None = Field(default=None, ge=0)
    pixel_y: float | None = Field(default=None, ge=0)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def marked_pixel_required(self) -> GcpObservationUpdate:
        if self.status == "marked" and (self.pixel_x is None or self.pixel_y is None):
            raise ValueError("marked observations require pixel_x and pixel_y")
        if self.status != "marked" and (self.pixel_x is not None or self.pixel_y is not None):
            raise ValueError("only marked observations may contain pixel coordinates")
        return self
