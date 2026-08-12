from datetime import UTC, datetime, timedelta

import pytest

from shared.stage_scheduler import (
    SchedulingLimits,
    StageAllocation,
    StageCandidate,
    select_stage_candidates,
    stages_are_independent,
)

NOW = datetime.now(UTC)


def _candidate(
    run_id: str,
    owner: str,
    mission_id: int,
    *,
    age: int = 0,
    stage: str = "reconstruction",
    resource_class: str = "gpu-geometry",
    resource_units: int = 1,
) -> StageCandidate:
    return StageCandidate(
        run_id=run_id,
        mission_id=mission_id,
        owner_subject=owner,
        stage=stage,  # type: ignore[arg-type]
        resource_class=resource_class,  # type: ignore[arg-type]
        resource_units=resource_units,
        created_at=NOW - timedelta(seconds=age),
    )


def test_scheduler_round_robins_owners_and_preserves_age_within_each_owner():
    selected = select_stage_candidates(
        [
            _candidate("a-new", "owner-a", 2),
            _candidate("a-old", "owner-a", 1, age=20),
            _candidate("b-old", "owner-b", 3, age=10),
        ],
        [],
        SchedulingLimits(global_active=3, per_owner_active=2),
    )

    assert [item.run_id for item in selected] == ["a-old", "b-old", "a-new"]


def test_scheduler_enforces_active_owner_and_resource_limits():
    active = [
        StageAllocation(
            run_id="active-a",
            mission_id=10,
            owner_subject="owner-a",
            stage="reconstruction",
            resource_class="gpu-geometry",
        )
    ]
    selected = select_stage_candidates(
        [
            _candidate("blocked-owner", "owner-a", 11, age=30),
            _candidate("blocked-gpu", "owner-b", 12, age=20),
            _candidate(
                "cpu-ready",
                "owner-c",
                13,
                age=10,
                stage="rasterization",
                resource_class="cpu-standard",
            ),
        ],
        active,
        SchedulingLimits(
            global_active=3,
            per_owner_active=1,
            resource_active={"gpu-geometry": 1},
        ),
    )

    assert [item.run_id for item in selected] == ["cpu-ready"]


def test_resource_head_of_line_does_not_block_same_owner_cpu_work():
    active = [
        StageAllocation(
            run_id="active-gpu",
            mission_id=10,
            owner_subject="owner-a",
            stage="reconstruction",
            resource_class="gpu-geometry",
        )
    ]
    selected = select_stage_candidates(
        [
            _candidate("waiting-gpu", "owner-b", 11, age=20),
            _candidate(
                "ready-cpu",
                "owner-b",
                12,
                age=10,
                stage="rasterization",
                resource_class="cpu-standard",
            ),
        ],
        active,
        SchedulingLimits(
            global_active=2,
            resource_active={"gpu-geometry": 1},
        ),
    )

    assert [item.run_id for item in selected] == ["ready-cpu"]


def test_same_mission_parallelism_requires_independent_dag_nodes():
    assert not stages_are_independent("reconstruction", "gaussian_training")
    assert not stages_are_independent("rasterization", "detection")
    active = [
        StageAllocation(
            run_id="active",
            mission_id=42,
            owner_subject="owner-a",
            stage="reconstruction",
            resource_class="gpu-geometry",
        )
    ]

    assert select_stage_candidates(
        [_candidate("dependent", "owner-a", 42, stage="gaussian_training")],
        active,
        SchedulingLimits(global_active=2, per_owner_active=2, per_mission_active=2),
    ) == []


def test_scheduler_caps_physical_units_without_changing_logical_fairness():
    selected = select_stage_candidates(
        [
            _candidate(
                "tenant-a-shards",
                "tenant-a",
                1,
                age=20,
                stage="detection",
                resource_class="gpu-standard",
                resource_units=4,
            ),
            _candidate(
                "tenant-b-shards",
                "tenant-b",
                2,
                age=10,
                stage="detection",
                resource_class="gpu-standard",
                resource_units=4,
            ),
        ],
        [],
        SchedulingLimits(
            global_active=2,
            resource_active={"gpu-standard": 2},
        ),
    )

    assert [(item.run_id, item.resource_units) for item in selected] == [
        ("tenant-a-shards", 2)
    ]
    assert sum(item.resource_units for item in selected) == 2


def test_scheduling_limits_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        SchedulingLimits(global_active=0)
    with pytest.raises(ValueError, match="resource units"):
        StageAllocation(
            run_id="invalid",
            mission_id=1,
            owner_subject="tenant-a",
            stage="detection",
            resource_class="gpu-standard",
            resource_units=0,
        )
