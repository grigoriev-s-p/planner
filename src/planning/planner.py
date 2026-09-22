"""Deterministic score-driven atomic planner for a six-sprint quarter."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.skills import canonicalize_skill

from .allocation import allocate_available_hours
from .capacity import calculate_team_capacity
from .dependency_rules import dependency_timing
from .eligibility import EligibilityContext, EligibilityResult, EligibilityService
from .feasibility import FeasibilityContext, FeasibilityService
from .models import FeasibilityReason
from .plan_models import (
    PlacementAttempt,
    PlacementOutcome,
    PlanStatus,
    PlanningRecommendation,
    QuarterPlan,
    RecommendationType,
    TaskAllocation,
    TaskPlanResult,
)
from .ownership import evaluate_task_ownership, merge_task_ownership
from .planning_calendar import PlanningCalendar, shift_days, shift_direction
from .reasons import ReasonCode
from .scoring import (
    PlanningContext,
    ScoringConfig,
    TaskScore,
    calculate_task_score,
    date_ordinal,
    score_sort_key,
)
from .sprint_state import SprintLedger
from .story_points import (
    StoryPointDistribution,
    distribute_capacity_sp_load,
)
from .task_states import is_historical_done


SPRINT_COUNT = 6
PLANNABLE_SOURCE_STATUSES = {"todo", "inprogress"}
COMPLETED_SOURCE_STATUS = "done"


@dataclass(frozen=True)
class _TaskSimulation:
    assigned_team_id: str
    success: bool
    ledgers: dict[int, SprintLedger]
    allocations: tuple[TaskAllocation, ...]
    attempts: tuple[PlacementAttempt, ...]
    remaining: dict[str, float]
    start_sprint: int | None
    completion_sprint: int | None
    no_hours_warning: FeasibilityReason | None


@dataclass(frozen=True)
class _InitiativeWork:
    priority_profile: tuple[float, ...]
    total_sp: float
    total_hours: float


def initiative_priority_profile(
    tasks: Iterable[Mapping[str, Any]],
) -> tuple[float, ...]:
    """Return the official initiative priority: its maximum task rung."""

    rungs = tuple(_number(task.get("rung")) or 0.0 for task in tasks)
    return (max(rungs),) if rungs else ()


def compare_priority_profiles(
    left: Iterable[float],
    right: Iterable[float],
) -> int:
    """Compare rung profiles lexicographically without rewarding a longer tail.

    Returns 1 when ``left`` has higher priority, -1 when ``right`` has higher
    priority, and 0 when the shared prefix is equal. Extra values after an
    equal shared prefix do not affect the result.
    """

    for left_rung, right_rung in zip(left, right):
        if math.isclose(left_rung, right_rung):
            continue
        return 1 if left_rung > right_rung else -1
    return 0


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or (
        isinstance(value, str) and not value.strip()
    )


def _text(value: Any) -> str | None:
    return None if _missing(value) else str(value).strip()


def _number(value: Any) -> float | None:
    if _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _boolean(value: Any) -> bool:
    return value is True or str(value).strip().casefold() in {"true", "1", "yes"}


def _select_priority_task_id(
    candidate_ids: Iterable[str],
    tasks: Mapping[str, Mapping[str, Any]],
    scores: Mapping[str, TaskScore],
    reference_plan: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Select by absolute rung, optional start date, then secondary scoring.

    A start date is comparable only when every candidate at the winning rung
    has one. Otherwise the existing score and stable ID tie-break decide, so a
    missing date is never converted into an artificial date.
    """

    candidates = tuple(sorted(candidate_ids))
    if not candidates:
        raise ValueError("At least one planning candidate is required.")
    highest_rung = max(
        _number(tasks[task_id].get("rung")) or 0.0 for task_id in candidates
    )
    highest_rung_ids = tuple(
        task_id
        for task_id in candidates
        if (_number(tasks[task_id].get("rung")) or 0.0) == highest_rung
    )
    planned_starts = {
        task_id: date_ordinal(tasks[task_id].get("planned_start"))
        for task_id in highest_rung_ids
    }
    use_planned_start = all(value is not None for value in planned_starts.values())
    return min(
        highest_rung_ids,
        key=lambda task_id: (
            score_sort_key(tasks[task_id], scores[task_id], use_planned_start=use_planned_start)[0],
            -scores[task_id].dynamic_score,
            -scores[task_id].base_score,
            (
                tuple(reference_plan.get(task_id, {}).get("planned_sprints") or ())[:1]
                or (999,)
            ),
            score_sort_key(tasks[task_id], scores[task_id], use_planned_start=use_planned_start)[1],
            score_sort_key(tasks[task_id], scores[task_id], use_planned_start=use_planned_start)[3],
            task_id,
        ) if reference_plan is not None else score_sort_key(
            tasks[task_id], scores[task_id], use_planned_start=use_planned_start
        ),
    )


