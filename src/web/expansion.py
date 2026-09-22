"""Additive sprint opportunities using the existing quarter planner."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .service import PlanningAPIService
from .kpi_projection import project_management_kpis


def _planned_tasks(plan: dict) -> dict[str, dict]:
    return {str(row["task_id"]): row for row in plan.get("tasks") or []
            if row.get("task_id") and row.get("status") == "PLANNED"}


def _reserved_usage(plan: dict, start_sprint: int) -> dict[int, dict]:
    usage = {sprint: {"team_sp_used": defaultdict(float),
                      "engineer_hours_used": defaultdict(float),
                      "engineer_team_hours_used": defaultdict(lambda: defaultdict(float))}
             for sprint in range(start_sprint, 7)}
    for task in _planned_tasks(plan).values():
        team_id = str(task.get("assigned_team_id") or task.get("original_team_id") or "")
        for sprint, sp in (task.get("sp_load_by_sprint") or {}).items():
            if int(sprint) in usage and team_id:
                usage[int(sprint)]["team_sp_used"][team_id] += float(sp or 0)
        for row in task.get("allocations") or []:
            sprint = int(row.get("sprint") or 0)
            if sprint not in usage:
                continue
            engineer_id = str(row.get("engineer_id") or "")
            source_team = str(row.get("source_team_id") or "")
            hours = float(row.get("allocated_hours") or 0)
            if engineer_id:
                usage[sprint]["engineer_hours_used"][engineer_id] += hours
                if source_team:
                    usage[sprint]["engineer_team_hours_used"][engineer_id][source_team] += hours
    return usage


def candidate_ids(approved: dict, tables: dict) -> list[str]:
    planned = set(_planned_tasks(approved))
    completed = set((approved.get("_planning_state") or {}).get("actual_completed_tasks") or {})
    return sorted({str(row["task_id"]) for row in tables.get("tasks") or []
                   if row.get("task_id") and str(row.get("task_id")) not in planned | completed
                   and str(row.get("status") or "").casefold() in {"todo", "inprogress"}
                   and str(row.get("is_placeholder") or "").casefold() not in {"true", "1", "yes"}},
                  key=lambda task_id: (-next((float(row.get("rung") or 0)
                                            for row in tables["tasks"]
                                            if str(row.get("task_id")) == task_id), 0), task_id))


def simulate_additions(approved: dict, tables: dict, selected_ids: set[str] | None = None) -> dict:
    """Plan only candidates, with Approved hours/SP reserved in every future sprint."""

    completed = int((approved.get("_planning_state") or {}).get("completed_through_sprint") or 0)
    if completed >= 6:
        return {"tasks": [], "sprints": [], "selected_ids": []}
    candidates = set(candidate_ids(approved, tables))
    requested = candidates if selected_ids is None else selected_ids
    if not requested <= candidates:
        raise ValueError("Список задач для расширения устарел.")
    fixed = {task_id: int(task["completion_sprint"])
             for task_id, task in _planned_tasks(approved).items()
             if task.get("completion_sprint") is not None}
    actual = {str(key): int(value) for key, value in
              ((approved.get("_planning_state") or {}).get("actual_completed_tasks") or {}).items()}
    planning_tables = deepcopy(tables)
    planning_tables["tasks"] = [
        {**row, "status": "Done"} if str(row.get("task_id")) in fixed | actual else row
        for row in planning_tables.get("tasks") or []
        if str(row.get("task_id")) in requested | set(fixed) | set(actual)
        or str(row.get("status") or "").casefold() == "done"
    ]
    service = PlanningAPIService.from_tables(
        planning_tables, plan_version="sprint-additive-simulation",
        planner_options={
            "allow_team_reassignment": False,
            "allow_cross_team": True,
            "start_sprint": completed + 1,
            "actual_completed_tasks": actual,
            "fixed_planned_completion_by_task": fixed,
            "reserved_resource_usage_by_sprint": _reserved_usage(approved, completed + 1),
        },
    )
    result = service.dashboard
    result["tasks"] = [row for row in result["tasks"]
                       if row["task_id"] in requested and row["status"] == "PLANNED"]
    result["selected_ids"] = [row["task_id"] for row in result["tasks"]]
    return result


def initiative_completion_count(tasks: list[dict], catalog: dict) -> int:
    members: dict[str, set[str]] = defaultdict(set)
    for row in catalog.get("tasks") or []:
        initiative = str(row.get("initiative_id") or "")
        status = str(row.get("status") or "").casefold()
        if (initiative and row.get("task_id") and status in {"todo", "inprogress", "done"}
                and str(row.get("is_placeholder") or "").casefold() not in {"true", "1", "yes"}):
            members[initiative].add(str(row["task_id"]))
    covered = {str(row["task_id"]) for row in tasks
               if row.get("status") in {"PLANNED", "DONE", "COMPLETED"}}
    covered.update(str(row["task_id"]) for row in catalog.get("tasks") or []
                   if row.get("task_id") and str(row.get("status") or "").casefold() == "done")
    return sum(bool(ids) and ids <= covered for ids in members.values())


def build_proposal(approved: dict, tables: dict, *, catalog_revision: int,
                   availability_constraints: list[dict], workflow_id: str) -> dict:
    """Find additions without changing Approved; package only required dependencies."""

    closed = int((approved.get("_planning_state") or {}).get("completed_through_sprint") or 0)
    base_revision = int((approved.get("approved_snapshot") or {}).get("revision") or 0)
    proposal = {
        "workflow_id": f"expansion-{workflow_id}", "kind": "ADDITIVE_EXPANSION",
        "status": "QUARTER_COMPLETE" if closed >= 6 else "EMPTY",
        "triggered_by_sprint": closed, "base_approved_revision": base_revision,
        "catalog_revision": int(catalog_revision),
        "base_availability_constraints": deepcopy(availability_constraints),
        "recommendations": [], "best_task_ids": [], "selected_ids": [],
        "rejected_ids": [], "message": "",
        "before": {"tasks": len(_planned_tasks(approved)),
                   "initiatives": initiative_completion_count(approved.get("tasks") or [], tables)},
        "best_effect": {"added_tasks": 0, "added_initiatives": 0},
    }
    if closed >= 6:
        proposal["message"] = "Квартал завершён. Доступных спринтов для расширения плана нет."
        return proposal
    best = simulate_additions(approved, tables)
    best_rows = {row["task_id"]: row for row in best["tasks"]}
    best_ids = set(best_rows)
    proposal["best_task_ids"] = sorted(best_ids)
    proposal["best_effect"] = {
        "added_tasks": len(best_ids),
        "added_initiatives": max(
            initiative_completion_count(list(approved.get("tasks") or []) + best["tasks"], tables)
            - proposal["before"]["initiatives"], 0),
    }
    if not best_ids:
        proposal["message"] = "Дополнительных выполнимых задач пока нет."
        return proposal
    # A dependency is packaged only if its dependent task cannot be planned
    # alone and the predecessor is itself one of the newly added tasks.
    parents = {task_id: task_id for task_id in best_ids}

    def root(task_id: str) -> str:
        while parents[task_id] != task_id:
            task_id = parents[task_id]
        return task_id

    def union(left: str, right: str) -> None:
        parents[root(right)] = root(left)

    for row in tables.get("task_dependencies") or []:
        task_id = str(row.get("blocked_task_id") or "")
        blocker = str(row.get("blocking_task_id") or "")
        if task_id in best_ids and blocker in best_ids:
            alone = simulate_additions(approved, tables, {task_id})
            if task_id not in {task["task_id"] for task in alone["tasks"]}:
                union(task_id, blocker)
    components: dict[str, list[str]] = defaultdict(list)
    for task_id in sorted(best_ids):
        components[root(task_id)].append(task_id)
    tasks_by_id = {str(row.get("task_id")): row for row in tables.get("tasks") or []}
    before_initiatives = initiative_completion_count(approved.get("tasks") or [], tables)
    for ids in components.values():
        simulated = simulate_additions(approved, tables, set(ids))
        if set(simulated["selected_ids"]) != set(ids):
            raise RuntimeError("Не удалось независимо проверить пакет дополнительных задач.")
        combined = list(approved.get("tasks") or []) + simulated["tasks"]
        first = best_rows[ids[0]]
        proposal["recommendations"].append({
            "recommendation_id": f"additional-{uuid4().hex[:12]}",
            "type": "ATOMIC_PACKAGE" if len(ids) > 1 else "ADDITIONAL_TASK",
            "task_ids": ids,
            "title": (f"Совместно добавить {len(ids)} задачи" if len(ids) > 1
                      else f"Добавить задачу {ids[0]}"),
            "tasks": [{"task_id": task_id,
                       "title": tasks_by_id.get(task_id, {}).get("title") or task_id,
                       "initiative_id": tasks_by_id.get(task_id, {}).get("initiative_id"),
                       "team_id": best_rows[task_id].get("assigned_team_id"),
                       "start_sprint": best_rows[task_id].get("start_sprint"),
                       "completion_sprint": best_rows[task_id].get("completion_sprint"),
                       "rung": best_rows[task_id].get("rung"),
                       "allocations": best_rows[task_id].get("allocations") or []}
                      for task_id in ids],
            "effect": {"added_tasks": len(ids), "added_initiatives": max(
                initiative_completion_count(combined, tables) - before_initiatives, 0)},
            "status": "AVAILABLE",
            "reason": "Задача стала допустимой при актуальных ресурсах и ограничениях.",
            "rung": max(float(best_rows[task_id].get("rung") or 0) for task_id in ids),
            "team_id": first.get("assigned_team_id"),
        })
    proposal["recommendations"].sort(key=lambda row: (-row["rung"], row["task_ids"][0]))
    proposal["status"] = "OPEN"
    proposal["message"] = f"Найдено {len(best_ids)} дополнительных задач."
    return proposal


def merge_additions(approved: dict, simulation: dict, catalog: dict,
                    *, triggered_by_sprint: int | None = None) -> dict:
    """Append newly planned tasks while keeping every pre-existing task byte-for-byte."""

    result = deepcopy(approved)
    additions = {row["task_id"]: deepcopy(row) for row in simulation.get("tasks") or []}
    for task in additions.values():
        task["expansion_origin"] = {
            "source": "sprint_expansion", "triggered_by_sprint": triggered_by_sprint,
        }
    original = {row["task_id"]: deepcopy(row) for row in approved.get("tasks") or []}
    original.update(additions)
    result["tasks"] = list(original.values())
    result["risks"] = [row for row in result.get("risks") or []
                       if str(row.get("task_id") or "") not in additions]
    plan = result.setdefault("plan", {})
    plan["tasks"] = deepcopy(result["tasks"])
    plan["allocations"] = [
        {"task_id": task["task_id"], **deepcopy(row)}
        for task in result["tasks"] for row in task.get("allocations") or []
    ]
    existing_engineers = {row.get("engineer_id") for row in result.get("engineers") or []}
    for engineer in simulation.get("engineers") or []:
        if engineer.get("engineer_id") not in existing_engineers:
            result.setdefault("engineers", []).append(deepcopy(engineer))
    existing_teams = {row.get("team_id") for row in result.get("teams") or []}
    for team in simulation.get("teams") or []:
        if team.get("team_id") not in existing_teams:
            result.setdefault("teams", []).append(deepcopy(team))
    for engineer in result.get("engineers") or []:
        engineer_id = engineer.get("engineer_id")
        if engineer_id not in existing_engineers:
            continue
        for task in additions.values():
            for allocation in task.get("allocations") or []:
                if allocation.get("engineer_id") != engineer_id:
                    continue
                sprint_key = str(allocation["sprint"])
                hours = float(allocation.get("allocated_hours") or 0)
                engineer.setdefault("allocations", []).append({"task_id": task["task_id"],
                                                                  **deepcopy(allocation)})
                used = engineer.setdefault("used_hours_by_sprint", {})
                used[sprint_key] = float(used.get(sprint_key) or 0) + hours
                team_used = engineer.setdefault("used_hours_by_team_by_sprint", {}).setdefault(sprint_key, {})
                source_team = str(allocation.get("source_team_id") or "")
                team_used[source_team] = float(team_used.get(source_team) or 0) + hours
                free = engineer.setdefault("free_hours_by_sprint", {})
                free[sprint_key] = max(float(free.get(sprint_key) or 0) - hours, 0)
                source = next((row for row in result.get("teams") or []
                               if row.get("team_id") == source_team), None)
                if source is not None:
                    source_used = source.setdefault("source_used_hours_by_sprint", {})
                    source_used[sprint_key] = float(source_used.get(sprint_key) or 0) + hours

    for task in additions.values():
        owner = next((row for row in result.get("teams") or []
                      if row.get("team_id") == task.get("assigned_team_id")), None)
        if owner is None:
            continue
        if owner.get("team_id") not in existing_teams:
            continue
        for sprint_key, sp in (task.get("sp_load_by_sprint") or {}).items():
            sp_row = owner.setdefault("sp_by_sprint", {}).setdefault(str(sprint_key),
                {"capacity": owner.get("capacity_sp") or 0, "used": 0, "remaining": owner.get("capacity_sp") or 0})
            sp_row["used"] = float(sp_row.get("used") or 0) + float(sp or 0)
            sp_row["remaining"] = max(float(sp_row.get("capacity") or 0) - sp_row["used"], 0)
        for sprint_key, hours in (task.get("hours_by_sprint") or {}).items():
            work = owner.setdefault("task_work_hours_by_sprint", {})
            work[str(sprint_key)] = float(work.get(str(sprint_key)) or 0) + float(hours or 0)
    by_sprint = {int(row["sprint_number"]): row for row in
                 (simulation.get("plan") or {}).get("sprints") or []}
    for sprint in plan.get("sprints") or []:
        number = int(sprint["sprint_number"])
        new_rows = [deepcopy(row) for row in (by_sprint.get(number) or {}).get("tasks") or []
                    if row.get("task_id") in additions]
        sprint.setdefault("tasks", []).extend(new_rows)
        for row in new_rows:
            team_id = row.get("assigned_team_id")
            team = next((item for item in sprint.get("teams") or []
                         if item.get("team_id") == team_id), None)
            if team is None:
                team = deepcopy(next((item for item in
                                      (by_sprint.get(number) or {}).get("teams") or []
                                      if item.get("team_id") == team_id), None))
                if team is not None:
                    sprint.setdefault("teams", []).append(team)
            if team is not None:
                team["used_sp"] = float(team.get("used_sp") or 0) + float(row.get("capacity_sp_load") or 0)
                team["remaining_sp"] = max(float(team.get("capacity_sp") or 0) - team["used_sp"], 0)
            task = additions[row["task_id"]]
            for role, hours in (task.get("role_hours_by_sprint") or {}).get(str(number), {}).items():
                resource = next((item for item in sprint.get("role_resources") or []
                                 if item.get("team_id") == team_id and item.get("role") == role), None)
                if resource is None:
                    resource = {"team_id": team_id, "role": role, "required_hours": 0,
                                "available_hours": None, "deficit_hours": None}
                    sprint.setdefault("role_resources", []).append(resource)
                resource["required_hours"] += float(hours)
    baseline = initiative_completion_count(approved.get("tasks") or [], catalog)
    completed = initiative_completion_count(result["tasks"], catalog)
    summary = result.setdefault("summary", {})
    summary["planned_tasks"] = sum(row.get("status") == "PLANNED" for row in result["tasks"])
    summary["done_tasks"] = sum(row.get("status") in {"DONE", "COMPLETED"} for row in result["tasks"])
    summary["moved_tasks"] = sum(row.get("status") == "MOVED" for row in result["tasks"])
    summary["unplannable_tasks"] = sum(row.get("status") == "UNPLANNABLE" for row in result["tasks"])
    summary["multi_sprint_tasks"] = sum(bool(row.get("multi_sprint")) for row in result["tasks"])
    summary["cross_team_tasks"] = sum(any(allocation.get("cross_team")
                                          for allocation in row.get("allocations") or [])
                                       for row in result["tasks"])
    summary["reassigned_tasks"] = sum(bool(row.get("team_reassigned")) for row in result["tasks"])
    summary["quarter_overflow_tasks"] = sum(bool(row.get("outside_quarter")) for row in result["tasks"])
    summary["total_tasks"] = len(result["tasks"])
    summary["completed_initiatives"] = completed
    plan.setdefault("summary", {}).update({key: summary[key] for key in (
        "planned_tasks", "done_tasks", "moved_tasks", "unplannable_tasks",
        "multi_sprint_tasks", "cross_team_tasks", "reassigned_tasks",
        "quarter_overflow_tasks", "total_tasks", "completed_initiatives")})
    initiative_members: dict[str, list[str]] = defaultdict(list)
    for source in catalog.get("tasks") or []:
        if (source.get("initiative_id") and source.get("task_id")
                and str(source.get("is_placeholder") or "").casefold() not in {"true", "1", "yes"}):
            initiative_members[str(source["initiative_id"])].append(str(source["task_id"]))
    current_initiatives = {str(row.get("initiative_id")): deepcopy(row)
                           for row in result.get("initiatives") or [] if row.get("initiative_id")}
    covered = {task_id for task_id, task in original.items()
               if task.get("status") in {"PLANNED", "DONE", "COMPLETED"}}
    covered.update(str(row["task_id"]) for row in catalog.get("tasks") or []
                   if row.get("task_id") and str(row.get("status") or "").casefold() == "done")
    result["initiatives"] = []
    for initiative_id, ids in sorted(initiative_members.items()):
        row = current_initiatives.get(initiative_id) or {"initiative_id": initiative_id}
        row.update({"task_ids": sorted(ids), "task_count": len(ids),
                    "planned_task_count": sum(task_id in covered for task_id in ids),
                    "completed_in_quarter": bool(ids) and all(task_id in covered for task_id in ids)})
        result["initiatives"].append(row)
    plan["initiatives"] = deepcopy(result["initiatives"])
    result["expansion_effect"] = {"added_tasks": len(additions),
                                  "added_initiatives": max(completed - baseline, 0)}
    state = result.setdefault("_planning_state", {})
    state["tables"] = deepcopy(catalog)
    result = project_management_kpis(result)
    return result
