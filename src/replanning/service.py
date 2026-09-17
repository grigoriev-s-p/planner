"""Composition boundary that the future planner can call after every sprint."""

from __future__ import annotations

from dataclasses import dataclass

from .alerts import (
    Alert,
    AlertPolicy,
    RiskSignal,
    alerts_from_plan_diff,
    evaluate_risk_signals,
    sort_alerts,
)
from .diff import PlanDiff, compare_plans
from .metrics import (
    KPIResult,
    calculate_pi_predictability,
    calculate_pi_predictability_fact,
    calculate_sprint_say_do,
)
from .models import QUARTER_SPRINTS, ActualResults, QuarterPlan


@dataclass(frozen=True, slots=True)
class ReplanningSnapshot:
    original_plan: QuarterPlan
    previous_plan: QuarterPlan
    current_plan: QuarterPlan
    actual_results: ActualResults
    pi_predictability_fact: KPIResult
    pi_predictability_forecast: KPIResult | None
    sprint_say_do: tuple[KPIResult, ...]
    plan_diff: PlanDiff
    alerts: tuple[Alert, ...]


def build_replanning_snapshot(
    original_plan: QuarterPlan,
    previous_plan: QuarterPlan,
    current_plan: QuarterPlan,
    actual_results: ActualResults,
    risk_signals: tuple[RiskSignal, ...] = (),
    alert_policy: AlertPolicy = AlertPolicy(),
) -> ReplanningSnapshot:
    """Build a read model without modifying any input plan or factual result."""

    quarter_ids = {
        original_plan.quarter_id,
        previous_plan.quarter_id,
        current_plan.quarter_id,
        actual_results.quarter_id,
    }
    if len(quarter_ids) != 1:
        raise ValueError("all replanning inputs must belong to the same quarter")
    if original_plan.as_of_sprint != 0:
        raise ValueError("original_plan must be the sprint-zero baseline")
    if current_plan.as_of_sprint != actual_results.completed_through_sprint:
        raise ValueError(
            "current_plan.as_of_sprint must equal actual_results.completed_through_sprint"
        )
    if previous_plan.as_of_sprint > current_plan.as_of_sprint:
        raise ValueError("previous_plan cannot be newer than current_plan")
    plan_diff = compare_plans(previous_plan, current_plan)
    risk_alerts = evaluate_risk_signals(
        tuple(risk_signals), actual_results.completed_through_sprint, alert_policy
    )
    change_alerts = alerts_from_plan_diff(plan_diff)
    alerts = sort_alerts((*risk_alerts, *change_alerts))
    return ReplanningSnapshot(
        original_plan=original_plan,
        previous_plan=previous_plan,
        current_plan=current_plan,
        actual_results=actual_results,
        pi_predictability_fact=calculate_pi_predictability_fact(
            original_plan, actual_results
        ),
        pi_predictability_forecast=(
            calculate_pi_predictability(original_plan, current_plan, actual_results)
            if actual_results.completed_through_sprint < QUARTER_SPRINTS
            else None
        ),
        sprint_say_do=tuple(
            calculate_sprint_say_do(
                original_plan, current_plan, actual_results, sprint_number
            )
            for sprint_number in range(1, QUARTER_SPRINTS + 1)
        ),
        plan_diff=plan_diff,
        alerts=alerts,
    )
