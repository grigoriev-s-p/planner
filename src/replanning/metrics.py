"""Pure KPI calculations with an immutable original-plan denominator."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    QUARTER_SPRINTS,
    ActualResults,
    MeasureKind,
    PlanState,
    QuarterPlan,
)


@dataclass(frozen=True, slots=True)
class KPIResult:
    code: str
    kind: MeasureKind
    numerator: float
    denominator: float
    percentage: float | None
    explanation: str


def _percentage(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else numerator / denominator * 100.0


def _require_same_quarter(*snapshots: QuarterPlan | ActualResults) -> None:
    quarter_ids = {snapshot.quarter_id for snapshot in snapshots}
    if len(quarter_ids) != 1:
        raise ValueError(f"quarter_id mismatch: {', '.join(sorted(quarter_ids))}")


def _baseline_by_initiative(original_plan: QuarterPlan) -> dict[str, set[str]]:
    baseline: dict[str, set[str]] = {}
    for item in original_plan.baseline_items():
        baseline.setdefault(item.initiative_id, set()).add(item.task_id)
    return baseline


def _actually_completed_initiatives(
    baseline_by_initiative: dict[str, set[str]],
    actual_results: ActualResults,
) -> set[str]:
    completed_tasks = {
        entry.task_id for entry in actual_results.entries if entry.task_completed
    }
    explicitly_completed = {
        entry.initiative_id
        for entry in actual_results.entries
        if entry.initiative_completed
    }
    return {
        initiative_id
        for initiative_id, task_ids in baseline_by_initiative.items()
        if initiative_id in explicitly_completed or task_ids.issubset(completed_tasks)
    }


def calculate_pi_predictability_fact(
    original_plan: QuarterPlan,
    actual_results: ActualResults,
) -> KPIResult:
    """Return factual initiative completion to date against the week-zero plan."""

    _require_same_quarter(original_plan, actual_results)
    baseline = _baseline_by_initiative(original_plan)
    completed = _actually_completed_initiatives(baseline, actual_results)
    numerator = float(len(completed))
    denominator = float(len(baseline))
    return KPIResult(
        code="pi_predictability_fact",
        kind=MeasureKind.FACT,
        numerator=numerator,
        denominator=denominator,
        percentage=_percentage(numerator, denominator),
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
    denominator = sum(
        item.story_points
        for item in original_plan.baseline_items()
        if item.completion_sprint == sprint_number
    )

    if sprint_number <= actual_results.completed_through_sprint:
        numerator = sum(
            entry.completed_story_points
            for entry in actual_results.for_sprint(sprint_number)
        )
        kind = MeasureKind.FACT
        explanation = (
            "Fact: story points delivered in the closed sprint / story points "
            "promised for that sprint in original_plan."
        )
    else:
        numerator = sum(
            item.story_points
            for item in current_plan.items
            if item.state
            in {PlanState.PLANNED, PlanState.IN_PROGRESS, PlanState.COMPLETED}
            and item.completion_sprint == sprint_number
        )
        kind = MeasureKind.FORECAST
        explanation = (
            "Forecast: story points currently scheduled for the sprint / story "
            "points promised for that sprint in original_plan."
        )

    return KPIResult(
        code=f"sprint_{sprint_number}_say_do",
        kind=kind,
        numerator=float(numerator),
        denominator=float(denominator),
        percentage=_percentage(float(numerator), float(denominator)),
        explanation=explanation,
    )
