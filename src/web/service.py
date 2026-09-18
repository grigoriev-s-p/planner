"""Stable API DTOs built from processed data and the quarter planner.

The browser never reads CSV files or reconstructs planning decisions.  This
module is the boundary between pandas/domain objects and JSON consumers.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from src.bus_factor import BusFactorService
from src.planning import FeasibilityContext, build_quarter_plan
from src.planning.capacity import calculate_team_capacity
from src.planning.ownership import evaluate_ownership_rows
from src.replanning import (
    ActualResults,
    PlanItem,
    PlanState,
    QuarterPlan,
    RoleHoursAllocation,
    SprintAllocation,
    calculate_expected_quarter_kpi,
    calculate_pi_predictability_fact,
    calculate_sprint_say_do,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TABLE_FILES = {
    "tasks": "tasks.csv",
    "task_role_effort": "task_role_effort.csv",
    "task_dependencies": "task_dependencies.csv",
    "engineers": "engineers.csv",
    "engineer_skills": "engineer_skills.csv",
    "team_history": "team_history.csv",
}


class ServiceDataError(RuntimeError):
    """Processed data cannot be loaded into the product API."""


def _missing(value: Any) -> bool:
    return value is None or (
        isinstance(value, float) and (math.isnan(value) or not math.isfinite(value))
    )


def _clean(value: Any) -> Any:
    """Recursively convert pandas/numpy values to JSON-compatible primitives."""

    if _missing(value):
        return None
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value]
    if hasattr(value, "item"):
        return _clean(value.item())
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    return value


def _text(value: Any) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    text = str(cleaned).strip()
    return text or None


def _number(value: Any) -> float | None:
    if _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_table(directory: Path, table_name: str) -> list[dict[str, Any]]:
    path = directory / TABLE_FILES[table_name]
    if not path.is_file():
        raise ServiceDataError(f"Missing processed file: {path.name}")
    try:
        frame = pd.read_csv(path)
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        raise ServiceDataError(f"Cannot read {path.name}: {error}") from error
    return [_clean(row) for row in frame.to_dict("records")]


STATUS_LABELS = {
    "DONE": "Завершена",
    "PLANNED": "Запланирована",
    "MOVED": "Перенесена",
    "UNPLANNABLE": "Невыполнима",
}

RISK_PRESENTATION = {
    "MULTI_SPRINT": ("Сроки", "Работа в нескольких спринтах"),
    "CROSS_TEAM_DEPENDENCY": ("Ресурсы", "Зависимость от другой команды"),
    "QUARTER_OVERFLOW": ("Сроки", "Выход за пределы квартала"),
    "RESOURCE_BOTTLENECK": ("Ресурсы", "Дефицит ресурса"),
}


def _human_reason_blocks(reasons: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Presentation DTO for product users; raw planner codes remain internal."""
    groups: dict[str, dict[str, Any]] = {}
    order = {"dependency": 0, "specialists": 1, "engineer_hours": 2, "team_capacity": 3, "ownership": 4, "quarter": 5, "other": 6}
    for reason in reasons:
        code = str(reason.get("code") or "")
        details = reason.get("details") or {}
        if code in {"DEPENDENCY_NOT_COMPLETED", "UNKNOWN_DEPENDENCY_DATE", "MISSING_DEPENDENCY_REFERENCE", "DEPENDENCY_DELAY"}:
            key, title = "dependency", "Задача заблокирована зависимостью"
            values = [f"Сначала должна быть завершена {details.get('blocking_task_id')}."] if details.get("blocking_task_id") else ["Ожидается завершение связанной задачи."]
        elif code in {"MISSING_SPECIALIST", "NO_AVAILABLE_ROLE_SPECIALIST", "INSUFFICIENT_SKILL_CAPACITY"}:
            key, title = "specialists", "Не хватает специалистов"
            values = [str(details.get("role"))] if details.get("role") else ["Нет специалиста с нужными компетенциями."]
        elif code in {"INSUFFICIENT_ROLE_CAPACITY", "INSUFFICIENT_ENGINEER_CAPACITY"}:
            key, title = "engineer_hours", "Не хватает доступного времени специалистов"
            role = details.get("role")
            deficit = _number(details.get("deficit_hours"))
            values = [f"{role}: дефицит {deficit:g} ч." if role and deficit is not None else (f"{role}: недостаточно часов." if role else "Недостаточно часов специалистов.")]
        elif code in {"INSUFFICIENT_TEAM_CAPACITY", "INSUFFICIENT_SP_CAPACITY"}:
            key, title = "team_capacity", "Недостаточно ресурсов команды"
            deficit = _number(details.get("deficit_sp"))
            values = [f"Не хватает {deficit:g} SP." if deficit else "Не хватает доступной ёмкости команды."]
        elif code == "INVALID_TASK_OWNERSHIP":
            key, title = "ownership", "Команда не может быть основным исполнителем"
            values = [str(details.get("status_message") or "Большую часть работы пришлось бы выполнять специалистам других команд.")]
        elif code in {"OUTSIDE_QUARTER", "QUARTER_OVERFLOW"}:
            key, title, values = "quarter", "Не помещается в текущий квартал", ["Для завершения требуется срок за пределами шести спринтов."]
        elif code in {"ALLOCATION_ROLLED_BACK", "MULTI_SPRINT_REQUIRED"}:
            # Diagnostic planner events add no new user cause when a resource
            # block is already present.
            continue
        else:
            key, title, values = "other", "Требуется уточнение данных", ["Планировщик не получил достаточно данных для размещения задачи."]
        group = groups.setdefault(key, {"type": key, "title": title, "details": []})
        for value in values:
            if value not in group["details"]:
                group["details"].append(value)
    return [groups[key] for key in sorted(groups, key=lambda item: order[item])]


