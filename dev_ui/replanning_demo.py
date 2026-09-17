"""Reproducible demonstration data for the KPI and replanning UI page."""

from __future__ import annotations

from enum import Enum

from src.replanning import (
    ActualResult,
    ActualResults,
    EngineerHoursAllocation,
    PlanItem,
    PlanState,
    QuarterPlan,
    ReferenceCatalog,
    ReplanningSnapshot,
    RiskSignal,
    RoleHoursAllocation,
    SprintAllocation,
    build_replanning_snapshot,
)


class DemoScenario(str, Enum):
    ON_TRACK = "on_track"
    QA_SHORTAGE = "qa_shortage"


def _reference_value(
    values: tuple[str, ...],
    fragment: str,
) -> str | None:
    fragment = fragment.casefold()
    return next((value for value in values if fragment in value.casefold()), None)


def _source_labels(
    catalog: ReferenceCatalog | None,
    sprint_completed: bool,
) -> tuple[str | None, str | None, str | None]:
    if catalog is None:
        return None, None, None
    moved_status = _reference_value(catalog.planning_results, "не будет взято")
    resource_reason = _reference_value(catalog.mismatch_reasons, "отсутствие ресурсов")
    closure_fragment = (
        "планируемый результат работ достигнут"
        if sprint_completed
        else "планируемый результат работ не достигнут"
    )
    closure_status = _reference_value(catalog.closure_results, closure_fragment)
    return moved_status, resource_reason, closure_status


def _long_task_allocations(
    first_sprint: int,
    second_sprint: int,
) -> tuple[SprintAllocation, ...]:
    return (
        SprintAllocation(
            first_sprint,
            (RoleHoursAllocation("Backend", 80),),
            (EngineerHoursAllocation("ENG-BACKEND-1", "Backend", 80),),
        ),
        SprintAllocation(
            second_sprint,
            (RoleHoursAllocation("Backend", 50),),
            (EngineerHoursAllocation("ENG-BACKEND-1", "Backend", 50),),
        ),
    )


def build_demo_snapshot(
    scenario: DemoScenario = DemoScenario.QA_SHORTAGE,
    delivered_story_points: float = 5,
    critical_bus_factor: bool = True,
    catalog: ReferenceCatalog | None = None,
) -> ReplanningSnapshot:
    """Build a deterministic after-Sprint-1 snapshot for interactive display."""

    if not 0 <= delivered_story_points <= 5:
        raise ValueError("delivered_story_points must be between 0 and 5")
    sprint_completed = delivered_story_points >= 5
    moved_status, resource_reason, closure_status = _source_labels(
        catalog, sprint_completed
    )

    original_items = (
        PlanItem("T-101", "PRODF-100", 5, 1, team_id="Team A"),
        PlanItem("T-102", "PRODF-200", 8, 2, team_id="Team A"),
        PlanItem("T-103", "PRODF-200", 5, 3, team_id="Team A"),
        PlanItem(
            "T-104",
            "PRODF-300",
            13,
            4,
            team_id="Team B",
            allocations=_long_task_allocations(3, 4),
        ),
        PlanItem("T-105", "PRODF-400", 3, 5, team_id="Team B"),
    )
    original_plan = QuarterPlan("DEMO-2026-Q1", 1, 0, original_items)

    first_task = PlanItem(
        "T-101",
        "PRODF-100",
        5,
        1 if sprint_completed else 2,
        PlanState.COMPLETED if sprint_completed else PlanState.IN_PROGRESS,
        team_id="Team A",
    )
    if scenario is DemoScenario.QA_SHORTAGE:
        current_items = (
            first_task,
            PlanItem(
                "T-102",
                "PRODF-200",
                8,
                3,
                team_id="Team A",
                mismatch_reason=resource_reason,
            ),
            PlanItem("T-103", "PRODF-200", 5, 4, team_id="Team A"),
            PlanItem(
                "T-104",
                "PRODF-300",
                13,
                7,
                PlanState.MOVED,
                team_id="Team B",
                decision_status=moved_status,
                mismatch_reason=resource_reason,
                allocations=_long_task_allocations(6, 7),
            ),
            original_items[4],
        )
        risk_signals = [
            RiskSignal(
                "T-104",
                forecast_completion_sprint=7,
                deadline_sprint=6,
            ),
            RiskSignal(
                "T-102",
                role="QA",
                role_shortage_hours=12,
                shortage_sprint=2,
            ),
            RiskSignal("T-103", dependency_shift_sprints=1),
        ]
    else:
        current_items = (first_task, *original_items[1:])
        risk_signals = []

    if critical_bus_factor:
        risk_signals.append(
            RiskSignal(
                "Team A / Kafka",
                skill="Kafka",
                bus_factor=1,
                skill_needed_sprint=2,
            )
        )

    current_plan = QuarterPlan("DEMO-2026-Q1", 2, 1, current_items)
    actual_results = ActualResults(
        "DEMO-2026-Q1",
        1,
        (
            ActualResult(
                "T-101",
                "PRODF-100",
                1,
                delivered_story_points,
                task_completed=sprint_completed,
                initiative_completed=sprint_completed,
                closure_status=closure_status,
            ),
        ),
    )
    return build_replanning_snapshot(
        original_plan,
        original_plan,
        current_plan,
        actual_results,
        tuple(risk_signals),
    )
