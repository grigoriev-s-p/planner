"""Executable remaining-horizon replanning for an approved quarter plan."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from .service import PlanningAPIService
from src.planning.ownership import evaluate_ownership_rows
from .lifecycle import capture_closed_sprint


def _task_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("task_id")): row for row in payload.get("tasks", []) if row.get("task_id")}


def _effort_by_task(tables: dict[str, Any]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in tables.get("task_role_effort", []):
        task_id = row.get("task_id")
        if task_id:
            totals[str(task_id)] += max(float(row.get("required_hours") or 0.0), 0.0)
    return dict(totals)


def _immutable_task_baseline(tables: dict[str, Any]) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, float]] = defaultdict(dict)
    for row in tables.get("task_role_effort", []):
        task_id, role = row.get("task_id"), row.get("role")
        if task_id and role:
            roles[str(task_id)][str(role)] = roles[str(task_id)].get(str(role), 0.0) + float(row.get("required_hours") or 0.0)
    return {
        str(task.get("task_id")): {
            "original_story_points": float(task.get("story_points") or 0.0),
            "original_role_requirements": roles.get(str(task.get("task_id")), {}),
            "original_total_required_hours": sum(roles.get(str(task.get("task_id")), {}).values()),
        }
        for task in tables.get("tasks", [])
        if task.get("task_id")
    }


def _remaining_story_points(
    baseline_tables: dict[str, Any],
    remaining_by_task: dict[str, dict[str, float]],
    current_tasks: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return future SP and immutable original hour denominators per task."""

    baseline_hours = _effort_by_task(baseline_tables)
    baseline_tasks = _task_map(baseline_tables)
    overrides: dict[str, float] = {}
    denominators: dict[str, float] = {}
    for task_id, roles in remaining_by_task.items():
        original_hours = baseline_hours.get(task_id, 0.0)
        if original_hours <= 0:
            continue
        source = baseline_tasks.get(task_id) or current_tasks.get(task_id) or {}
        original_sp = float(source.get("story_points") or 0.0)
        remaining_hours = sum(max(float(value or 0.0), 0.0) for value in roles.values())
        denominators[task_id] = original_hours
        overrides[task_id] = max(
            original_sp * remaining_hours / original_hours,
            0.0,
        )
    return overrides, denominators


def _reference_plan_by_task(tasks: dict[str, dict[str, Any]], closed_sprint: int) -> dict[str, dict[str, Any]]:
    reference: dict[str, dict[str, Any]] = {}
    for task_id, task in tasks.items():
        future = [
            int(row.get("sprint") or 0)
            for row in task.get("allocations") or []
            if int(row.get("sprint") or 0) > closed_sprint
        ]
        if future:
            reference[task_id] = {
                "planned_sprints": sorted(set(future)),
                "assigned_team_id": task.get("assigned_team_id"),
            }
    return reference


def _preferred_engineer_hours_by_task(
    tasks: dict[str, dict[str, Any]],
    closed_sprint: int,
    carry_over_task_ids: set[str] | None = None,
) -> dict[str, dict[str, float]]:
    carry_over_task_ids = carry_over_task_ids or set()
    result: dict[str, dict[str, float]] = {}
    for task_id, task in tasks.items():
        by_engineer: dict[str, float] = defaultdict(float)
        for row in task.get("allocations") or []:
            sprint = int(row.get("sprint") or 0)
            if sprint < closed_sprint or (
                sprint == closed_sprint and task_id not in carry_over_task_ids
            ):
                continue
            engineer_id = row.get("engineer_id")
            if engineer_id:
                by_engineer[str(engineer_id)] += max(float(row.get("allocated_hours") or 0.0), 0.0)
        if by_engineer:
            result[task_id] = dict(by_engineer)
    return result


