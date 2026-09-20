"""Pure KPI calculations with an immutable original-plan denominator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import (
    QUARTER_SPRINTS,
    ActualResults,
    MeasureKind,
    PlanItem,
    PlanState,
    QuarterPlan,
)


@dataclass(frozen=True, slots=True)
class KPIResult:
    code: str
    kind: MeasureKind
    numerator: float | None
    denominator: float
    percentage: float | None
    has_actual: bool
    explanation: str

    def to_dict(self) -> dict[str, str | float | bool | None]:
        return {
            "code": self.code,
            "kind": self.kind.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "percentage": self.percentage,
            "has_actual": self.has_actual,
            "explanation": self.explanation,
        }


def _percentage(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else numerator / denominator * 100.0


def calculate_expected_quarter_kpi(
    baseline_initiative_ids: Iterable[str],
    completed_initiative_ids: Iterable[str],
) -> KPIResult:
    """Return expected completion of Week-0 initiatives by the current plan.

    The planner remains responsible for deciding whether every required task of
    an initiative is completed inside the quarter. This function only anchors
    that result to the immutable baseline initiative set.
    """

    baseline = {initiative_id for initiative_id in baseline_initiative_ids if initiative_id}
    completed = {
        initiative_id for initiative_id in completed_initiative_ids if initiative_id
    }
    numerator = float(len(baseline & completed))
    denominator = float(len(baseline))
    return KPIResult(
        code="pi_predictability_expected",
        kind=MeasureKind.FORECAST,
        numerator=numerator,
        denominator=denominator,
        percentage=_percentage(numerator, denominator),
        has_actual=False,
        explanation=(
            "Expected: baseline initiatives fully completed by the current plan "
            "inside the quarter / initiatives in the Week-0 baseline."
        ),
    )


def _require_same_quarter(*snapshots: QuarterPlan | ActualResults) -> None:
    quarter_ids = {snapshot.quarter_id for snapshot in snapshots}
    if len(quarter_ids) != 1:
        raise ValueError(f"quarter_id mismatch: {', '.join(sorted(quarter_ids))}")


def _baseline_by_initiative(original_plan: QuarterPlan) -> dict[str, set[str]]:
    """Return every initiative/task in the immutable Week-0 business pool."""

    baseline: dict[str, set[str]] = {}
    for item in original_plan.items:
        baseline.setdefault(item.initiative_id, set()).add(item.task_id)
    return baseline


def _actually_completed_initiatives(
    baseline_by_initiative: dict[str, set[str]],
    actual_results: ActualResults,
) -> set[str]:
    completed_tasks = {
        entry.task_id for entry in actual_results.entries if entry.task_completed
    }
    return {
        initiative_id
        for initiative_id, task_ids in baseline_by_initiative.items()
        if task_ids.issubset(completed_tasks)
    }


def _allocation_hours(item: PlanItem) -> dict[int, float]:
    hours_by_sprint: dict[int, float] = {}
    for allocation in item.allocations:
        role_hours = sum(entry.hours for entry in allocation.role_hours)
        engineer_hours = sum(entry.hours for entry in allocation.engineer_hours)
        hours_by_sprint[allocation.sprint_number] = (
            role_hours if role_hours > 0 else engineer_hours
        )
    return hours_by_sprint


def _planned_story_points_for_sprint(
    original_plan: QuarterPlan,
    sprint_number: int,
) -> float:
    total = 0.0
    for item in original_plan.baseline_items():
        hours_by_sprint = _allocation_hours(item)
        total_hours = sum(hours_by_sprint.values())
        if total_hours > 0:
            total += item.story_points * hours_by_sprint.get(sprint_number, 0.0) / total_hours
        elif item.completion_sprint == sprint_number:
            total += item.story_points
    return total


def _actual_story_points_by_sprint(
    original_plan: QuarterPlan,
    actual_results: ActualResults,
) -> dict[int, float]:
    """Return factual SP per sprint without crediting a task more than once."""

    baseline_by_task = original_plan.by_task_id()
    remaining_sp = {
        task_id: item.story_points for task_id, item in baseline_by_task.items()
    }
    totals = {sprint: 0.0 for sprint in range(1, QUARTER_SPRINTS + 1)}
    for entry in sorted(
        actual_results.entries,
        key=lambda item: (item.sprint_number, item.task_id),
    ):
        baseline_item = baseline_by_task.get(entry.task_id)
        if entry.completed_story_points is not None:
            delivered_sp = float(entry.completed_story_points)
        elif baseline_item is not None and entry.completed_work_hours is not None:
            planned_hours = sum(_allocation_hours(baseline_item).values())
            delivered_sp = (
                baseline_item.story_points * entry.completed_work_hours / planned_hours
                if planned_hours > 0
                else 0.0
            )
        else:
            delivered_sp = 0.0

        if baseline_item is not None:
            delivered_sp = min(delivered_sp, remaining_sp[entry.task_id])
            remaining_sp[entry.task_id] -= delivered_sp
        totals[entry.sprint_number] += delivered_sp
    return totals


def calculate_pi_predictability_fact(
    original_plan: QuarterPlan,
    actual_results: ActualResults,
) -> KPIResult:
    """Return factual initiative completion to date against the week-zero plan."""

    _require_same_quarter(original_plan, actual_results)
    baseline = _baseline_by_initiative(original_plan)
    completed = _actually_completed_initiatives(baseline, actual_results)
    has_actual = actual_results.completed_through_sprint > 0
    numerator = float(len(completed)) if has_actual else None
    denominator = float(len(baseline))
    return KPIResult(
        code="pi_predictability_fact",
        kind=MeasureKind.FACT,
        numerator=numerator,
        denominator=denominator,
        percentage=(
            _percentage(numerator, denominator) if numerator is not None else None
        ),
        has_actual=has_actual,
        explanation=(
            "Fact to date: completed baseline initiatives / initiatives in "
            "original_plan."
        ),
    )


def calculate_pi_predictability(
    original_plan: QuarterPlan,
    current_plan: QuarterPlan,
    actual_results: ActualResults,
) -> KPIResult:
    """Calculate completed/forecast initiatives against the original baseline.

    Before sprint six closes, the numerator is a conservative forecast: an
    initiative counts if it is already complete or every originally promised
    task is either complete or still forecast inside the quarter.
    """

    _require_same_quarter(original_plan, current_plan, actual_results)
    baseline_by_initiative = _baseline_by_initiative(original_plan)

    completed_tasks = {
        entry.task_id for entry in actual_results.entries if entry.task_completed
    }
    actually_completed = _actually_completed_initiatives(
        baseline_by_initiative, actual_results
    )

    if actual_results.completed_through_sprint >= QUARTER_SPRINTS:
        counted = actually_completed
        kind = MeasureKind.FACT
        explanation = (
            "Fact: baseline initiatives completed during the six closed sprints / "
            "initiatives in original_plan."
        )
    else:
        forecast_task_ids = completed_tasks | {
            item.task_id
            for item in current_plan.items
            if item.state
            in {PlanState.PLANNED, PlanState.IN_PROGRESS, PlanState.COMPLETED}
            and item.completion_sprint is not None
            and item.completion_sprint <= QUARTER_SPRINTS
        }
        forecast_completed = {
            initiative_id
            for initiative_id, task_ids in baseline_by_initiative.items()
            if initiative_id in actually_completed or task_ids.issubset(forecast_task_ids)
        }
        counted = forecast_completed
        kind = MeasureKind.FORECAST
        explanation = (
            "Forecast: baseline initiatives already complete or still fully scheduled "
            "inside the quarter / initiatives in original_plan."
        )

    numerator = float(len(counted))
    denominator = float(len(baseline_by_initiative))
    return KPIResult(
        code="pi_predictability",
        kind=kind,
        numerator=numerator,
        denominator=denominator,
        percentage=_percentage(numerator, denominator),
        has_actual=actual_results.completed_through_sprint > 0,
        explanation=explanation,
    )


def calculate_sprint_say_do(
    original_plan: QuarterPlan,
    current_plan: QuarterPlan,
    actual_results: ActualResults,
    sprint_number: int,
) -> KPIResult:
    """Calculate delivered/forecast SP over the SP originally promised in a sprint.

    Actual SP include delivered unplanned scope, so a result above 100% is valid.
    The denominator never changes after replanning.
    """

    if not 1 <= sprint_number <= QUARTER_SPRINTS:
        raise ValueError(f"sprint_number must be between 1 and {QUARTER_SPRINTS}")
    _require_same_quarter(original_plan, current_plan, actual_results)
    denominator = _planned_story_points_for_sprint(original_plan, sprint_number)

    if sprint_number <= actual_results.completed_through_sprint:
        numerator = _actual_story_points_by_sprint(
            original_plan, actual_results
        )[sprint_number]
        kind = MeasureKind.FACT
        has_actual = True
        explanation = (
            "Fact: story points delivered in the closed sprint / story points "
            "promised for that sprint in original_plan."
        )
    else:
        numerator = None
        kind = MeasureKind.FORECAST
        has_actual = False
        explanation = (
            "Actual results have not been loaded for this sprint; the immutable "
            "original-plan denominator is retained without reporting 0%."
        )

    return KPIResult(
        code=f"sprint_{sprint_number}_say_do",
        kind=kind,
        numerator=float(numerator) if numerator is not None else None,
        denominator=float(denominator),
        percentage=(
            _percentage(float(numerator), float(denominator))
            if numerator is not None
            else None
        ),
        has_actual=has_actual,
        explanation=explanation,
    )
