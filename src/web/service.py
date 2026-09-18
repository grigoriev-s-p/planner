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

RISK_SEVERITY_PRIORITY = {"RED": 0, "ORANGE": 1, "YELLOW": 2}
RESOURCE_REASON_CODES = {
    "INSUFFICIENT_ROLE_CAPACITY",
    "INSUFFICIENT_ENGINEER_CAPACITY",
    "INSUFFICIENT_SKILL_CAPACITY",
    "MISSING_SPECIALIST",
}
RESOURCE_REASON_PRIORITY = {
    "MISSING_SPECIALIST": 4,
    "INSUFFICIENT_SKILL_CAPACITY": 3,
    "INSUFFICIENT_ENGINEER_CAPACITY": 2,
    "INSUFFICIENT_ROLE_CAPACITY": 1,
}


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
    ):
        self.processed_dir = processed_dir
        self._plan_version_override = plan_version
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
        self.plan = build_quarter_plan(self.context)
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
    ) -> "PlanningAPIService":
        """Build API DTOs from temporary normalized records without file writes."""

        return cls(tables=tables, plan_version=plan_version)

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
                    "reasons": reasons,
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
    def _risk_bus_factor_service(self) -> BusFactorService:
        """Reuse the existing Bus Factor engine only as staffing context.

        Sprint availability is deliberately not calculated here.  The planner
        remains the source of truth for required/available/deficit hours.
        """

        return BusFactorService(
            engineers=_frame(
                self.tables.get("engineers", []),
                ("engineer_id", "team_id", "role", "grade", "capacity_rate"),
            ),
            engineer_skills=_frame(
                self.tables.get("engineer_skills", []),
                ("engineer_id", "technology"),
            ),
            tasks=_frame(
                self.tables.get("tasks", []),
                ("task_id", "team_id", "status"),
            ),
            task_role_effort=_frame(
                self.tables.get("task_role_effort", []),
                ("task_id", "role", "required_hours"),
            ),
            task_skill_requirements=_frame(
                self.tables.get("task_skill_requirements", []),
                ("task_id", "technology", "required_hours"),
            ),
        )

    def _downstream_task_ids(self, task_id: str) -> list[str]:
        graph: dict[str, set[str]] = defaultdict(set)
        for row in self.tables.get("task_dependencies", []):
            blocking = _text(row.get("blocking_task_id"))
            blocked = _text(row.get("blocked_task_id"))
            if blocking and blocked:
                graph[blocking].add(blocked)

        visited: set[str] = set()
        queue = list(sorted(graph.get(task_id, set())))
        while queue:
            current = queue.pop(0)
            if current in visited or current == task_id:
                continue
            visited.add(current)
            queue.extend(sorted(graph.get(current, set()) - visited))
        return sorted(visited)

    def _resource_reason_sprint(self, task_id: str, role: str | None) -> int | None:
        result = self._task_results.get(task_id)
        if result is None:
            return None
        candidates: list[int] = []
        for attempt in result.placement_attempts:
            for reason in attempt.reasons:
                if reason.code.value not in RESOURCE_REASON_CODES:
                    continue
                reason_role = _text(reason.details.get("role"))
                if role and reason_role and reason_role != role:
                    continue
                candidates.append(attempt.sprint_number)
        return min(candidates, default=None)

    def _role_bus_factor_context(
        self, team_id: str | None, role: str | None
    ) -> dict[str, Any] | None:
        if not team_id or not role:
            return None
        try:
            coverage = self._risk_bus_factor_service.role_coverage(team_id)
        except KeyError:
            return None
        if coverage.empty:
            return None
        matches = coverage[
            coverage["competency"].astype(str).str.casefold() == role.casefold()
        ]
        if matches.empty:
            return None
        row = matches.iloc[0]
        helpers = self._risk_bus_factor_service.get_potential_helpers(
            team_id, "role", role
        )
        helper_rows = []
        if not helpers.empty:
            for item in helpers.head(5).to_dict("records"):
                helper_rows.append(
                    {
                        "engineer_id": item.get("engineer_id"),
                        "source_team_id": item.get("source_team_id"),
                        "role": item.get("role"),
                        "grade": item.get("grade"),
                        "capacity_rate": item.get("capacity_rate"),
                        "match_score": item.get("match_score"),
                    }
                )
        return _clean(
            {
                "role": role,
                "team_bus_factor": int(row["team_bus_factor"]),
                "organization_bus_factor": int(row["organization_bus_factor"]),
                "coverage_status": row["coverage_status"],
                "quarter_relevance": row["quarter_relevance"],
                "potential_helpers": helper_rows,
                "availability_note": (
                    "Bus Factor показывает наличие подходящих специалистов, "
                    "но не их свободные часы в конкретном спринте."
                ),
            }
        )

    @staticmethod
    def _evidence(label: str, value: Any, unit: str | None = None) -> dict[str, Any]:
        return {"label": label, "value": value, "unit": unit}

    @cached_property
    def risks(self) -> list[dict[str, Any]]:
        """Product-facing actionable alerts built only from planner facts.

        Normal multi-sprint execution and successful cross-team allocations are
        intentionally not risks.  Alerts follow the agreed product semantics:
        RED = outside the six-sprint quarter, YELLOW = dependency shift,
        ORANGE = concrete role/capacity deficit.
        """

        output: list[dict[str, Any]] = []
        for task in self.tasks:
            reasons = task["reasons"]
            reason_codes = {reason["code"] for reason in reasons}

            # RED: forecast cannot fit inside the six-sprint quarter.
            for index, reason in enumerate(
                (item for item in reasons if item["code"] == "QUARTER_OVERFLOW"),
                start=1,
            ):
                details = dict(reason.get("details") or {})
                forecast_sprint = (
                    details.get("forecast_completion_sprint_minimum")
                    or details.get("forecast_start_sprint")
                )
                last_sprint = details.get("last_sprint") or 6
                initiative_id = task.get("initiative_id")
                cause = (
                    f"Прогнозное завершение задачи {task['task_id']} — не раньше "
                    f"Sprint {forecast_sprint}, что выходит за границу квартала "
                    f"(Sprint {last_sprint})."
                    if forecast_sprint
                    else (
                        f"Прогноз выполнения задачи {task['task_id']} выходит "
                        f"за границу квартала (Sprint {last_sprint})."
                    )
                )
                effect = (
                    f"Бизнес-инициатива {initiative_id} не может быть полностью "
                    "завершена в текущем квартале."
                    if initiative_id
                    else (
                        f"Задача {task['task_id']} не может быть завершена в "
                        "текущем квартале."
                    )
                )
                evidence = [
                    self._evidence("Прогноз завершения", forecast_sprint, "спринт"),
                    self._evidence("Граница квартала", last_sprint, "спринт"),
                ]
                blockers = details.get("blocking_task_ids") or []
                if blockers:
                    evidence.append(
                        self._evidence("Блокирующие задачи", ", ".join(blockers))
                    )
                output.append(
                    {
                        "risk_id": f"{task['task_id']}:DEADLINE_OUTSIDE_QUARTER:{index}",
                        "code": "DEADLINE_OUTSIDE_QUARTER",
                        "severity": "RED",
                        "severity_label": "Красный алерт",
                        "type": "Сроки",
                        "title": "Критический риск",
                        "object": task["task_id"],
                        "sprint": forecast_sprint,
                        "cause": cause,
                        "effect": effect,
                        "recommendation": (
                            "Перенести задачу на следующий квартал или устранить "
                            "ограничивающую зависимость/ресурс и пересчитать план."
                        ),
                        "evidence": evidence,
                        "affected_tasks": self._downstream_task_ids(task["task_id"]),
                        "task_id": task["task_id"],
                        "team_id": task["assigned_team_id"],
                        "initiative_id": initiative_id,
                        "bus_factor_context": None,
                        "details": details,
                    }
                )

            # YELLOW: a dependency already shifts this task, but the quarter
            # boundary is not yet violated.
            if "QUARTER_OVERFLOW" not in reason_codes:
                for index, reason in enumerate(
                    (item for item in reasons if item["code"] == "DEPENDENCY_DELAY"),
                    start=1,
                ):
                    details = dict(reason.get("details") or {})
                    earliest = details.get("earliest_allowed_sprint")
                    if earliest is not None and int(earliest) > 6:
                        continue
                    blocker = details.get("blocking_task_id")
                    blocker_completion = details.get("blocking_completion_sprint")
                    downstream = self._downstream_task_ids(task["task_id"])
                    affected = [task["task_id"], *downstream]
                    if downstream:
                        effect = (
                            "Сдвиг распространяется на зависимую цепочку: "
                            + " → ".join(affected)
                            + ". Финальная граница квартала пока не нарушена."
                        )
                    else:
                        effect = (
                            f"Старт {task['task_id']} сдвинут зависимостью, но "
                            "прогноз пока остаётся внутри квартала. Дальнейшая "
                            "задержка blocker-а может создать каскадный сдвиг."
                        )
                    replanning_start = blocker_completion + 1
                    cause = (
                        f"{task['task_id']} зависит от {blocker}. В исходном плане после "
                        f"планового завершения {blocker} в Sprint {blocker_completion} "
                        f"действует один спринт запаса, поэтому {task['task_id']} может "
                        f"стартовать не раньше Sprint {earliest}. Если {blocker} фактически "
                        f"завершится по итогам Sprint {blocker_completion}, при replanning "
                        f"возможен старт уже в Sprint {replanning_start}."
                    )
                    output.append(
                        {
                            "risk_id": f"{task['task_id']}:DEPENDENCY_SHIFT:{index}",
                            "code": "DEPENDENCY_SHIFT",
                            "severity": "YELLOW",
                            "severity_label": "Жёлтый алерт",
                            "type": "Зависимости",
                            "title": "Риск каскадного сдвига",
                            "object": task["task_id"],
                            "sprint": earliest,
                            "cause": cause,
                            "effect": effect,
                            "recommendation": (
                                f"Контролировать завершение {blocker}; при фактической "
                                "задержке пересчитать всю downstream-цепочку."
                            ),
                            "evidence": [
                                self._evidence("Blocker", blocker),
                                self._evidence(
                                    "Завершение blocker-а", blocker_completion, "спринт"
                                ),
                                self._evidence(
                                    "Самый ранний старт", earliest, "спринт"
                                ),
                                self._evidence(
                                    "Затронуто задач", len(affected), "шт."
                                ),
                            ],
                            "affected_tasks": affected,
                            "task_id": task["task_id"],
                            "team_id": task["assigned_team_id"],
                            "initiative_id": task["initiative_id"],
                            "bus_factor_context": None,
                            "details": details,
                        }
                    )

            # ORANGE: one actionable alert per role/task.  If several planner
            # reasons describe the same shortage, keep the most specific one.
            resource_by_role: dict[str, dict[str, Any]] = {}
            for reason in reasons:
                code = reason["code"]
                if code not in RESOURCE_REASON_CODES:
                    continue
                details = dict(reason.get("details") or {})
                role = _text(details.get("role")) or "Неуказанная роль"
                current = resource_by_role.get(role)
                if current is None or RESOURCE_REASON_PRIORITY[code] > RESOURCE_REASON_PRIORITY[current["code"]]:
                    resource_by_role[role] = reason

            for index, (role, reason) in enumerate(
                sorted(resource_by_role.items()), start=1
            ):
                details = dict(reason.get("details") or {})
                code = reason["code"]
                required = _number(details.get("required_hours"))
                if required is None:
                    required = _number((task.get("role_requirements") or {}).get(role))
                if code == "MISSING_SPECIALIST":
                    available = 0.0
                else:
                    available = _number(
                        details.get("available_hours", details.get("available_matching_hours"))
                    )
                deficit = _number(details.get("deficit_hours"))
                if deficit is None and required is not None and available is not None:
                    deficit = max(required - available, 0.0)
                sprint = self._resource_reason_sprint(task["task_id"], role)
                bf_context = self._role_bus_factor_context(
                    task.get("assigned_team_id"), role
                )

                if required is not None and available is not None:
                    scope = f" в Sprint {sprint}" if sprint else ""
                    cause = (
                        f"Для {task['task_id']}{scope} требуется роль «{role}»: "
                        f"{required:g} ч. Доступно: {available:g} ч. "
                        f"Дефицит: {(deficit or 0):g} ч."
                    )
                else:
                    cause = reason.get("message") or (
                        f"Для {task['task_id']} недостаточно ресурса роли «{role}»."
                    )

                if task["status"] in {"MOVED", "UNPLANNABLE"}:
                    effect = (
                        f"Без дополнительного ресурса задача {task['task_id']} "
                        "не может быть размещена в текущем квартальном плане."
                    )
                else:
                    effect = (
                        f"Без дополнительного ресурса выполнение {task['task_id']} "
                        "в запланированном спринте находится под риском."
                    )

                helpers = (bf_context or {}).get("potential_helpers", [])
                org_bf = (bf_context or {}).get("organization_bus_factor")
                if helpers:
                    recommendation = (
                        f"Проверить доступность {len(helpers)} потенциальных "
                        "cross-team специалистов в planner-е; Bus Factor сам "
                        "свободные часы не подтверждает."
                    )
                elif org_bf == 0:
                    recommendation = (
                        "В организации нет специалиста этой роли. Нужен внешний "
                        "ресурс, изменение объёма работ или перенос задачи."
                    )
                else:
                    recommendation = (
                        "Проверить cross-team allocation в planner-е или перенести "
                        "часть нагрузки на другой спринт."
                    )

                evidence = [self._evidence("Роль", role)]
                if required is not None:
                    evidence.append(self._evidence("Требуется", required, "ч"))
                if available is not None:
                    evidence.append(self._evidence("Доступно", available, "ч"))
                if deficit is not None:
                    evidence.append(self._evidence("Дефицит", deficit, "ч"))
                if sprint is not None:
                    evidence.append(self._evidence("Проверяемый спринт", sprint, "спринт"))

                output.append(
                    {
                        "risk_id": f"{task['task_id']}:ROLE_CAPACITY_DEFICIT:{index}",
                        "code": "ROLE_CAPACITY_DEFICIT",
                        "source_reason_code": code,
                        "severity": "ORANGE",
                        "severity_label": "Оранжевый алерт",
                        "type": "Ресурсы",
                        "title": "Ресурсный дефицит",
                        "object": task["task_id"],
                        "sprint": sprint,
                        "cause": cause,
                        "effect": effect,
                        "recommendation": recommendation,
                        "evidence": evidence,
                        "affected_tasks": [task["task_id"]],
                        "task_id": task["task_id"],
                        "team_id": task["assigned_team_id"],
                        "initiative_id": task["initiative_id"],
                        "bus_factor_context": bf_context,
                        "details": details,
                    }
                )

        output.sort(
            key=lambda item: (
                RISK_SEVERITY_PRIORITY.get(item["severity"], 99),
                item["sprint"] if item["sprint"] is not None else 99,
                item["task_id"],
                item["risk_id"],
            )
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
