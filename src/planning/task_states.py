"""Shared predicates for source task lifecycle and planning participation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DONE_SOURCE_STATUS = "done"
ACTIVE_SOURCE_STATUSES = frozenset({"todo", "inprogress"})


def is_historical_done(task: Mapping[str, Any]) -> bool:
    """Done is a factual completion before the current planning horizon."""

    if str(task.get("is_placeholder") or "").strip().casefold() in {"1", "true", "yes"}:
        return False
    return any(
        str(task.get(field) or "").strip().casefold()
        in {DONE_SOURCE_STATUS, "completed"}
        for field in ("source_status", "status")
    )


def is_active_planning_task(task: Mapping[str, Any]) -> bool:
    """Only ToDo and InProgress source tasks are new quarter work."""

    statuses = {
        str(task.get(field) or "").strip().casefold()
        for field in ("source_status", "status")
    }
    return not is_historical_done(task) and bool(statuses & ACTIVE_SOURCE_STATUSES)


def dependency_is_satisfied(
    blocking_task_id: str,
    *,
    known_tasks: Mapping[str, Mapping[str, Any]],
    active_scenario_task_ids: set[str],
) -> bool:
    """A dependency is satisfied by historical Done or planned active context.

    An unknown blocker and a known active blocker outside the scenario both
    remain unsatisfied and are reported differently by the caller.
    """

    blocker = known_tasks.get(blocking_task_id)
    return bool(
        blocker is not None
        and (
            is_historical_done(blocker)
            or blocking_task_id in active_scenario_task_ids
        )
    )