def sort_tasks_for_planning(
    tasks: Iterable[Mapping[str, Any]],
    dependencies: Iterable[Mapping[str, Any]],
    scoring_config: ScoringConfig | None = None,
) -> list[dict[str, Any]]:
    """Return the score order assuming each selected task is then committed."""

    task_rows = [dict(task) for task in tasks]
    dependency_rows = [dict(row) for row in dependencies]
    scoring_context = PlanningContext.from_records(task_rows, dependency_rows)
    config = scoring_config or ScoringConfig()
    candidates = {
        task_id: dict(task)
        for task in task_rows
        if (task_id := _text(task.get("task_id")))
        and not _boolean(task.get("is_placeholder"))
        and (_text(task.get("status")) or "").casefold() in PLANNABLE_SOURCE_STATUSES
    }
    outgoing: dict[str, set[str]] = {task_id: set() for task_id in candidates}
    indegree = {task_id: 0 for task_id in candidates}
    for dependency in dependency_rows:
        blocker = _text(dependency.get("blocking_task_id"))
        blocked = _text(dependency.get("blocked_task_id"))
        if blocker in candidates and blocked in candidates and blocked not in outgoing[blocker]:
            outgoing[blocker].add(blocked)
            indegree[blocked] += 1

    ready = {task_id for task_id, degree in indegree.items() if degree == 0}
    ordered: list[dict[str, Any]] = []
    while ready:
        scored = {
            task_id: calculate_task_score(candidates[task_id], scoring_context, config)
            for task_id in ready
        }
        task_id = _select_priority_task_id(ready, candidates, scored)
        ready.remove(task_id)
        ordered.append(candidates[task_id])
        scoring_context = scoring_context.with_committed(task_id)
        for dependent in sorted(outgoing[task_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.add(dependent)

    planned_ids = {_text(task.get("task_id")) for task in ordered}
    unresolved = sorted(
        (task for task_id, task in candidates.items() if task_id not in planned_ids),
        key=lambda task: score_sort_key(
            task, calculate_task_score(task, scoring_context, config)
        ),
    )
    return ordered + unresolved


def _deduplicate_reasons(reasons: Iterable[FeasibilityReason]) -> tuple[FeasibilityReason, ...]:
    unique: list[FeasibilityReason] = []
    seen: set[tuple[str, str]] = set()
    for reason in reasons:
        key = (reason.code.value, repr(sorted(reason.details.items())))
        if key not in seen:
            seen.add(key)
            unique.append(reason)
    return tuple(unique)


class BaselineQuarterPlanner:
    """Score-driven dependency-aware planner with atomic resource commits."""

    def __init__(
        self,
        context: FeasibilityContext,
        quarter_start_date: Any = None,
        scoring_config: ScoringConfig | None = None,
        *,
        allow_team_reassignment: bool = True,
        allow_cross_team: bool = True,
        cross_team_task_ids: set[str] | None = None,
        start_sprint: int = 1,
        actual_completed_tasks: dict[str, int] | None = None,
        unavailable_engineer_ids_by_sprint: Mapping[int, set[str]] | None = None,
        engineer_availability_rates_by_sprint: Mapping[int, Mapping[str, float]] | None = None,
        carry_over_engineer_hours_by_task: Mapping[str, Mapping[str, float]] | None = None,
        carry_over_task_ids: set[str] | None = None,
        prior_task_ownership_by_task: Mapping[str, Any] | None = None,
        story_points_override_by_task: Mapping[str, float] | None = None,
        original_total_required_hours_by_task: Mapping[str, float] | None = None,
        preferred_engineer_hours_by_task: Mapping[str, Mapping[str, float]] | None = None,
        reference_plan_by_task: Mapping[str, Mapping[str, Any]] | None = None,
        earliest_sprint_by_task: Mapping[str, int] | None = None,
        reserved_resource_usage_by_sprint: Mapping[int, Mapping[str, Any]] | None = None,
        fixed_planned_completion_by_task: Mapping[str, int] | None = None,
    ):
        self.context = context
        self.feasibility = FeasibilityService(context)
        self.calendar = PlanningCalendar.from_value(quarter_start_date)
        self.scoring_config = scoring_config or ScoringConfig()
        self.allow_team_reassignment = allow_team_reassignment
        self.allow_cross_team = allow_cross_team
        # Recommendation simulations may open external help only for explicitly
        # accepted tasks.  The regular plan keeps its former all-or-nothing API.
        self.cross_team_task_ids = frozenset(cross_team_task_ids or ())
        if not 1 <= start_sprint <= SPRINT_COUNT:
            raise ValueError(f"start_sprint must be between 1 and {SPRINT_COUNT}")
        self.start_sprint = start_sprint
        self.actual_completed_tasks = dict(actual_completed_tasks or {})
        self.fixed_planned_completion_by_task = dict(fixed_planned_completion_by_task or {})
        self.reserved_resource_usage_by_sprint = dict(reserved_resource_usage_by_sprint or {})
        self.unavailable_engineer_ids_by_sprint = {
            int(sprint): {str(engineer_id) for engineer_id in engineer_ids}
            for sprint, engineer_ids in (unavailable_engineer_ids_by_sprint or {}).items()
        }
        self.engineer_availability_rates_by_sprint = {
            int(sprint): {
                str(engineer_id): float(rate)
                for engineer_id, rate in rates.items()
            }
            for sprint, rates in (engineer_availability_rates_by_sprint or {}).items()
        }
        for sprint, engineer_ids in self.unavailable_engineer_ids_by_sprint.items():
            rates = self.engineer_availability_rates_by_sprint.setdefault(sprint, {})
            for engineer_id in engineer_ids:
                rates[engineer_id] = 0.0
        self.carry_over_engineer_hours_by_task = {
            str(task_id): {
                str(engineer_id): max(float(hours), 0.0)
                for engineer_id, hours in allocations.items()
                if float(hours) > 0
            }
            for task_id, allocations in (carry_over_engineer_hours_by_task or {}).items()
        }
        self.carry_over_task_ids = frozenset(str(task_id) for task_id in (carry_over_task_ids or ()))
        self.prior_task_ownership_by_task = dict(prior_task_ownership_by_task or {})
        self.story_points_override_by_task = {
            str(task_id): max(float(value), 0.0)
            for task_id, value in (story_points_override_by_task or {}).items()
            if value is not None
        }
        self.original_total_required_hours_by_task = {
            str(task_id): max(float(value), 0.0)
            for task_id, value in (original_total_required_hours_by_task or {}).items()
            if value is not None
        }
        self.preferred_engineer_hours_by_task = {
            str(task_id): {
                str(engineer_id): max(float(hours), 0.0)
                for engineer_id, hours in allocations.items()
                if float(hours) > 0
            }
            for task_id, allocations in (preferred_engineer_hours_by_task or {}).items()
        }
        self.reference_plan_by_task = {
            str(task_id): dict(value)
            for task_id, value in (reference_plan_by_task or {}).items()
        }
        self.earliest_sprint_by_task = {
            str(task_id): max(self.start_sprint, min(int(sprint), SPRINT_COUNT + 1))
            for task_id, sprint in (earliest_sprint_by_task or {}).items()
        }
        self.tasks = {
            task_id: dict(task)
            for task in context.tasks
            if (task_id := _text(task.get("task_id"))) is not None
        }
        self.engineer_skills_by_id: dict[str, set[str]] = {}
        self.engineer_skill_effective_from: dict[str, dict[str, int]] = {}
        for row in context.engineer_skills:
            engineer_id = _text(row.get("engineer_id"))
            technology = canonicalize_skill(_text(row.get("technology")))
            if engineer_id and technology:
                self.engineer_skills_by_id.setdefault(engineer_id, set()).add(technology)
                starts = self.engineer_skill_effective_from.setdefault(engineer_id, {})
                starts[technology] = min(starts.get(technology, 7),
                                         int(row.get("effective_from_sprint") or 1))
        self.eligibility = EligibilityService(
            EligibilityContext.from_records(
                task_role_effort=context.task_role_effort,
                engineers=context.engineers,
            )
        )
        self.eligibility_results = {
            task_id: self.eligibility.check(task)
            for task_id, task in self.tasks.items()
        }
        self.ineligible_task_ids = frozenset(
            task_id
            for task_id, task in self.tasks.items()
            if not self.eligibility_results[task_id].eligible
            and not _boolean(task.get("is_placeholder"))
            and (_text(task.get("status")) or "").casefold() in PLANNABLE_SOURCE_STATUSES
        )
        self.scoring_context = PlanningContext.from_records(
            context.tasks,
            context.dependencies,
            excluded_task_ids=self.ineligible_task_ids,
        )
        self.priority_order: dict[str, int] = {}
        self.team_ids = tuple(
            sorted(
                {
                    team_id
                    for row in (*context.team_history, *context.engineers)
                    if (team_id := _text(row.get("team_id"))) is not None
                }
            )
        )
        self._initiative_work = self._build_initiative_work()
        self._primary_initiative_id: str | None = None
        self._initiative_choice_queue: list[str] = []

    def _covered_allocated_skills(self, allocations: Iterable[Any]) -> set[str]:
        return {
            skill
            for allocation in allocations
            for item in allocation.engineer_allocations
            if item.hours > 0
            for skill, first_sprint in self.engineer_skill_effective_from.get(
                item.engineer_id, {}
            ).items()
            if first_sprint <= allocation.sprint_number
        }

    def _build_initiative_work(self) -> dict[str, _InitiativeWork]:
        """Return deterministic initiative objective keys.

        Exact plan comparison uses completed count first, the rung profile as
        secondary objective and completion time as tertiary objective.
        """

        members: dict[str, list[dict[str, Any]]] = {}
        for task_id in self.scoring_context.eligible_task_ids:
            task = self.tasks[task_id]
            initiative_id = _text(task.get("initiative_id"))
            if initiative_id in self.scoring_context.reliable_initiative_ids:
                members.setdefault(initiative_id, []).append(task)
        keys: dict[str, _InitiativeWork] = {}
        for initiative_id, tasks in members.items():
            total_sp = sum(max(_number(task.get("story_points")) or 0.0, 0.0) for task in tasks)
            total_hours = sum(
                sum(self.feasibility.task_role_requirements(_text(task.get("task_id")) or "").values())
                for task in tasks
            )
            keys[initiative_id] = _InitiativeWork(
                priority_profile=initiative_priority_profile(tasks),
                total_sp=total_sp,
                total_hours=total_hours,
            )
        return keys

    def _same_rung_initiative_choice(
        self,
        selectable_ids: Iterable[str],
        pending: Mapping[str, Mapping[str, Any]],
        ledgers: dict[int, SprintLedger],
        completed_tasks: dict[str, int],
        results: dict[str, TaskPlanResult],
    ) -> str | None:
        """Choose initiative completion only inside the highest task-rung tier.

        An initiative participates only when all of its remaining tasks have
        the current highest rung. This prevents initiative completion from
        pulling a lower-rung task ahead of any feasible higher-rung task.
        """

        candidates = tuple(selectable_ids)
        if len(candidates) < 2:
            return None
        highest_rung = max(
            _number(pending[task_id].get("rung")) or 0.0
            for task_id in candidates
        )
        highest_ids = {
            task_id
            for task_id in candidates
            if (_number(pending[task_id].get("rung")) or 0.0) == highest_rung
        }
        initiative_ids = {
            _text(pending[task_id].get("initiative_id")) for task_id in highest_ids
        } - {None}
        tie_pending: dict[str, Mapping[str, Any]] = {}
        for initiative_id in initiative_ids:
            remaining = {
                task_id: task
                for task_id, task in pending.items()
                if _text(task.get("initiative_id")) == initiative_id
            }
            if remaining and all(
                (_number(task.get("rung")) or 0.0) == highest_rung
                for task in remaining.values()
            ):
                tie_pending.update(remaining)
        if not tie_pending:
            return None
        return self._primary_initiative_choice(
            tie_pending, ledgers, completed_tasks, results
        )

    def _compare_initiative_orders(
        self,
        left: tuple[str, ...],
        right: tuple[str, ...],
    ) -> int:
        """Compare equally sized completed-initiative sequences by rung profile."""

        for left_id, right_id in zip(left, right):
            comparison = compare_priority_profiles(
                self._initiative_work[left_id].priority_profile,
                self._initiative_work[right_id].priority_profile,
            )
            if comparison:
                return comparison
        return 0

    def _initiative_plan_is_better(
        self,
        candidate: tuple[int, tuple[str, ...], tuple[int, ...]],
        current: tuple[int, tuple[str, ...], tuple[int, ...]],
    ) -> bool:
        """Apply count, max rung, planned date, completion, then stable ID."""

        if candidate[0] != current[0]:
            return candidate[0] > current[0]
        profile_comparison = self._compare_initiative_orders(
            candidate[1], current[1]
        )
        if profile_comparison:
            return profile_comparison > 0
        for candidate_id, current_id in zip(candidate[1], current[1]):
            candidate_dates = tuple(
                date_ordinal(task.get("planned_start"))
                for task in self.tasks.values()
                if _text(task.get("initiative_id")) == candidate_id
            )
            current_dates = tuple(
                date_ordinal(task.get("planned_start"))
                for task in self.tasks.values()
                if _text(task.get("initiative_id")) == current_id
            )
            if (
                candidate_dates
                and current_dates
                and all(value is not None for value in (*candidate_dates, *current_dates))
                and min(candidate_dates) != min(current_dates)
            ):
                return min(candidate_dates) < min(current_dates)
        if candidate[2] != current[2]:
            return candidate[2] < current[2]
        return candidate[1] < current[1]

    @staticmethod
    def _initiative_completion_sprint(
        initiative_id: str,
        remaining: Mapping[str, Mapping[str, Any]],
        completed: Mapping[str, int],
    ) -> int:
        return max(
            completed[task_id]
            for task_id, task in remaining.items()
            if _text(task.get("initiative_id")) == initiative_id
        )

    def _task_result(
        self,
        task: Mapping[str, Any],
        *,
        status: PlanStatus,
        start_sprint: int | None = None,
        completion_sprint: int | None = None,
        remaining_role_hours: dict[str, float] | None = None,
        allocations: tuple[TaskAllocation, ...] = (),
        dependencies: tuple[str, ...] = (),
        final_reasons: tuple[FeasibilityReason, ...] = (),
        placement_attempts: tuple[PlacementAttempt, ...] = (),
        outside_quarter: bool = False,
        task_score: TaskScore | None = None,
        eligibility_result: EligibilityResult | None = None,
        assigned_team_id: str | None = None,
        team_assignment_reason: str | None = None,
        recommendations: tuple[PlanningRecommendation, ...] = (),
    ) -> TaskPlanResult:
        task_id = _text(task.get("task_id")) or ""
        score = task_score or calculate_task_score(
            task, self.scoring_context, self.scoring_config
        )
        eligibility = eligibility_result or self.eligibility_results.get(
            task_id, EligibilityResult(task_id=task_id, eligible=True)
        )
        baseline_start = _text(task.get("planned_start"))
        baseline_end = _text(task.get("planned_end"))
        forecast_start = (
            self.calendar.sprint_start(start_sprint) if self.calendar and start_sprint else None
        )
        forecast_end = (
            self.calendar.sprint_end(completion_sprint)
            if self.calendar and completion_sprint and completion_sprint > 0
            else None
        )
        start_shift = shift_days(forecast_start, baseline_start)
        end_shift = shift_days(forecast_end, baseline_end)
        original_team_id = _text(task.get("team_id"))
        selected_team_id = assigned_team_id or original_team_id
        return TaskPlanResult(
            task_id=task_id,
            initiative_id=_text(task.get("initiative_id")),
            team_id=selected_team_id,
            original_team_id=original_team_id,
            assigned_team_id=selected_team_id,
            team_reassigned=(
                selected_team_id is not None and selected_team_id != original_team_id
            ),
            team_assignment_reason=team_assignment_reason,
            story_points=_number(task.get("story_points")),
            source_status=_text(task.get("status")),
            source_rung=_number(task.get("rung")),
            eligibility=eligibility,
            score=score,
            priority_order=self.priority_order.get(task_id),
            status=status,
            start_sprint=start_sprint,
            completion_sprint=completion_sprint,
            remaining_role_hours=remaining_role_hours or {},
            allocations=allocations,
            dependencies=dependencies,
            final_reasons=final_reasons,
            placement_attempts=placement_attempts,
            baseline_start_date=baseline_start,
            baseline_end_date=baseline_end,
            forecast_start_date=forecast_start.isoformat() if forecast_start else None,
            forecast_end_date=forecast_end.isoformat() if forecast_end else None,
            start_shift_days=start_shift,
            end_shift_days=end_shift,
            start_shift_direction=shift_direction(start_shift),
            end_shift_direction=shift_direction(end_shift),
            outside_quarter=outside_quarter,
            recommendations=recommendations,
        )

    def _incoming_blockers(self, task_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    blocker
                    for row in self.feasibility.incoming_task_dependencies(task_id)
                    if (blocker := _text(row.get("blocking_task_id"))) is not None
                }
            )
        )

    def _dependency_start(
        self,
        task_id: str,
        results: dict[str, TaskPlanResult],
        completed_tasks: dict[str, int],
    ) -> tuple[int | None, tuple[FeasibilityReason, ...], tuple[str, ...]]:
        blockers = self._incoming_blockers(task_id)
        earliest = self.earliest_sprint_by_task.get(task_id, self.start_sprint)
        reasons: list[FeasibilityReason] = []
        for blocker in blockers:
            timing = dependency_timing(
                blocker,
                completed_tasks,
                actual_completed_tasks=self.actual_completed_tasks,
            )
            if timing is not None:
                earliest = max(earliest, timing.earliest_start_sprint)
                continue
            blocker_result = results.get(blocker)
            blocker_task = self.tasks.get(blocker)
            details = {
                "blocking_task_id": blocker,
                "blocked_task_id": task_id,
                "blocking_plan_status": blocker_result.status.value if blocker_result else None,
                "blocking_is_placeholder": bool(
                    blocker_task and _boolean(blocker_task.get("is_placeholder"))
                ),
            }
            reasons.append(FeasibilityReason(ReasonCode.DEPENDENCY_NOT_COMPLETED, details))
            diagnostic_code = (
                ReasonCode.MISSING_DEPENDENCY_REFERENCE
                if blocker_task is None
                else ReasonCode.UNKNOWN_DEPENDENCY_DATE
            )
            reasons.append(FeasibilityReason(diagnostic_code, details))
        return (None if reasons else earliest), tuple(reasons), blockers

    @staticmethod
    def _clone_ledgers(ledgers: dict[int, SprintLedger]) -> dict[int, SprintLedger]:
        return {number: ledger.clone() for number, ledger in ledgers.items()}

    @staticmethod
    def _attempt_bottleneck_reasons(
        attempts: Iterable[PlacementAttempt],
    ) -> tuple[FeasibilityReason, ...]:
        """Return one final resource reason per code from diagnostic attempts."""

        by_code: dict[ReasonCode, FeasibilityReason] = {}
        for attempt in attempts:
            for reason in attempt.reasons:
                if reason.code == ReasonCode.MULTI_SPRINT_REQUIRED:
                    continue
                by_code.setdefault(reason.code, reason)
        return tuple(by_code[code] for code in sorted(by_code, key=lambda item: item.value))

    def _simulate_task(
        self,
        task: Mapping[str, Any],
        earliest_sprint: int,
        ledgers: dict[int, SprintLedger],
        completed_tasks: dict[str, int],
        assigned_team_id: str,
    ) -> tuple[
        bool,
        dict[int, SprintLedger],
        tuple[TaskAllocation, ...],
        tuple[PlacementAttempt, ...],
        dict[str, float],
        int | None,
        int | None,
        FeasibilityReason | None,
    ]:
        task_id = _text(task.get("task_id")) or ""
        team_id = assigned_team_id
        source_story_points = _number(task.get("story_points"))
        story_points = self.story_points_override_by_task.get(task_id, source_story_points)
        original_requirements = self.feasibility.task_role_requirements(task_id)
        remaining = dict(original_requirements)
        # The immutable original total is used by replanning to derive the
        # remaining SP before entering the planner.  Allocation must pair that
        # SP value with the current remaining role-hours, otherwise a changed
        # scope distorts hours-per-SP and can make valid carry-over disappear.
        total_required_hours = sum(original_requirements.values())
        trial_ledgers = self._clone_ledgers(ledgers)
        allocations: list[TaskAllocation] = []
        attempts: list[PlacementAttempt] = []
        start_sprint: int | None = None
        completion_sprint: int | None = None
        no_hours_warning = None
        assigned_capacity_sp = 0.0
        allocated_hours_total = 0.0
        carry_over_remaining = dict(self.carry_over_engineer_hours_by_task.get(task_id, {}))
        preferred_engineer_hours = dict(
            self.preferred_engineer_hours_by_task.get(task_id, {})
        )
        for engineer_id, hours in carry_over_remaining.items():
            preferred_engineer_hours[engineer_id] = max(
                preferred_engineer_hours.get(engineer_id, 0.0), hours
            )

        if story_points is not None and story_points > 0 and total_required_hours <= 0:
            no_hours_warning = FeasibilityReason(
                ReasonCode.STORY_POINTS_WITHOUT_HOURS,
                {
                    "task_id": task_id,
                    "story_points": source_story_points,
                    "planning_story_points": story_points,
                    "fallback": "Distribute capacity SP load without inventing hours.",
                },
            )

        if total_required_hours <= 0:
            state = trial_ledgers[earliest_sprint].feasibility_state(
                completed_tasks, self.actual_completed_tasks
            )
            feasibility = self.feasibility.check(
                task_id,
                state,
                role_hours_override={},
                story_points_override=0.0 if story_points is not None else None,
                team_id_override=team_id,
                allow_cross_team=(self.allow_cross_team and (
                    not self.cross_team_task_ids or task_id in self.cross_team_task_ids
                )),
            )
            if not feasibility.can_complete or team_id is None:
                attempts.append(
                    PlacementAttempt(
                        earliest_sprint,
                        PlacementOutcome.REJECTED,
                        reasons=feasibility.reasons,
                    )
                )
            else:
                start_sprint = earliest_sprint
        else:
            for sprint_number in range(earliest_sprint, SPRINT_COUNT + 1):
                ledger = trial_ledgers[sprint_number]
                max_task_hours = float("inf")
                available_sp = 0.0
                sp_capacity_reason: FeasibilityReason | None = None
                if (
                    story_points is not None
                    and story_points > 0
                    and team_id is not None
                ):
                    available_sp = max(
                        ledger.team_capacity_sp.get(team_id, 0.0)
                        - ledger.team_sp_used.get(team_id, 0.0),
                        0.0,
                    )
                    assignable_sp = min(
                        available_sp,
                        max(story_points - assigned_capacity_sp, 0.0),
                    )
                    max_task_hours = assignable_sp * total_required_hours / story_points
                    if max_task_hours <= 1e-9:
                        sp_capacity_reason = FeasibilityReason(
                            ReasonCode.INSUFFICIENT_TEAM_CAPACITY,
                            {
                                "team_id": team_id,
                                "required_sp": story_points - assigned_capacity_sp,
                                "available_sp": available_sp,
                            },
                        )
                state = ledger.feasibility_state(completed_tasks, self.actual_completed_tasks)
                feasibility = self.feasibility.check(
                    task_id,
                    state,
                role_hours_override=remaining,
                story_points_override=0.0 if story_points is not None else None,
                team_id_override=team_id,
                allow_cross_team=(self.allow_cross_team and (
                    not self.cross_team_task_ids or task_id in self.cross_team_task_ids
                )),
                    unavailable_engineer_ids=self.unavailable_engineer_ids_by_sprint.get(
                        sprint_number, set()
                    ),
                    preferred_engineer_ids=set(preferred_engineer_hours),
                    availability_rates=self.engineer_availability_rates_by_sprint.get(
                        sprint_number, {}
                    ),
                )
                if not feasibility.can_start:
                    attempts.append(
                        PlacementAttempt(
                            sprint_number,
                            PlacementOutcome.REJECTED,
                            reasons=feasibility.reasons
                            + ((sp_capacity_reason,) if sp_capacity_reason else ()),
                        )
                    )
                    continue
                if sp_capacity_reason is not None:
                    attempts.append(
                        PlacementAttempt(
                            sprint_number,
                            PlacementOutcome.REJECTED,
                            reasons=feasibility.reasons + (sp_capacity_reason,),
                        )
                    )
                    continue

                covered_task_skills = self._covered_allocated_skills(allocations)
                outcome = allocate_available_hours(
                    remaining,
                    feasibility,
                    max_total_hours=max_task_hours,
                    prior_ownership=merge_task_ownership(
                        self.prior_task_ownership_by_task.get(task_id),
                        evaluate_task_ownership(team_id, allocations),
                    ),
                    enforce_owner_majority=True,
                    preferred_engineer_hours=preferred_engineer_hours,
                    covered_task_skills=covered_task_skills,
                )
                if outcome.total_hours <= 1e-9:
                    attempts.append(
                        PlacementAttempt(
                            sprint_number,
                            PlacementOutcome.REJECTED,
                            reasons=feasibility.reasons,
                        )
                    )
                    continue

                next_allocated_hours = allocated_hours_total + outcome.total_hours
                capacity_sp_load = 0.0
                if story_points is not None and story_points > 0:
                    capacity_sp_load = (
                        story_points * outcome.total_hours / total_required_hours
                    )

                next_remaining = dict(remaining)
                for role, hours in outcome.allocated_role_hours.items():
                    next_remaining[role] = max(
                        next_remaining.get(role, 0.0) - hours, 0.0
                    )
                next_remaining = {
                    role: hours
                    for role, hours in next_remaining.items()
                    if hours > 1e-9
                }
                if not next_remaining and story_points is not None:
                    # Absorb only floating-point residue in the completion sprint.
                    capacity_sp_load = max(
                        story_points - assigned_capacity_sp,
                        0.0,
                    )
                # Keep persisted SP splits deterministic when role-hour
                # arithmetic contains tiny floating-point residue.
                capacity_sp_load = round(capacity_sp_load, 9)
                allocation = TaskAllocation(
                    task_id=task_id,
                    sprint_number=sprint_number,
                    allocated_role_hours=dict(outcome.allocated_role_hours),
                    engineer_allocations=outcome.engineer_allocations,
                    task_story_points=source_story_points or 0.0,
                    capacity_sp_load=capacity_sp_load,
                    planned_completion_sp=0.0,
                    completed_sp=0.0,
                    is_started=start_sprint is None,
                    is_completed=False,
                    check_reasons=feasibility.reasons,
                )
                allocations.append(allocation)
                attempts.append(
                    PlacementAttempt(
                        sprint_number,
                        PlacementOutcome.TENTATIVE,
                        allocated_hours=outcome.total_hours,
                        reasons=feasibility.reasons,
                    )
                )
                if start_sprint is None:
                    start_sprint = sprint_number
                assigned_capacity_sp += capacity_sp_load
                allocated_hours_total = next_allocated_hours
                for engineer in outcome.engineer_allocations:
                    if engineer.engineer_id in carry_over_remaining:
                        carry_over_remaining[engineer.engineer_id] = max(
                            carry_over_remaining[engineer.engineer_id] - engineer.hours,
                            0.0,
                        )
                carry_over_remaining = {
                    engineer_id: hours
                    for engineer_id, hours in carry_over_remaining.items()
                    if hours > 1e-9
                }
                remaining = next_remaining
                if not remaining:
                    break

        # A task can exhaust the team SP budget before all of its role-hours
        # are allocated. Record that concrete constraint for the result layer;
        # the trial ledger is still discarded unless the whole task succeeds.
        if (
            remaining
            and story_points is not None
            and story_points > 0
            and team_id is not None
            and assigned_capacity_sp < story_points - 1e-9
        ):
            free_sp = sum(
                max(
                    trial_ledgers[sprint].team_capacity_sp.get(team_id, 0.0)
                    - trial_ledgers[sprint].team_sp_used.get(team_id, 0.0),
                    0.0,
                )
                for sprint in range(earliest_sprint, SPRINT_COUNT + 1)
            )
            sp_reason = FeasibilityReason(
                ReasonCode.INSUFFICIENT_TEAM_CAPACITY,
                {
                    "team_id": team_id,
                    "required_sp": story_points - assigned_capacity_sp,
                    "available_sp": free_sp,
                    "deficit_sp": max(story_points - assigned_capacity_sp - free_sp, 0.0),
                    "attempted_sprint": SPRINT_COUNT,
                },
            )
            # Preserve one placement attempt per tried sprint. This adds the
            # SP bottleneck to the last trial rather than manufacturing an
            # additional planner action only for explainability.
            if attempts:
                last = attempts[-1]
                attempts[-1] = PlacementAttempt(
                    last.sprint_number,
                    last.outcome,
                    allocated_hours=last.allocated_hours,
                    capacity_sp_load=last.capacity_sp_load,
                    planned_completion_sp=last.planned_completion_sp,
                    completed_sp=last.completed_sp,
                    reasons=last.reasons + (sp_reason,),
                )

        required_task_skills = set(
            self.feasibility.task_skill_requirements(task_id).get(None, set())
        )
        assigned_engineer_ids = {
            item.engineer_id
            for allocation in allocations
            for item in allocation.engineer_allocations
            if item.hours > 0
        }
        covered_task_skills = self._covered_allocated_skills(allocations)
        missing_task_skills = sorted(required_task_skills - covered_task_skills)
        if not remaining and missing_task_skills:
            skill_reason = FeasibilityReason(
                ReasonCode.INSUFFICIENT_SKILL_CAPACITY,
                {
                    "role": None,
                    "missing_skills": missing_task_skills,
                    "required_technologies": sorted(required_task_skills),
                    "candidate_engineers": sorted(assigned_engineer_ids),
                },
            )
            if attempts:
                last = attempts[-1]
                attempts[-1] = PlacementAttempt(
                    last.sprint_number,
                    last.outcome,
                    allocated_hours=last.allocated_hours,
                    capacity_sp_load=last.capacity_sp_load,
                    planned_completion_sp=last.planned_completion_sp,
                    completed_sp=last.completed_sp,
                    reasons=last.reasons + (skill_reason,),
                )

        can_distribute_sp = (
            not remaining
            and not missing_task_skills
            and start_sprint is not None
            and team_id is not None
            and story_points is not None
        )
        distribution = None
        if can_distribute_sp:
            if total_required_hours > 0:
                distribution = StoryPointDistribution(
                    capacity_sp_load={
                        allocation.sprint_number: allocation.capacity_sp_load
                        for allocation in allocations
                    },
                    remaining_sp=max(story_points - assigned_capacity_sp, 0.0),
                )
            else:
                available_sp = {
                    sprint: max(
                        trial_ledgers[sprint].team_capacity_sp.get(team_id, 0.0)
                        - trial_ledgers[sprint].team_sp_used.get(team_id, 0.0),
                        0.0,
                    )
                    for sprint in range(start_sprint, SPRINT_COUNT + 1)
                }
                try:
                    distribution = distribute_capacity_sp_load(
                        task_story_points=story_points,
                        active_sprint_hours={start_sprint: 0.0},
                        available_sp_by_sprint=available_sp,
                        last_sprint=SPRINT_COUNT,
                    )
                except ValueError as error:
                    attempts.append(
                        PlacementAttempt(
                            start_sprint,
                            PlacementOutcome.REJECTED,
                            reasons=(
                                FeasibilityReason(
                                    ReasonCode.UNKNOWN_TASK_DATA,
                                    {"task_id": task_id, "message": str(error)},
                                ),
                            ),
                        )
                    )

            if distribution is not None and distribution.remaining_sp > 1e-9:
                free_sp = sum(
                    max(
                        trial_ledgers[sprint].team_capacity_sp.get(team_id, 0.0)
                        - trial_ledgers[sprint].team_sp_used.get(team_id, 0.0),
                        0.0,
                    )
                    for sprint in range(start_sprint, SPRINT_COUNT + 1)
                )
                attempts.append(
                    PlacementAttempt(
                        SPRINT_COUNT,
                        PlacementOutcome.REJECTED,
                        reasons=(
                            FeasibilityReason(
                                ReasonCode.INSUFFICIENT_TEAM_CAPACITY,
                                {
                                    "team_id": team_id,
                                    "required_sp": story_points,
                                    "available_sp": free_sp,
                                    "deficit_sp": distribution.remaining_sp,
                                },
                            ),
                        ),
                    )
                )

        success = bool(
            distribution is not None and distribution.remaining_sp <= 1e-9
        )
        if success and distribution is not None and team_id is not None and story_points is not None:
            provisional = {item.sprint_number: item for item in allocations}
            active_sprints = sorted(
                set(provisional)
                | {
                    sprint
                    for sprint, load in distribution.capacity_sp_load.items()
                    if load > 0
                }
            )
            if not active_sprints:
                active_sprints = [start_sprint or earliest_sprint]
            start_sprint = min(active_sprints)
            completion_sprint = max(active_sprints)
            finalized_allocations: list[TaskAllocation] = []
            attempts_by_sprint = {
                attempt.sprint_number: attempt
                for attempt in attempts
                if attempt.outcome == PlacementOutcome.TENTATIVE
            }
            for sprint_number in active_sprints:
                previous = provisional.get(sprint_number)
                load = distribution.capacity_sp_load.get(sprint_number, 0)
                completes_now = sprint_number == completion_sprint
                allocation = TaskAllocation(
                    task_id=task_id,
                    sprint_number=sprint_number,
                    allocated_role_hours=(
                        dict(previous.allocated_role_hours) if previous else {}
                    ),
                    engineer_allocations=(
                        previous.engineer_allocations if previous else ()
                    ),
                    task_story_points=source_story_points or 0.0,
                    capacity_sp_load=load,
                    planned_completion_sp=(story_points if completes_now else 0.0),
                    completed_sp=0.0,
                    is_started=sprint_number == start_sprint,
                    is_completed=completes_now,
                    check_reasons=(previous.check_reasons if previous else ()),
                )
                finalized_allocations.append(allocation)
                previous_attempt = attempts_by_sprint.get(sprint_number)
                updated_attempt = PlacementAttempt(
                    sprint_number,
                    PlacementOutcome.TENTATIVE,
                    allocated_hours=allocation.allocated_hours,
                    capacity_sp_load=load,
                    planned_completion_sp=allocation.planned_completion_sp,
                    completed_sp=allocation.completed_sp,
                    reasons=(previous_attempt.reasons if previous_attempt else ()),
                )
                if previous_attempt is None:
                    attempts.append(updated_attempt)
                else:
                    attempts[attempts.index(previous_attempt)] = updated_attempt
            allocations = finalized_allocations
            ownership = merge_task_ownership(
                self.prior_task_ownership_by_task.get(task_id),
                evaluate_task_ownership(team_id, allocations),
            )
            if not ownership.valid:
                ownership_reason = FeasibilityReason(
                    ReasonCode.INVALID_TASK_OWNERSHIP,
                    ownership.to_dict(),
                )
                tentative_indexes = [
                    index
                    for index, attempt in enumerate(attempts)
                    if attempt.outcome == PlacementOutcome.TENTATIVE
                ]
                if tentative_indexes:
                    index = tentative_indexes[-1]
                    attempt = attempts[index]
                    attempts[index] = PlacementAttempt(
                        attempt.sprint_number,
                        attempt.outcome,
                        allocated_hours=attempt.allocated_hours,
                        capacity_sp_load=attempt.capacity_sp_load,
                        planned_completion_sp=attempt.planned_completion_sp,
                        completed_sp=attempt.completed_sp,
                        reasons=attempt.reasons + (ownership_reason,),
                    )
                else:
                    attempts.append(
                        PlacementAttempt(
                            completion_sprint,
                            PlacementOutcome.REJECTED,
                            reasons=(ownership_reason,),
                        )
                    )
                success = False
            else:
                for allocation in allocations:
                    trial_ledgers[allocation.sprint_number].add_allocation(
                        team_id, allocation
                    )

        final_outcome = PlacementOutcome.COMMITTED if success else PlacementOutcome.ROLLED_BACK
        finalized_attempts = tuple(
            attempt.with_outcome(final_outcome)
            if attempt.outcome == PlacementOutcome.TENTATIVE
            else attempt
            for attempt in attempts
        )
        return (
            success,
            trial_ledgers,
            tuple(allocations),
            finalized_attempts,
            remaining,
            start_sprint,
            completion_sprint,
            no_hours_warning,
        )

    def _select_team_assignment(
        self,
        task: Mapping[str, Any],
        earliest_sprint: int,
        ledgers: dict[int, SprintLedger],
        completed_tasks: dict[str, int],
    ) -> tuple[_TaskSimulation, str | None, _TaskSimulation | None]:
        """Simulate existing teams and prefer the source team on equal finish."""

        original_team_id = _text(task.get("team_id"))
        candidate_team_ids = (
            list(self.team_ids)
            if self.allow_team_reassignment or original_team_id is None
            else [original_team_id]
        )
        if original_team_id in candidate_team_ids:
            candidate_team_ids.remove(original_team_id)
            candidate_team_ids.insert(0, original_team_id)
        if not candidate_team_ids:
            candidate_team_ids = [original_team_id or ""]
        simulations: list[_TaskSimulation] = []
        for team_id in candidate_team_ids:
            simulation = self._simulate_task(
                task,
                earliest_sprint,
                ledgers,
                completed_tasks,
                team_id,
            )
            simulations.append(_TaskSimulation(team_id, *simulation))

        original = next(
            (
                simulation
                for simulation in simulations
                if simulation.assigned_team_id == original_team_id
            ),
            None,
        )
        successful = [simulation for simulation in simulations if simulation.success]
        if successful:
            chosen = min(
                successful,
                key=lambda simulation: (
                    simulation.completion_sprint or SPRINT_COUNT + 1,
                    simulation.start_sprint or SPRINT_COUNT + 1,
                    0 if simulation.assigned_team_id == original_team_id else 1,
                    simulation.assigned_team_id,
                ),
            )
        else:
            chosen = original or simulations[0]

        assignment_reason = None
        if chosen.assigned_team_id != original_team_id:
            if original is None or not original.success:
                assignment_reason = "ORIGINAL_TEAM_INFEASIBLE"
            else:
                assignment_reason = "EARLIER_COMPLETION_WITH_EXISTING_TEAM"
        return chosen, assignment_reason, original

    def _simulate_complete_initiative(
        self,
        initiative_id: str,
        pending: Mapping[str, Mapping[str, Any]],
        ledgers: dict[int, SprintLedger],
        completed_tasks: dict[str, int],
        results: dict[str, TaskPlanResult],
    ) -> tuple[bool, dict[int, SprintLedger], dict[str, int]]:
        """Preflight all remaining tasks without committing partial initiative work."""

        remaining = {
            task_id: dict(task)
            for task_id, task in pending.items()
            if _text(task.get("initiative_id")) == initiative_id
        }
        trial_ledgers = self._clone_ledgers(ledgers)
        trial_completed = dict(completed_tasks)
        while remaining:
            ready = [
                task_id
                for task_id in remaining
                if not set(self._incoming_blockers(task_id)).intersection(remaining)
            ]
            if not ready:
                return False, ledgers, completed_tasks
            scored = {
                task_id: calculate_task_score(
                    remaining[task_id], self.scoring_context, self.scoring_config
                )
                for task_id in ready
            }
            task_id = min(
                ready,
                key=lambda candidate_id: score_sort_key(
                    remaining[candidate_id], scored[candidate_id]
                ),
            )
            task = remaining.pop(task_id)
            earliest, dependency_reasons, _ = self._dependency_start(
                task_id, results, trial_completed
            )
            if earliest is None or dependency_reasons or earliest > SPRINT_COUNT:
                return False, ledgers, completed_tasks
            chosen, _, _ = self._select_team_assignment(
                task, earliest, trial_ledgers, trial_completed
            )
            if not chosen.success or chosen.completion_sprint is None:
                return False, ledgers, completed_tasks
            trial_ledgers = chosen.ledgers
            trial_completed[task_id] = chosen.completion_sprint
        return True, trial_ledgers, trial_completed

    def _can_complete_initiative(
        self,
        initiative_id: str,
        pending: Mapping[str, Mapping[str, Any]],
        ledgers: dict[int, SprintLedger],
        completed_tasks: dict[str, int],
        results: dict[str, TaskPlanResult],
    ) -> bool:
        return self._simulate_complete_initiative(
            initiative_id, pending, ledgers, completed_tasks, results
        )[0]

    def _primary_initiative_choice(
        self,
        pending: Mapping[str, Mapping[str, Any]],
        ledgers: dict[int, SprintLedger],
        completed_tasks: dict[str, int],
        results: dict[str, TaskPlanResult],
    ) -> str | None:
        """Choose a first initiative that maximizes complete initiatives.

        Resource and dependency simulation remains authoritative instead of
        treating task count, SP, or hours as the primary objective.
        """

        initiative_ids = tuple(sorted({
            _text(task.get("initiative_id"))
            for task in pending.values()
            if _text(task.get("initiative_id")) in self._initiative_work
        }))
        if not initiative_ids:
            return None
        self._initiative_choice_queue = [
            initiative_id
            for initiative_id in self._initiative_choice_queue
            if initiative_id in initiative_ids
        ]
        if self._initiative_choice_queue:
            return self._initiative_choice_queue[0]

        memo: dict[
            tuple[
                tuple[str, ...],
                tuple[tuple[int, tuple, tuple, tuple], ...],
                tuple[tuple[str, int], ...],
            ],
            tuple[int, tuple[str, ...], tuple[int, ...]],
        ] = {}

        def state_key(
            candidate_ids: tuple[str, ...],
            state: Mapping[int, SprintLedger],
            completed: Mapping[str, int],
        ) -> tuple[
            tuple[str, ...],
            tuple[tuple[int, tuple, tuple, tuple], ...],
            tuple[tuple[str, int], ...],
        ]:
            ledger_key = tuple(
                (
                    sprint,
                    tuple(sorted(ledger.team_sp_used.items())),
                    tuple(sorted(ledger.engineer_hours_used.items())),
                    tuple(
                        (engineer_id, tuple(sorted(team_hours.items())))
                        for engineer_id, team_hours in sorted(
                            ledger.engineer_team_hours_used.items()
                        )
                    ),
                )
                for sprint, ledger in sorted(state.items())
            )
            return candidate_ids, ledger_key, tuple(sorted(completed.items()))

        def search(
            remaining: Mapping[str, Mapping[str, Any]],
            state: dict[int, SprintLedger],
            completed: dict[str, int],
        ) -> tuple[int, tuple[str, ...], tuple[int, ...]]:
            candidates = tuple(sorted({
                _text(task.get("initiative_id"))
                for task in remaining.values()
                if _text(task.get("initiative_id")) in self._initiative_work
            }))
            key = state_key(candidates, state, completed)
            if key in memo:
                return memo[key]
            best: tuple[int, tuple[str, ...], tuple[int, ...]] = (0, (), ())
            for initiative_id in candidates:
                success, next_state, next_completed = self._simulate_complete_initiative(
                    initiative_id, remaining, state, completed, results
                )
                if not success:
                    continue
                next_remaining = {
                    task_id: task
                    for task_id, task in remaining.items()
                    if _text(task.get("initiative_id")) != initiative_id
                }
                count, order, completion_sprints = search(
                    next_remaining, next_state, next_completed
                )
                candidate = (
                    count + 1,
                    (initiative_id, *order),
                    (
                        self._initiative_completion_sprint(
                            initiative_id, remaining, next_completed
                        ),
                        *completion_sprints,
                    ),
                )
                if self._initiative_plan_is_better(candidate, best):
                    best = candidate
            memo[key] = best
            return best

        _, order, _ = search(pending, ledgers, completed_tasks)
        self._initiative_choice_queue = list(order)
        return order[0] if order else None

    def plan(self) -> QuarterPlan:
        team_ids = list(self.team_ids)
        team_capacities = {
            team_id: calculate_team_capacity(team_id, self.context.team_history).capacity_sp
            for team_id in team_ids
        }
        ledgers = {
            sprint: SprintLedger(sprint, dict(team_capacities))
            for sprint in range(1, SPRINT_COUNT + 1)
        }
        for sprint, usage in self.reserved_resource_usage_by_sprint.items():
            ledger = ledgers[int(sprint)]
            ledger.team_sp_used.update({str(key): float(value) for key, value in
                                        (usage.get("team_sp_used") or {}).items()})
            ledger.engineer_hours_used.update({str(key): float(value) for key, value in
                                              (usage.get("engineer_hours_used") or {}).items()})
            ledger.engineer_team_hours_used.update({str(key): {
                str(team): float(hours) for team, hours in teams.items()
            } for key, teams in (usage.get("engineer_team_hours_used") or {}).items()})
        completed_tasks: dict[str, int] = dict(self.fixed_planned_completion_by_task)
        results: dict[str, TaskPlanResult] = {}
        quarter_warnings: list[FeasibilityReason] = []

        for task_id, task in sorted(self.tasks.items()):
            if _boolean(task.get("is_placeholder")):
                reason = FeasibilityReason(
                    ReasonCode.UNKNOWN_TASK_DATA,
                    {"task_id": task_id, "is_placeholder": True},
                )
                results[task_id] = self._task_result(
                    task, status=PlanStatus.UNKNOWN, final_reasons=(reason,)
                )
                quarter_warnings.append(reason)
            elif task_id in self.actual_completed_tasks:
                completed_tasks[task_id] = self.actual_completed_tasks[task_id]
                results[task_id] = self._task_result(
                    task,
                    status=PlanStatus.COMPLETED,
                    completion_sprint=self.actual_completed_tasks[task_id],
                    final_reasons=(
                        FeasibilityReason(
                            ReasonCode.ALREADY_COMPLETED,
                            {"task_id": task_id, "actual_completion": True},
                        ),
                    ),
                )
            elif task_id in self.fixed_planned_completion_by_task:
                results[task_id] = self._task_result(
                    task, status=PlanStatus.COMPLETED,
                    completion_sprint=self.fixed_planned_completion_by_task[task_id],
                )
            elif is_historical_done(task):
                completed_tasks[task_id] = 0
                results[task_id] = self._task_result(
                    task,
                    status=PlanStatus.COMPLETED,
                    completion_sprint=0,
                    final_reasons=(
                        FeasibilityReason(
                            ReasonCode.ALREADY_COMPLETED,
                            {"task_id": task_id, "completed_before_quarter": True},
                        ),
                    ),
                )
            elif task_id in self.ineligible_task_ids:
                eligibility = self.eligibility_results[task_id]
                reasons = tuple(
                    FeasibilityReason(
                        reason.code,
                        {
                            "task_id": task_id,
                            "role": reason.role,
                            "required_hours": reason.required_hours,
                            "available_specialists": reason.available_specialists,
                        },
                    )
                    for reason in eligibility.reasons
                )
                results[task_id] = self._task_result(
                    task,
                    status=PlanStatus.INELIGIBLE_DATA_CONSTRAINT,
                    remaining_role_hours=self.feasibility.task_role_requirements(task_id),
                    final_reasons=reasons,
                    eligibility_result=eligibility,
                )
                quarter_warnings.extend(reasons)

        candidates = {
            task_id: task
            for task_id, task in self.tasks.items()
            if task_id in self.scoring_context.eligible_task_ids
        }
        pending = dict(candidates)
        pending_blockers: dict[str, set[str]] = {
            task_id: set() for task_id in candidates
        }
        for dependency in self.context.dependencies:
            blocker = _text(dependency.get("blocking_task_id"))
            blocked = _text(dependency.get("blocked_task_id"))
            if blocker in candidates and blocked in candidates:
                pending_blockers[blocked].add(blocker)

        while pending:
            ready_ids = [
                task_id
                for task_id in pending
                if not pending_blockers[task_id].intersection(pending)
            ]
            # Cycles are invalid input, but selecting deterministically lets the
            # existing dependency checks reject them without hanging the planner.
            selectable_ids = ready_ids or list(pending)
            scored = {
                task_id: calculate_task_score(
                    pending[task_id], self.scoring_context, self.scoring_config
                )
                for task_id in selectable_ids
            }
            highest_rung = max(
                _number(pending[candidate_id].get("rung")) or 0.0
                for candidate_id in selectable_ids
            )
            preferred_initiative = self._same_rung_initiative_choice(
                selectable_ids, pending, ledgers, completed_tasks, results
            )
            if preferred_initiative:
                preferred_ids = [
                    candidate_id
                    for candidate_id in selectable_ids
                    if _text(pending[candidate_id].get("initiative_id"))
                    == preferred_initiative
                    and (_number(pending[candidate_id].get("rung")) or 0.0)
                    == highest_rung
                ]
            else:
                preferred_ids = selectable_ids
            carryover_ids = [
                candidate_id
                for candidate_id in preferred_ids
                if (
                    candidate_id in self.carry_over_task_ids
                    or self.carry_over_engineer_hours_by_task.get(candidate_id)
                )
                and (_number(pending[candidate_id].get("rung")) or 0.0) == highest_rung
            ]
            # After hard dependency/rung/initiative rules, unfinished work is
            # continued before new work can consume the same sprint capacity.
            task_id = _select_priority_task_id(
                carryover_ids or preferred_ids,
                pending,
                scored,
                self.reference_plan_by_task,
            )
            task = pending.pop(task_id)
            task_score = scored[task_id]
            self.priority_order[task_id] = len(self.priority_order) + 1
            earliest, dependency_reasons, blockers = self._dependency_start(
                task_id, results, completed_tasks
            )
            if earliest is None or earliest > SPRINT_COUNT:
                final_reasons = list(dependency_reasons)
                outside_quarter = earliest is not None and earliest > SPRINT_COUNT
                if outside_quarter:
                    final_reasons.append(
                        FeasibilityReason(
                            ReasonCode.OUTSIDE_QUARTER,
                            {
                                "task_id": task_id,
                                "forecast_start_sprint": earliest,
                                "forecast_completion_sprint_minimum": earliest,
                                "last_sprint": SPRINT_COUNT,
                                "blocking_task_ids": list(blockers),
                                "resources_committed": False,
                            },
                        )
                    )
                final_reason_tuple = _deduplicate_reasons(final_reasons)
                results[task_id] = self._task_result(
                    task,
                    status=PlanStatus.MOVED,
                    remaining_role_hours=self.feasibility.task_role_requirements(task_id),
                    dependencies=blockers,
                    final_reasons=final_reason_tuple,
                    outside_quarter=outside_quarter,
                    task_score=task_score,
                    recommendations=(
                        (
                            PlanningRecommendation(
                                RecommendationType.REASSIGN_TO_EXISTING_TEAM,
                                False,
                                "A team change cannot resolve the current dependency constraint.",
                            ),
                            PlanningRecommendation(
                                RecommendationType.MOVE_TO_NEXT_QUARTER,
                                True,
                                "The earliest dependency-based forecast is beyond Sprint 6.",
                            ),
                        )
                        if outside_quarter
                        else ()
                    ),
                )
                quarter_warnings.extend(final_reason_tuple)
                continue

            chosen, team_assignment_reason, original_simulation = (
                self._select_team_assignment(
                    task, earliest, ledgers, completed_tasks
                )
            )
            success = chosen.success
            trial_ledgers = chosen.ledgers
            allocations = chosen.allocations
            attempts = chosen.attempts
            simulated_remaining = chosen.remaining
            simulated_start = chosen.start_sprint
            completion_sprint = chosen.completion_sprint
            no_hours_warning = chosen.no_hours_warning

            if success and completion_sprint is not None:
                ledgers = trial_ledgers
                completed_tasks[task_id] = completion_sprint
                self.scoring_context = self.scoring_context.with_committed(task_id)
                final_reasons: list[FeasibilityReason] = [
                    FeasibilityReason(
                        ReasonCode.PLANNED_IN_QUARTER,
                        {
                            "task_id": task_id,
                            "start_sprint": simulated_start,
                            "completion_sprint": completion_sprint,
                            "priority_order": self.priority_order.get(task_id),
                            "checked_constraints": [
                                "team_sp",
                                "roles",
                                "engineer_hours",
                                "task_ownership",
                                "dependencies",
                                "quarter_horizon",
                            ],
                            "dependency_completion_sprints": {
                                blocker: completed_tasks.get(blocker) for blocker in blockers
                            },
                        },
                    )
                ]
                if len(allocations) > 1:
                    final_reasons.append(
                        FeasibilityReason(
                            ReasonCode.MULTI_SPRINT_REQUIRED,
                            {
                                "task_id": task_id,
                                "start_sprint": simulated_start,
                                "completion_sprint": completion_sprint,
                                "sprint_count": len(allocations),
                                "total_required_hours": sum(
                                    allocation.allocated_hours for allocation in allocations
                                ),
                                "source_story_points": _number(task.get("story_points")),
                                "allocations": [
                                    {
                                        "sprint_number": allocation.sprint_number,
                                        "allocated_hours": allocation.allocated_hours,
                                        "capacity_sp_load": allocation.capacity_sp_load,
                                        "planned_completion_sp": allocation.planned_completion_sp,
                                        "completed_sp": allocation.completed_sp,
                                    }
                                    for allocation in allocations
                                ],
                            },
                        )
                    )
                if no_hours_warning:
                    final_reasons.append(no_hours_warning)
                    quarter_warnings.append(no_hours_warning)
                if team_assignment_reason:
                    original_reason_codes = sorted(
                        {
                            reason.code.value
                            for attempt in (
                                original_simulation.attempts
                                if original_simulation is not None
                                else ()
                            )
                            for reason in attempt.reasons
                        }
                    )
                    final_reasons.append(
                        FeasibilityReason(
                            ReasonCode.TEAM_REASSIGNED,
                            {
                                "task_id": task_id,
                                "original_team_id": _text(task.get("team_id")),
                                "assigned_team_id": chosen.assigned_team_id,
                                "reason": team_assignment_reason,
                                "original_team_reason_codes": original_reason_codes,
                                "assigned_completion_sprint": completion_sprint,
                                "original_completion_sprint": (
                                    original_simulation.completion_sprint
                                    if original_simulation is not None
                                    else None
                                ),
                            },
                        )
                    )
                results[task_id] = self._task_result(
                    task,
                    status=PlanStatus.PLANNED,
                    start_sprint=simulated_start,
                    completion_sprint=completion_sprint,
                    allocations=allocations,
                    dependencies=blockers,
                    final_reasons=tuple(final_reasons),
                    placement_attempts=attempts,
                    task_score=task_score,
                    assigned_team_id=chosen.assigned_team_id,
                    team_assignment_reason=team_assignment_reason,
                    recommendations=(
                        (
                            PlanningRecommendation(
                                RecommendationType.REASSIGN_TO_EXISTING_TEAM,
                                True,
                                "Reassignment to an existing team makes the task feasible in the quarter.",
                                target_team_id=chosen.assigned_team_id,
                            ),
                        )
                        if team_assignment_reason
                        else ()
                    ),
                )
                continue

            rolled_back_hours = sum(
                attempt.allocated_hours
                for attempt in attempts
                if attempt.outcome == PlacementOutcome.ROLLED_BACK
            )
            rolled_back_sp = sum(
                attempt.capacity_sp_load
                for attempt in attempts
                if attempt.outcome == PlacementOutcome.ROLLED_BACK
            )
            final_reasons = []
            if rolled_back_hours > 0 or rolled_back_sp > 0:
                final_reasons.append(
                    FeasibilityReason(
                        ReasonCode.ALLOCATION_ROLLED_BACK,
                        {
                            "task_id": task_id,
                            "simulated_start_sprint": simulated_start,
                            "rolled_back_hours": rolled_back_hours,
                            "rolled_back_capacity_sp_load": rolled_back_sp,
                            "rolled_back_completed_sp": 0,
                        },
                    )
                )
            final_reasons.extend(self._attempt_bottleneck_reasons(attempts))
            if no_hours_warning:
                final_reasons.append(no_hours_warning)
                quarter_warnings.append(no_hours_warning)
            results[task_id] = self._task_result(
                task,
                status=PlanStatus.MOVED,
                remaining_role_hours=self.feasibility.task_role_requirements(task_id),
                dependencies=blockers,
                final_reasons=tuple(final_reasons),
                placement_attempts=attempts,
                outside_quarter=False,
                task_score=task_score,
                assigned_team_id=chosen.assigned_team_id,
                recommendations=(
                    PlanningRecommendation(
                        RecommendationType.REASSIGN_TO_EXISTING_TEAM,
                        False,
                        "No existing team can complete the task within the current quarter.",
                    ),
                    PlanningRecommendation(
                        RecommendationType.MOVE_TO_NEXT_QUARTER,
                        True,
                        "Move the uncommitted task to the next quarter for replanning.",
                    ),
                ),
            )
            quarter_warnings.extend(final_reasons)

        for task_id, task in sorted(self.tasks.items()):
            if task_id in results:
                continue
            reason = FeasibilityReason(
                ReasonCode.UNSUPPORTED_TASK_STATUS,
                {
                    "task_id": task_id,
                    "message": "Source status is not handled by the quarterly planner.",
                    "source_status": _text(task.get("status")),
                    "resources_committed": False,
                },
            )
            results[task_id] = self._task_result(
                task,
                status=PlanStatus.MOVED,
                final_reasons=(reason,),
                outside_quarter=False,
            )
            quarter_warnings.append(reason)

        return QuarterPlan(
            sprints=tuple(ledgers[number].freeze() for number in range(1, SPRINT_COUNT + 1)),
            task_results=tuple(results[task_id] for task_id in sorted(results)),
            warnings=_deduplicate_reasons(quarter_warnings),
            quarter_start_date=(
                self.calendar.quarter_start.isoformat() if self.calendar else None
            ),
            quarter_end_date=self.calendar.quarter_end.isoformat() if self.calendar else None,
        )