def _allocation_view(task: dict[str, Any] | None) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], float] = defaultdict(float)
    for row in (task or {}).get("allocations") or []:
        key = (
            int(row.get("sprint") or 0),
            str(row.get("engineer_id") or ""),
            str(row.get("role") or ""),
            str(row.get("source_team_id") or ""),
            str(row.get("assigned_task_team_id") or ""),
            bool(row.get("cross_team")),
        )
        grouped[key] += float(row.get("allocated_hours") or 0.0)
    return [
        {
            "sprint": key[0],
            "engineer_id": key[1],
            "role": key[2],
            "source_team_id": key[3] or None,
            "owner_team_id": key[4] or None,
            "cross_team": key[5],
            "hours": round(hours, 6),
        }
        for key, hours in sorted(grouped.items())
    ]


def _task_diff_view(
    task: dict[str, Any] | None,
    closed_sprint: int | None = None,
) -> dict[str, Any] | None:
    if task is None:
        return None
    horizon_start = int(closed_sprint or 0)
    allocations = [
        row for row in _allocation_view(task)
        if int(row.get("sprint") or 0) > horizon_start
    ]
    planned_sprints = sorted({
        int(value) for value in task.get("planned_sprints") or []
        if int(value) > horizon_start
    })
    completion_sprint = task.get("completion_sprint")
    if completion_sprint is not None and int(completion_sprint) <= horizon_start:
        completion_sprint = None
    return {
        "status": task.get("status"),
        "team_id": task.get("assigned_team_id") or task.get("original_team_id"),
        "rung": task.get("rung"),
        "planned_sprints": planned_sprints,
        "completion_sprint": completion_sprint,
        "sp_load_by_sprint": {
            str(key): round(float(value or 0.0), 6)
            for key, value in (task.get("sp_load_by_sprint") or {}).items()
            if int(key) > horizon_start
        },
        "total_hours": round(sum(row["hours"] for row in allocations), 6),
        "allocations": allocations,
        "reason_codes": [
            str(reason.get("code"))
            for reason in (task.get("reasons") or [])
            if reason.get("code")
        ],
    }


