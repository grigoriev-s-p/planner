"""Stable Base -> Best recommendations for one constructor planning session.

The quarter planner remains the only source of planning decisions. A single
organizationally optimized shadow plan is built when the draft is created.
Apply, Reject and Undo change Current Draft without moving the saved Best.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import logging
from time import perf_counter
from typing import Any, Iterable

from src.planning.ownership import evaluate_ownership_rows

from .constructor import _without_support_tasks
from .service import PlanningAPIService

LOGGER = logging.getLogger(__name__)
TYPE_ORDER = {"TASK_SWAP": 0, "TASK_REASSIGNMENT": 1, "ATOMIC_PACKAGE": 2, "CROSS_TEAM_HELP": 3}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kpi(plan: dict[str, Any]) -> float | None:
    value = plan.get("kpis", {}).get("expected_kpi", {}).get("completion_percent")
    return float(value) if value is not None else None


def _task(plan: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next((item for item in plan.get("tasks", []) if item.get("task_id") == task_id), {})


def _completion(item: dict[str, Any]) -> int | None:
    value = item.get("completion_sprint")
    return int(value) if value is not None else None


def _improves(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if after.get("status") == "PLANNED" and before.get("status") != "PLANNED":
        return True
    old, new = _completion(before), _completion(after)
    return bool(after.get("status") == "PLANNED" and old is not None and new is not None and new < old)


def _signature(action: dict[str, Any]) -> str:
    raw = json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:18]


def _atomic_actions(action: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one user-visible atomic package into planner configuration actions."""
    if action.get("type") == "TASK_SWAP":
        return [
            *[deepcopy(item) for item in action.get("changes", [])],
            *[deepcopy(item) for item in action.get("linked_actions", [])],
        ]
    primary = {key: deepcopy(value) for key, value in action.items() if key != "linked_actions"}
    return [primary, *[deepcopy(item) for item in action.get("linked_actions", [])]]


def _flatten(actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for action in actions for item in _atomic_actions(action)]


def _action_task_ids(action: dict[str, Any]) -> list[str]:
    return sorted({str(item["task_id"]) for item in _atomic_actions(action) if item.get("task_id")})


