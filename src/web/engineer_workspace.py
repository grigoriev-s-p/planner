"""Read-only personal projection of an already approved quarter plan."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _profile(engineer: dict[str, Any] | None, engineer_id: str) -> dict[str, Any]:
    source = engineer or {}
    return {
        "engineer_id": engineer_id,
        "name": source.get("name") or engineer_id,
        "roles": list(source.get("roles") or []),
        "grade": source.get("grade"),
        "team_ids": list(source.get("team_ids") or []),
        "capacity_hours": _number(source.get("capacity_hours")),
        "capacity_buckets": deepcopy(source.get("capacity_buckets") or []),
    }


def _active_dependencies(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "blocking_task_id": item.get("blocking_task_id"),
            "blocking_task_title": item.get("blocking_task_title"),
            "dependency_type": item.get("dependency_type"),
            "status_message": item.get("status_message"),
        }
        for item in (task.get("dependencies") or {}).get("incoming", [])
        if not item.get("satisfied") and item.get("state") != "HISTORICAL_DONE"
    ]


def _group_sprint_assignments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_id = str(row.get("task_id"))
        target = grouped.setdefault(task_id, {**deepcopy(row), "roles": [], "source_team_ids": [], "allocated_hours": 0.0})
        target["allocated_hours"] += _number(row.get("allocated_hours"))
        target["cross_team"] = bool(target.get("cross_team")) or bool(row.get("cross_team"))
        if row.get("role") and row["role"] not in target["roles"]:
            target["roles"].append(row["role"])
        if row.get("source_team_id") and row["source_team_id"] not in target["source_team_ids"]:
            target["source_team_ids"].append(row["source_team_id"])
    return sorted(grouped.values(), key=lambda row: str(row.get("task_id")))


def build_engineer_workspace(
    approved: dict[str, Any] | None,
    engineer_id: str,
    *,
    catalog_engineer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return UI-ready data without invoking or duplicating planner logic."""

    if approved is None:
        return {
            "status": "NO_APPROVED_PLAN",
            "approved_plan": False,
            "message": "На текущий квартал пока нет утверждённого плана.",
            "engineer": _profile(catalog_engineer, engineer_id),
            "quarter": {"current_sprint": 1, "sprint_count": 6},
            "summary": {},
            "current_assignments": [],
            "cross_team_assignments": [],
            "sprints": [],
            "tasks": {"current": [], "upcoming": [], "completed": [], "all": []},
        }

    engineers = approved.get("engineers") or []
    approved_engineer = next(
        (item for item in engineers if item.get("engineer_id") == engineer_id), None
    )
    engineer = _profile(approved_engineer or catalog_engineer, engineer_id)
    quarter = (approved.get("plan") or {}).get("quarter") or {}
    current_sprint = int(quarter.get("current_sprint") or 1)
    sprint_count = int(quarter.get("sprint_count") or 6)

    task_rows: list[dict[str, Any]] = []
    per_sprint: dict[int, list[dict[str, Any]]] = defaultdict(list)
    cross_team_rows: list[dict[str, Any]] = []
    for task in approved.get("tasks") or []:
        allocations = [
            row for row in task.get("allocations") or []
            if row.get("engineer_id") == engineer_id and _number(row.get("allocated_hours")) > 0
        ]
        if not allocations:
            continue
        hours_by_sprint: dict[int, float] = defaultdict(float)
        roles: set[str] = set()
        source_teams: set[str] = set()
        task_cross_team = False
        for allocation in allocations:
            sprint = int(allocation.get("sprint") or 0)
            if not 1 <= sprint <= sprint_count:
                continue
            hours = _number(allocation.get("allocated_hours"))
            hours_by_sprint[sprint] += hours
            if allocation.get("role"):
                roles.add(str(allocation["role"]))
            source_team = allocation.get("source_team_id")
            if source_team:
                source_teams.add(str(source_team))
            is_cross_team = bool(allocation.get("cross_team")) or bool(
                source_team and task.get("assigned_team_id")
                and source_team != task.get("assigned_team_id")
            )
            task_cross_team = task_cross_team or is_cross_team
            sprint_row = {
                "task_id": task.get("task_id"),
                "title": task.get("title"),
                "rung": task.get("rung"),
                "status": task.get("status"),
                "status_label": task.get("status_label"),
                "owner_team_id": task.get("assigned_team_id"),
                "source_team_id": source_team,
                "role": allocation.get("role"),
                "sprint": sprint,
                "allocated_hours": hours,
                "cross_team": is_cross_team,
                "continuation": False,
                "completion_sprint": task.get("completion_sprint"),
            }
            per_sprint[sprint].append(sprint_row)
            if is_cross_team:
                cross_team_rows.append(deepcopy(sprint_row))

        planned_sprints = sorted(hours_by_sprint)
        if not planned_sprints:
            continue
        for sprint in planned_sprints[1:]:
            for row in per_sprint[sprint]:
                if row["task_id"] == task.get("task_id"):
                    row["continuation"] = True
        dependencies = _active_dependencies(task)
        task_rows.append({
            "task_id": task.get("task_id"),
            "title": task.get("title"),
            "rung": task.get("rung"),
            "status": task.get("status"),
            "status_label": task.get("status_label"),
            "owner_team_id": task.get("assigned_team_id"),
            "roles": sorted(roles),
            "source_team_ids": sorted(source_teams),
            "allocated_hours": sum(hours_by_sprint.values()),
            "hours_by_sprint": {str(key): value for key, value in sorted(hours_by_sprint.items())},
            "planned_sprints": planned_sprints,
            "completion_sprint": task.get("completion_sprint"),
            "multi_sprint": len(planned_sprints) > 1,
            "cross_team": task_cross_team,
            "dependencies": dependencies,
            "blocked": bool(dependencies),
        })

    capacity = _number(engineer.get("capacity_hours"))
    sprints = []
    for sprint in range(1, sprint_count + 1):
        assignments = _group_sprint_assignments(per_sprint.get(sprint, []))
        allocated = sum(row["allocated_hours"] for row in assignments)
        cross_team_hours = sum(row["allocated_hours"] for row in assignments if row["cross_team"])
        bucket_hours = {
            bucket.get("team_id"): sum(
                row["allocated_hours"] for row in assignments
                if row.get("source_team_id") == bucket.get("team_id")
            )
            for bucket in engineer.get("capacity_buckets") or []
        }
        sprints.append({
            "sprint": sprint,
            "current": sprint == current_sprint,
            "capacity_hours": capacity,
            "allocated_hours": allocated,
            "free_hours": max(capacity - allocated, 0.0),
            "own_team_hours": allocated - cross_team_hours,
            "cross_team_hours": cross_team_hours,
            "capacity_buckets": [
                {**deepcopy(bucket), "allocated_hours": bucket_hours.get(bucket.get("team_id"), 0.0)}
                for bucket in engineer.get("capacity_buckets") or []
            ],
            "assignments": assignments,
        })

    completed = [row for row in task_rows if str(row.get("status")).upper() == "DONE"]
    current = [row for row in task_rows if current_sprint in row["planned_sprints"] and row not in completed]
    upcoming = [
        row for row in task_rows
        if row not in completed and row not in current
        and any(sprint > current_sprint for sprint in row["planned_sprints"])
    ]
    current_load = sprints[current_sprint - 1]
    status = "AVAILABLE" if task_rows else "NO_ASSIGNMENTS"
    return {
        "status": status,
        "approved_plan": True,
        "message": (
            None if task_rows else "В утверждённом плане для вас пока нет назначений."
        ),
        "engineer": engineer,
        "quarter": {
            "current_sprint": current_sprint,
            "sprint_count": sprint_count,
            "plan_version": quarter.get("plan_version"),
        },
        "summary": {
            "unique_task_count": len(task_rows),
            "current_task_count": len(current),
            "planned_hours": current_load["allocated_hours"],
            "capacity_hours": current_load["capacity_hours"],
            "free_hours": current_load["free_hours"],
            "blocked_task_count": sum(1 for row in current if row["blocked"]),
        },
        "current_assignments": current_load["assignments"],
        "cross_team_assignments": sorted(
            cross_team_rows, key=lambda row: (row["sprint"], str(row["task_id"]))
        ),
        "sprints": sprints,
        "tasks": {"current": current, "upcoming": upcoming, "completed": completed, "all": task_rows},
    }