def _allocation_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return manager-visible allocation deltas without ordering noise."""

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(row.get("sprint") or 0),
            str(row.get("engineer_id") or ""),
            str(row.get("role") or ""),
            str(row.get("source_team_id") or ""),
            str(row.get("owner_team_id") or ""),
            bool(row.get("cross_team")),
        )

    left = {key(row): float(row.get("hours") or 0.0) for row in before}
    right = {key(row): float(row.get("hours") or 0.0) for row in after}
    changes: list[dict[str, Any]] = []
    for allocation_key in sorted(set(left) | set(right)):
        before_hours = round(left.get(allocation_key, 0.0), 6)
        after_hours = round(right.get(allocation_key, 0.0), 6)
        if abs(before_hours - after_hours) <= 1e-6:
            continue
        if allocation_key not in left:
            kind = "ADDED"
        elif allocation_key not in right:
            kind = "REMOVED"
        else:
            kind = "HOURS_CHANGED"
        changes.append({
            "kind": kind,
            "sprint": allocation_key[0],
            "engineer_id": allocation_key[1] or None,
            "role": allocation_key[2] or None,
            "source_team_id": allocation_key[3] or None,
            "owner_team_id": allocation_key[4] or None,
            "cross_team": allocation_key[5],
            "before_hours": before_hours,
            "after_hours": after_hours,
        })
    return changes


def _snapshot_diff(
    before: dict[str, Any],
    after: dict[str, Any],
    closed_sprint: int | None = None,
    carry_over_by_task: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured, auditable diff between Approved Plan and Replan Draft."""

    left, right = _task_map(before), _task_map(after)
    completed = set(
        str(task_id)
        for task_id in ((after.get("_planning_state") or {}).get("actual_completed_tasks") or {})
    )
    changes: list[dict[str, Any]] = []
    unchanged: list[str] = []
    affected_teams: set[str] = set()
    affected_engineers: set[str] = set()
    type_counts: dict[str, int] = defaultdict(int)

    carry_over_by_task = carry_over_by_task or {}
    for task_id in sorted(set(left) | set(right)):
        old, new = left.get(task_id), right.get(task_id)
        old_view = _task_diff_view(old, closed_sprint)
        new_view = _task_diff_view(new, closed_sprint)
        fields: list[str] = []
        old_in_future = bool(
            old_view
            and old_view["status"] == "PLANNED"
            and old_view["planned_sprints"]
        )
        new_in_future = bool(
            new_view
            and new_view["status"] == "PLANNED"
            and new_view["planned_sprints"]
        )
        carry_over = carry_over_by_task.get(task_id)
        allocation_changes: list[dict[str, Any]] = []
        if old is None:
            if new_in_future:
                fields = ["ADDED_TO_REMAINING_QUARTER"]
        elif new is None:
            expected_completion = (
                task_id in completed
                and closed_sprint is not None
                and int(old.get("completion_sprint") or 0) == int(closed_sprint)
            )
            if expected_completion:
                unchanged.append(task_id)
                continue
            if old_in_future:
                fields = ["TASK_COMPLETED" if task_id in completed else "TASK_REMOVED"]
        else:
            if not old_in_future and not new_in_future and carry_over is None:
                unchanged.append(task_id)
                continue
            if old_in_future and not new_in_future:
                fields.append("REMOVED_FROM_REMAINING_QUARTER")
            elif not old_in_future and new_in_future:
                fields.append("ADDED_TO_REMAINING_QUARTER")
            comparisons = (
                ("status", "STATUS_CHANGED"),
                ("team_id", "TEAM_CHANGED"),
                ("planned_sprints", "SPRINT_CHANGED"),
                ("completion_sprint", "COMPLETION_CHANGED"),
                ("sp_load_by_sprint", "SP_LOAD_CHANGED"),
                ("total_hours", "HOURS_CHANGED"),
            )
            fields.extend(
                code for field, code in comparisons
                if old_view[field] != new_view[field]
            )
            old_allocations = old_view["allocations"]
            new_allocations = new_view["allocations"]
            allocation_changes = _allocation_changes(old_allocations, new_allocations)
            if allocation_changes:
                old_engineers = {row["engineer_id"] for row in old_allocations if row["engineer_id"]}
                new_engineers = {row["engineer_id"] for row in new_allocations if row["engineer_id"]}
                if old_engineers != new_engineers:
                    fields.append("ENGINEERS_CHANGED")
                old_cross_team = {
                    tuple(sorted(row.items())) for row in old_allocations if row["cross_team"]
                }
                new_cross_team = {
                    tuple(sorted(row.items())) for row in new_allocations if row["cross_team"]
                }
                if old_cross_team != new_cross_team:
                    fields.append("CROSS_TEAM_CHANGED")
                if any(row["kind"] == "HOURS_CHANGED" for row in allocation_changes):
                    fields.append("HOURS_CHANGED")
                fields.append("ALLOCATION_CHANGED")
        if carry_over is not None:
            fields.insert(0, "CARRY_OVER")
        fields = list(dict.fromkeys(fields))
        if not fields:
            unchanged.append(task_id)
            continue

        for code in fields:
            type_counts[code] += 1
        for view in (old_view, new_view):
            if not view:
                continue
            if view.get("team_id"):
                affected_teams.add(str(view["team_id"]))
            for row in view["allocations"]:
                if row.get("engineer_id"):
                    affected_engineers.add(str(row["engineer_id"]))
                if row.get("source_team_id"):
                    affected_teams.add(str(row["source_team_id"]))
        if fields[0] == "CARRY_OVER":
            scheduled = bool(
                new_view
                and new_view.get("status") == "PLANNED"
                and any(
                    sprint > int(closed_sprint or 0)
                    for sprint in new_view.get("planned_sprints") or []
                )
            )
            reason = (
                "Незавершённый остаток перенесён в оставшийся горизонт."
                if scheduled
                else "Незавершённый остаток явно исключён из оставшегося горизонта planner-ом."
            )
            classification = "CARRY_OVER"
        elif fields[0] == "TASK_COMPLETED":
            reason = "Фактически завершена в закрытом спринте."
            classification = "REQUIRED_BY_ACTUAL"
        elif "ADDED_TO_REMAINING_QUARTER" in fields:
            reason = "Включена после пересчёта доступного будущего горизонта."
            classification = "PLANNER_DECISION"
        elif "REMOVED_FROM_REMAINING_QUARTER" in fields or fields[0] == "TASK_REMOVED":
            reason = "После учёта факта задача больше не помещается в оставшийся горизонт."
            classification = "PLANNER_DECISION"
        elif "SPRINT_CHANGED" in fields:
            reason = "Размещение в будущих спринтах изменилось после пересчёта."
            classification = "PLANNER_DECISION"
        elif "ENGINEERS_CHANGED" in fields or "HOURS_CHANGED" in fields:
            reason = "Будущие назначения ресурсов изменились после пересчёта остатка работ."
            classification = "PLANNER_DECISION"
        else:
            reason = "Пересчитана после фиксации факта спринта."
            classification = "PLANNER_DECISION"
        change = {
            "task_id": task_id,
            "title": (new or old or {}).get("title") or (new or old or {}).get("summary"),
            "change": fields[0],
            "fields": fields,
            "reason": reason,
            "classification": classification,
            "before": old_view,
            "after": new_view,
            "allocation_changes": allocation_changes,
        }
        if carry_over is not None:
            remaining_roles = {
                str(role): float(hours)
                for role, hours in (carry_over.get("remaining_role_hours") or {}).items()
                if float(hours) > 0
            }
            change["carry_over"] = {
                "closed_sprint": closed_sprint,
                "earliest_remaining_sprint": (
                    int(closed_sprint) + 1 if closed_sprint is not None else None
                ),
                "incomplete_reason": carry_over.get("incomplete_reason"),
                "remaining_role_hours": remaining_roles,
                "remaining_total_hours": sum(remaining_roles.values()),
                "remaining_story_points": (
                    ((after.get("_planning_state") or {}).get("remaining_story_points") or {}).get(task_id)
                ),
                "outcome": "SCHEDULED" if scheduled else "EXCLUDED",
            }
        changes.append(change)
    return {
        "closed_sprint": closed_sprint,
        "changed_tasks": changes,
        "affected_elements": changes,
        "changed_count": len(changes),
        "unchanged_tasks": unchanged,
        "affected_team_ids": sorted(affected_teams),
        "affected_engineer_ids": sorted(affected_engineers),
        "change_type_counts": dict(sorted(type_counts.items())),
        "summary": {
            "changed_tasks": len(changes),
            "unchanged_tasks": len(unchanged),
            "affected_teams": len(affected_teams),
            "affected_engineers": len(affected_engineers),
            "has_changes": bool(changes),
        },
    }


