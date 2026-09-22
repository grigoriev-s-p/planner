"""Training impact and future-horizon simulation over the existing planner."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .replanning import (
    _merge_allocations, _merge_task, _preferred_engineer_hours_by_task,
    _reference_plan_by_task, _remaining_from_plan, _remaining_story_points,
    _snapshot_diff, _task_map,
)
from .service import PlanningAPIService
from .kpi_projection import project_management_kpis


def apply_completed_capabilities(tables: dict[str, list[dict]], plans: list[dict]) -> None:
    """Add qualified roles/skills to an in-memory snapshot, never to RAW rows."""

    for plan in plans:
        engineer_id = plan["engineer_id"]
        if skill := plan.get("target_skill"):
            if not any(str(row.get("engineer_id")) == engineer_id
                       and str(row.get("technology")) == skill
                       for row in tables.get("engineer_skills", [])):
                tables.setdefault("engineer_skills", []).append({
                    "engineer_id": engineer_id, "technology": skill,
                    "effective_from_sprint": max(plan["selected_sprints"]) + 1,
                })
        if role := plan.get("target_role"):
            members = [row for row in tables.get("engineers", [])
                       if str(row.get("engineer_id")) == engineer_id]
            for row in members:
                if not any(str(existing.get("engineer_id")) == engineer_id
                           and str(existing.get("team_id")) == str(row.get("team_id"))
                           and str(existing.get("role")) == role
                           for existing in tables["engineers"]):
                    tables["engineers"].append({**row, "role": role,
                                                "capability_source": "TRAINING",
                                                "effective_from_sprint": max(plan["selected_sprints"]) + 1})


def training_constraints(existing: list[dict], plan: dict) -> list[dict]:
    rate = float(plan["availability_rate"])
    return [*deepcopy(existing), *(
        {"engineer_id": plan["engineer_id"], "sprint": sprint,
         "availability_rate": rate, "source_type": "TRAINING",
         "source_id": plan["training_id"]}
        for sprint in plan["selected_sprints"] if rate < 1
    )]


def build_training_replan(
    approved: dict[str, Any], constraints: list[dict],
    resolutions: dict[str, dict[str, Any]],
    training_engineer_id: str,
) -> dict[str, Any]:
    """Replan only after the current sprint, without closing it or changing facts."""

    state = deepcopy(approved.get("_planning_state") or {})
    tables = deepcopy(state.get("tables") or {})
    if not tables:
        raise ValueError("У утверждённого плана нет planning context.")
    current = int(state.get("completed_through_sprint") or 0) + 1
    if current >= 6:
        raise ValueError("После текущего спринта не осталось горизонта для пересчёта.")
    tables["engineer_availability"] = deepcopy(constraints)
    tasks = _task_map(approved)
    # Future work for a multi-sprint task is the already approved remainder.
    future_roles = {
        task_id: _remaining_from_plan(task, current)
        for task_id, task in tasks.items()
        if task.get("status") == "PLANNED" and any(
            int(sprint) > current for sprint in task.get("planned_sprints") or []
        )
    }
    future_roles = {key: roles for key, roles in future_roles.items() if roles}
    completed_for_simulation = {
        **{str(key): int(value) for key, value in
           (state.get("actual_completed_tasks") or {}).items()},
        **{
            task_id: current for task_id, task in tasks.items()
            if task.get("status") == "PLANNED"
            and int(task.get("completion_sprint") or 0) <= current
        },
    }
    for row in tables.get("tasks", []):
        task_id = str(row.get("task_id") or "")
        if task_id in completed_for_simulation:
            row["status"] = "Done"
        elif task_id in tasks and tasks[task_id].get("assigned_team_id"):
            row["team_id"] = tasks[task_id]["assigned_team_id"]
    baseline_tables = state.get("baseline_tables") or state.get("tables") or tables
    sp_overrides, original_hours = _remaining_story_points(
        baseline_tables, future_roles, tasks
    )
    tables["task_role_effort"] = [
        row for row in tables.get("task_role_effort", [])
        if str(row.get("task_id") or "") not in future_roles
    ]
    for task_id, roles in future_roles.items():
        tables["task_role_effort"].extend(
            {"task_id": task_id, "role": role, "role_raw": role,
             "required_hours": hours}
            for role, hours in roles.items()
        )
    preferred = _preferred_engineer_hours_by_task(tasks, current)
    cross_team_tasks = {
        task_id for task_id, task in tasks.items()
        if any(row.get("cross_team") for row in task.get("allocations") or [])
    }
    earliest: dict[str, int] = {}
    for resolution in resolutions.values():
        task_id = str(resolution.get("task_id") or "")
        if task_id not in tasks:
            raise ValueError(f"Неизвестная задача {task_id}.")
        if resolution.get("action") == "MOVE":
            earliest[task_id] = max(earliest.get(task_id, current + 1),
                                    int(resolution["sprint"]) + 1)
            preferred.pop(task_id, None)
        elif resolution.get("action") == "REPLACE":
            engineer_id = str(resolution.get("engineer_id") or "")
            if not engineer_id:
                raise ValueError(f"Для {task_id} не выбран инженер на замену.")
            preferred.get(task_id, {}).pop(training_engineer_id, None)
            original_future = [int(sprint) for sprint in
                               tasks[task_id].get("planned_sprints") or []
                               if int(sprint) > current]
            if original_future:
                earliest[task_id] = max(earliest.get(task_id, current + 1),
                                        min(original_future))
            preferred.setdefault(task_id, {})[engineer_id] = (
                preferred.get(task_id, {}).get(engineer_id, 0.0)
                + float(resolution["hours"])
            )
            cross_team_tasks.add(task_id)
        else:
            raise ValueError(f"Для {task_id} выберите замену или перенос.")
    candidate = deepcopy(PlanningAPIService.from_tables(
        tables, plan_version=f"training-replan-after-sprint-{current}",
        planner_options={
            "allow_team_reassignment": False,
            "allow_cross_team": bool(cross_team_tasks),
            "cross_team_task_ids": cross_team_tasks,
            "start_sprint": current + 1,
            "actual_completed_tasks": completed_for_simulation,
            "story_points_override_by_task": sp_overrides,
            "original_total_required_hours_by_task": original_hours,
            "preferred_engineer_hours_by_task": preferred,
            "reference_plan_by_task": _reference_plan_by_task(tasks, current),
            "earliest_sprint_by_task": earliest,
        },
    ).dashboard)
    new_tasks = _task_map(candidate)
    for resolution in resolutions.values():
        task_id = str(resolution["task_id"])
        task = new_tasks.get(task_id) or {}
        sprint = int(resolution["sprint"])
        rows = [row for row in task.get("allocations") or []
                if int(row.get("sprint") or 0) == sprint]
        if resolution["action"] == "MOVE" and rows:
            raise ValueError(f"Задача {task_id} не была перенесена из Sprint {sprint}.")
        if resolution["action"] == "REPLACE":
            assigned = sum(float(row.get("allocated_hours") or 0)
                           for row in rows
                           if str(row.get("engineer_id")) == resolution["engineer_id"]
                           and str(row.get("role")) == str(resolution["role"]))
            if assigned + 1e-9 < float(resolution["hours"]):
                raise ValueError(f"Выбранная замена не получила нужные часы задачи {task_id}.")
    merged = {task_id: _merge_task(tasks.get(task_id), task, current)
              for task_id, task in new_tasks.items()}
    # The simulation marks current-sprint completions as Done solely to keep
    # them out of the future planner. They remain planned in the real draft.
    for task_id, old in tasks.items():
        if task_id in completed_for_simulation and task_id not in (state.get("actual_completed_tasks") or {}):
            merged[task_id] = deepcopy(old)
    candidate["tasks"] = list(merged.values())
    old_plan = approved.get("plan") or {}
    new_plan = candidate.get("plan") or {}
    new_plan["sprints"] = [
        *[deepcopy(row) for row in old_plan.get("sprints") or []
          if int(row.get("sprint_number") or 0) <= current],
        *[deepcopy(row) for row in new_plan.get("sprints") or []
          if int(row.get("sprint_number") or 0) > current],
    ]
    new_plan["allocations"] = _merge_allocations(
        old_plan.get("allocations") or [], new_plan.get("allocations") or [], current
    )
    new_plan["tasks"] = deepcopy(candidate["tasks"])
    new_plan.setdefault("quarter", {})["current_sprint"] = current
    state["tables"] = tables
    candidate["_planning_state"] = state
    candidate["replanning"] = {
        "completed_through_sprint": current - 1,
        "frozen_sprints": list(range(1, current + 1)),
        "remaining_sprints": list(range(current + 1, 7)),
        "diff": _snapshot_diff(approved, candidate, current, {}),
    }
    baseline_kpis = (state.get("week0_baseline") or approved).get("kpis") or {}
    candidate_kpis = candidate.setdefault("kpis", {})
    if baseline_kpis.get("expected_kpi"):
        candidate_kpis.setdefault("expected_kpi", {})["baseline_initiatives"] = (
            baseline_kpis["expected_kpi"].get("baseline_initiatives")
        )
    for candidate_row, baseline_row in zip(
        candidate_kpis.get("sprint_kpis") or [], baseline_kpis.get("sprint_kpis") or []
    ):
        candidate_row["baseline_sp"] = baseline_row.get("baseline_sp")
    return project_management_kpis(candidate)
