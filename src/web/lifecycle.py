"""Read models for the current task and sprint lifecycle."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


STATUS_LABELS = {
    "DONE": "Завершена",
    "IN_PROGRESS": "В процессе",
    "PLANNED": "Запланирована",
}


def _completed_through(payload: dict[str, Any]) -> int:
    return int((payload.get("_planning_state") or {}).get("completed_through_sprint") or 0)


def _revision(payload: dict[str, Any]) -> int:
    return int((payload.get("approved_snapshot") or {}).get("revision") or 0)


def _task_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("task_id")): row
        for row in payload.get("tasks") or []
        if row.get("task_id")
    }


def _actual_for_sprint(payload: dict[str, Any], sprint_number: int) -> dict[str, dict[str, Any]]:
    state = payload.get("_planning_state") or {}
    actual = {
        str(row.get("task_id")): deepcopy(row)
        for row in state.get("actual_results") or []
        if row.get("task_id") and int(row.get("sprint_number") or 0) == sprint_number
    }
    for task_id, completed_sprint in (state.get("actual_completed_tasks") or {}).items():
        if int(completed_sprint or 0) == sprint_number:
            actual.setdefault(str(task_id), {
                "task_id": str(task_id),
                "sprint_number": sprint_number,
                "task_completed": True,
                "auto_completed": True,
            })
    return actual


def _outcome(task: dict[str, Any], sprint_number: int, actual: dict[str, Any] | None) -> dict[str, Any]:
    if actual and actual.get("task_completed"):
        status = "COMPLETED"
        label = "Завершено"
    elif actual is not None:
        status = "UNFINISHED"
        label = "Не завершена"
    elif int(task.get("completion_sprint") or 0) > sprint_number:
        status = "CONTINUING"
        label = "Продолжение"
    else:
        status = "UNFINISHED"
        label = "Не завершена"
    return {
        "status": status,
        "label": label,
        "completed": status == "COMPLETED",
        "incomplete_reason": (actual or {}).get("incomplete_reason"),
        "starting_story_points": (actual or {}).get("starting_story_points"),
        "completed_story_points": (actual or {}).get("completed_story_points"),
        "completed_early": bool((actual or {}).get("completed_early")),
        "remaining_story_points": (actual or {}).get("remaining_story_points"),
        "remaining_role_hours": deepcopy((actual or {}).get("remaining_role_hours") or {}),
        "remaining_engineer_work": deepcopy((actual or {}).get("remaining_engineer_work") or []),
        "unavailable_engineer_ids": list((actual or {}).get("unavailable_engineer_ids") or []),
    }


def capture_closed_sprint(
    approved: dict[str, Any],
    sprint_number: int,
    actual_by_task: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Freeze the approved sprint view together with its factual outcomes."""

    sprint = next(
        (
            row for row in (approved.get("plan") or {}).get("sprints") or []
            if int(row.get("sprint_number") or 0) == sprint_number
        ),
        {"sprint_number": sprint_number, "tasks": []},
    )
    tasks = _task_map(approved)
    frozen_tasks = []
    for sprint_task in sprint.get("tasks") or []:
        task_id = str(sprint_task.get("task_id") or "")
        task = tasks.get(task_id)
        if not task:
            continue
        snapshot = deepcopy(task)
        snapshot["sprint_number"] = sprint_number
        snapshot["sprint_capacity_sp_load"] = float(
            sprint_task.get("capacity_sp_load")
            or (task.get("sp_load_by_sprint") or {}).get(str(sprint_number), 0.0)
            or 0.0
        )
        snapshot["sprint_allocated_hours"] = float(
            sprint_task.get("allocated_hours")
            or (task.get("hours_by_sprint") or {}).get(str(sprint_number), 0.0)
            or 0.0
        )
        snapshot["outcome"] = _outcome(
            task, sprint_number, actual_by_task.get(task_id)
        )
        frozen_tasks.append(snapshot)
    frozen_baseline = ((approved.get("_planning_state") or {}).get(
        "sprint_baselines"
    ) or {}).get(str(sprint_number))
    return {
        "sprint_number": sprint_number,
        "planned_revision": (approved.get("approved_snapshot") or {}).get("revision"),
        "sprint_baseline": deepcopy(frozen_baseline) if frozen_baseline else None,
        "tasks": frozen_tasks,
    }


def _reconstruct_closed_sprints(
    snapshots: Iterable[dict[str, Any]], completed_through: int
) -> list[dict[str, Any]]:
    ordered = sorted((deepcopy(row) for row in snapshots), key=lambda row: (_completed_through(row), _revision(row)))
    result = []
    for sprint_number in range(1, completed_through + 1):
        before = max(
            (row for row in ordered if _completed_through(row) < sprint_number),
            key=lambda row: (_completed_through(row), _revision(row)),
            default=None,
        )
        after = min(
            (row for row in ordered if _completed_through(row) >= sprint_number),
            key=lambda row: (_completed_through(row), _revision(row)),
            default=None,
        )
        if before is not None and after is not None:
            result.append(capture_closed_sprint(
                before, sprint_number, _actual_for_sprint(after, sprint_number)
            ))
    return result