def _remaining_from_plan(task: dict[str, Any], closed_sprint: int) -> dict[str, float]:
    remaining: dict[str, float] = defaultdict(float)
    for sprint, roles in (task.get("role_hours_by_sprint") or {}).items():
        if int(sprint) <= closed_sprint:
            continue
        for role, hours in (roles or {}).items():
            remaining[str(role)] += float(hours or 0.0)
    return dict(remaining)


def _merge_allocations(
    current_rows: list[dict[str, Any]],
    future_rows: list[dict[str, Any]],
    closed_sprint: int,
) -> list[dict[str, Any]]:
    return [
        *[deepcopy(row) for row in current_rows if int(row.get("sprint") or 0) <= closed_sprint],
        *[deepcopy(row) for row in future_rows if int(row.get("sprint") or 0) > closed_sprint],
    ]


def _merge_task(
    old: dict[str, Any] | None,
    new: dict[str, Any],
    closed_sprint: int,
) -> dict[str, Any]:
    result = deepcopy(new)
    if old is None:
        return result
    result["allocations"] = _merge_allocations(
        old.get("allocations", []), new.get("allocations", []), closed_sprint
    )
    result["planned_sprints"] = sorted({
        int(row.get("sprint")) for row in result["allocations"] if row.get("sprint")
    })
    for field in ("hours_by_sprint", "role_hours_by_sprint", "sp_load_by_sprint"):
        merged = {
            str(sprint): deepcopy(value)
            for sprint, value in (old.get(field) or {}).items()
            if int(sprint) <= closed_sprint
        }
        merged.update({
            str(sprint): deepcopy(value)
            for sprint, value in (new.get(field) or {}).items()
            if int(sprint) > closed_sprint
        })
        result[field] = merged
    return result


