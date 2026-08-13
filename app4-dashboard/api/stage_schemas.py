"""Typed commands for independently rerunnable mission stages."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.stage_contracts import StageId


class StageRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameters: dict[str, Any] = Field(default_factory=dict)
    upstream_artifact_ids: dict[StageId, str] = Field(default_factory=dict)

    @field_validator("upstream_artifact_ids")
    @classmethod
    def validate_artifact_ids(
        cls,
        values: dict[StageId, str],
    ) -> dict[StageId, str]:
        for artifact_id in values.values():
            UUID(artifact_id)
        return values


class ArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    artifact_id: str
    kind: str = Field(min_length=1, max_length=64)
    uri: str = Field(min_length=1, max_length=2048)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_artifact_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("parent_artifact_ids")
    @classmethod
    def validate_parent_ids(cls, values: list[str]) -> list[str]:
        normalized = [str(UUID(value)) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("parent artifact identifiers must be unique")
        return normalized
