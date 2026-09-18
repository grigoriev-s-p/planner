"""Structured decision explanations derived from committed planner results.

This module is deliberately an adapter over the planner result.  It does not
participate in scoring, feasibility or allocation, so API and UI clients get a
stable contract without duplicating business rules or changing plan behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, TYPE_CHECKING

from .reasons import ReasonCode

if TYPE_CHECKING:
    from .plan_models import TaskPlanResult


class ExplanationStatus(str, Enum):
    DONE = "DONE"
    PLANNED = "PLANNED"
    MOVED = "MOVED"
    UNPLANNABLE = "UNPLANNABLE"


class ExplanationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RiskCode(str, Enum):
    MULTI_SPRINT = "MULTI_SPRINT"
    CROSS_TEAM_DEPENDENCY = "CROSS_TEAM_DEPENDENCY"
    QUARTER_OVERFLOW = "QUARTER_OVERFLOW"
    RESOURCE_BOTTLENECK = "RESOURCE_BOTTLENECK"


class RecommendationCode(str, Enum):
    USE_CROSS_TEAM_ENGINEER = "USE_CROSS_TEAM_ENGINEER"
    REASSIGN_TEAM = "REASSIGN_TEAM"
    MOVE_NEXT_QUARTER = "MOVE_NEXT_QUARTER"


@dataclass(frozen=True)
class DecisionReason:
    code: ReasonCode
    severity: ExplanationSeverity
    title: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class PlanningRisk:
    code: RiskCode
    severity: ExplanationSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class TaskRecommendation:
    code: RecommendationCode
    title: str
    message: str
    available: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "title": self.title,
            "message": self.message,
            "available": self.available,
            "details": self.details,
        }


@dataclass(frozen=True)
class TaskPlanningExplanation:
    task_id: str
    status: ExplanationStatus
    initiative_id: str | None
    original_team_id: str | None
    assigned_team_id: str | None
    team_reassigned: bool
    planned_sprints: tuple[int, ...]
    completion_sprint: int | None
    multi_sprint: bool
    task_story_points: float | None
    sp_load_by_sprint: dict[int, float]
    allocations: tuple[dict[str, Any], ...]
    reasons: tuple[DecisionReason, ...]
    risks: tuple[PlanningRisk, ...]
    recommendations: tuple[TaskRecommendation, ...]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "initiative_id": self.initiative_id,
            "original_team_id": self.original_team_id,
            "assigned_team_id": self.assigned_team_id,
            "team_reassigned": self.team_reassigned,
            "planned_sprints": list(self.planned_sprints),
            "completion_sprint": self.completion_sprint,
            "multi_sprint": self.multi_sprint,
            "task_story_points": self.task_story_points,
            "sp_load_by_sprint": dict(self.sp_load_by_sprint),
            "allocations": list(self.allocations),
            "reasons": [item.to_dict() for item in self.reasons],
            "risks": [item.to_dict() for item in self.risks],
            "recommendations": [item.to_dict() for item in self.recommendations],
            "summary": self.summary,
        }


@dataclass(frozen=True)
class InitiativePlanningExplanation:
    initiative_id: str
    task_count: int
    planned_task_count: int
    completed_in_quarter: bool
    completion_sprint: int | None
    selected_for_plan: bool
    selection_reasons: tuple[DecisionReason, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "initiative_id": self.initiative_id,
            "task_count": self.task_count,
            "planned_task_count": self.planned_task_count,
            "completed_in_quarter": self.completed_in_quarter,
            "completion_sprint": self.completion_sprint,
            "selected_for_plan": self.selected_for_plan,
            "selection_reasons": [item.to_dict() for item in self.selection_reasons],
        }


@dataclass(frozen=True)
class PlanExplanationSummary:
    planned_tasks: int
    done_tasks: int
    moved_tasks: int
    unplannable_tasks: int
    completed_initiatives: int
    multi_sprint_tasks: int
    cross_team_tasks: int
    reassigned_tasks: int
    quarter_overflow_tasks: int

    def to_dict(self) -> dict[str, int]:
        return {
            "planned_tasks": self.planned_tasks,
            "done_tasks": self.done_tasks,
            "moved_tasks": self.moved_tasks,
            "unplannable_tasks": self.unplannable_tasks,
            "completed_initiatives": self.completed_initiatives,
            "multi_sprint_tasks": self.multi_sprint_tasks,
            "cross_team_tasks": self.cross_team_tasks,
            "reassigned_tasks": self.reassigned_tasks,
            "quarter_overflow_tasks": self.quarter_overflow_tasks,
        }


_LEGACY_REASON_CODES = {
    ReasonCode.ALREADY_COMPLETED: ReasonCode.DONE_STATUS_ACCEPTED_AS_FACT,
    ReasonCode.MULTI_SPRINT_REQUIRED: ReasonCode.MULTI_SPRINT,
    ReasonCode.INSUFFICIENT_TEAM_CAPACITY: ReasonCode.INSUFFICIENT_SP_CAPACITY,
    ReasonCode.NO_AVAILABLE_ROLE_SPECIALIST: ReasonCode.MISSING_SPECIALIST,
    ReasonCode.OUTSIDE_QUARTER: ReasonCode.QUARTER_OVERFLOW,
}

_REASON_META = {
    ReasonCode.PLANNED_IN_QUARTER: (ExplanationSeverity.INFO, "Включено в квартальный план"),
    ReasonCode.DONE_STATUS_ACCEPTED_AS_FACT: (ExplanationSeverity.INFO, "Статус Done принят как факт"),
    ReasonCode.MULTI_SPRINT: (ExplanationSeverity.WARNING, "Задача выполняется в нескольких спринтах"),
    ReasonCode.CROSS_TEAM_HELP: (ExplanationSeverity.INFO, "Привлечён специалист другой команды"),
    ReasonCode.TEAM_REASSIGNED: (ExplanationSeverity.INFO, "Задача назначена другой команде"),
    ReasonCode.DEPENDENCY_DELAY: (ExplanationSeverity.WARNING, "Старт отложен зависимостью"),
    ReasonCode.INSUFFICIENT_SP_CAPACITY: (ExplanationSeverity.WARNING, "Не хватает SP capacity команды"),
    ReasonCode.INSUFFICIENT_ROLE_CAPACITY: (ExplanationSeverity.WARNING, "Не хватает часов специалистов нужной роли"),
    ReasonCode.INSUFFICIENT_ENGINEER_CAPACITY: (ExplanationSeverity.WARNING, "Не хватает доступного времени инженеров"),
    ReasonCode.INSUFFICIENT_SKILL_CAPACITY: (ExplanationSeverity.WARNING, "Не хватает специалистов с нужными компетенциями"),
    ReasonCode.MISSING_SPECIALIST: (ExplanationSeverity.ERROR, "Нет специалиста требуемой роли"),
    ReasonCode.QUARTER_OVERFLOW: (ExplanationSeverity.ERROR, "Задача не помещается в квартал"),
}


def _status(value: str) -> ExplanationStatus:
    if value == "COMPLETED":
        return ExplanationStatus.DONE
    if value in {"INELIGIBLE_DATA_CONSTRAINT", "UNKNOWN"}:
        return ExplanationStatus.UNPLANNABLE
    return ExplanationStatus(value if value in {"PLANNED", "MOVED"} else "UNPLANNABLE")


def _message(code: ReasonCode, task_id: str, details: dict[str, Any]) -> str:
    if code == ReasonCode.CROSS_TEAM_HELP:
        return (
            f"Для задачи {task_id} привлечён {details['engineer_id']} из "
            f"{details.get('source_team_id') or 'другой команды'} на "
            f"{details['allocated_hours']:g} ч."
        )
    if code == ReasonCode.DEPENDENCY_DELAY:
        if details.get("completion_source") == "ACTUAL":
            return (
                f"{details['blocking_task_id']} завершена по факту в Sprint "
                f"{details['blocking_completion_sprint']}. Зависимая задача может быть "
                f"запланирована с Sprint {details['earliest_allowed_sprint']}."
            )
        return (
            f"Задача зависит от {details['blocking_task_id']}. По квартальному плану "
            f"{details['blocking_task_id']} завершается в Sprint "
            f"{details['blocking_completion_sprint']}, поэтому после резервного спринта "
            f"задача может начаться не раньше Sprint "
            f"{details['earliest_allowed_sprint']}."
        )
    if code == ReasonCode.MULTI_SPRINT:
        return (
            f"Задача {task_id} распределена с {details.get('start_sprint')} по "
            f"спринт {details.get('completion_sprint')}."
        )
    if code == ReasonCode.QUARTER_OVERFLOW:
        return f"Задача {task_id} не может быть завершена в пределах шести спринтов."
    if code == ReasonCode.MISSING_SPECIALIST:
        return f"Для задачи {task_id} нет специалиста роли {details.get('role', '')}."
    title = _REASON_META.get(code, (ExplanationSeverity.WARNING, code.value))[1]
    return f"{title}: {task_id}."


def _reason_from_legacy(reason: Any, task_id: str) -> DecisionReason:
    code = _LEGACY_REASON_CODES.get(reason.code, reason.code)
    severity, title = _REASON_META.get(code, (ExplanationSeverity.WARNING, code.value))
    return DecisionReason(code, severity, title, _message(code, task_id, reason.details), dict(reason.details))


def build_task_explanation(
    result: "TaskPlanResult",
    *,
    blocker_completion_sprints: dict[str, int | None] | None = None,
    blocker_completion_sources: dict[str, str] | None = None,
) -> TaskPlanningExplanation:
    """Build an explanation only from facts present in a completed result."""

    planned_sprints = tuple(allocation.sprint_number for allocation in result.allocations)
    sp_load = {allocation.sprint_number: allocation.capacity_sp_load for allocation in result.allocations}
    allocations = tuple(
        {
            "sprint": allocation.sprint_number,
            "allocated_hours": allocation.allocated_hours,
            "allocated_role_hours": dict(allocation.allocated_role_hours),
            "capacity_sp_load": allocation.capacity_sp_load,
            "planned_completion_sp": allocation.planned_completion_sp,
            "completed_sp": allocation.completed_sp,
            "engineer_allocations": [item.to_dict() for item in allocation.engineer_allocations],
        }
        for allocation in result.allocations
    )
    reasons = [_reason_from_legacy(reason, result.task_id) for reason in result.final_reasons]
    codes = {item.code for item in reasons}
    if result.is_multi_sprint and ReasonCode.MULTI_SPRINT not in codes:
        details = {
            "start_sprint": result.start_sprint,
            "completion_sprint": result.completion_sprint,
            "total_hours": sum(item["allocated_hours"] for item in allocations),
            "total_sp": result.story_points,
            "allocations": list(allocations),
        }
        reasons.append(DecisionReason(ReasonCode.MULTI_SPRINT, ExplanationSeverity.WARNING,
                                      _REASON_META[ReasonCode.MULTI_SPRINT][1],
                                      _message(ReasonCode.MULTI_SPRINT, result.task_id, details), details))
        codes.add(ReasonCode.MULTI_SPRINT)
    cross_team_rows = [
        {**item.to_dict(), "sprint": allocation.sprint_number}
        for allocation in result.allocations
        for item in allocation.engineer_allocations
        if item.cross_team
    ]
    for row in cross_team_rows:
        details = {
            "engineer_id": row["engineer_id"], "source_team_id": row["source_team_id"],
            "assigned_team_id": result.assigned_team_id, "role": row["role"],
            "allocated_hours": row["allocated_hours"], "sprint": row["sprint"],
            "capacity_bucket_hours": row["capacity_bucket_hours"],
            "capacity_used_share": row["capacity_used_share"],
        }
        reasons.append(DecisionReason(ReasonCode.CROSS_TEAM_HELP, ExplanationSeverity.INFO,
                                      _REASON_META[ReasonCode.CROSS_TEAM_HELP][1],
                                      _message(ReasonCode.CROSS_TEAM_HELP, result.task_id, details), details))
    for blocker in result.dependencies:
        completed = (blocker_completion_sprints or {}).get(blocker)
        source = (blocker_completion_sources or {}).get(blocker, "PLANNED")
        # A Done blocker accepted before the quarter does not delay the task.
        if completed is not None and completed > 0:
            earliest = completed + (1 if source == "ACTUAL" else 2)
            details = {"blocking_task_id": blocker, "blocking_completion_sprint": completed,
                       "earliest_allowed_sprint": earliest,
                       "completion_source": source}
            reasons.append(DecisionReason(ReasonCode.DEPENDENCY_DELAY, ExplanationSeverity.WARNING,
                                          _REASON_META[ReasonCode.DEPENDENCY_DELAY][1],
                                          _message(ReasonCode.DEPENDENCY_DELAY, result.task_id, details), details))
    risks: list[PlanningRisk] = []
    if result.is_multi_sprint:
        risks.append(PlanningRisk(RiskCode.MULTI_SPRINT, ExplanationSeverity.WARNING,
                                  "Выполнение задачи зависит от нескольких спринтов.",
                                  {"completion_sprint": result.completion_sprint}))
    if cross_team_rows:
        risks.append(PlanningRisk(RiskCode.CROSS_TEAM_DEPENDENCY, ExplanationSeverity.WARNING,
                                  "Для выполнения требуется ресурс другой команды.",
                                  {"allocations": cross_team_rows}))
    if ReasonCode.QUARTER_OVERFLOW in {reason.code for reason in reasons}:
        risks.append(PlanningRisk(RiskCode.QUARTER_OVERFLOW, ExplanationSeverity.ERROR,
                                  "Прогноз выполнения выходит за пределы квартала.", {}))
    bottlenecks = [reason.to_dict() for reason in reasons if reason.code in {
        ReasonCode.INSUFFICIENT_SP_CAPACITY, ReasonCode.INSUFFICIENT_ROLE_CAPACITY,
        ReasonCode.INSUFFICIENT_ENGINEER_CAPACITY, ReasonCode.INSUFFICIENT_SKILL_CAPACITY,
        ReasonCode.MISSING_SPECIALIST,
    }]
    if bottlenecks:
        risks.append(PlanningRisk(RiskCode.RESOURCE_BOTTLENECK, ExplanationSeverity.WARNING,
                                  "План ограничен доступностью ресурсов.", {"reasons": bottlenecks}))
    recommendations: list[TaskRecommendation] = []
    for recommendation in result.recommendations:
        if recommendation.recommendation_type.value == "REASSIGN_TO_EXISTING_TEAM":
            code = RecommendationCode.REASSIGN_TEAM
            title = "Переназначить другой команде"
            details = {"from_team": result.original_team_id, "to_team": recommendation.target_team_id}
        else:
            code = RecommendationCode.MOVE_NEXT_QUARTER
            title = "Перенести на следующий квартал"
            details = {}
        recommendations.append(TaskRecommendation(code, title, recommendation.reason,
                                                  recommendation.available, details))
    if cross_team_rows:
        recommendations.append(TaskRecommendation(
            RecommendationCode.USE_CROSS_TEAM_ENGINEER, "Использовать помощь другой команды",
            "В плане уже использована доступная помощь другой команды.", True,
            {"allocations": cross_team_rows},
        ))
    if result.status.value == "PLANNED":
        summary = f"Задача запланирована на спринт {result.start_sprint}"
        if result.completion_sprint != result.start_sprint:
            summary += f"–{result.completion_sprint}"
        summary += "."
    elif result.status.value == "COMPLETED":
        summary = "Задача имеет статус Done и принята как завершённая до квартала."
    elif result.status.value in {"UNKNOWN", "INELIGIBLE_DATA_CONSTRAINT"}:
        summary = "Задача не может быть запланирована по имеющимся данным."
    else:
        summary = "Задача не вошла в текущий квартальный план."
    return TaskPlanningExplanation(
        task_id=result.task_id, status=_status(result.status.value), initiative_id=result.initiative_id,
        original_team_id=result.original_team_id, assigned_team_id=result.assigned_team_id,
        team_reassigned=result.team_reassigned, planned_sprints=planned_sprints,
        completion_sprint=result.completion_sprint, multi_sprint=result.is_multi_sprint,
        task_story_points=result.story_points, sp_load_by_sprint=sp_load, allocations=allocations,
        reasons=tuple(reasons), risks=tuple(risks), recommendations=tuple(recommendations), summary=summary,
    )


def build_initiative_explanations(
    explanations: Iterable[TaskPlanningExplanation],
) -> tuple[InitiativePlanningExplanation, ...]:
    grouped: dict[str, list[TaskPlanningExplanation]] = {}
    for item in explanations:
        if item.initiative_id:
            grouped.setdefault(item.initiative_id, []).append(item)
    output: list[InitiativePlanningExplanation] = []
    for initiative_id, tasks in sorted(grouped.items()):
        selected = [task for task in tasks if task.status in {ExplanationStatus.PLANNED, ExplanationStatus.DONE}]
        completed = len(selected) == len(tasks)
        completion = max((task.completion_sprint or 0 for task in selected), default=None) if completed else None
        selection_reasons: tuple[DecisionReason, ...] = ()
        if completed:
            details = {"initiative_id": initiative_id, "task_count": len(tasks), "completion_sprint": completion}
            selection_reasons = (DecisionReason(ReasonCode.CLOSES_INITIATIVE, ExplanationSeverity.INFO,
                                                "Инициатива закрывается в квартале",
                                                f"Все задачи инициативы {initiative_id} вошли в план.", details),)
        output.append(InitiativePlanningExplanation(initiative_id, len(tasks), len(selected), completed,
                                                     completion, bool(selected), selection_reasons))
    return tuple(output)


def build_plan_explanation_summary(
    tasks: Iterable[TaskPlanningExplanation], initiatives: Iterable[InitiativePlanningExplanation]
) -> PlanExplanationSummary:
    rows = tuple(tasks)
    return PlanExplanationSummary(
        planned_tasks=sum(item.status == ExplanationStatus.PLANNED for item in rows),
        done_tasks=sum(item.status == ExplanationStatus.DONE for item in rows),
        moved_tasks=sum(item.status == ExplanationStatus.MOVED for item in rows),
        unplannable_tasks=sum(item.status == ExplanationStatus.UNPLANNABLE for item in rows),
        completed_initiatives=sum(item.completed_in_quarter for item in initiatives),
        multi_sprint_tasks=sum(item.multi_sprint for item in rows),
        cross_team_tasks=sum(any(reason.code == ReasonCode.CROSS_TEAM_HELP for reason in item.reasons) for item in rows),
        reassigned_tasks=sum(item.team_reassigned for item in rows),
        quarter_overflow_tasks=sum(any(reason.code == ReasonCode.QUARTER_OVERFLOW for reason in item.reasons) for item in rows),
    )
