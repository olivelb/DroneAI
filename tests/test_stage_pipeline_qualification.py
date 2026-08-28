"""CPU qualification scenarios for immutable multi-mission stage chains."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from shared import stage_execution
from shared.database import (
    Dataset,
    DatasetUploadSession,
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionStageRun,
    Organization,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
)
from shared.stage_contracts import (
    STAGE_ARTIFACT_KINDS,
    STAGE_DEPENDENCIES,
    STAGE_ORDER,
    StageId,
)
from shared.stage_execution import StageExecutionContext, StageExecutionResult


RESOURCE_CLASS_BY_STAGE = {
    "reconstruction": "gpu-geometry",
    "gaussian_training": "gpu-high-memory",
    "gaussian_filtering": "gpu-standard",
    "rasterization": "gpu-standard",
    "detection": "gpu-standard",
    "gaussian_viewer": "cpu-high-memory",
}


@pytest.fixture
def qualification_sessions(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Organization.__table__.create(engine)
    OrganizationSaasPolicy.__table__.create(engine)
    OrganizationUsageEvent.__table__.create(engine)
    DatasetUploadSession.__table__.create(engine)
    Dataset.__table__.create(engine)
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
    MissionArtifactParent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope(**_context: object) -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(stage_execution, "get_session", scope)
    return scope


def _seed_mission(scope, *, vol_id: str, owner: str, run_digits: str) -> int:
    with scope() as session:
        mission = Mission(
            vol_id=vol_id,
            owner_subject=owner,
            workspace_prefix=f"missions/{vol_id}",
            status="pending",
            params={"quality_profile": "normal-v3"},
        )
        session.add(mission)
        session.flush()
        mission_id = int(mission.id)
        session.add_all(
            MissionStageRun(
                run_id=digit * 32,
                mission_id=mission_id,
                stage=stage,
                attempt=0,
                status="queued" if index == 0 else "blocked",
                executor="kubernetes-job",
                resource_class=RESOURCE_CLASS_BY_STAGE[stage],
                idempotency_key=digit * 64,
            )
            for index, (stage, digit) in enumerate(
                zip(STAGE_ORDER, run_digits, strict=True)
            )
        )
    return mission_id


def _result(stage: StageId, checksum_digit: str) -> StageExecutionResult:
    return StageExecutionResult(
        kind=STAGE_ARTIFACT_KINDS[stage],
        uri=f"s3://drone-ai/qualification/{checksum_digit}/{stage}.json",
        checksum_sha256=checksum_digit * 64,
        size_bytes=1,
        quality_metrics={"qualified": True},
        provenance={"qualification": "cpu-contract-v1"},
    )


def _execute_chain(run_digits: str) -> list[str]:
    artifact_ids: list[str] = []
    artifact_by_stage: dict[StageId, str] = {}
    for stage, digit in zip(STAGE_ORDER, run_digits, strict=True):
        expected_parent_ids = tuple(
            artifact_by_stage[parent] for parent in STAGE_DEPENDENCIES[stage]
        )

        def handler(
            context: StageExecutionContext,
            _control: Any,
            *,
            current_stage: StageId = stage,
            result_digit: str = digit,
            parent_ids: tuple[str, ...] = expected_parent_ids,
        ) -> StageExecutionResult:
            assert context.stage == current_stage
            assert tuple(item.artifact_id for item in context.inputs) == parent_ids
            return _result(current_stage, result_digit)

        artifact_id = stage_execution.execute_one_shot_stage(
            stage,
            handler,
            run_id=digit * 32,
            heartbeat_interval_seconds=60,
        )
        artifact_ids.append(artifact_id)
        artifact_by_stage[stage] = artifact_id
    return artifact_ids


def test_two_missions_and_repeated_ai_run_keep_exact_artifact_lineage(
    qualification_sessions,
):
    first_mission_id = _seed_mission(
        qualification_sessions,
        vol_id="qualification-a",
        owner="operator-a",
        run_digits="123456",
    )
    second_mission_id = _seed_mission(
        qualification_sessions,
        vol_id="qualification-b",
        owner="operator-b",
        run_digits="789abc",
    )

    first_artifacts = _execute_chain("123456")
    second_artifacts = _execute_chain("789abc")

    retry_run_id = "d" * 32
    with qualification_sessions() as session:
        session.add(
            MissionStageRun(
                run_id=retry_run_id,
                mission_id=first_mission_id,
                stage="detection",
                attempt=1,
                status="queued",
                executor="kubernetes-job",
                resource_class="gpu-standard",
                upstream_artifact_ids=[first_artifacts[3]],
                idempotency_key="d" * 64,
                parameters={"ai": {"backend": "sam3", "sam_prompt": "vehicle"}},
            )
        )

    retry_artifact = stage_execution.execute_one_shot_stage(
        "detection",
        lambda context, _control: (
            _result("detection", "c")
            if [item.artifact_id for item in context.inputs] == first_artifacts[3:4]
            else pytest.fail("retry did not select the exact raster parent")
        ),
        run_id=retry_run_id,
        heartbeat_interval_seconds=60,
    )

    with qualification_sessions() as session:
        artifacts = session.query(MissionArtifact).all()
        edges = session.query(MissionArtifactParent).all()
        runs = session.query(MissionStageRun).all()
        artifacts_by_public_id = {item.artifact_id: item for item in artifacts}

        assert len(artifacts) == 13
        assert len(edges) == 11
        assert all(run.status == "succeeded" for run in runs)
        assert {
            artifacts_by_public_id[item].mission_id for item in first_artifacts
        } == {first_mission_id}
        assert {
            artifacts_by_public_id[item].mission_id for item in second_artifacts
        } == {second_mission_id}
        assert retry_artifact != first_artifacts[4]
        retry_parent_ids = {
            edge.parent.artifact_id
            for edge in artifacts_by_public_id[retry_artifact].parent_edges
        }
        assert retry_parent_ids == {first_artifacts[3]}
        assert artifacts_by_public_id[first_artifacts[5]].checksum_sha256 == "6" * 64
