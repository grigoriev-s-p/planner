"""Serializable result models for the initiative-aware quarterly planner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .eligibility import EligibilityResult
from .models import FeasibilityReason
from .scoring import TaskScore


class PlanStatus(str, Enum):
    PLANNED = "PLANNED"
    MOVED = "MOVED"
    INELIGIBLE_DATA_CONSTRAINT = "INELIGIBLE_DATA_CONSTRAINT"
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"


class PlacementOutcome(str, Enum):
    REJECTED = "REJECTED"
    TENTATIVE = "TENTATIVE"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"


class RecommendationType(str, Enum):
    REASSIGN_TO_EXISTING_TEAM = "REASSIGN_TO_EXISTING_TEAM"
    MOVE_TO_NEXT_QUARTER = "MOVE_TO_NEXT_QUARTER"


@dataclass(frozen=True)
class PlanningRecommendation:
    recommendation_type: RecommendationType
    available: bool
    reason: str
    target_team_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_type": self.recommendation_type.value,
            "available": self.available,
            "target_team_id": self.target_team_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EngineerTaskAllocation:
    engineer_id: str
    role: str
    hours: float
    teams: tuple[str, ...] = ()
    task_team: str | None = None
    source_team_id: str | None = None
    source_bucket_capacity_hours: float | None = None

    @property
    def assigned_task_team_id(self) -> str | None:
        return self.task_team

    @property
    def capacity_share(self) -> float | None:
        if not self.source_bucket_capacity_hours:
            return None
        return self.hours / self.source_bucket_capacity_hours

    @property
    def cross_team(self) -> bool:
        if self.task_team is None:
            return False
        if self.source_team_id is not None:
            return self.source_team_id != self.task_team
        return self.task_team not in self.teams

    @property
    def allocated_hours(self) -> float:
        return self.hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer_id": self.engineer_id,
            "role": self.role,
            "hours": self.hours,
            "allocated_hours": self.allocated_hours,
            "teams": list(self.teams),
            "task_team": self.task_team,
            "assigned_task_team_id": self.assigned_task_team_id,
            "source_team_id": self.source_team_id,
            "source_bucket_capacity_hours": self.source_bucket_capacity_hours,
            "capacity_bucket_hours": self.source_bucket_capacity_hours,
            "capacity_share": self.capacity_share,
            "capacity_used_share": self.capacity_share,
            "cross_team": self.cross_team,
        }


@dataclass(frozen=True)
class TaskAllocation:
    task_id: str
    sprint_number: int
    allocated_role_hours: dict[str, float]
    engineer_allocations: tuple[EngineerTaskAllocation, ...]
    task_story_points: float
    capacity_sp_load: float
    planned_completion_sp: float
    completed_sp: float
    is_started: bool
    is_completed: bool
    check_reasons: tuple[FeasibilityReason, ...] = ()

    @property
    def allocated_hours(self) -> float:
        return sum(self.allocated_role_hours.values())

    @property
    def team_sp_consumed(self) -> float:
        """SP consumed by the task team in this sprint."""

        return self.capacity_sp_load

    @property
    def story_points_charged(self) -> float:
        """Backward-compatible alias for team capacity SP load."""

        return self.capacity_sp_load

    @property
    def allocated_story_points(self) -> float:
        """Backward-compatible alias for team capacity SP load."""

        return self.capacity_sp_load

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sprint_number": self.sprint_number,
            "allocated_role_hours": dict(self.allocated_role_hours),
            "engineer_allocations": [item.to_dict() for item in self.engineer_allocations],
            "task_story_points": self.task_story_points,
            "capacity_sp_load": self.capacity_sp_load,
            "planned_completion_sp": self.planned_completion_sp,
            "completed_sp": self.completed_sp,
            "team_sp_consumed": self.team_sp_consumed,
            "story_points_charged": self.story_points_charged,
            "allocated_story_points": self.allocated_story_points,
            "allocated_hours": self.allocated_hours,
            "is_started": self.is_started,
            "is_completed": self.is_completed,
            "check_reasons": [reason.to_dict() for reason in self.check_reasons],
        }


@dataclass(frozen=True)
class PlacementAttempt:
    sprint_number: int
    outcome: PlacementOutcome
    allocated_hours: float = 0.0
    capacity_sp_load: float = 0.0
    planned_completion_sp: float = 0.0
    completed_sp: float = 0.0
    reasons: tuple[FeasibilityReason, ...] = ()

    def with_outcome(self, outcome: PlacementOutcome) -> "PlacementAttempt":
        return PlacementAttempt(
            sprint_number=self.sprint_number,
            outcome=outcome,
            allocated_hours=self.allocated_hours,
            capacity_sp_load=self.capacity_sp_load,
            planned_completion_sp=self.planned_completion_sp,
            completed_sp=self.completed_sp,
            reasons=self.reasons,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprint_number": self.sprint_number,
            "outcome": self.outcome.value,
            "allocated_hours": self.allocated_hours,
            "capacity_sp_load": self.capacity_sp_load,
            "planned_completion_sp": self.planned_completion_sp,
            "completed_sp": self.completed_sp,
            "team_sp_consumed": self.capacity_sp_load,
            "allocated_story_points": self.allocated_story_points,
            "reason_codes": [reason.code.value for reason in self.reasons],
            "reasons": [reason.to_dict() for reason in self.reasons],
        }

    @property
    def allocated_story_points(self) -> float:
        """Backward-compatible alias for team capacity SP load."""

        return self.capacity_sp_load


@dataclass(frozen=True)
class SprintPlan:
    sprint_number: int
    team_capacity_sp: dict[str, float]
    team_sp_used: dict[str, float]
    engineer_hours_used: dict[str, float]
    engineer_team_hours_used: dict[str, dict[str, float]]
    task_allocations: tuple[TaskAllocation, ...]
    warnings: tuple[FeasibilityReason, ...] = ()

    @property
    def completed_sp(self) -> float:
        """SP of tasks completed in this sprint, ready for Sprint Say/Do KPI."""

        return sum(item.completed_sp for item in self.task_allocations)

    @property
    def capacity_sp_load(self) -> float:
        return sum(item.capacity_sp_load for item in self.task_allocations)

    @property
    def planned_completion_sp(self) -> float:
        return sum(item.planned_completion_sp for item in self.task_allocations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprint_number": self.sprint_number,
            "team_capacity_sp": dict(self.team_capacity_sp),
            "team_sp_used": dict(self.team_sp_used),
            "engineer_hours_used": dict(self.engineer_hours_used),
            "engineer_team_hours_used": {
                engineer_id: dict(team_hours)
                for engineer_id, team_hours in self.engineer_team_hours_used.items()
            },
            "task_allocations": [item.to_dict() for item in self.task_allocations],
            "capacity_sp_load": self.capacity_sp_load,
            "planned_completion_sp": self.planned_completion_sp,
            "completed_sp": self.completed_sp,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(frozen=True)
class TaskPlanResult:
    task_id: str
    initiative_id: str | None
    team_id: str | None
    original_team_id: str | None
    assigned_team_id: str | None
    team_reassigned: bool
    team_assignment_reason: str | None
    story_points: float | None
    source_status: str | None
    source_rung: float | None
    eligibility: EligibilityResult
    score: TaskScore
    priority_order: int | None
    status: PlanStatus
    start_sprint: int | None
    completion_sprint: int | None
    remaining_role_hours: dict[str, float]
    allocations: tuple[TaskAllocation, ...]
    dependencies: tuple[str, ...]
    final_reasons: tuple[FeasibilityReason, ...]
    placement_attempts: tuple[PlacementAttempt, ...]
    baseline_start_date: str | None
    baseline_end_date: str | None
    forecast_start_date: str | None
    forecast_end_date: str | None
    start_shift_days: int | None
    end_shift_days: int | None
    start_shift_direction: str | None
    end_shift_direction: str | None
    outside_quarter: bool
    recommendations: tuple[PlanningRecommendation, ...] = ()

    @property
    def is_multi_sprint(self) -> bool:
        return len(self.allocations) > 1

    @property
    def completed_sp(self) -> float:
        """Actual SP: source Done tasks are complete; forecasts remain zero."""

        if self.status == PlanStatus.COMPLETED:
            return self.story_points or 0.0
        return sum(item.completed_sp for item in self.allocations)

    @property
    def task_story_points(self) -> float | None:
        return self.story_points

    @property
    def capacity_sp_load(self) -> float:
        return sum(item.capacity_sp_load for item in self.allocations)

    @property
    def planned_completion_sp(self) -> float:
        return sum(item.planned_completion_sp for item in self.allocations)

    @property
    def reasons(self) -> tuple[FeasibilityReason, ...]:
        """Backward-compatible alias for final decision reasons only."""

        return self.final_reasons

    @property
    def rejected_attempts(self) -> tuple[PlacementAttempt, ...]:
        return tuple(
            item for item in self.placement_attempts if item.outcome == PlacementOutcome.REJECTED
        )

    @property
    def priority_value(self) -> float | None:
        """Backward-compatible access to raw source rung; it is not used for ordering."""

        return self.source_rung

    def to_dict(self) -> dict[str, Any]:
        from .explanations import build_task_explanation

        explanation = build_task_explanation(self)
        return {
            "task_id": self.task_id,
            "initiative_id": self.initiative_id,
            "team_id": self.team_id,
            "original_team_id": self.original_team_id,
            "assigned_team_id": self.assigned_team_id,
            "team_reassigned": self.team_reassigned,
            "team_assignment_reason": self.team_assignment_reason,
            "story_points": self.story_points,
            "task_story_points": self.task_story_points,
            "source_status": self.source_status,
            "priority_value": self.priority_value,
            "source_rung": self.source_rung,
            "eligibility": self.eligibility.to_dict(),
            "score": self.score.to_dict(),
            "priority_order": self.priority_order,
            "status": self.status.value,
            "start_sprint": self.start_sprint,
            "completion_sprint": self.completion_sprint,
            "remaining_role_hours": dict(self.remaining_role_hours),
            "allocations": [item.to_dict() for item in self.allocations],
            "dependencies": list(self.dependencies),
            "reasons": [reason.to_dict() for reason in self.final_reasons],
            "final_decision": [reason.to_dict() for reason in self.final_reasons],
            "placement_attempts": [attempt.to_dict() for attempt in self.placement_attempts],
            "rejected_attempts": [attempt.to_dict() for attempt in self.rejected_attempts],
            "baseline_start_date": self.baseline_start_date,
            "baseline_end_date": self.baseline_end_date,
            "forecast_start_date": self.forecast_start_date,
            "forecast_end_date": self.forecast_end_date,
            "start_shift_days": self.start_shift_days,
            "end_shift_days": self.end_shift_days,
            "start_shift_direction": self.start_shift_direction,
            "end_shift_direction": self.end_shift_direction,
            "outside_quarter": self.outside_quarter,
            "recommendations": [item.to_dict() for item in self.recommendations],
            "is_multi_sprint": self.is_multi_sprint,
            "capacity_sp_load": self.capacity_sp_load,
            "planned_completion_sp": self.planned_completion_sp,
            "completed_sp": self.completed_sp,
            "explanation": explanation.to_dict(),
        }


@dataclass(frozen=True)
class QuarterPlan:
    sprints: tuple[SprintPlan, ...]
    task_results: tuple[TaskPlanResult, ...]
    warnings: tuple[FeasibilityReason, ...] = ()
    quarter_start_date: str | None = None
    quarter_end_date: str | None = None

    @property
    def planned_tasks(self) -> tuple[TaskPlanResult, ...]:
        return tuple(item for item in self.task_results if item.status == PlanStatus.PLANNED)

    @property
    def moved_tasks(self) -> tuple[TaskPlanResult, ...]:
        return tuple(item for item in self.task_results if item.status == PlanStatus.MOVED)

    @property
    def ineligible_tasks(self) -> tuple[TaskPlanResult, ...]:
        return tuple(
            item
            for item in self.task_results
            if item.status == PlanStatus.INELIGIBLE_DATA_CONSTRAINT
        )

    @property
    def eligible_tasks(self) -> tuple[TaskPlanResult, ...]:
        return tuple(
            item
            for item in self.task_results
            if (item.source_status or "").strip().casefold() in {"todo", "inprogress"}
            and item.eligibility.eligible
        )

    @property
    def placeholder_tasks(self) -> tuple[TaskPlanResult, ...]:
        return tuple(item for item in self.task_results if item.status == PlanStatus.UNKNOWN)

    @property
    def precompleted_tasks(self) -> tuple[TaskPlanResult, ...]:
        return tuple(item for item in self.task_results if item.status == PlanStatus.COMPLETED)

    @property
    def multi_sprint_tasks(self) -> tuple[TaskPlanResult, ...]:
        return tuple(item for item in self.planned_tasks if item.is_multi_sprint)

    @property
    def completed_initiative_ids(self) -> tuple[str, ...]:
        by_initiative: dict[str, list[TaskPlanResult]] = {}
        for task in self.task_results:
            if task.initiative_id:
                by_initiative.setdefault(task.initiative_id, []).append(task)
        return tuple(
            sorted(
                initiative_id
                for initiative_id, tasks in by_initiative.items()
                if tasks
                and all(
                    task.status in {PlanStatus.PLANNED, PlanStatus.COMPLETED}
                    for task in tasks
                )
            )
        )

    @property
    def completed_story_points(self) -> float:
        return sum(
            allocation.completed_sp
            for sprint in self.sprints
            for allocation in sprint.task_allocations
        )

    @property
    def capacity_sp_load(self) -> float:
        return sum(
            allocation.capacity_sp_load
            for sprint in self.sprints
            for allocation in sprint.task_allocations
        )

    @property
    def planned_completion_story_points(self) -> float:
        return sum(
            allocation.planned_completion_sp
            for sprint in self.sprints
            for allocation in sprint.task_allocations
        )

    @property
    def allocated_story_points(self) -> float:
        """Backward-compatible alias for total capacity SP load."""

        return self.capacity_sp_load

    @property
    def available_story_points(self) -> float:
        return sum(sum(sprint.team_capacity_sp.values()) for sprint in self.sprints)

    @property
    def task_explanations(self):
        """Structured, JSON-safe explanations built from committed plan facts."""

        from .explanations import build_task_explanation

        completions = {
            item.task_id: item.completion_sprint
            for item in self.task_results
            if item.completion_sprint is not None
        }
        return tuple(
            build_task_explanation(item, blocker_completion_sprints=completions)
            for item in self.task_results
        )

    @property
    def initiative_explanations(self):
        from .explanations import build_initiative_explanations

        return build_initiative_explanations(self.task_explanations)

    @property
    def explainability_summary(self):
        from .explanations import build_plan_explanation_summary

        return build_plan_explanation_summary(
            self.task_explanations, self.initiative_explanations
        )

    def summary(self) -> dict[str, Any]:
        return {
            "total_tasks": len(self.task_results),
            "planned_tasks": len(self.planned_tasks),
            "moved_tasks": len(self.moved_tasks),
            "eligible_tasks": len(self.eligible_tasks),
            "blocked_by_missing_specialists": len(self.ineligible_tasks),
            "placeholder_tasks": len(self.placeholder_tasks),
            "precompleted_tasks": len(self.precompleted_tasks),
            "multi_sprint_tasks": len(self.multi_sprint_tasks),
            "completed_initiatives": len(self.completed_initiative_ids),
            "allocated_story_points": self.allocated_story_points,
            "completed_story_points": self.completed_story_points,
            "capacity_sp_load": self.capacity_sp_load,
            "planned_completion_story_points": self.planned_completion_story_points,
            "available_story_points": self.available_story_points,
            "quarter_start_date": self.quarter_start_date,
            "quarter_end_date": self.quarter_end_date,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprints": [sprint.to_dict() for sprint in self.sprints],
            "task_results": [task.to_dict() for task in self.task_results],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "summary": self.summary(),
            "quarter_start_date": self.quarter_start_date,
            "quarter_end_date": self.quarter_end_date,
            "explanations": {
                "tasks": [item.to_dict() for item in self.task_explanations],
                "initiatives": [item.to_dict() for item in self.initiative_explanations],
                "summary": self.explainability_summary.to_dict(),
            },
        }