def build_remaining_horizon_plan(
    approved: dict[str, Any],
    sprint_number: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Freeze closed sprints and rebuild only the open horizon."""

    state = deepcopy(approved.get("_planning_state") or {})
    tables = deepcopy(state.get("tables") or {})
    if not tables:
        raise ValueError("У утверждённого плана отсутствует planning context.")
    completed_through = int(state.get("completed_through_sprint") or 0)
    if sprint_number != completed_through + 1:
        raise ValueError("Спринты должны закрываться последовательно.")
    if not 1 <= sprint_number <= 6:
        raise ValueError("Номер спринта должен быть от 1 до 6.")

    current_tasks = _task_map(approved)
    baseline_tables = deepcopy(state.get("baseline_tables") or tables)
    known_ids = {str(row.get("task_id")) for row in tables["tasks"]}
    result_by_id = {str(row["task_id"]): dict(row) for row in results}
    explicitly_reported_ids = set(result_by_id)
    unknown = sorted(set(result_by_id) - known_ids)
    if unknown:
        raise ValueError("Неизвестные task_id в факте: " + ", ".join(unknown))
    # A planned task is completed by default only when its approved completion
    # sprint is the sprint being closed. Explicit unfinished facts always win.
    for task_id, task in current_tasks.items():
        if task_id in result_by_id:
            continue
        if (
            task.get("status") == "PLANNED"
            and int(task.get("completion_sprint") or 0) == sprint_number
        ):
            result_by_id[task_id] = {
                "task_id": task_id,
                "task_completed": True,
                "remaining_role_hours": {},
                "remaining_engineer_allocations": {},
                "employee_deviation": None,
                "unavailable_next_sprint": False,
                "auto_completed": True,
            }

    for task_id, row in result_by_id.items():
        if not row.get("task_completed") and not row.get("remaining_role_hours"):
            raise ValueError(
                f"Для незавершённой задачи {task_id} укажите remaining_role_hours."
            )

    actual_completed = {
        **{str(key): int(value) for key, value in (state.get("actual_completed_tasks") or {}).items()},
        **{
            task_id: sprint_number
            for task_id, row in result_by_id.items()
            if row.get("task_completed")
        },
    }
    for row in tables["tasks"]:
        task_id = str(row.get("task_id"))
        if task_id in actual_completed:
            row["status"] = "Done"
        elif task_id in current_tasks and current_tasks[task_id].get("assigned_team_id"):
            row["team_id"] = current_tasks[task_id]["assigned_team_id"]

    remaining_by_task: dict[str, dict[str, float]] = {}
    for task_id, task in current_tasks.items():
        if task_id in actual_completed:
            continue
        if task_id in result_by_id:
            remaining_by_task[task_id] = {
                str(role): float(hours)
                for role, hours in result_by_id[task_id]["remaining_role_hours"].items()
                if float(hours) > 0
            }
        else:
            derived = _remaining_from_plan(task, sprint_number)
            if derived:
                remaining_by_task[task_id] = derived

    remaining_story_points, original_total_hours = _remaining_story_points(
        baseline_tables,
        remaining_by_task,
        current_tasks,
    )

    original_effort = tables["task_role_effort"]
    tables["task_role_effort"] = [
        row for row in original_effort
        if str(row.get("task_id")) not in remaining_by_task
        and str(row.get("task_id")) not in actual_completed
    ]
    for task_id, roles in remaining_by_task.items():
        tables["task_role_effort"].extend(
            {
                "task_id": task_id,
                "role": role,
                "role_raw": role,
                "required_hours": hours,
            }
            for role, hours in roles.items()
        )

    carry_over = {
        task_id: row.get("remaining_engineer_allocations") or {}
        for task_id, row in result_by_id.items()
        if not row.get("task_completed") and row.get("remaining_engineer_allocations")
    }
    carry_over_task_ids = {
        task_id for task_id, row in result_by_id.items()
        if not row.get("task_completed")
    }
    unavailable_next = {
        str(engineer_id)
        for row in result_by_id.values()
        if row.get("unavailable_next_sprint")
        for engineer_id in (
            row.get("unavailable_engineer_ids")
            or (row.get("remaining_engineer_allocations") or {})
        )
    }
    cross_team_tasks = {
        task_id
        for task_id, row in result_by_id.items()
        if not row.get("task_completed") and row.get("unavailable_next_sprint")
    }
    # Preserve an already accepted external allocation as a valid replan
    # option.  Cross-team help is still opened only for these task IDs.
    for task_id, task in current_tasks.items():
        owner_team = task.get("assigned_team_id") or task.get("original_team_id")
        if owner_team and any(
            row.get("source_team_id")
            and row.get("source_team_id") != owner_team
            and (
                int(row.get("sprint") or 0) > sprint_number
                or task_id in carry_over_task_ids
            )
            for row in task.get("allocations") or []
        ):
            cross_team_tasks.add(task_id)
    prior_ownership = {
        task_id: evaluate_ownership_rows(
            task.get("assigned_team_id") or task.get("original_team_id") or "",
            task.get("allocations") or [],
        )
        for task_id, task in current_tasks.items()
        if task.get("allocations") and (task.get("assigned_team_id") or task.get("original_team_id"))
    }
    reference_plan = _reference_plan_by_task(current_tasks, sprint_number)
    preferred_engineer_hours = _preferred_engineer_hours_by_task(
        current_tasks, sprint_number, carry_over_task_ids
    )

    if sprint_number < 6:
        candidate = deepcopy(PlanningAPIService.from_tables(
            tables,
            plan_version=f"replan-after-sprint-{sprint_number}",
            planner_options={
                "allow_team_reassignment": False,
                # Cross-team is opened only for explicitly unavailable
                # employee residuals; ordinary replan ownership remains local.
                "allow_cross_team": bool(cross_team_tasks),
                "cross_team_task_ids": cross_team_tasks,
                "start_sprint": sprint_number + 1,
                "actual_completed_tasks": actual_completed,
                "unavailable_engineer_ids_by_sprint": {
                    sprint_number + 1: unavailable_next
                },
                "carry_over_engineer_hours_by_task": carry_over,
                "carry_over_task_ids": carry_over_task_ids,
                "prior_task_ownership_by_task": prior_ownership,
                "story_points_override_by_task": remaining_story_points,
                "original_total_required_hours_by_task": original_total_hours,
                "preferred_engineer_hours_by_task": preferred_engineer_hours,
                "reference_plan_by_task": reference_plan,
            },
        ).dashboard)
    else:
        # Sprint 6 closes the quarter. There is no Sprint 7 planner run.
        candidate = deepcopy(approved)
        for task in candidate.get("tasks") or []:
            task_id = str(task.get("task_id") or "")
            if task_id not in carry_over_task_ids:
                continue
            task.update({
                "status": "MOVED",
                "status_label": "Перенесена",
                "completion_sprint": None,
                "outside_quarter": True,
                "reasons": [{
                    "code": "QUARTER_OVERFLOW",
                    "severity": "WARNING",
                    "title": "Остаток выходит за пределы квартала",
                    "message": (
                        f"После закрытия Sprint 6 остаток задачи {task_id} "
                        "должен быть запланирован в следующем квартале."
                    ),
                    "details": {
                        "task_id": task_id,
                        "last_sprint": 6,
                        "resources_committed": False,
                    },
                }],
            })
    completed_ids = set(actual_completed)
    candidate["tasks"] = [row for row in candidate["tasks"] if row["task_id"] not in completed_ids]
    new_tasks = _task_map(candidate)
    candidate["tasks"] = [
        _merge_task(current_tasks.get(task_id), task, sprint_number)
        for task_id, task in new_tasks.items()
    ]
    for task in candidate["tasks"]:
        task_id = str(task.get("task_id"))
        if task_id in remaining_story_points:
            task["remaining_story_points"] = remaining_story_points[task_id]

    invalid_carry_over: list[str] = []
    for task_id in sorted(carry_over_task_ids):
        task = new_tasks.get(task_id)
        if task is None:
            invalid_carry_over.append(f"{task_id}: отсутствует в результате planner")
            continue
        scheduled = task.get("status") == "PLANNED" and any(
            int(value) > sprint_number
            for value in task.get("planned_sprints") or []
        )
        explicitly_excluded = (
            task.get("status") in {
                "MOVED", "UNPLANNABLE", "INELIGIBLE_DATA_CONSTRAINT", "UNKNOWN"
            }
            and bool(task.get("reasons"))
        )
        if not scheduled and not explicitly_excluded:
            invalid_carry_over.append(
                f"{task_id}: нет будущего размещения или явной причины исключения"
            )
    if invalid_carry_over:
        raise RuntimeError(
            "Нарушен invariant carry-over: " + "; ".join(invalid_carry_over)
        )

    old_plan = approved.get("plan") or {}
    new_plan = candidate.get("plan") or {}
    if sprint_number < 6:
        new_plan["sprints"] = [
            *[deepcopy(row) for row in old_plan.get("sprints", []) if int(row.get("sprint_number") or 0) <= sprint_number],
            *[deepcopy(row) for row in new_plan.get("sprints", []) if int(row.get("sprint_number") or 0) > sprint_number],
        ]
        new_plan["allocations"] = _merge_allocations(
            old_plan.get("allocations", []), new_plan.get("allocations", []), sprint_number
        )
    new_plan["tasks"] = deepcopy(candidate["tasks"])

    baseline = state.get("week0_baseline") or approved
    baseline_kpis = baseline.get("kpis") or {}
    candidate_kpis = candidate.get("kpis") or {}
    if baseline_kpis.get("expected_kpi"):
        denominator = baseline_kpis["expected_kpi"].get("baseline_initiatives")
        candidate_kpis.setdefault("expected_kpi", {})["baseline_initiatives"] = denominator
    for current_row, baseline_row in zip(
        candidate_kpis.get("sprint_kpis", []), baseline_kpis.get("sprint_kpis", [])
    ):
        current_row["baseline_sp"] = baseline_row.get("baseline_sp")

    closed_sprint_history = [
        deepcopy(row)
        for row in state.get("closed_sprint_history") or []
        if int(row.get("sprint_number") or 0) != sprint_number
    ]
    closed_sprint_history.append(
        capture_closed_sprint(approved, sprint_number, result_by_id)
    )
    closed_sprint_history.sort(key=lambda row: int(row.get("sprint_number") or 0))

    state.update({
        "tables": tables,
        "baseline_tables": baseline_tables,
        "original_task_baseline": state.get("original_task_baseline") or _immutable_task_baseline(baseline_tables),
        "completed_through_sprint": sprint_number,
        "actual_completed_tasks": actual_completed,
        "closed_sprint_history": closed_sprint_history,
        "remaining_story_points": remaining_story_points,
        "actual_results": [
            *(state.get("actual_results") or []),
            *[
                {**deepcopy(row), "sprint_number": sprint_number}
                for task_id, row in result_by_id.items()
                if task_id not in explicitly_reported_ids
            ],
            *[{**deepcopy(row), "sprint_number": sprint_number} for row in results],
        ],
    })
    if sprint_number == 6:
        state["quarter_status"] = "COMPLETED"
    candidate["_planning_state"] = state
    candidate["replanning"] = {
        "completed_through_sprint": sprint_number,
        "frozen_sprints": list(range(1, sprint_number + 1)),
        "remaining_sprints": list(range(sprint_number + 1, 7)),
        "quarter_status": "COMPLETED" if sprint_number == 6 else "IN_PROGRESS",
        "diff": _snapshot_diff(
            approved,
            candidate,
            sprint_number,
            {
                task_id: row
                for task_id, row in result_by_id.items()
                if not row.get("task_completed")
            },
        ),
    }
    quarter = candidate.setdefault("plan", {}).setdefault("quarter", {})
    quarter["current_sprint"] = 6 if sprint_number == 6 else sprint_number + 1
    quarter["current_sprint_source"] = "replanning_state"
    candidate.setdefault("features", {})["replanning"] = "AVAILABLE"
    return candidate
