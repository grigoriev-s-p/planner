"""Read projections for stable Team and Engineer entity pages."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable


def _revision(snapshot: dict[str, Any]) -> int:
    return int((snapshot.get("approved_snapshot") or {}).get("revision") or 0)


def _completed_through(snapshot: dict[str, Any]) -> int:
    return int((snapshot.get("_planning_state") or {}).get("completed_through_sprint") or 0)


def _task_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(task.get("task_id")): task
        for task in snapshot.get("tasks", [])
        if task.get("task_id")
    }


def _allocation_rows(task: dict[str, Any], sprint: int | None = None) -> list[dict[str, Any]]:
    rows = task.get("allocations") or []
    if sprint is None:
        return [dict(row) for row in rows]
    return [dict(row) for row in rows if int(row.get("sprint") or 0) == sprint]


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "initiative_id": task.get("initiative_id"),
        "status": task.get("status"),
        "original_story_points": task.get("story_points"),
        "remaining_story_points": task.get("remaining_story_points", task.get("story_points")),
        "remaining_role_hours": deepcopy(task.get("role_requirements") or {}),
        "assigned_team_id": task.get("assigned_team_id"),
        "original_team_id": task.get("original_team_id"),
        "planned_sprints": list(task.get("planned_sprints") or []),
        "completion_sprint": task.get("completion_sprint"),
    }


def _snapshot_pair(
    snapshots: list[dict[str, Any]], sprint: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    before = [item for item in snapshots if _completed_through(item) < sprint]
    after = [item for item in snapshots if _completed_through(item) >= sprint]
    return (
        max(before, key=lambda item: (_completed_through(item), _revision(item)), default=None),
        min(after, key=lambda item: (_completed_through(item), _revision(item)), default=None),
    )


def _actual_for_sprint(snapshot: dict[str, Any] | None, sprint: int) -> dict[str, dict[str, Any]]:
    if snapshot is None:
        return {}
    state = snapshot.get("_planning_state") or {}
    result = {
        str(row.get("task_id")): dict(row)
        for row in state.get("actual_results") or []
        if row.get("task_id") and int(row.get("sprint_number") or 0) == sprint
    }
    for task_id, completion_sprint in (state.get("actual_completed_tasks") or {}).items():
        if int(completion_sprint or 0) == sprint:
            result.setdefault(str(task_id), {
                "task_id": str(task_id),
                "sprint_number": sprint,
                "task_completed": True,
                "auto_completed": True,
            })
    return result


def _outcome(task_id: str, actual: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    row = actual.get(task_id)
    if row is None:
        return None
    return {
        "task_id": task_id,
        "completed": bool(row.get("task_completed")),
        "status": "COMPLETED" if row.get("task_completed") else "UNFINISHED",
        "incomplete_reason": row.get("incomplete_reason"),
        "remaining_role_hours": deepcopy(row.get("remaining_role_hours") or {}),
        "actual_hours": deepcopy(row.get("actual_hours")) if row.get("actual_hours") is not None else None,
    }


def _sorted_snapshots(snapshots: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((deepcopy(item) for item in snapshots), key=_revision)


def build_team_projection(
    dashboard: dict[str, Any], snapshots: Iterable[dict[str, Any]], team_id: str
) -> dict[str, Any] | None:
    team = next((row for row in dashboard.get("teams", []) if row.get("team_id") == team_id), None)
    star_team = next((row for row in (dashboard.get("star_map") or {}).get("teams", []) if row.get("team_id") == team_id), None)
    if team is None and star_team is None:
        return None
    current_sprint = int((dashboard.get("plan") or {}).get("quarter", {}).get("current_sprint") or 1)
    approved_revision = int((dashboard.get("approved_snapshot") or {}).get("revision") or 0) or None
    is_approved = bool((dashboard.get("draft") or {}).get("approved")) or approved_revision is not None
    tasks = dashboard.get("tasks") or []

    owner_current = []
    provided_current: dict[str, dict[str, Any]] = {}
    received_current: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task.get("task_id"))
        allocations = _allocation_rows(task, current_sprint)
        if task.get("assigned_team_id") == team_id and current_sprint in (task.get("planned_sprints") or []):
            item = _task_summary(task)
            item["participation_type"] = "OWNER"
            item["sprint"] = current_sprint
            owner_current.append(item)
        for row in allocations:
            source = row.get("source_team_id")
            owner = row.get("assigned_task_team_id") or task.get("assigned_team_id")
            if source == team_id and owner and owner != team_id:
                item = provided_current.setdefault(task_id, {
                    **_task_summary(task), "participation_type": "CROSS_TEAM_PROVIDED",
                    "owner_team_id": owner, "planned_hours": 0.0, "roles": set(),
                })
                item["planned_hours"] += float(row.get("allocated_hours") or 0.0)
                if row.get("role"):
                    item["roles"].add(str(row["role"]))
            if owner == team_id and source and source != team_id:
                item = received_current.setdefault(task_id, {
                    **_task_summary(task), "participation_type": "CROSS_TEAM_RECEIVED",
                    "source_team_ids": set(), "planned_hours": 0.0,
                })
                item["planned_hours"] += float(row.get("allocated_hours") or 0.0)
                item["source_team_ids"].add(str(source))

    future_sprints = []
    for sprint in range(current_sprint + 1, 7):
        sprint_tasks = [
            {**_task_summary(task), "sprint_sp": float((task.get("sp_load_by_sprint") or {}).get(str(sprint), 0.0) or 0.0)}
            for task in tasks
            if task.get("assigned_team_id") == team_id and sprint in (task.get("planned_sprints") or [])
        ]
        future_sprints.append({
            "sprint": sprint,
            "task_count": len(sprint_tasks),
            "capacity_sp_load": sum(item["sprint_sp"] for item in sprint_tasks),
            "tasks": sprint_tasks,
        })

    engineers = dashboard.get("engineers") or []
    members = []
    for engineer in engineers:
        bucket = next((row for row in engineer.get("capacity_buckets") or [] if row.get("team_id") == team_id), None)
        if bucket is None:
            continue
        allocations = [
            row for row in engineer.get("allocations") or []
            if int(row.get("sprint") or 0) == current_sprint and row.get("source_team_id") == team_id
        ]
        members.append({
            "engineer_id": engineer.get("engineer_id"),
            "name": engineer.get("name"),
            "roles": list(engineer.get("roles") or []),
            "grade": engineer.get("grade"),
            "skills": list(engineer.get("skills") or []),
            "capacity_rate": bucket.get("capacity_rate"),
            "capacity_hours": bucket.get("capacity_hours"),
            "planned_hours": sum(float(row.get("allocated_hours") or 0.0) for row in allocations),
            "cross_team_planned_hours": sum(
                float(row.get("allocated_hours") or 0.0)
                for row in allocations if row.get("assigned_task_team_id") != team_id
            ),
        })

    tables = (dashboard.get("_planning_state") or {}).get("tables") or {}
    capacity_by_role: dict[str, float] = defaultdict(float)
    seen_role_buckets: set[tuple[str, str]] = set()
    for row in tables.get("engineers", []):
        if str(row.get("team_id") or "") != team_id or not row.get("engineer_id") or not row.get("role"):
            continue
        key = (str(row["engineer_id"]), str(row["role"]))
        if key in seen_role_buckets:
            continue
        seen_role_buckets.add(key)
        capacity_by_role[str(row["role"])] += 80.0 * float(row.get("capacity_rate") or 0.0)
    used_by_role: dict[str, float] = defaultdict(float)
    for task in tasks:
        for row in _allocation_rows(task, current_sprint):
            if row.get("source_team_id") == team_id and row.get("role"):
                used_by_role[str(row["role"])] += float(row.get("allocated_hours") or 0.0)

    competencies = deepcopy((star_team or {}).get("competencies") or [])
    for item in provided_current.values():
        item["roles"] = sorted(item["roles"])
    for item in received_current.values():
        item["source_team_ids"] = sorted(item["source_team_ids"])
    sp = (team or {}).get("sp_by_sprint", {}).get(str(current_sprint), {})
    return {
        **deepcopy(team or {}),
        "identity": {
            "team_id": team_id,
            "name": (team or {}).get("name") or (star_team or {}).get("team_name") or team_id,
        },
        "current_sprint": current_sprint,
        "approved_revision": approved_revision,
        "current_tasks": sorted([*owner_current, *provided_current.values()], key=lambda item: str(item.get("task_id"))),
        "future_sprints": future_sprints,
        "members": sorted(members, key=lambda item: str(item.get("name") or item.get("engineer_id"))),
        "capacity": {
            "sp": {"used": float(sp.get("used") or 0.0), "capacity": float(sp.get("capacity") or 0.0)},
            "roles": [
                {"role": role, "used_hours": used_by_role.get(role, 0.0), "capacity_hours": capacity}
                for role, capacity in sorted(capacity_by_role.items())
            ],
        },
        "cross_team": {
            "provided": sorted(provided_current.values(), key=lambda item: str(item.get("task_id"))),
            "received": sorted(received_current.values(), key=lambda item: str(item.get("task_id"))),
        },
        "competencies": competencies,
        "star_map_summary": deepcopy((star_team or {}).get("summary")),
        "source": "LATEST_APPROVED_PLAN" if is_approved else "WORKING_CATALOG",
    }


def build_team_history(
    snapshots: Iterable[dict[str, Any]], team_id: str
) -> dict[str, Any]:
    ordered = _sorted_snapshots(snapshots)
    completed = max((_completed_through(item) for item in ordered), default=0)
    closed_sprints = []
    for sprint in range(1, completed + 1):
        before, after = _snapshot_pair(ordered, sprint)
        if before is None:
            continue
        before_tasks, after_tasks = _task_map(before), _task_map(after or {})
        actual = _actual_for_sprint(after, sprint)
        owner_tasks = []
        provided, received = [], []
        for task_id, task in before_tasks.items():
            allocations = _allocation_rows(task, sprint)
            if (
                task.get("assigned_team_id") == team_id
                and sprint in (task.get("planned_sprints") or [])
            ):
                owner_tasks.append({**_task_summary(task), "outcome": _outcome(task_id, actual)})
            provided_hours = sum(
                float(row.get("allocated_hours") or 0.0)
                for row in allocations
                if row.get("source_team_id") == team_id
                and (row.get("assigned_task_team_id") or task.get("assigned_team_id")) != team_id
            )
            received_hours = sum(
                float(row.get("allocated_hours") or 0.0)
                for row in allocations
                if task.get("assigned_team_id") == team_id
                and row.get("source_team_id") not in {None, team_id}
            )
            if provided_hours:
                provided.append({**_task_summary(task), "planned_hours": provided_hours})
            if received_hours:
                received.append({**_task_summary(task), "planned_hours": received_hours})

        ownership_changes = []
        for task_id in sorted(set(before_tasks) & set(after_tasks)):
            old_owner = before_tasks[task_id].get("assigned_team_id")
            new_owner = after_tasks[task_id].get("assigned_team_id")
            if old_owner != new_owner and team_id in {old_owner, new_owner}:
                ownership_changes.append({
                    "task_id": task_id,
                    "title": before_tasks[task_id].get("title") or after_tasks[task_id].get("title"),
                    "from_team_id": old_owner,
                    "to_team_id": new_owner,
                    "direction": "TRANSFERRED_OUT" if old_owner == team_id else "RECEIVED",
                })
        diff_rows = [
            row for row in ((after or {}).get("replanning") or {}).get("diff", {}).get("changed_tasks", [])
            if row.get("task_id") in before_tasks or row.get("task_id") in after_tasks
            if team_id in {
                (before_tasks.get(str(row.get("task_id"))) or {}).get("assigned_team_id"),
                (after_tasks.get(str(row.get("task_id"))) or {}).get("assigned_team_id"),
            }
        ]
        closed_sprints.append({
            "sprint": sprint,
            "planned_revision": _revision(before) or None,
            "resulting_revision": _revision(after or {}) or None,
            "owner_tasks": owner_tasks,
            "completed_tasks": [item for item in owner_tasks if (item.get("outcome") or {}).get("completed")],
            "unfinished_tasks": [item for item in owner_tasks if (item.get("outcome") or {}).get("status") == "UNFINISHED"],
            "cross_team_provided": provided,
            "cross_team_received": received,
            "ownership_changes": ownership_changes,
            "replanning_changes": deepcopy(diff_rows),
        })
    return {"team_id": team_id, "closed_sprints": closed_sprints}


def build_engineer_projection(
    dashboard: dict[str, Any], snapshots: Iterable[dict[str, Any]], engineer_id: str
) -> dict[str, Any] | None:
    engineer = next((row for row in dashboard.get("engineers", []) if row.get("engineer_id") == engineer_id), None)
    star = next((row for row in (dashboard.get("star_map") or {}).get("engineers", []) if row.get("engineer_id") == engineer_id), None)
    if engineer is None and star is None:
        return None
    current_sprint = int((dashboard.get("plan") or {}).get("quarter", {}).get("current_sprint") or 1)
    approved_revision = int((dashboard.get("approved_snapshot") or {}).get("revision") or 0) or None
    is_approved = bool((dashboard.get("draft") or {}).get("approved")) or approved_revision is not None
    task_by_id = {str(task.get("task_id")): task for task in dashboard.get("tasks") or [] if task.get("task_id")}
    current_allocations, future_allocations = [], []
    for row in (engineer or {}).get("allocations") or []:
        task = task_by_id.get(str(row.get("task_id")), {})
        owner_team_id = row.get("assigned_task_team_id") or task.get("assigned_team_id")
        source_team_id = row.get("source_team_id")
        item = {
            **dict(row),
            "task_title": task.get("title"),
            "owner_team_id": owner_team_id,
            "participation_type": (
                "CROSS_TEAM"
                if source_team_id and owner_team_id and source_team_id != owner_team_id
                else "OWN_TEAM"
            ),
            "hours_semantics": "PLANNED",
        }
        if int(row.get("sprint") or 0) == current_sprint:
            current_allocations.append(item)
        elif int(row.get("sprint") or 0) > current_sprint:
            future_allocations.append(item)
    buckets = {
        str(item.get("team_id")): dict(item)
        for item in (engineer or {}).get("capacity_buckets") or []
        if item.get("team_id")
    }
    star_memberships = {
        str(item.get("team_id")): dict(item)
        for item in (star or {}).get("memberships") or []
        if item.get("team_id")
    }
    memberships = []
    for team_id in sorted(set(buckets) | set(star_memberships)):
        membership = {**buckets.get(team_id, {}), **star_memberships.get(team_id, {})}
        membership["team_id"] = team_id
        membership["planned_hours"] = sum(
            float(item.get("allocated_hours") or 0.0)
            for item in current_allocations
            if str(item.get("source_team_id") or "") == team_id
        )
        membership["cross_team_planned_hours"] = sum(
            float(item.get("allocated_hours") or 0.0)
            for item in current_allocations
            if str(item.get("source_team_id") or "") == team_id
            and item.get("participation_type") == "CROSS_TEAM"
        )
        memberships.append(membership)
    return {
        **deepcopy(engineer or {}),
        "identity": {
            "engineer_id": engineer_id,
            "name": (engineer or {}).get("name") or (star or {}).get("name") or engineer_id,
        },
        "current_sprint": current_sprint,
        "approved_revision": approved_revision,
        "memberships": memberships,
        "roles": deepcopy((star or {}).get("roles") or (engineer or {}).get("roles") or []),
        "skills": deepcopy((star or {}).get("skills") or (engineer or {}).get("skills") or []),
        "team_criticality": deepcopy((star or {}).get("team_criticality") or []),
        "current_allocations": sorted(current_allocations, key=lambda item: (str(item.get("task_id")), str(item.get("role")))),
        "future_allocations": sorted(future_allocations, key=lambda item: (int(item.get("sprint") or 0), str(item.get("task_id")))),
        "cross_team": {
            "current": [item for item in current_allocations if item.get("participation_type") == "CROSS_TEAM"],
            "future": [item for item in future_allocations if item.get("participation_type") == "CROSS_TEAM"],
        },
        "source": "LATEST_APPROVED_PLAN" if is_approved else "WORKING_CATALOG",
    }


def build_engineer_history(
    snapshots: Iterable[dict[str, Any]], engineer_id: str
) -> dict[str, Any]:
    ordered = _sorted_snapshots(snapshots)
    completed = max((_completed_through(item) for item in ordered), default=0)
    closed_sprints = []
    for sprint in range(1, completed + 1):
        before, after = _snapshot_pair(ordered, sprint)
        if before is None:
            continue
        actual = _actual_for_sprint(after, sprint)
        participations = []
        before_tasks = _task_map(before)
        after_tasks = _task_map(after or {})
        for task_id, task in before_tasks.items():
            for row in _allocation_rows(task, sprint):
                if str(row.get("engineer_id")) != engineer_id:
                    continue
                owner = row.get("assigned_task_team_id") or task.get("assigned_team_id")
                source = row.get("source_team_id")
                participations.append({
                    "task_id": task_id,
                    "task_title": task.get("title"),
                    "owner_team_id": owner,
                    "source_team_id": source,
                    "role": row.get("role"),
                    "planned_hours": float(row.get("allocated_hours") or 0.0),
                    "hours_semantics": "PLANNED",
                    "participation_type": "CROSS_TEAM" if source and owner and source != owner else "OWN_TEAM",
                    "outcome": _outcome(task_id, actual),
                })
        changed_tasks = []
        for task_id in sorted(set(before_tasks) | set(after_tasks)):
            old_rows = [row for row in _allocation_rows(before_tasks.get(task_id, {})) if str(row.get("engineer_id")) == engineer_id and int(row.get("sprint") or 0) > sprint]
            new_rows = [row for row in _allocation_rows(after_tasks.get(task_id, {})) if str(row.get("engineer_id")) == engineer_id and int(row.get("sprint") or 0) > sprint]
            old_key = sorted((int(row.get("sprint") or 0), str(row.get("role") or ""), float(row.get("allocated_hours") or 0.0)) for row in old_rows)
            new_key = sorted((int(row.get("sprint") or 0), str(row.get("role") or ""), float(row.get("allocated_hours") or 0.0)) for row in new_rows)
            if old_key != new_key and (old_rows or new_rows):
                changed_tasks.append({"task_id": task_id, "before": old_key, "after": new_key})
        closed_sprints.append({
            "sprint": sprint,
            "planned_revision": _revision(before) or None,
            "resulting_revision": _revision(after or {}) or None,
            "participations": participations,
            "allocation_changes": changed_tasks,
        })
    return {"engineer_id": engineer_id, "closed_sprints": closed_sprints}
