"""The single localized interpretation of raw dependency direction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


RAW_DIRECTION_DESCRIPTION = "blocking_task_id -> blocked_task_id"


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
) -> tuple[bool, int | None, int | None]:
    """A task completed in Sprint N unlocks its dependent task in Sprint N+1."""

    completed_sprint = completed_tasks.get(blocking_task_id)
    if completed_sprint is None:
        return False, None, None
    completed_sprint = int(completed_sprint)
    earliest_start = completed_sprint + 1
    return sprint_number >= earliest_start, completed_sprint, earliest_start