BUS_FACTOR_PRESENTATION = {
    "critical": {"label": "Критично", "rank": 0},
    "warning": {"label": "Требует внимания", "rank": 1},
    "healthy": {"label": "Достаточное покрытие", "rank": 2},
}


def _frame(
    rows: Iterable[dict[str, Any]], columns: tuple[str, ...]
) -> pd.DataFrame:
    """Build an empty-safe DataFrame for the existing Bus Factor service."""

    materialized = [dict(row) for row in rows]
    if not materialized:
        return pd.DataFrame(columns=list(columns))
    frame = pd.DataFrame(materialized)
    for column in columns:
        if column not in frame:
            frame[column] = None
    return frame


def _bus_factor_risk(team_bus_factor: int) -> str:
    if team_bus_factor <= 1:
        return "critical"
    if team_bus_factor == 2:
        return "warning"
    return "healthy"


def build_bus_factor_payload(
    tables: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Adapt the existing calculator to the product-facing Star Map DTO.

    The adapter deliberately does not infer skills or recalculate Bus Factor. It
    only attaches catalog labels, holder identities and presentation metadata to
    values returned by :class:`BusFactorService`.
    """

    engineers = _frame(
        tables.get("engineers", []),
        ("engineer_id", "team_id", "role", "grade", "capacity_rate"),
    )
    skills = _frame(
        tables.get("engineer_skills", []), ("engineer_id", "technology")
    )

    if engineers.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "message": "Недостаточно данных для расчёта Bus Factor.",
            "method": "existing_bus_factor_service",
            "teams": [],
            "engineers": [],
        }

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
        tasks=_frame(
            tables.get("tasks", []), ("task_id", "team_id", "status")
        ),
        task_role_effort=_frame(
            tables.get("task_role_effort", []),
            ("task_id", "role", "required_hours"),
        ),
        task_skill_requirements=_frame(
            tables.get("task_skill_requirements", []),
            ("task_id", "technology", "required_hours"),
        ),
    )

    team_names = {
        team_id: (_text(row.get("team_name")) or _text(row.get("name")) or team_id)
        for row in tables.get("team_history", [])
        if (team_id := _text(row.get("team_id")))
    }
    raw_profiles: dict[str, dict[str, Any]] = {}
    memberships: dict[str, dict[str, float | None]] = defaultdict(dict)
    for row in tables.get("engineers", []):
        engineer_id = _text(row.get("engineer_id"))
        team_id = _text(row.get("team_id"))
        if not engineer_id or not team_id:
            continue
        profile = raw_profiles.setdefault(
            engineer_id,
            {"names": set(), "roles": set(), "grades": set(), "team_ids": set()},
        )
        profile["team_ids"].add(team_id)
        if name := _text(row.get("name")):
            profile["names"].add(name)
        if role := _text(row.get("role")):
            profile["roles"].add(role)
        if grade := _text(row.get("grade")):
            profile["grades"].add(grade)
        rate = _number(row.get("capacity_rate"))
        current = memberships[engineer_id].get(team_id)
        memberships[engineer_id][team_id] = (
            rate if current is None else max(current, rate or 0.0)
        )

    skill_sets: dict[str, set[str]] = defaultdict(set)
    for row in service.engineer_skills.itertuples(index=False):
        skill_sets[row.engineer_id].add(row.skill)

    def engineer_dto(engineer_id: str) -> dict[str, Any]:
        profile = raw_profiles.get(engineer_id, {})
        names = sorted(profile.get("names", set()))
        return {
            "engineer_id": engineer_id,
            "name": names[0] if names else engineer_id,
            "roles": sorted(profile.get("roles", set())),
            "grade": ", ".join(sorted(profile.get("grades", set()))) or None,
            "team_ids": sorted(profile.get("team_ids", set())),
            "memberships": [
                {"team_id": team_id, "capacity_rate": rate}
                for team_id, rate in sorted(memberships.get(engineer_id, {}).items())
            ],
            "skills": sorted(skill_sets.get(engineer_id, set())),
        }

    impacts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    team_payloads = []
    for team_id in service.teams():
        coverage = service.skill_coverage(team_id)
        competencies = []
        if not coverage.empty:
            coverage = coverage.sort_values(["team_bus_factor", "competency"])
        for item in coverage.itertuples(index=False):
            team_bf = int(item.team_bus_factor)
            # Without explicit task -> skill requirements, BF=0 means "not a
            # team competency", not a staffing gap. The existing module uses
            # the same rule for recommendations.
            if team_bf == 0 and item.quarter_relevance != "required":
                continue

            risk_level = _bus_factor_risk(team_bf)
            holders = service.competency_engineers(team_id, "skill", item.competency)
            external_engineers = []
            organization_holders = service.organization_competency_engineers(
                "skill", item.competency
            )
            for engineer_id in sorted(set(organization_holders) - set(holders)):
                external = engineer_dto(engineer_id)
                external["source_team_ids"] = [
                    source_team_id
                    for source_team_id in external["team_ids"]
                    if source_team_id != team_id
                ]
                if not external["source_team_ids"]:
                    continue
                external_engineers.append(external)

            if team_bf == 0:
                reason = (
                    f"В {team_names.get(team_id, team_id)} нет инженеров, "
                    "которые покрывают эту компетенцию."
                )
            elif team_bf == 1:
                reason = (
                    f"Только один инженер в {team_names.get(team_id, team_id)} "
                    "покрывает эту компетенцию."
                )
            elif team_bf == 2:
                reason = (
                    f"Два инженера в {team_names.get(team_id, team_id)} "
                    "покрывают эту компетенцию."
                )
            else:
                reason = (
                    f"Компетенцию покрывают {team_bf} инженера команды."
                )

            competency = {
                "competency_type": "skill",
                "skill": item.competency,
                "bus_factor": team_bf,
                "organization_bus_factor": int(item.organization_bus_factor),
                "risk_level": risk_level,
                "risk_label": BUS_FACTOR_PRESENTATION[risk_level]["label"],
                "coverage_status": item.coverage_status,
                "quarter_relevance": item.quarter_relevance,
                "engineers": [engineer_dto(engineer_id) for engineer_id in holders],
                "external_engineers": external_engineers,
                "reason": reason,
                "recommendation": (
                    "Требуется дополнительное покрытие компетенции."
                    if team_bf <= 2
                    else None
                ),
            }
            competencies.append(competency)
            for engineer_id in holders:
                impacts[engineer_id].append(
                    {
                        "team_id": team_id,
                        "team_name": team_names.get(team_id, team_id),
                        "skill": item.competency,
                        "bus_factor": team_bf,
                        "risk_level": risk_level,
                        "risk_label": BUS_FACTOR_PRESENTATION[risk_level]["label"],
                        "single_person_dependency": team_bf == 1,
                    }
                )

        competencies.sort(
            key=lambda item: (
                BUS_FACTOR_PRESENTATION[item["risk_level"]]["rank"],
                item["bus_factor"],
                item["skill"].casefold(),
            )
        )
        summary = {
            "zero_coverage_count": sum(
                item["bus_factor"] == 0 for item in competencies
            ),
            "single_owner_count": sum(
                item["bus_factor"] == 1 for item in competencies
            ),
            "critical_count": sum(
                item["risk_level"] == "critical" for item in competencies
            ),
            "warning_count": sum(
                item["risk_level"] == "warning" for item in competencies
            ),
            "healthy_count": sum(
                item["risk_level"] == "healthy" for item in competencies
            ),
        }
        member_ids = service.team_engineers(team_id)
        team_payloads.append(
            {
                "team_id": team_id,
                "team_name": team_names.get(team_id, team_id),
                "member_ids": member_ids,
                "engineer_count": len(member_ids),
                "competency_count": len(competencies),
                "bus_factor_available": bool(competencies),
                "status": "AVAILABLE" if competencies else "NO_COMPETENCY_DATA",
                "message": (
                    None
                    if competencies
                    else "Для этой команды пока нет данных о компетенциях."
                ),
                "competencies": competencies,
                "summary": summary,
            }
        )

    engineers_payload = []
    for engineer_id in sorted(raw_profiles):
        profile = engineer_dto(engineer_id)
        profile["team_criticality"] = sorted(
            impacts.get(engineer_id, []),
            key=lambda item: (
                BUS_FACTOR_PRESENTATION[item["risk_level"]]["rank"],
                item["team_name"].casefold(),
                item["skill"].casefold(),
            ),
        )
        engineers_payload.append(profile)

    has_competencies = any(team["competencies"] for team in team_payloads)
    return _clean(
        {
            "status": "AVAILABLE" if has_competencies else "INSUFFICIENT_DATA",
            "message": (
                None
                if has_competencies
                else "Недостаточно данных для расчёта Bus Factor."
            ),
            "method": "existing_bus_factor_service",
            "thresholds": {
                "critical": "Bus Factor 0–1",
                "warning": "Bus Factor 2",
                "healthy": "Bus Factor больше 2",
            },
            "teams": team_payloads,
            "engineers": engineers_payload,
        }
    )


class PlanningAPIService:
    """Load one processed snapshot and expose stable JSON DTOs."""

    def __init__(
        self,
        processed_dir: Path = PROCESSED_DIR,
        *,
        tables: dict[str, list[dict[str, Any]]] | None = None,
        plan_version: str | None = None,
        planner_options: dict[str, Any] | None = None,
    ):
        self.processed_dir = processed_dir
        self._plan_version_override = plan_version
        self._planner_options = dict(planner_options or {})
        if tables is None:
            self.tables = {
                name: _read_table(processed_dir, name) for name in TABLE_FILES
            }
        else:
            missing = sorted(set(TABLE_FILES) - set(tables))
            if missing:
                raise ServiceDataError(
                    f"Missing in-memory planning tables: {', '.join(missing)}"
                )
            self.tables = {
                name: [dict(row) for row in rows] for name, rows in tables.items()
            }
        self.tables.setdefault("task_skill_requirements", [])
        self.context = FeasibilityContext.from_records(
            tasks=self.tables["tasks"],
            task_role_effort=self.tables["task_role_effort"],
            dependencies=self.tables["task_dependencies"],
            engineers=self.tables["engineers"],
            engineer_skills=self.tables["engineer_skills"],
            team_history=self.tables["team_history"],
            task_skill_requirements=self.tables["task_skill_requirements"],
        )
        self.plan = build_quarter_plan(self.context, **self._planner_options)
        self._raw_tasks = {
            task_id: row
            for row in self.tables["tasks"]
            if (task_id := _text(row.get("task_id")))
        }
        self._task_results = {item.task_id: item for item in self.plan.task_results}
        self._task_explanations = {
            item.task_id: item for item in self.plan.task_explanations
        }

    @classmethod
    def from_tables(
        cls,
        tables: dict[str, list[dict[str, Any]]],
        *,
        plan_version: str = "in-memory",
        planner_options: dict[str, Any] | None = None,
    ) -> "PlanningAPIService":
        """Build API DTOs from temporary normalized records without file writes."""

        return cls(
            tables=tables,
            plan_version=plan_version,
            planner_options=planner_options,
        )

    @cached_property
    def plan_version(self) -> str:
        if self._plan_version_override:
            return self._plan_version_override
        manifest = self.processed_dir / "_dataset_manifest.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            fingerprint = str(payload.get("raw_file_hash") or "")[:8]
        except (OSError, ValueError, TypeError):
            fingerprint = "unknown"
        return f"baseline-{fingerprint}"

    @cached_property
    def kpi_baseline_plan(self) -> QuarterPlan:
        """Immutable Week-0 snapshot adapted from the initial planner result."""

        items = []
        for task in self.tasks:
            if task["status"] != "PLANNED" or not task["planned_sprints"]:
                continue
            allocations = tuple(
                SprintAllocation(
                    sprint_number=int(sprint),
                    role_hours=tuple(
                        RoleHoursAllocation(role, float(hours))
                        for role, hours in sorted(role_hours.items())
                    ),
                )
                for sprint, role_hours in sorted(
                    task["role_hours_by_sprint"].items(), key=lambda item: int(item[0])
                )
            )
            items.append(
                PlanItem(
                    task_id=task["task_id"],
                    initiative_id=task["initiative_id"],
                    story_points=float(task["story_points"] or 0.0),
                    completion_sprint=int(task["completion_sprint"]),
                    state=PlanState.PLANNED,
                    team_id=task["assigned_team_id"],
                    allocations=allocations,
                )
            )
        return QuarterPlan(
            quarter_id=self.plan_version,
            version=1,
            as_of_sprint=0,
            items=tuple(items),
        )

    @cached_property
    def kpi_actual_results(self) -> ActualResults:
        """No sprint fact is available until a factual upload is integrated."""

        return ActualResults(
            quarter_id=self.kpi_baseline_plan.quarter_id,
            completed_through_sprint=0,
            entries=(),
        )

    @cached_property
    def kpis(self) -> dict[str, Any]:
        baseline = self.kpi_baseline_plan
        actuals = self.kpi_actual_results
        expected = calculate_expected_quarter_kpi(
            (item.initiative_id for item in baseline.items),
            self.plan.completed_initiative_ids,
        )
        quarter = calculate_pi_predictability_fact(baseline, actuals)
        sprints = [
            calculate_sprint_say_do(baseline, baseline, actuals, sprint)
            for sprint in range(1, 7)
        ]
        return _clean(
            {
                "expected_kpi": {
                    "completed_initiatives": expected.numerator,
                    "baseline_initiatives": expected.denominator,
                    "incomplete_initiatives": expected.denominator - expected.numerator,
                    "completion_percent": expected.percentage,
                },
                "quarter_kpi": {
                    "completed_initiatives": quarter.numerator,
                    "baseline_initiatives": quarter.denominator,
                    "completion_percent": quarter.percentage,
                    "has_actual": quarter.has_actual,
                },
                "sprint_kpis": [
                    {
                        "sprint": sprint,
                        "baseline_sp": result.denominator,
                        "actual_sp": result.numerator,
                        "completion_percent": result.percentage,
                        "has_actual": result.has_actual,
                    }
                    for sprint, result in enumerate(sprints, start=1)
                ],
            }
        )

    def _task_role_requirements(self, task_id: str) -> dict[str, float]:
        values: dict[str, float] = defaultdict(float)
        for row in self.tables["task_role_effort"]:
            if _text(row.get("task_id")) != task_id:
                continue
            role = _text(row.get("role"))
            hours = _number(row.get("required_hours"))
            if role and hours is not None and hours > 0:
                values[role] += hours
        return dict(sorted(values.items()))

    def _task_dependencies(self, task_id: str) -> dict[str, list[dict[str, Any]]]:
        incoming = []
        outgoing = []
        for row in self.tables["task_dependencies"]:
            blocker_id = _text(row.get("blocking_task_id"))
            blocker = self._raw_tasks.get(blocker_id or "")
            if blocker is None:
                dependency_state = "MISSING"
                satisfied = False
                blocker_title = None
            elif (_text(blocker.get("status")) or "").casefold() == "done":
                dependency_state = "HISTORICAL_DONE"
                satisfied = True
                blocker_title = _text(blocker.get("summary"))
            else:
                dependency_state = "ACTIVE"
                satisfied = False
                blocker_title = _text(blocker.get("summary"))
            payload = {
                "blocking_task_id": blocker_id,
                "blocked_task_id": _text(row.get("blocked_task_id")),
                "dependency_type": _text(row.get("dependency_type")),
                "state": dependency_state,
                "satisfied": satisfied,
                "blocking_task_title": blocker_title,
                "status_message": (
                    "Задача завершена до начала квартала"
                    if dependency_state == "HISTORICAL_DONE"
                    else (
                        "Блокирующая задача неизвестна каталогу"
                        if dependency_state == "MISSING"
                        else "Активная зависимость"
                    )
                ),
            }
            if payload["blocked_task_id"] == task_id:
                incoming.append(payload)
            if payload["blocking_task_id"] == task_id:
                outgoing.append(payload)
        return {"incoming": incoming, "outgoing": outgoing}

    def _task_allocations(self, task_id: str) -> list[dict[str, Any]]:
        explanation = self._task_explanations[task_id]
        rows = []
        for sprint_allocation in explanation.allocations:
            sprint = sprint_allocation["sprint"]
            for allocation in sprint_allocation["engineer_allocations"]:
                rows.append(
                    {
                        "engineer_id": allocation["engineer_id"],
                        "role": allocation["role"],
                        "source_team_id": allocation["source_team_id"],
                        "assigned_task_team_id": explanation.assigned_team_id,
                        "sprint": sprint,
                        "allocated_hours": allocation["allocated_hours"],
                        "cross_team": allocation["cross_team"],
                        "capacity_bucket_hours": allocation["capacity_bucket_hours"],
                        "capacity_used_share": allocation["capacity_used_share"],
                    }
                )
        return rows

    @cached_property
    def tasks(self) -> list[dict[str, Any]]:
        output = []
        for task_id in sorted(self._raw_tasks):
            raw = self._raw_tasks[task_id]
            result = self._task_results[task_id]
            explanation = self._task_explanations[task_id]
            allocations = self._task_allocations(task_id)
            ownership = (
                evaluate_ownership_rows(explanation.assigned_team_id, allocations).to_dict()
                if explanation.assigned_team_id
                else None
            )
            role_hours_by_sprint = {
                str(item["sprint"]): dict(item["allocated_role_hours"])
                for item in explanation.allocations
            }
            reasons = [item.to_dict() for item in explanation.reasons]
            output.append(
                {
                    "task_id": task_id,
                    "title": _text(raw.get("summary")) or "Unknown task",
                    "description": _text(raw.get("summary")),
                    "initiative_id": explanation.initiative_id,
                    "status": explanation.status.value,
                    "status_label": STATUS_LABELS[explanation.status.value],
                    "source_status": _text(raw.get("status")),
                    "rung": _number(raw.get("rung")),
                    "priority_label": (
                        f"Rung {_number(raw.get('rung')):g}"
                        if _number(raw.get("rung")) is not None
                        else "Rung не указан"
                    ),
                    "story_points": explanation.task_story_points,
                    "original_team_id": explanation.original_team_id,
                    "assigned_team_id": explanation.assigned_team_id,
                    "team_reassigned": explanation.team_reassigned,
                    "planned_sprints": list(explanation.planned_sprints),
                    "start_sprint": min(explanation.planned_sprints, default=None),
                    "completion_sprint": explanation.completion_sprint,
                    "multi_sprint": explanation.multi_sprint,
                    "sp_load_by_sprint": {
                        str(key): value for key, value in explanation.sp_load_by_sprint.items()
                    },
                    "hours_by_sprint": {
                        str(item["sprint"]): item["allocated_hours"]
                        for item in explanation.allocations
                    },
                    "role_hours_by_sprint": role_hours_by_sprint,
                    "role_requirements": self._task_role_requirements(task_id),
                    "dependencies": self._task_dependencies(task_id),
                    "allocations": allocations,
                    "ownership": ownership,
                    "reasons": reasons,
                    "reason_blocks": _human_reason_blocks(reasons),
                    "risks": [item.to_dict() for item in explanation.risks],
                    "recommendations": [item.to_dict() for item in explanation.recommendations],
                    "summary": explanation.summary,
                    "outside_quarter": result.outside_quarter,
                    "baseline_start_date": result.baseline_start_date,
                    "baseline_end_date": result.baseline_end_date,
                    "forecast_start_date": result.forecast_start_date,
                    "forecast_end_date": result.forecast_end_date,
                    "is_placeholder": bool(raw.get("is_placeholder")),
                }
            )
        return _clean(output)

    @cached_property
    def task_by_id(self) -> dict[str, dict[str, Any]]:
        return {item["task_id"]: item for item in self.tasks}

    @cached_property
    def initiatives(self) -> list[dict[str, Any]]:
        tasks_by_initiative: dict[str, list[str]] = defaultdict(list)
        for task in self.tasks:
            if task["initiative_id"]:
                tasks_by_initiative[task["initiative_id"]].append(task["task_id"])
        output = []
        for item in self.plan.initiative_explanations:
            reasons = [reason.to_dict() for reason in item.selection_reasons]
            output.append(
                {
                    **item.to_dict(),
                    "task_ids": sorted(tasks_by_initiative[item.initiative_id]),
                    "summary": reasons[0]["message"] if reasons else None,
                }
            )
        return _clean(output)

    @cached_property
    def summary(self) -> dict[str, Any]:
        result = self.plan.explainability_summary.to_dict()
        result.update(
            {
                "total_tasks": len(self.tasks),
                "total_initial_initiatives": len(self.initiatives),
                "plan_version": self.plan_version,
            }
        )
        return result

    @cached_property
    def bus_factor_payload(self) -> dict[str, Any]:
        return build_bus_factor_payload(self.tables)

    @cached_property
    def teams(self) -> list[dict[str, Any]]:
        team_ids = sorted(
            {
                value
                for rows, field in (
                    (self.tables["team_history"], "team_id"),
                    (self.tables["engineers"], "team_id"),
                    (self.tasks, "assigned_team_id"),
                )
                for row in rows
                if (value := _text(row.get(field)))
            }
        )
        skills_by_engineer: dict[str, set[str]] = defaultdict(set)
        for row in self.tables["engineer_skills"]:
            engineer_id = _text(row.get("engineer_id"))
            technology = _text(row.get("technology"))
            if engineer_id and technology:
                skills_by_engineer[engineer_id].add(technology)
        team_names = {
            team_id: (_text(row.get("team_name")) or _text(row.get("name")) or team_id)
            for row in self.tables["team_history"]
            if (team_id := _text(row.get("team_id")))
        }
        output = []
        bus_factor_teams = {
            item["team_id"]: item for item in self.bus_factor_payload["teams"]
        }
        all_allocations = [
            {"task_id": task["task_id"], **allocation}
            for task in self.tasks
            for allocation in task["allocations"]
        ]
        for team_id in team_ids:
            bus_factor = bus_factor_teams.get(team_id)
            profiles = [
                row for row in self.tables["engineers"]
                if _text(row.get("team_id")) == team_id
            ]
            member_ids = sorted(
                {engineer_id for row in profiles if (engineer_id := _text(row.get("engineer_id")))}
            )
            capacity = calculate_team_capacity(team_id, self.tables["team_history"])
            member_bucket_rates: dict[tuple[str, str], float] = {}
            for row in profiles:
                engineer_id = _text(row.get("engineer_id"))
                rate = _number(row.get("capacity_rate"))
                if engineer_id and rate is not None:
                    key = (engineer_id, team_id)
                    member_bucket_rates[key] = min(
                        member_bucket_rates.get(key, rate), rate
                    )
            member_capacity = sum(80.0 * rate for rate in member_bucket_rates.values())
            output.append(
                {
                    "team_id": team_id,
                    "name": team_names.get(team_id, team_id),
                    "member_ids": member_ids,
                    "roles": sorted({role for row in profiles if (role := _text(row.get("role")))}),
                    "technologies": sorted(
                        {skill for engineer_id in member_ids for skill in skills_by_engineer[engineer_id]}
                    ),
                    "average_velocity_sp": capacity.average_velocity_sp,
                    "focus_factor": capacity.focus_factor,
                    "capacity_sp": capacity.capacity_sp,
                    "sp_by_sprint": {
                        str(sprint.sprint_number): {
                            "capacity": sprint.team_capacity_sp.get(team_id, 0.0),
                            "used": sprint.team_sp_used.get(team_id, 0.0),
                            "remaining": max(
                                sprint.team_capacity_sp.get(team_id, 0.0)
                                - sprint.team_sp_used.get(team_id, 0.0),
                                0.0,
                            ),
                        }
                        for sprint in self.plan.sprints
                    },
                    "member_capacity_hours": member_capacity,
                    "source_used_hours_by_sprint": {
                        str(sprint): sum(
                            item["allocated_hours"]
                            for item in all_allocations
                            if item["sprint"] == sprint
                            and item["source_team_id"] == team_id
                        )
                        for sprint in range(1, 7)
                    },
                    "task_work_hours_by_sprint": {
                        str(sprint): sum(
                            item["allocated_hours"]
                            for item in all_allocations
                            if item["sprint"] == sprint
                            and item["assigned_task_team_id"] == team_id
                        )
                        for sprint in range(1, 7)
                    },
                    "bus_factor": bus_factor["summary"] if bus_factor else None,
                    "bus_factor_status": (
                        bus_factor["status"] if bus_factor else "INSUFFICIENT_DATA"
                    ),
                }
            )
        return _clean(output)

    @cached_property
    def team_by_id(self) -> dict[str, dict[str, Any]]:
        return {item["team_id"]: item for item in self.teams}

    @cached_property
    def engineers(self) -> list[dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {}
        bucket_rates: dict[tuple[str, str], float] = {}
        for row in self.tables["engineers"]:
            engineer_id = _text(row.get("engineer_id"))
            team_id = _text(row.get("team_id"))
            if not engineer_id or not team_id:
                continue
            target = profiles.setdefault(
                engineer_id,
                {
                    "names": set(),
                    "roles": set(),
                    "grades": set(),
                    "team_ids": set(),
                    "skills": set(),
                },
            )
            target["team_ids"].add(team_id)
            if name := _text(row.get("name")):
                target["names"].add(name)
            if role := _text(row.get("role")):
                target["roles"].add(role)
            if grade := _text(row.get("grade")):
                target["grades"].add(grade)
            rate = _number(row.get("capacity_rate"))
            if rate is not None and 0 < rate <= 1:
                key = (engineer_id, team_id)
                bucket_rates[key] = min(bucket_rates.get(key, rate), rate)
        for row in self.tables["engineer_skills"]:
            engineer_id = _text(row.get("engineer_id"))
            technology = _text(row.get("technology"))
            if engineer_id in profiles and technology:
                profiles[engineer_id]["skills"].add(technology)

        all_allocations = [
            {"task_id": task["task_id"], **allocation}
            for task in self.tasks
            for allocation in task["allocations"]
        ]
        output = []
        for engineer_id, profile in sorted(profiles.items()):
            buckets = [
                {
                    "team_id": team_id,
                    "capacity_rate": bucket_rates[(engineer_id, team_id)],
                    "capacity_hours": 80.0 * bucket_rates[(engineer_id, team_id)],
                }
                for team_id in sorted(profile["team_ids"])
            ]
            allocations = [
                item for item in all_allocations if item["engineer_id"] == engineer_id
            ]
            total_capacity = sum(item["capacity_hours"] for item in buckets)
            used_by_sprint = {
                str(sprint): sum(
                    item["allocated_hours"] for item in allocations if item["sprint"] == sprint
                )
                for sprint in range(1, 7)
            }
            used_by_team_by_sprint = {
                str(sprint): {
                    team_id: sum(
                        item["allocated_hours"]
                        for item in allocations
                        if item["sprint"] == sprint
                        and item["source_team_id"] == team_id
                    )
                    for team_id in sorted(profile["team_ids"])
                }
                for sprint in range(1, 7)
            }
            output.append(
                {
                    "engineer_id": engineer_id,
                    "name": sorted(profile["names"])[0] if profile["names"] else engineer_id,
                    "roles": sorted(profile["roles"]),
                    "grade": ", ".join(sorted(profile["grades"])) or None,
                    "team_ids": sorted(profile["team_ids"]),
                    "capacity_buckets": buckets,
                    "capacity_hours": total_capacity,
                    "used_hours_by_sprint": used_by_sprint,
                    "used_hours_by_team_by_sprint": used_by_team_by_sprint,
                    "free_hours_by_sprint": {
                        key: max(total_capacity - value, 0.0)
                        for key, value in used_by_sprint.items()
                    },
                    "skills": sorted(profile["skills"]),
                    "allocations": allocations,
                }
            )
        return _clean(output)

    @cached_property
    def engineer_by_id(self) -> dict[str, dict[str, Any]]:
        return {item["engineer_id"]: item for item in self.engineers}

    @cached_property
    def risks(self) -> list[dict[str, Any]]:
        output = []
        for task in self.tasks:
            for index, risk in enumerate(task["risks"], start=1):
                category, title = RISK_PRESENTATION.get(
                    risk["code"], ("Ресурсы", risk["code"])
                )
                output.append(
                    {
                        "risk_id": f"{task['task_id']}:{risk['code']}:{index}",
                        "code": risk["code"],
                        "severity": risk["severity"],
                        "type": category,
                        "title": title,
                        "object": task["task_id"],
                        "sprint": task["completion_sprint"],
                        "cause": risk["message"],
                        "effect": None,
                        "task_id": task["task_id"],
                        "team_id": task["assigned_team_id"],
                        "initiative_id": task["initiative_id"],
                        "details": risk["details"],
                    }
                )
        return _clean(output)

    @cached_property
    def sprints(self) -> list[dict[str, Any]]:
        output = []
        for sprint in self.plan.sprints:
            task_rows = []
            role_required: dict[tuple[str, str], float] = defaultdict(float)
            for allocation in sprint.task_allocations:
                task = self.task_by_id[allocation.task_id]
                task_rows.append(
                    {
                        "task_id": allocation.task_id,
                        "assigned_team_id": task["assigned_team_id"],
                        "capacity_sp_load": allocation.capacity_sp_load,
                        "allocated_hours": allocation.allocated_hours,
                        "is_started": allocation.is_started,
                        "is_completed": allocation.is_completed,
                    }
                )
                for role, hours in allocation.allocated_role_hours.items():
                    role_required[(task["assigned_team_id"], role)] += hours
            output.append(
                {
                    "sprint_number": sprint.sprint_number,
                    "status": "CURRENT" if sprint.sprint_number == 1 else "PLANNED",
                    "teams": [
                        {
                            "team_id": team_id,
                            "capacity_sp": capacity,
                            "used_sp": sprint.team_sp_used.get(team_id, 0.0),
                            "remaining_sp": max(
                                capacity - sprint.team_sp_used.get(team_id, 0.0), 0.0
                            ),
                        }
                        for team_id, capacity in sorted(sprint.team_capacity_sp.items())
                    ],
                    "role_resources": [
                        {
                            "team_id": team_id,
                            "role": role,
                            "required_hours": hours,
                            "available_hours": None,
                            "deficit_hours": None,
                        }
                        for (team_id, role), hours in sorted(role_required.items())
                    ],
                    "tasks": task_rows,
                }
            )
        return _clean(output)

    @cached_property
    def plan_payload(self) -> dict[str, Any]:
        return {
            "quarter": {
                "sprint_count": 6,
                "current_sprint": 1,
                "current_sprint_source": "default_without_replanning_fact",
                "plan_version": self.plan_version,
                "quarter_start_date": self.plan.quarter_start_date,
                "quarter_end_date": self.plan.quarter_end_date,
            },
            "summary": self.summary,
            "sprints": self.sprints,
            "tasks": self.tasks,
            "allocations": [
                {"task_id": task["task_id"], **allocation}
                for task in self.tasks
                for allocation in task["allocations"]
            ],
            "initiatives": self.initiatives,
        }

    @cached_property
    def star_map(self) -> dict[str, Any]:
        return self.bus_factor_payload

    @cached_property
    def dashboard(self) -> dict[str, Any]:
        return {
            "plan": self.plan_payload,
            "summary": self.summary,
            "tasks": self.tasks,
            "initiatives": self.initiatives,
            "risks": self.risks,
            "teams": self.teams,
            "engineers": self.engineers,
            "star_map": self.star_map,
            "kpis": self.kpis,
            "features": {
                "plan_constructor": "AVAILABLE_EPHEMERAL",
                "replanning": "NOT_IMPLEMENTED",
                "bus_factor": "AVAILABLE",
                "initial_excel_upload": "AVAILABLE_PERSISTENT_CATALOG",
            },
        }


@lru_cache(maxsize=1)
def get_planning_service() -> PlanningAPIService:
    """Return the immutable processed snapshot used by the running service."""

    return PlanningAPIService()
