"""Story Point capacity distribution for quarterly planning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


EPSILON = 1e-9


@dataclass(frozen=True)
class StoryPointDistribution:
    """Proportional team-capacity load and any SP outside the horizon."""

    capacity_sp_load: dict[int, float]
    remaining_sp: float


def distribute_capacity_sp_load(
    *,
    task_story_points: float,
    active_sprint_hours: Mapping[int, float],
    available_sp_by_sprint: Mapping[int, float],
    last_sprint: int,
) -> StoryPointDistribution:
    """Distribute SP in proportion to hours while respecting sprint caps.

    If no hours are available, use the earliest free sprint capacity as an
    explicit fallback. Capacity load may be fractional; completed SP remains
    a separate all-or-nothing value.
    """

    total_sp = float(task_story_points)
    if not math.isfinite(total_sp) or total_sp < 0:
        raise ValueError("Story Points must be finite and non-negative.")
    if not available_sp_by_sprint:
        return StoryPointDistribution({}, total_sp)

    active = sorted(
        int(sprint)
        for sprint, hours in active_sprint_hours.items()
        if 1 <= int(sprint) <= last_sprint and float(hours) >= 0
    )
    first_sprint = min(active or [int(sprint) for sprint in available_sp_by_sprint])
    candidates = list(range(first_sprint, last_sprint + 1))
    capacities = {
        sprint: max(float(available_sp_by_sprint.get(sprint, 0.0)), 0.0)
        for sprint in candidates
    }
    total_hours = sum(
        max(float(active_sprint_hours.get(sprint, 0.0)), 0.0) for sprint in active
    )
    if total_hours > EPSILON:
        desired = {
            sprint: total_sp
            * max(float(active_sprint_hours.get(sprint, 0.0)), 0.0)
            / total_hours
            for sprint in candidates
        }
    else:
        desired = {
            sprint: (total_sp if sprint == first_sprint else 0.0)
            for sprint in candidates
        }

    loads = {
        sprint: min(desired.get(sprint, 0.0), capacities[sprint])
        for sprint in candidates
    }
    remaining = max(total_sp - sum(loads.values()), 0.0)
    for sprint in candidates:
        if remaining <= EPSILON:
            break
        free = max(capacities[sprint] - loads[sprint], 0.0)
        added = min(remaining, free)
        loads[sprint] += added
        remaining -= added

    if remaining <= EPSILON:
        remaining = 0.0
        residual = total_sp - sum(loads.values())
        used = [sprint for sprint, load in loads.items() if load > EPSILON]
        if residual and used:
            loads[max(used)] += residual

    return StoryPointDistribution(
        {sprint: load for sprint, load in sorted(loads.items()) if load > EPSILON},
        remaining,
    )


def completed_story_points(*, task_story_points: float, is_completed: bool) -> float:
    """Return all task SP on completion and zero for work still in progress."""

    if task_story_points < 0:
        raise ValueError("Story Points cannot be negative.")
    return task_story_points if is_completed else 0.0