def build_quarter_plan(
    context: FeasibilityContext,
    quarter_start_date: Any = None,
    scoring_config: ScoringConfig | None = None,
    *,
    allow_team_reassignment: bool = True,
    allow_cross_team: bool = True,
    cross_team_task_ids: set[str] | None = None,
    start_sprint: int = 1,
    actual_completed_tasks: dict[str, int] | None = None,
    unavailable_engineer_ids_by_sprint: Mapping[int, set[str]] | None = None,
    engineer_availability_rates_by_sprint: Mapping[int, Mapping[str, float]] | None = None,
    carry_over_engineer_hours_by_task: Mapping[str, Mapping[str, float]] | None = None,
    carry_over_task_ids: set[str] | None = None,
    prior_task_ownership_by_task: Mapping[str, Any] | None = None,
    story_points_override_by_task: Mapping[str, float] | None = None,
    original_total_required_hours_by_task: Mapping[str, float] | None = None,
    preferred_engineer_hours_by_task: Mapping[str, Mapping[str, float]] | None = None,
    reference_plan_by_task: Mapping[str, Mapping[str, Any]] | None = None,
    earliest_sprint_by_task: Mapping[str, int] | None = None,
    reserved_resource_usage_by_sprint: Mapping[int, Mapping[str, Any]] | None = None,
    fixed_planned_completion_by_task: Mapping[str, int] | None = None,
) -> QuarterPlan:
    """Build the scored plan; resource state and atomic rollback are automatic."""

    return BaselineQuarterPlanner(
        context,
        quarter_start_date=quarter_start_date,
        scoring_config=scoring_config,
        allow_team_reassignment=allow_team_reassignment,
        allow_cross_team=allow_cross_team,
        cross_team_task_ids=cross_team_task_ids,
        start_sprint=start_sprint,
        actual_completed_tasks=actual_completed_tasks,
        unavailable_engineer_ids_by_sprint=unavailable_engineer_ids_by_sprint,
        engineer_availability_rates_by_sprint=engineer_availability_rates_by_sprint,
        carry_over_engineer_hours_by_task=carry_over_engineer_hours_by_task,
        carry_over_task_ids=carry_over_task_ids,
        prior_task_ownership_by_task=prior_task_ownership_by_task,
        story_points_override_by_task=story_points_override_by_task,
        original_total_required_hours_by_task=original_total_required_hours_by_task,
        preferred_engineer_hours_by_task=preferred_engineer_hours_by_task,
        reference_plan_by_task=reference_plan_by_task,
        earliest_sprint_by_task=earliest_sprint_by_task,
        reserved_resource_usage_by_sprint=reserved_resource_usage_by_sprint,
        fixed_planned_completion_by_task=fixed_planned_completion_by_task,
    ).plan()
