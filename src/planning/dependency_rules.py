"""The single localized interpretation of raw dependency direction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


RAW_DIRECTION_DESCRIPTION = "blocking_task_id -> blocked_task_id"


class DependencyCompletionSource(str, Enum):
    """Source of the blocker completion used by the dependency rule."""

    PLANNED = "PLANNED"
    ACTUAL = "ACTUAL"
    PRE_QUARTER_DONE = "PRE_QUARTER_DONE"


@dataclass(frozen=True) 
class DependencyTiming:
    completion_sprint: int
    earliest_start_sprint: int
    completion_source: DependencyCompletionSource


def incoming_dependencies(
    task_id: str, dependencies: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return raw rows whose blocked_task_id is the requested task.

    dependency_type is retained as metadata and never changes the direction.
    """

    return [
        dict(row)
        for row in dependencies
        if str(row.get("blocked_task_id", "")).strip() == task_id
    ]


def dependency_is_satisfied(
    blocking_task_id: str,
    sprint_number: int,
    completed_tasks: Mapping[str, int],
    actual_completed_tasks: Mapping[str, int] | None = None,
) -> tuple[bool, int | None, int | None]:
    """Return whether the dependency permits a start in ``sprint_number``."""

    timing = dependency_timing(
        blocking_task_id,
        completed_tasks,
        actual_completed_tasks=actual_completed_tasks,
    )
    if timing is None:
        return False, None, None
    return (
        sprint_number >= timing.earliest_start_sprint,
        timing.completion_sprint,
        timing.earliest_start_sprint,
    )


def dependency_timing(
    blocking_task_id: str,
    planned_completed_tasks: Mapping[str, int],
    *,
    actual_completed_tasks: Mapping[str, int] | None = None,
) -> DependencyTiming | None:
    """Calculate the single authoritative hard-dependency timing.

    A planned completion keeps one full reserve sprint between the blocker and
    the dependent task. An observed completion unlocks the immediately
    following sprint. Sprint 0 represents work completed before the quarter and
    therefore permits a Sprint 1 start.
    """

    actual_completed_tasks = actual_completed_tasks or {}
    if blocking_task_id in actual_completed_tasks:
        completion_sprint = int(actual_completed_tasks[blocking_task_id])
        source = (
            DependencyCompletionSource.PRE_QUARTER_DONE
            if completion_sprint == 0
            else DependencyCompletionSource.ACTUAL
        )
    elif blocking_task_id in planned_completed_tasks:
        completion_sprint = int(planned_completed_tasks[blocking_task_id])
        source = (
            DependencyCompletionSource.PRE_QUARTER_DONE
            if completion_sprint == 0
            else DependencyCompletionSource.PLANNED
        )
    else:
        return None

    if completion_sprint < 0:
        raise ValueError("dependency completion sprint cannot be negative")
    if source == DependencyCompletionSource.PRE_QUARTER_DONE:
        earliest_start = 1
    elif source == DependencyCompletionSource.ACTUAL:
        earliest_start = completion_sprint + 1
    else:
        earliest_start = completion_sprint + 2
    return DependencyTiming(completion_sprint, earliest_start, source)