def _plan_fingerprint(plan: dict[str, Any]) -> str:
    tasks = []
    allocation_keys = (
        "engineer_id", "role", "source_team_id", "assigned_task_team_id",
        "sprint", "allocated_hours", "cross_team", "capacity_bucket_hours",
    )
    for item in sorted(plan.get("tasks", []), key=lambda row: row.get("task_id") or ""):
        allocations = sorted(
            ({key: row.get(key) for key in allocation_keys} for row in item.get("allocations", [])),
            key=lambda row: (row.get("sprint") or 0, row.get("engineer_id") or "", row.get("role") or ""),
        )
        tasks.append({
            "task_id": item.get("task_id"), "status": item.get("status"),
            "assigned_team_id": item.get("assigned_team_id"),
            "planned_sprints": item.get("planned_sprints", []),
            "completion_sprint": item.get("completion_sprint"),
            "sp_load_by_sprint": item.get("sp_load_by_sprint", {}),
            "allocations": allocations,
        })
    raw = json.dumps(tasks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _summary(plan: dict[str, Any], kpi: float | None = None) -> dict[str, Any]:
    planned = [item for item in plan.get("tasks", []) if item.get("status") == "PLANNED"]
    total: dict[float, int] = defaultdict(int)
    selected: dict[float, int] = defaultdict(int)
    for item in plan.get("tasks", []):
        rung = _number(item.get("rung"))
        total[rung] += 1
        if item.get("status") == "PLANNED":
            selected[rung] += 1
    return {
        "kpi": kpi,
        "planned_tasks": len(planned),
        "initiatives": sum(
            bool(item.get("completed_in_quarter")) for item in plan.get("initiatives", [])
        ),
        "rung_coverage": [
            {"rung": rung, "planned": selected[rung], "total": total[rung]}
            for rung in sorted(total, reverse=True)
        ],
    }


def _anchor_kpi_baseline(plan: dict[str, Any], baseline_plan: dict[str, Any]) -> None:
    """Keep Week-0 KPI denominators stable across draft variants."""

    current = plan.get("kpis") or {}
    baseline = baseline_plan.get("kpis") or {}
    expected = current.get("expected_kpi") or {}
    baseline_expected = baseline.get("expected_kpi") or {}
    denominator = baseline_expected.get("baseline_initiatives")
    completed = expected.get("completed_initiatives")
    if denominator is not None and completed is not None:
        expected["baseline_initiatives"] = denominator
        expected["incomplete_initiatives"] = max(float(denominator) - float(completed), 0.0)
        expected["completion_percent"] = (
            float(completed) / float(denominator) * 100 if float(denominator) else None
        )
    baseline_sprints = baseline.get("sprint_kpis") or []
    current_sprints = current.get("sprint_kpis") or []
    for current_row, baseline_row in zip(current_sprints, baseline_sprints):
        current_row["baseline_sp"] = baseline_row.get("baseline_sp")
    if "quarter_kpi" in baseline:
        current["quarter_kpi"] = deepcopy(baseline["quarter_kpi"])


class RecommendationDraft:
    """Mutable review state with immutable Base and Best plans."""

    def __init__(self, payload: dict[str, Any]) -> None:
        context = payload.pop("_recommendation_context", None) or {}
        self.base_plan = deepcopy(payload)
        self.extra_payload = {
            key: deepcopy(value) for key, value in payload.items()
            if key in {"catalog", "uploaded_dataset"}
        }
        self.plan_version = self.base_plan.get("plan", {}).get("quarter", {}).get("plan_version") or "constructor-current-draft"
        self.tables = deepcopy(context.get("tables") or {})
        self.support_task_ids = tuple(context.get("support_task_ids") or ())
        self.accepted: list[dict[str, Any]] = []
        self.rejected: set[str] = set()
        self.current_plan = deepcopy(self.base_plan)
        self.optimized_plan = deepcopy(self.base_plan)
        self.optimized_actions: list[dict[str, Any]] = []
        self._all_recommendations: dict[str, dict[str, Any]] = {}
        self.recommendations: dict[str, dict[str, Any]] = {}
        self.error: str | None = None
        self.revision = 0
        self.recommendations_revision: int | None = 0
        self.optimization_status = "ready"
        self.last_changed_tasks: list[dict[str, Any]] = []
        self.timings_ms: dict[str, float] = {}
        self._plan_cache: dict[str, dict[str, Any]] = {}
        self._shadow_cache: dict[str, dict[str, Any]] = {}
        self.base_fingerprint = _plan_fingerprint(self.base_plan)
        self.best_fingerprint = self.base_fingerprint
        if self.tables:
            self._initialize_best()

    @staticmethod
    def _actions_key(actions: list[dict[str, Any]]) -> str:
        return hashlib.sha256("|".join(sorted(_signature(action) for action in actions)).encode()).hexdigest()

    def _tables_for(self, actions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        tables = deepcopy(self.tables)
        assignments = {
            action["task_id"]: action["team_id"] for action in _flatten(actions)
            if action.get("type") == "TASK_REASSIGNMENT"
        }
        for row in tables.get("tasks", []):
            if row.get("task_id") in assignments:
                row["team_id"] = assignments[row["task_id"]]
        return tables

    def _plan(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        cache_key = self._actions_key(actions)
        if cache_key in self._plan_cache:
            self.timings_ms["plan_cache_hit"] = self.timings_ms.get("plan_cache_hit", 0) + 1
            return deepcopy(self._plan_cache[cache_key])
        atomic = _flatten(actions)
        cross_task_ids = {item["task_id"] for item in atomic if item.get("type") == "CROSS_TEAM_HELP"}
        started = perf_counter()
        service = PlanningAPIService.from_tables(
            self._tables_for(actions), plan_version=self.plan_version,
            planner_options={
                "allow_team_reassignment": False,
                "allow_cross_team": bool(cross_task_ids),
                "cross_team_task_ids": cross_task_ids,
            },
        )
        self.timings_ms["planner_run"] = round((perf_counter() - started) * 1000, 2)
        result = _without_support_tasks(service.dashboard, self.support_task_ids)
        _anchor_kpi_baseline(result, self.base_plan)
        self._plan_cache[cache_key] = deepcopy(result)
        return result

    def _shadow(self) -> dict[str, Any]:
        if "base" in self._shadow_cache:
            return deepcopy(self._shadow_cache["base"])
        service = PlanningAPIService.from_tables(
            self._tables_for([]), plan_version=self.plan_version,
            planner_options={"allow_team_reassignment": True, "allow_cross_team": True},
        )
        result = _without_support_tasks(service.dashboard, self.support_task_ids)
        self._shadow_cache["base"] = deepcopy(result)
        return result

    @staticmethod
    def _protect_higher_rungs(before: dict[str, Any], after: dict[str, Any], rung: float) -> bool:
        before_high = {
            item.get("task_id") for item in before.get("tasks", [])
            if _number(item.get("rung")) > rung and item.get("status") == "PLANNED"
        }
        after_high = {
            item.get("task_id") for item in after.get("tasks", [])
            if _number(item.get("rung")) > rung and item.get("status") == "PLANNED"
        }
        return before_high.issubset(after_high)

    def _shadow_actions(self, shadow: dict[str, Any]) -> list[dict[str, Any]]:
        reassignments: list[dict[str, Any]] = []
        cross_task_ids: set[str] = set()
        for before in self.base_plan.get("tasks", []):
            if before.get("status") == "DONE":
                continue
            task_id = before.get("task_id")
            after = _task(shadow, task_id)
            if not task_id or not after:
                continue
            source = before.get("assigned_team_id") or before.get("original_team_id")
            target = after.get("assigned_team_id")
            if target and target != source:
                reassignments.append({
                    "type": "TASK_REASSIGNMENT", "task_id": task_id,
                    "team_id": target, "source_team_id": source,
                })
            if any(row.get("cross_team") for row in after.get("allocations", [])):
                cross_task_ids.add(str(task_id))

        packages: list[dict[str, Any]] = []
        used: set[int] = set()
        for index, left in enumerate(reassignments):
            if index in used:
                continue
            partner = next((
                i for i, right in enumerate(reassignments[index + 1:], index + 1)
                if i not in used and left.get("source_team_id") == right.get("team_id")
                and left.get("team_id") == right.get("source_team_id")
            ), None)
            if partner is None:
                action = deepcopy(left)
                if left["task_id"] in cross_task_ids:
                    action["linked_actions"] = [{"type": "CROSS_TEAM_HELP", "task_id": left["task_id"]}]
                    cross_task_ids.remove(left["task_id"])
                packages.append(action)
                used.add(index)
                continue
            right = reassignments[partner]
            changes = [deepcopy(left), deepcopy(right)]
            for task_id in sorted({left["task_id"], right["task_id"]} & cross_task_ids):
                changes.append({"type": "CROSS_TEAM_HELP", "task_id": task_id})
                cross_task_ids.remove(task_id)
            packages.append({"type": "TASK_SWAP", "changes": changes})
            used.update({index, partner})
        packages.extend({"type": "CROSS_TEAM_HELP", "task_id": task_id} for task_id in sorted(cross_task_ids))
        return sorted(packages, key=lambda action: (
            TYPE_ORDER.get(action.get("type"), 99),
            -max((_number(_task(self.base_plan, task_id).get("rung")) for task_id in _action_task_ids(action)), default=0),
            min(_action_task_ids(action), default=""),
        ))

    @staticmethod
    def _link_actions(primary: dict[str, Any], dependent: dict[str, Any]) -> dict[str, Any]:
        """Keep planner actions that only work together in one user action."""
        package = deepcopy(primary)
        linked = package.setdefault("linked_actions", [])
        existing = {_signature(item) for item in _atomic_actions(package)}
        for item in _atomic_actions(dependent):
            if _signature(item) not in existing:
                linked.append(deepcopy(item))
                existing.add(_signature(item))
        return package

    def _action_affinity(self, left: dict[str, Any], right: dict[str, Any]) -> tuple[int, int]:
        """Prefer the recommendation that actually shares a dependency/resource."""
        left_ids, right_ids = set(_action_task_ids(left)), set(_action_task_ids(right))
        dependency_pairs = {
            (str(row.get("blocking_task_id")), str(row.get("blocked_task_id")))
            for row in self.tables.get("task_dependencies", [])
        }
        dependency = int(any(
            (left_id, right_id) in dependency_pairs or (right_id, left_id) in dependency_pairs
            for left_id in left_ids for right_id in right_ids
        ))

        def engineers(task_ids: set[str]) -> set[str]:
            return {
                str(row.get("engineer_id"))
                for task_id in task_ids
                for row in _task(self.optimized_plan, task_id).get("allocations", [])
                if row.get("engineer_id")
            }

        return dependency, len(engineers(left_ids) & engineers(right_ids))

    @staticmethod
    def _action_changed(action: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> bool:
        fields = ("status", "assigned_team_id", "planned_sprints", "completion_sprint", "allocations")
        return any(
            any(_task(before, task_id).get(field) != _task(after, task_id).get(field) for field in fields)
            for task_id in _action_task_ids(action)
        )

    def _recommendation_specs(
        self, actions: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Keep independent actions separate and package only proven joint changes."""
        specs: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for action in actions:
            recommendation = self._recommendation(action, self.base_plan, self._plan([action]))
            if recommendation:
                specs.append({"node": _signature(action), "action": deepcopy(action), "requires": [], "recommendation": recommendation})
            else:
                pending.append(deepcopy(action))

        def ordered_specs(action: dict[str, Any]) -> list[dict[str, Any]]:
            return sorted(
                specs,
                key=lambda spec: self._action_affinity(spec["action"], action),
                reverse=True,
            )

        while pending:
            progress = False
            for action in list(pending):
                # A prerequisite is not an atomic package: reveal this action
                # separately after the required recommendation was accepted.
                for prerequisite in ordered_specs(action):
                    before = self._plan([prerequisite["action"]])
                    after = self._plan([prerequisite["action"], action])
                    recommendation = self._recommendation(action, before, after)
                    if recommendation:
                        specs.append({
                            "node": _signature(action), "action": deepcopy(action),
                            "requires": [prerequisite["node"]], "recommendation": recommendation,
                        })
                        pending.remove(action)
                        progress = True
                        break
                if progress:
                    break

                # A support action that has no useful effect on its own belongs
                # to the smallest recommendation whose simulation it changes.
                for prerequisite in ordered_specs(action):
                    without_support = self._plan([prerequisite["action"]])
                    package = self._link_actions(prerequisite["action"], action)
                    with_support = self._plan([package])
                    recommendation = self._recommendation(package, self.base_plan, with_support)
                    if self._action_changed(action, without_support, with_support) and recommendation:
                        prerequisite["action"] = package
                        prerequisite["recommendation"] = recommendation
                        pending.remove(action)
                        progress = True
                        break
                if progress:
                    break

            if progress:
                continue

            # Neither change is feasible/useful alone in the same state. Only
            # this case is exposed as a genuine atomic package.
            packaged = False
            for index, left in enumerate(pending):
                for right in pending[index + 1:]:
                    package = self._link_actions(left, right)
                    recommendation = self._recommendation(
                        package, self.base_plan, self._plan([package])
                    )
                    if recommendation:
                        specs.append({
                            "node": _signature(package), "action": package,
                            "requires": [], "recommendation": recommendation,
                        })
                        pending.remove(left)
                        pending.remove(right)
                        packaged = True
                        break
                if packaged:
                    break
            if not packaged:
                raise ValueError("Не удалось разложить Best Plan на независимые рекомендации.")

        id_by_node = {
            spec["node"]: spec["recommendation"]["recommendation_id"] for spec in specs
        }
        recommendations = {}
        packages = []
        for spec in specs:
            recommendation = spec["recommendation"]
            recommendation["requires"] = [id_by_node[node] for node in spec["requires"]]
            recommendations[recommendation["recommendation_id"]] = recommendation
            packages.append(spec["action"])
        return packages, recommendations

    @staticmethod
    def _changed_tasks(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
        changes = []
        previous_by_id = {item.get("task_id"): item for item in before.get("tasks", [])}
        for current in after.get("tasks", []):
            task_id = current.get("task_id")
            previous = previous_by_id.get(task_id, {})
            old, new = _completion(previous), _completion(current)
            old_cross = any(row.get("cross_team") for row in previous.get("allocations", []))
            new_cross = any(row.get("cross_team") for row in current.get("allocations", []))
            team_changed = previous.get("assigned_team_id") != current.get("assigned_team_id")
            entered = previous.get("status") != "PLANNED" and current.get("status") == "PLANNED"
            if not (entered or old != new or old_cross != new_cross or team_changed):
                continue
            change = "added_to_plan" if entered else "moved" if old != new else "cross_team_added" if new_cross and not old_cross else "reassigned"
            changes.append({"task_id": task_id, "change": change, "from_sprint": old, "to_sprint": new, "rung": current.get("rung")})
        return sorted(changes, key=lambda item: (-_number(item.get("rung")), item.get("task_id") or ""))

    @staticmethod
    def _all_allocations(plan: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"task_id": task.get("task_id"), **allocation}
            for task in plan.get("tasks", []) for allocation in task.get("allocations", [])
        ]

    @staticmethod
    def _engineer_name(plan: dict[str, Any], engineer_id: str) -> str:
        engineer = next((item for item in plan.get("engineers", []) if item.get("engineer_id") == engineer_id), {})
        return str(engineer.get("name") or engineer_id)

    @staticmethod
    def _task_ownership(task: dict[str, Any]) -> dict[str, Any]:
        owner_team = str(task.get("assigned_team_id") or "").strip()
        if not owner_team:
            return {}
        calculated = evaluate_ownership_rows(
            owner_team, task.get("allocations", [])
        ).to_dict()
        # Recalculate from the final allocation even when the API DTO already
        # contains evidence, so recommendation validation never trusts stale
        # ownership metadata.
        return calculated

    def _cross_team_evidence(self, before: dict[str, Any], after: dict[str, Any], task_ids: list[str]) -> list[dict[str, Any]]:
        allocations = self._all_allocations(after)
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in allocations:
            if row.get("task_id") in task_ids and row.get("cross_team"):
                key = (
                    row.get("task_id"), row.get("engineer_id"), row.get("role"),
                    row.get("source_team_id"), row.get("assigned_task_team_id"), row.get("sprint"),
                )
                groups[key].append(row)
        output = []
        for key, rows in sorted(groups.items(), key=lambda item: tuple(str(value) for value in item[0])):
            task_id, engineer_id, role, source_team, target_team, sprint = key
            capacity = max(_number(row.get("capacity_bucket_hours")) for row in rows)
            recommended = sum(_number(row.get("allocated_hours")) for row in rows)
            own = sum(
                _number(row.get("allocated_hours")) for row in allocations
                if row.get("engineer_id") == engineer_id and row.get("source_team_id") == source_team
                and row.get("sprint") == sprint and not row.get("cross_team")
                and row.get("assigned_task_team_id") == source_team
            )
            total = sum(
                _number(row.get("allocated_hours")) for row in allocations
                if row.get("engineer_id") == engineer_id and row.get("source_team_id") == source_team
                and row.get("sprint") == sprint
            )
            source_before = {
                item.get("task_id") for item in before.get("tasks", [])
                if item.get("assigned_team_id") == source_team and item.get("status") == "PLANNED"
            }
            source_after = {
                item.get("task_id") for item in after.get("tasks", [])
                if item.get("assigned_team_id") == source_team and item.get("status") == "PLANNED"
            }
            ownership = self._task_ownership(_task(after, str(task_id)))
            output.append({
                "engineer_id": engineer_id,
                "engineer_name": self._engineer_name(after, str(engineer_id)),
                "role": role, "source_team": source_team, "target_team": target_team,
                "target_task": task_id, "sprint": sprint, "help_hours": recommended,
                "ownership": ownership,
                "engineer_capacity": {
                    "sprint": sprint, "source_team": source_team,
                    "capacity_hours": capacity, "already_allocated_hours": own,
                    "recommended_hours": recommended, "resulting_total_hours": total,
                    "remaining_hours": max(capacity - total, 0.0),
                    "fits_capacity": total <= capacity + 1e-9,
                    "source_team_plan_stays_feasible": source_before.issubset(source_after),
                },
            })
        return output

    @staticmethod
    def _source_bottlenecks(task: dict[str, Any]) -> list[str]:
        messages = []
        for block in task.get("reason_blocks", []):
            title, details = block.get("title"), block.get("details") or []
            if title:
                messages.append(f"{title}{': ' + ', '.join(map(str, details)) if details else ''}")
        if not messages:
            messages = [reason.get("message") for reason in task.get("reasons", []) if reason.get("message")]
        return messages or ["В исходной команде задача не помещается в текущий квартальный план."]

    def _role_capacity(self, plan: dict[str, Any], task_id: str, team_id: str, sprint: int | None) -> tuple[dict[str, float], dict[str, float]]:
        task = _task(plan, task_id)
        required = {str(role): _number(hours) for role, hours in (task.get("role_requirements") or {}).items()}
        if sprint is None:
            return required, {role: 0.0 for role in required}
        profiles: dict[tuple[str, str, str], float] = {}
        for row in self.tables.get("engineers", []):
            if row.get("team_id") != team_id or not row.get("engineer_id") or not row.get("role"):
                continue
            key = (str(row["engineer_id"]), str(team_id), str(row["role"]))
            profiles[key] = max(profiles.get(key, 0.0), 80.0 * _number(row.get("capacity_rate")))
        capacity: dict[str, float] = defaultdict(float)
        for (_, _, role), hours in profiles.items():
            capacity[role] += hours
        used: dict[str, float] = defaultdict(float)
        for row in self._all_allocations(plan):
            if row.get("task_id") != task_id and row.get("assigned_task_team_id") == team_id and row.get("sprint") == sprint:
                used[str(row.get("role"))] += _number(row.get("allocated_hours"))
        return required, {role: max(capacity.get(role, 0.0) - used.get(role, 0.0), 0.0) for role in required}

    def _reassignment_evidence(self, before: dict[str, Any], after: dict[str, Any], action: dict[str, Any]) -> list[dict[str, Any]]:
        output = []
        for change in _atomic_actions(action):
            if change.get("type") != "TASK_REASSIGNMENT":
                continue
            task_id, target_team = str(change["task_id"]), str(change["team_id"])
            old_task, new_task = _task(before, task_id), _task(after, task_id)
            sprint = _completion(new_task)
            team = next((item for item in after.get("teams", []) if item.get("team_id") == target_team), {})
            sp_row = (team.get("sp_by_sprint") or {}).get(str(sprint), {}) if sprint else {}
            task_sp_load = _number((new_task.get("sp_load_by_sprint") or {}).get(str(sprint)))
            required, available = self._role_capacity(after, task_id, target_team, sprint)
            ownership = self._task_ownership(new_task)
            enters = old_task.get("status") != "PLANNED" and new_task.get("status") == "PLANNED"
            output.append({
                "task_id": task_id,
                "from_team": old_task.get("assigned_team_id") or old_task.get("original_team_id"),
                "to_team": target_team, "before_sprint": _completion(old_task), "after_sprint": sprint,
                "task_sp": _number(new_task.get("story_points")),
                "source_bottlenecks": self._source_bottlenecks(old_task),
                "target_capacity": {
                    "sprint": sprint, "available_sp": _number(sp_row.get("remaining")) + task_sp_load,
                    "task_sp": _number(new_task.get("story_points")),
                    "resulting_used_sp": _number(sp_row.get("used")), "capacity_sp": _number(sp_row.get("capacity")),
                    "role_hours_required": required, "role_hours_available": available,
                    "fits_capacity": (
                        new_task.get("status") == "PLANNED"
                        and bool(ownership.get("valid", True))
                    ),
                },
                "ownership": ownership,
                "workload": {
                    "total_hours": ownership.get("total_hours", 0.0),
                    "owner_hours": ownership.get("owner_hours", 0.0),
                    "external_hours": ownership.get("external_hours", 0.0),
                    "owner_engineer_count": ownership.get("owner_engineer_count", 0),
                    "external_engineer_count": ownership.get("external_engineer_count", 0),
                    "owner_share_percent": ownership.get("owner_share_percent"),
                },
                "effect": "Задача войдёт в квартал." if enters else f"Завершение: Sprint {_completion(old_task)} → Sprint {sprint}.",
            })
        return output

    def _recommendation(self, action: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
        task_ids = _action_task_ids(action)
        atomic_actions = _atomic_actions(action)
        old_tasks, new_tasks = ([_task(plan, task_id) for task_id in task_ids] for plan in (before, after))
        if any(
            task.get("status") == "PLANNED"
            and task.get("allocations")
            and not self._task_ownership(task).get("valid", False)
            for task in new_tasks
        ):
            return None
        if not any(_improves(left, right) for left, right in zip(old_tasks, new_tasks)):
            return None
        rung = max((_number(item.get("rung")) for item in old_tasks), default=0.0)
        if not self._protect_higher_rungs(before, after, rung):
            return None
        cross = self._cross_team_evidence(before, after, task_ids)
        if any(not row["engineer_capacity"]["fits_capacity"] for row in cross):
            return None
        reassignment = self._reassignment_evidence(before, after, action)
        action_type = str(action.get("type"))
        is_atomic_package = action_type != "TASK_SWAP" and len(task_ids) > 1
        display_type = "ATOMIC_PACKAGE" if is_atomic_package else action_type
        if action_type == "CROSS_TEAM_HELP" and not cross:
            return None
        title = {
            "TASK_SWAP": "Обменять задачи между командами",
            "TASK_REASSIGNMENT": "Передать задачу другой команде",
            "CROSS_TEAM_HELP": "Подключить специалиста другой команды",
        }.get(action_type, "Улучшить распределение задач")
        if is_atomic_package:
            title = f"Совместное перераспределение {len(task_ids)} задач"
        enters = [right.get("task_id") for left, right in zip(old_tasks, new_tasks) if left.get("status") != "PLANNED" and right.get("status") == "PLANNED"]
        moved = [(_completion(left), _completion(right)) for left, right in zip(old_tasks, new_tasks) if _completion(left) is not None and _completion(right) is not None and _completion(right) < _completion(left)]
        summary = f"+{len(enters)} задач(а) войдёт в квартал." if enters else f"Завершение: Sprint {moved[0][0]} → Sprint {moved[0][1]}." if moved else "Устранено ресурсное ограничение задачи."
        recommendation_id = _signature(action)
        changed = [
            item for item in self._changed_tasks(before, after)
            if item.get("task_id") in task_ids
        ]
        changed_ids = {item["task_id"] for item in changed}
        task_states = [deepcopy(item) for item in after.get("tasks", []) if item.get("task_id") in changed_ids]
        primary = old_tasks[0] if old_tasks else {}
        first_cross, first_reassign = (cross or [{}])[0], (reassignment or [{}])[0]
        return {
            "recommendation_id": recommendation_id, "type": display_type,
            "group_id": "task-allocation" if display_type != "CROSS_TEAM_HELP" else "cross-team-help",
            "state": "available", "title": title, "tasks": task_ids,
            "task_id": task_ids[0] if task_ids else None, "rung": rung,
            "engineer_id": first_cross.get("engineer_id"),
            "source_team_id": first_cross.get("source_team") or first_reassign.get("from_team"),
            "target_team_id": first_cross.get("target_team") or first_reassign.get("to_team"),
            "current_team_id": first_reassign.get("from_team"),
            "proposed_team_id": first_reassign.get("to_team"),
            "sprint": first_cross.get("sprint") or first_reassign.get("after_sprint"),
            "hours": first_cross.get("help_hours"),
            "reason": "Изменение получено из сохранённого выполнимого лучшего варианта.",
            "details": summary,
            "effect": {
                "before_sprint": _completion(primary),
                "after_sprint": _completion(new_tasks[0]) if new_tasks else None,
                "enters_quarter": bool(enters), "tasks_entering_quarter": enters, "summary": summary,
            },
            "engineer_help": cross, "task_reassignment": reassignment,
            "is_atomic_package": is_atomic_package,
            "package_actions": [{
                "type": item.get("type"), "task_id": item.get("task_id"),
                "source_team_id": item.get("source_team_id"), "team_id": item.get("team_id"),
            } for item in atomic_actions] if is_atomic_package else [],
            "plan_delta": {
                "base_revision": 0, "base_fingerprint": self.base_fingerprint,
                "recommendation_signature": recommendation_id,
                "changed_tasks": changed, "task_states": task_states,
                "resulting_summary": _summary(after, _kpi(after)),
            },
            "action": deepcopy(action),
        }

    def _initialize_best(self) -> None:
        total_started = perf_counter()
        try:
            started = perf_counter()
            shadow = self._shadow()
            self.timings_ms["shadow_plan"] = round((perf_counter() - started) * 1000, 2)
            raw_actions = self._shadow_actions(shadow)
            self.optimized_plan = self._plan(raw_actions)
            self.optimized_actions, recommendations = self._recommendation_specs(raw_actions)
            self.best_fingerprint = _plan_fingerprint(self.optimized_plan)
            started = perf_counter()
            self._all_recommendations = recommendations
            self._refresh_available()
            self.timings_ms["recommendation_precompute"] = round((perf_counter() - started) * 1000, 2)
            self.error = None
        except Exception as error:
            self.error = str(error)
            self.optimization_status = "error"
            self._all_recommendations = {}
            self.recommendations = {}
        finally:
            self.timings_ms["optimization_total"] = round((perf_counter() - total_started) * 1000, 2)
            LOGGER.info("constructor stable optimization timings_ms=%s", self.timings_ms)

    def _refresh_available(self) -> None:
        accepted_ids = {item["change_id"] for item in self.accepted}
        accepted_actions = self._accepted_actions()
        available = []
        for recommendation_id, stored in self._all_recommendations.items():
            if (
                recommendation_id in accepted_ids
                or recommendation_id in self.rejected
                or not set(stored.get("requires", [])).issubset(accepted_ids)
            ):
                continue
            # Older persisted/test recommendation records may not contain the
            # executable action. They can still be listed, but cannot be
            # recalculated against the current draft.
            if "action" not in stored:
                available.append(deepcopy(stored))
                continue
            candidate = self._plan([*accepted_actions, stored["action"]])
            current = self._recommendation(stored["action"], self.current_plan, candidate)
            if current is None:
                continue
            current["requires"] = deepcopy(stored.get("requires", []))
            available.append(current)
        available.sort(key=lambda item: (
            TYPE_ORDER.get(item.get("type"), 99), -_number(item.get("rung")),
            _task(self.base_plan, item.get("task_id") or "").get("baseline_start_date") or "9999-12-31",
            item.get("task_id") or "",
        ))
        self.recommendations = {item["recommendation_id"]: item for item in available}

    def _accepted_actions(self) -> list[dict[str, Any]]:
        return [item["action"] for item in self.accepted]

    def _set_current_from_actions(self, previous: dict[str, Any]) -> None:
        accepted_ids = {item["change_id"] for item in self.accepted}
        best_ids = {_signature(action) for action in self.optimized_actions}
        if best_ids and accepted_ids == best_ids:
            self.current_plan = deepcopy(self.optimized_plan)
        elif not self.accepted:
            self.current_plan = deepcopy(self.base_plan)
        else:
            self.current_plan = self._plan(self._accepted_actions())
        self.revision += 1
        self.last_changed_tasks = self._changed_tasks(previous, self.current_plan)
        self.recommendations_revision = self.revision
        self.optimization_status = "ready"
        self._refresh_available()

    def refresh_optimization(self, expected_revision: int | None = None) -> None:
        """Compatibility endpoint: the immutable Best needs no refresh."""
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("stale revision")
        self.optimization_status = "ready"
        self.recommendations_revision = self.revision

    def snapshot(self, draft_id: str) -> dict[str, Any]:
        payload = deepcopy(self.current_plan)
        payload.update(deepcopy(self.extra_payload))
        base_kpi, current_kpi, best_kpi = _kpi(self.base_plan), _kpi(self.current_plan), _kpi(self.optimized_plan)
        current_fingerprint = _plan_fingerprint(self.current_plan)
        payload["draft"] = {**payload.get("draft", {}), "id": draft_id, "storage": "server_memory", "server_persisted": True, "approved": False}
        state_fingerprint = hashlib.sha256(json.dumps({
            "accepted": sorted(item["change_id"] for item in self.accepted),
            "rejected": sorted(self.rejected),
        }, sort_keys=True).encode()).hexdigest()[:20]
        payload["optimization"] = {
            "objective": "hard_constraints_then_rung_then_completed_tasks",
            "kpi_is_objective": False, "kpi_unit": "percent",
            "base_kpi_percent": base_kpi, "current_kpi_percent": current_kpi,
            "optimized_kpi_percent": best_kpi,
            "kpi_delta": current_kpi - base_kpi if current_kpi is not None and base_kpi is not None else None,
            "base_summary": _summary(self.base_plan, base_kpi),
            "current_summary": _summary(self.current_plan, current_kpi),
            "optimized_summary": _summary(self.optimized_plan, best_kpi),
            "optimized_actions": deepcopy(self.optimized_actions),
            "accepted_changes": deepcopy(self.accepted),
            "rejected_recommendation_ids": sorted(self.rejected),
            "recommendations": list(self.recommendations.values()),
            "recommendation_error": self.error, "status": self.optimization_status,
            "draft_revision": self.revision, "draft_fingerprint": state_fingerprint,
            "input_plan_fingerprint": self.base_fingerprint,
            "best_plan_fingerprint": self.best_fingerprint,
            "current_plan_fingerprint": current_fingerprint,
            "best_selected": current_fingerprint == self.best_fingerprint,
            "recommendations_revision": self.recommendations_revision,
            "needs_refresh": False,
            "changed_tasks": deepcopy(self.last_changed_tasks),
            "current_changes": self._changed_tasks(self.base_plan, self.current_plan),
            "timings_ms": deepcopy(self.timings_ms),
        }
        return payload

    def apply(self, recommendation_id: str) -> None:
        recommendation = self.recommendations.get(recommendation_id)
        if recommendation is None:
            raise KeyError(recommendation_id)
        previous = self.current_plan
        started = perf_counter()
        candidate = self._plan([*self._accepted_actions(), recommendation["action"]])
        current_recommendation = self._recommendation(
            recommendation["action"], self.current_plan, candidate
        )
        if current_recommendation is None:
            raise KeyError(recommendation_id)
        current_recommendation["requires"] = deepcopy(recommendation.get("requires", []))
        recommendation = current_recommendation
        self.timings_ms["atomic_simulation"] = round((perf_counter() - started) * 1000, 2)
        label = recommendation["title"]
        if recommendation["type"] == "CROSS_TEAM_HELP":
            row = (recommendation.get("engineer_help") or [{}])[0]
            label = f"{row.get('engineer_name') or 'Специалист'} помогает {row.get('target_team') or 'команде'}"
        elif recommendation["type"] == "TASK_REASSIGNMENT":
            row = (recommendation.get("task_reassignment") or [{}])[0]
            label = f"{row.get('task_id')} → {row.get('to_team')}"
        self.accepted.append({
            "change_id": recommendation_id, "action": deepcopy(recommendation["action"]),
            "title": label, "task_id": recommendation.get("task_id"),
            "tasks": deepcopy(recommendation.get("tasks", [])),
            "summary": recommendation["effect"]["summary"],
        })
        self.current_plan = candidate
        self.revision += 1
        self.last_changed_tasks = self._changed_tasks(previous, candidate)
        self.recommendations_revision = self.revision
        self._refresh_available()

    def reject(self, recommendation_id: str) -> None:
        if recommendation_id not in self.recommendations:
            raise KeyError(recommendation_id)
        self.rejected.add(recommendation_id)
        self.revision += 1
        self.last_changed_tasks = []
        self.recommendations_revision = self.revision
        self._refresh_available()

    def apply_all(self) -> None:
        previous = self.current_plan
        final_changes = {
            item["task_id"]: item
            for item in self._changed_tasks(self.base_plan, self.optimized_plan)
        }
        self.accepted = []
        for action in self.optimized_actions:
            change_id = _signature(action)
            recommendation = self._all_recommendations.get(change_id, {})
            label = recommendation.get("title") or "Изменение лучшего варианта"
            if recommendation.get("type") == "CROSS_TEAM_HELP":
                row = (recommendation.get("engineer_help") or [{}])[0]
                label = f"{row.get('engineer_name') or row.get('engineer_id') or 'Специалист'} помогает {row.get('target_team') or 'команде'}"
            elif recommendation.get("type") == "TASK_REASSIGNMENT":
                row = (recommendation.get("task_reassignment") or [{}])[0]
                label = f"{row.get('task_id') or recommendation.get('task_id')} → {row.get('to_team') or 'другая команда'}"
            task_ids = recommendation.get("tasks") or _action_task_ids(action)
            action_changes = [final_changes[task_id] for task_id in task_ids if task_id in final_changes]
            summary = recommendation.get("effect", {}).get("summary") or "Изменение из сохранённого лучшего варианта."
            if len(action_changes) == 1:
                change = action_changes[0]
                if change["change"] == "added_to_plan":
                    summary = "Задача добавлена в квартальный план."
                elif change["change"] == "moved":
                    summary = f"Перенос: Sprint {change['from_sprint']} → Sprint {change['to_sprint']}."
                elif change["change"] == "cross_team_added":
                    summary = "К задаче подключена помощь другой команды."
                elif change["change"] == "reassigned":
                    summary = "Задача перераспределена между командами."
            self.accepted.append({
                "change_id": change_id, "action": deepcopy(action),
                "title": label,
                "task_id": recommendation.get("task_id") or min(task_ids, default=None),
                "tasks": deepcopy(task_ids),
                "summary": summary,
                "task_changes": deepcopy(action_changes),
            })
        self.rejected.clear()
        self.current_plan = deepcopy(self.optimized_plan)
        self.revision += 1
        self.last_changed_tasks = self._changed_tasks(previous, self.current_plan)
        self.recommendations_revision = self.revision
        self._refresh_available()

    def undo(self, change_id: str) -> None:
        original = len(self.accepted)
        removed = {change_id}
        while True:
            dependent = {
                item["change_id"] for item in self.accepted
                if set(self._all_recommendations.get(item["change_id"], {}).get("requires", [])) & removed
            }
            if dependent.issubset(removed):
                break
            removed.update(dependent)
        self.accepted = [item for item in self.accepted if item["change_id"] not in removed]
        if len(self.accepted) == original:
            raise KeyError(change_id)
        self._set_current_from_actions(self.current_plan)

    def reset(self) -> None:
        previous = self.current_plan
        self.accepted = []
        self.rejected = set()
        self.current_plan = deepcopy(self.base_plan)
        self.revision += 1
        self.last_changed_tasks = self._changed_tasks(previous, self.current_plan)
        self.recommendations_revision = self.revision
        self._refresh_available()
