"""Pure scheduling policy for resource-aware mission stage runs."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

from shared.stage_contracts import (
    STAGE_DEPENDENCIES,
    ResourceClassId,
    StageId,
)


@dataclass(frozen=True)
class StageAllocation:
    run_id: str
    mission_id: int
    owner_subject: str
    stage: StageId
    resource_class: ResourceClassId


@dataclass(frozen=True)
class StageCandidate(StageAllocation):
    created_at: datetime


@dataclass(frozen=True)
class SchedulingLimits:
    global_active: int = 2
    per_owner_active: int = 1
    per_mission_active: int = 1
    resource_active: dict[ResourceClassId, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        values = (
            self.global_active,
            self.per_owner_active,
            self.per_mission_active,
            *self.resource_active.values(),
        )
        if any(value < 1 for value in values):
            raise ValueError("Scheduling concurrency limits must be positive")


def _ancestors(stage: StageId) -> set[StageId]:
    result: set[StageId] = set()
    pending = list(STAGE_DEPENDENCIES[stage])
    while pending:
        dependency = pending.pop()
        if dependency in result:
            continue
        result.add(dependency)
        pending.extend(STAGE_DEPENDENCIES[dependency])
    return result


def stages_are_independent(left: StageId, right: StageId) -> bool:
    """Return whether two stages can execute concurrently for one mission."""
    return (
        left != right
        and left not in _ancestors(right)
        and right not in _ancestors(left)
    )


def select_stage_candidates(
    candidates: list[StageCandidate],
    active: list[StageAllocation],
    limits: SchedulingLimits,
) -> list[StageCandidate]:
    """Select ready runs fairly without exceeding any concurrency envelope."""
    selected: list[StageCandidate] = []
    owner_counts = Counter(item.owner_subject for item in active)
    mission_allocations: dict[int, list[StageAllocation]] = defaultdict(list)
    for item in active:
        mission_allocations[item.mission_id].append(item)
    resource_counts = Counter(item.resource_class for item in active)
    capacity = limits.global_active - len(active)
    if capacity <= 0:
        return selected

    owner_queues: dict[str, deque[StageCandidate]] = defaultdict(deque)
    for queued_candidate in sorted(
        candidates,
        key=lambda item: (item.created_at, item.run_id),
    ):
        owner_queues[queued_candidate.owner_subject].append(queued_candidate)
    owner_order = deque(owner_queues)

    while owner_order and len(selected) < capacity:
        progress = False
        for _ in range(len(owner_order)):
            owner = owner_order.popleft()
            queue = owner_queues[owner]
            if not queue:
                continue
            candidate: StageCandidate | None = None
            for _ in range(len(queue)):
                current = queue[0]
                same_mission = mission_allocations[current.mission_id]
                resource_limit = limits.resource_active.get(current.resource_class)
                if (
                    owner_counts[owner] < limits.per_owner_active
                    and len(same_mission) < limits.per_mission_active
                    and all(
                        stages_are_independent(current.stage, item.stage)
                        for item in same_mission
                    )
                    and (
                        resource_limit is None
                        or resource_counts[current.resource_class] < resource_limit
                    )
                ):
                    candidate = current
                    break
                queue.rotate(-1)
            if candidate is not None:
                queue.popleft()
                selected.append(candidate)
                allocation = StageAllocation(
                    run_id=candidate.run_id,
                    mission_id=candidate.mission_id,
                    owner_subject=candidate.owner_subject,
                    stage=candidate.stage,
                    resource_class=candidate.resource_class,
                )
                owner_counts[owner] += 1
                mission_allocations[candidate.mission_id].append(allocation)
                resource_counts[candidate.resource_class] += 1
                progress = True
            if queue:
                owner_order.append(owner)
        if not progress:
            break
    return selected