def project_lifecycle(
    payload: dict[str, Any], snapshots: Iterable[dict[str, Any]] = ()
) -> dict[str, Any]:
    """Apply one authoritative sprint and task status projection to a dashboard."""

    current = deepcopy(payload)
    state = current.get("_planning_state") or {}
    completed_through = int(state.get("completed_through_sprint") or 0)
    quarter_completed = state.get("quarter_status") == "COMPLETED"
    current_sprint = 6 if quarter_completed else min(completed_through + 1, 6)

    quarter = current.setdefault("plan", {}).setdefault("quarter", {})
    quarter["current_sprint"] = current_sprint
    quarter["current_sprint_source"] = (
        "replanning_state" if completed_through else "default_without_replanning_fact"
    )

    closed_sprints = deepcopy(state.get("closed_sprint_history") or [])
    if completed_through and len(closed_sprints) < completed_through:
        closed_sprints = _reconstruct_closed_sprints(snapshots, completed_through)
    closed_by_number = {
        int(row.get("sprint_number") or 0): row for row in closed_sprints
    }

    current_tasks = _task_map(current)
    completed_tasks: dict[str, int] = {
        str(task_id): int(sprint)
        for task_id, sprint in (state.get("actual_completed_tasks") or {}).items()
    }
    participated_before: set[str] = set()
    for closed in closed_sprints:
        for task in closed.get("tasks") or []:
            task_id = str(task.get("task_id") or "")
            if task_id:
                participated_before.add(task_id)
            if (
                completed_tasks.get(task_id) == int(closed.get("sprint_number") or 0)
                and task_id not in current_tasks
            ):
                completed = deepcopy(task)
                completed.update({
                    "status": "DONE",
                    "status_label": STATUS_LABELS["DONE"],
                    "current_status": "DONE",
                    "current_status_label": STATUS_LABELS["DONE"],
                    "actual_completion_sprint": completed_tasks[task_id],
                    "historical_only": True,
                })
                current.setdefault("tasks", []).append(completed)
                current_tasks[task_id] = completed

    for task in current.get("tasks") or []:
        task_id = str(task.get("task_id") or "")
        source_status = str(task.get("source_status") or "").replace(" ", "").upper()
        planned_sprints = [int(value) for value in task.get("planned_sprints") or []]
        if source_status in {"CANCELLED", "CANCELED"}:
            status = "CANCELLED"
        elif task_id in completed_tasks:
            status = "DONE"
        elif (
            source_status == "INPROGRESS"
            or task_id in participated_before
            or current_sprint in planned_sprints
        ):
            status = "IN_PROGRESS"
        elif planned_sprints and min(planned_sprints) > current_sprint:
            status = "PLANNED"
        else:
            status = str(task.get("status") or "PLANNED")
        task["current_status"] = status
        task["current_status_label"] = STATUS_LABELS.get(
            status, task.get("status_label") or status
        )
        task["actual_completion_sprint"] = completed_tasks.get(task_id)

    projected_tasks = _task_map(current)
    for task in current.get("plan", {}).get("tasks") or []:
        projected = projected_tasks.get(str(task.get("task_id") or ""))
        if projected is not None:
            task["current_status"] = projected.get("current_status")
            task["current_status_label"] = projected.get("current_status_label")
            task["actual_completion_sprint"] = projected.get("actual_completion_sprint")

    for sprint in current.get("plan", {}).get("sprints", []):
        sprint_number = int(sprint.get("sprint_number") or 0)
        if sprint_number <= completed_through:
            presentation = "CLOSED"
            sprint["status"] = "COMPLETED"
            historical = closed_by_number.get(sprint_number)
            if historical is not None:
                sprint["tasks"] = deepcopy(historical.get("tasks") or [])
        elif sprint_number == current_sprint and not quarter_completed:
            presentation = "CURRENT"
            sprint["status"] = "CURRENT"
        else:
            presentation = "FUTURE"
            sprint["status"] = "PLANNED"
        sprint["presentation_state"] = presentation
        if presentation != "CLOSED":
            for task in sprint.get("tasks") or []:
                projected = projected_tasks.get(str(task.get("task_id") or ""))
                if projected is not None:
                    task["current_status"] = projected.get("current_status")
                    task["current_status_label"] = projected.get("current_status_label")

    current["historical_sprints"] = closed_sprints
    return current
