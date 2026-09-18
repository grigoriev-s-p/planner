from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.replanning import (  # noqa: E402
    ActualResult,
    ActualResults,
    AlertCode,
    AlertLevel,
    AlertPolicy,
    ChangeType,
    EngineerHoursAllocation,
    MeasureKind,
    PlanItem,
    PlanState,
    QuarterPlan,
    ReferenceCatalog,
    RoleHoursAllocation,
    RiskSignal,
    SprintAllocation,
    build_replanning_snapshot,
    calculate_expected_quarter_kpi,
    calculate_pi_predictability,
    calculate_pi_predictability_fact,
    calculate_sprint_say_do,
    compare_plans,
    validate_reference_usage,
)
from src.replanning.alerts import evaluate_risk_signals  # noqa: E402


def plan(version: int, items: tuple[PlanItem, ...], as_of_sprint: int = 0) -> QuarterPlan:
    return QuarterPlan("2026-Q1", version, as_of_sprint, items)


def item(
    task_id: str,
    initiative_id: str,
    story_points: float,
    sprint: int | None,
    state: PlanState = PlanState.PLANNED,
    **kwargs: object,
) -> PlanItem:
    return PlanItem(task_id, initiative_id, story_points, sprint, state, **kwargs)


def facts(completed_through: int, *entries: ActualResult) -> ActualResults:
    return ActualResults("2026-Q1", completed_through, tuple(entries))


class ModelTests(unittest.TestCase):
    def test_plan_snapshots_are_immutable(self) -> None:
        original = plan(1, (item("T-1", "I-1", 5, 1),))
        with self.assertRaises(FrozenInstanceError):
            original.version = 2  # type: ignore[misc]

    def test_current_plan_can_expose_forecast_outside_quarter(self) -> None:
        slipped = item("T-1", "I-1", 5, 7)
        self.assertFalse(slipped.is_in_quarter)

    def test_cancelled_item_cannot_have_a_sprint(self) -> None:
        with self.assertRaises(ValueError):
            item("T-1", "I-1", 5, 2, PlanState.CANCELLED)

    def test_duplicate_task_ids_are_rejected(self) -> None:
        duplicate = item("T-1", "I-1", 5, 1)
        with self.assertRaisesRegex(ValueError, "duplicate task_id"):
            plan(1, (duplicate, duplicate))

    def test_facts_cannot_be_recorded_for_an_open_sprint(self) -> None:
        entry = ActualResult("T-1", "I-1", 2, 5, True)
        with self.assertRaisesRegex(ValueError, "open sprint"):
            facts(1, entry)

    def test_duplicate_task_result_in_one_sprint_is_rejected(self) -> None:
        entry = ActualResult("T-1", "I-1", 1, 3, False)
        with self.assertRaisesRegex(ValueError, "duplicate actual result"):
            facts(1, entry, entry)

    def test_task_can_store_role_and_engineer_effort_across_sprints(self) -> None:
        multi_sprint = item(
            "T-1",
            "I-1",
            13,
            2,
            allocations=(
                SprintAllocation(
                    1,
                    (RoleHoursAllocation("Backend", 80),),
                    (EngineerHoursAllocation("E-1", "Backend", 80),),
                ),
                SprintAllocation(
                    2,
                    (RoleHoursAllocation("Backend", 50),),
                    (EngineerHoursAllocation("E-1", "Backend", 50),),
                ),
            ),
        )
        self.assertEqual(len(multi_sprint.allocations), 2)
        self.assertEqual(
            sum(
                allocation.hours
                for sprint in multi_sprint.allocations
                for allocation in sprint.role_hours
            ),
            130,
        )

    def test_allocation_cannot_be_later_than_task_completion(self) -> None:
        with self.assertRaisesRegex(ValueError, "later than completion"):
            item(
                "T-1",
                "I-1",
                5,
                1,
                allocations=(SprintAllocation(2),),
            )


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = plan(
            1,
            (
                item("T-1", "I-1", 5, 1),
                item("T-2", "I-1", 3, 2),
                item("T-3", "I-2", 8, 2),
            ),
        )

    def test_say_do_fact_uses_original_denominator(self) -> None:
        current = plan(
            2,
            (
                item("T-1", "I-1", 5, 2),
                item("T-2", "I-1", 3, 2),
                item("T-3", "I-2", 8, 2),
            ),
            1,
        )
        actual = facts(1, ActualResult("T-1", "I-1", 1, 4, False))
        result = calculate_sprint_say_do(self.original, current, actual, 1)
        self.assertEqual(result.kind, MeasureKind.FACT)
        self.assertEqual(result.numerator, 4)
        self.assertEqual(result.denominator, 5)
        self.assertEqual(result.percentage, 80)

    def test_say_do_can_exceed_one_hundred_percent(self) -> None:
        actual = facts(
            1,
            ActualResult("T-1", "I-1", 1, 5, True),
            ActualResult("T-NEW", "I-NEW", 1, 3, True),
        )
        result = calculate_sprint_say_do(self.original, self.original, actual, 1)
        self.assertEqual(result.percentage, 160)

    def test_future_say_do_is_unavailable_against_retained_baseline(self) -> None:
        current = plan(
            2,
            (
                item("T-1", "I-1", 5, 1, PlanState.COMPLETED),
                item("T-2", "I-1", 3, 3),
                item("T-3", "I-2", 8, 2),
            ),
            1,
        )
        result = calculate_sprint_say_do(self.original, current, facts(1), 2)
        self.assertEqual(result.kind, MeasureKind.FORECAST)
        self.assertIsNone(result.numerator)
        self.assertEqual(result.denominator, 11)
        self.assertIsNone(result.percentage)
        self.assertFalse(result.has_actual)

    def test_zero_baseline_returns_none_not_division_error(self) -> None:
        result = calculate_sprint_say_do(self.original, self.original, facts(0), 6)
        self.assertIsNone(result.percentage)

    def test_pi_forecast_detects_baseline_task_slipping_outside_quarter(self) -> None:
        current = plan(
            2,
            (
                item("T-1", "I-1", 5, 1, PlanState.COMPLETED),
                item("T-2", "I-1", 3, 7),
                item("T-3", "I-2", 8, 2),
            ),
            1,
        )
        actual = facts(1, ActualResult("T-1", "I-1", 1, 5, True))
        result = calculate_pi_predictability(self.original, current, actual)
        self.assertEqual(result.kind, MeasureKind.FORECAST)
        self.assertEqual(result.denominator, 2)
        self.assertEqual(result.numerator, 1)
        self.assertEqual(result.percentage, 50)

    def test_pi_fact_ignores_new_scope_and_uses_original_initiatives(self) -> None:
        current = plan(
            3,
            self.original.items + (item("T-NEW", "I-NEW", 13, 6),),
            6,
        )
        actual = facts(
            6,
            ActualResult("T-1", "I-1", 1, 5, True),
            ActualResult("T-2", "I-1", 2, 3, True, True),
            ActualResult("T-NEW", "I-NEW", 6, 13, True, True),
        )
        result = calculate_pi_predictability(self.original, current, actual)
        self.assertEqual(result.kind, MeasureKind.FACT)
        self.assertEqual(result.numerator, 1)
        self.assertEqual(result.denominator, 2)
        self.assertEqual(result.percentage, 50)

    def test_pi_fact_to_date_is_available_before_quarter_end(self) -> None:
        actual = facts(1, ActualResult("T-3", "I-2", 1, 8, True, True))
        result = calculate_pi_predictability_fact(self.original, actual)
        self.assertEqual(result.kind, MeasureKind.FACT)
        self.assertEqual(result.percentage, 50)

    def test_quarter_fact_is_four_of_ten_baseline_initiatives(self) -> None:
        original = plan(
            1,
            tuple(item(f"T-{index}", f"I-{index}", 1, 1) for index in range(10)),
        )
        actual = facts(
            1,
            *(
                ActualResult(f"T-{index}", f"I-{index}", 1, 1, True, True)
                for index in range(4)
            ),
        )
        result = calculate_pi_predictability_fact(original, actual)
        self.assertEqual(result.numerator, 4)
        self.assertEqual(result.denominator, 10)
        self.assertEqual(result.percentage, 40)

    def test_current_plan_does_not_change_quarter_baseline_denominator(self) -> None:
        original = plan(
            1,
            tuple(item(f"T-{index}", f"I-{index}", 1, 1) for index in range(10)),
        )
        current = plan(2, (item("T-NEW", "I-NEW", 1, 2),), as_of_sprint=1)
        actual = facts(
            6,
            *(
                ActualResult(f"T-{index}", f"I-{index}", 1, 1, True, True)
                for index in range(4)
            ),
        )
        result = calculate_pi_predictability(original, current, actual)
        self.assertEqual(result.denominator, 10)
        self.assertEqual(result.percentage, 40)

    def test_sprint_fact_is_fifteen_of_twenty_story_points(self) -> None:
        original = plan(1, (item("T-20", "I-1", 20, 1),))
        actual = facts(1, ActualResult("T-20", "I-1", 1, 15, False))
        result = calculate_sprint_say_do(original, original, actual, 1)
        self.assertEqual(result.numerator, 15)
        self.assertEqual(result.denominator, 20)
        self.assertEqual(result.percentage, 75)

    def test_multi_sprint_baseline_and_actual_sp_are_proportional_to_hours(self) -> None:
        multi_sprint = item(
            "T-MULTI",
            "I-1",
            10,
            3,
            allocations=(
                SprintAllocation(1, (RoleHoursAllocation("Backend", 80),)),
                SprintAllocation(2, (RoleHoursAllocation("Backend", 120),)),
                SprintAllocation(3, (RoleHoursAllocation("Backend", 100),)),
            ),
        )
        original = plan(1, (multi_sprint,))
        actual = facts(
            3,
            ActualResult(
                "T-MULTI", "I-1", 1, None, False, completed_work_hours=80
            ),
            ActualResult(
                "T-MULTI", "I-1", 2, None, False, completed_work_hours=120
            ),
            ActualResult(
                "T-MULTI", "I-1", 3, None, True, completed_work_hours=100
            ),
        )

        results = [
            calculate_sprint_say_do(original, original, actual, sprint)
            for sprint in range(1, 4)
        ]
        expected = (10 * 80 / 300, 10 * 120 / 300, 10 * 100 / 300)
        for result, expected_sp in zip(results, expected, strict=True):
            self.assertAlmostEqual(result.denominator, expected_sp)
            self.assertAlmostEqual(result.numerator or 0, expected_sp)
            self.assertEqual(result.percentage, 100)

    def test_initiative_flag_cannot_bypass_incomplete_baseline_task(self) -> None:
        original = plan(
            1,
            (item("T-1", "I-1", 5, 1), item("T-2", "I-1", 5, 1)),
        )
        actual = facts(1, ActualResult("T-1", "I-1", 1, 5, True, True))
        result = calculate_pi_predictability_fact(original, actual)
        self.assertEqual(result.numerator, 0)
        self.assertEqual(result.percentage, 0)

    def test_closed_sprint_with_zero_baseline_sp_is_unavailable(self) -> None:
        original = plan(1, (item("T-0", "I-1", 0, 1),))
        actual = facts(1, ActualResult("T-0", "I-1", 1, 0, True))
        result = calculate_sprint_say_do(original, original, actual, 1)
        self.assertEqual(result.denominator, 0)
        self.assertIsNone(result.percentage)

    def test_empty_quarter_baseline_is_unavailable(self) -> None:
        original = plan(1, ())
        result = calculate_pi_predictability_fact(original, facts(1))
        self.assertEqual(result.denominator, 0)
        self.assertIsNone(result.percentage)

    def test_expected_kpi_counts_five_of_seven_baseline_initiatives(self) -> None:
        baseline = tuple(f"I-{index}" for index in range(7))
        result = calculate_expected_quarter_kpi(baseline, baseline[:5])
        self.assertEqual(result.numerator, 5)
        self.assertEqual(result.denominator, 7)
        self.assertAlmostEqual(result.percentage or 0, 71.4285714)

    def test_expected_kpi_handles_all_none_and_empty_baseline(self) -> None:
        baseline = tuple(f"I-{index}" for index in range(7))
        all_completed = calculate_expected_quarter_kpi(baseline, baseline)
        none_completed = calculate_expected_quarter_kpi(baseline, ())
        empty = calculate_expected_quarter_kpi((), ())
        self.assertEqual(all_completed.percentage, 100)
        self.assertEqual(none_completed.percentage, 0)
        self.assertIsNone(empty.percentage)

    def test_expected_kpi_percent_contract_for_constructor_preview(self) -> None:
        baseline = tuple(f"I-{index}" for index in range(10))
        cases = ((5, 50.0), (10, 100.0), (0, 0.0))
        for completed_count, expected_percent in cases:
            with self.subTest(completed=completed_count):
                result = calculate_expected_quarter_kpi(
                    baseline, baseline[:completed_count]
                )
                self.assertEqual(result.percentage, expected_percent)
        self.assertIsNone(calculate_expected_quarter_kpi((), ()).percentage)

    def test_expected_forecast_rejects_partially_planned_initiative(self) -> None:
        original = plan(
            1,
            tuple(item(f"T-{index}", "I-1", 1, 1) for index in range(5)),
        )
        current = plan(2, original.items[:4])
        result = calculate_pi_predictability(original, current, facts(0))
        self.assertEqual(result.numerator, 0)
        self.assertEqual(result.percentage, 0)

    def test_expected_forecast_combines_done_and_planned_tasks(self) -> None:
        original = plan(
            1,
            tuple(item(f"T-{index}", "I-1", 1, 1) for index in range(4)),
        )
        current = plan(2, original.items[2:], as_of_sprint=1)
        actual = facts(
            1,
            ActualResult("T-0", "I-1", 1, 1, True),
            ActualResult("T-1", "I-1", 1, 1, True),
        )
        result = calculate_pi_predictability(original, current, actual)
        self.assertEqual(result.numerator, 1)
        self.assertEqual(result.percentage, 100)


class DiffTests(unittest.TestCase):
    def test_diff_reports_added_removed_and_changed_fields(self) -> None:
        before = plan(
            1,
            (
                item("T-1", "I-1", 5, 1, team_id="A"),
                item("T-2", "I-2", 3, 2),
            ),
        )
        after = plan(
            2,
            (
                item(
                    "T-1",
                    "I-CHANGED",
                    8,
                    3,
                    team_id="B",
                    decision_status="moved",
                    mismatch_reason="capacity",
                ),
                item("T-3", "I-3", 2, 2),
            ),
            1,
        )
        result = compare_plans(before, after)
        by_id = {entry.task_id: entry for entry in result.entries}
        self.assertEqual(by_id["T-2"].changes, (ChangeType.REMOVED,))
        self.assertEqual(by_id["T-3"].changes, (ChangeType.ADDED,))
        self.assertEqual(
            set(by_id["T-1"].changes),
            {
                ChangeType.SPRINT_CHANGED,
                ChangeType.STORY_POINTS_CHANGED,
                ChangeType.STATUS_CHANGED,
                ChangeType.REASON_CHANGED,
                ChangeType.TEAM_CHANGED,
                ChangeType.INITIATIVE_CHANGED,
            },
        )

    def test_unchanged_tasks_are_not_in_diff(self) -> None:
        original = plan(1, (item("T-1", "I-1", 5, 1),))
        current = plan(2, original.items)
        self.assertEqual(compare_plans(original, current).entries, ())


class AlertTests(unittest.TestCase):
    def test_deadline_is_red_and_role_capacity_deficit_is_orange(self) -> None:
        signal = RiskSignal(
            "T-1",
            forecast_completion_sprint=7,
            role="QA",
            role_shortage_hours=16,
            shortage_sprint=3,
        )
        result = evaluate_risk_signals((signal,), completed_through_sprint=2)
        self.assertEqual({alert.code for alert in result}, {
            AlertCode.DEADLINE_OUTSIDE_QUARTER,
            AlertCode.ROLE_CAPACITY_DEFICIT,
        })
        levels = {alert.code: alert.level for alert in result}
        self.assertEqual(levels[AlertCode.DEADLINE_OUTSIDE_QUARTER], AlertLevel.RED)
        self.assertEqual(levels[AlertCode.ROLE_CAPACITY_DEFICIT], AlertLevel.ORANGE)

    def test_bus_factor_is_orange_before_urgent_horizon_and_red_inside_it(self) -> None:
        result = evaluate_risk_signals(
            (
                RiskSignal("Skill-A", skill="Python", bus_factor=1, skill_needed_sprint=5),
                RiskSignal("Skill-B", skill="Go", bus_factor=1, skill_needed_sprint=3),
            ),
            completed_through_sprint=2,
        )
        levels = {alert.entity_id: alert.level for alert in result}
        self.assertEqual(levels, {"Skill-A": AlertLevel.ORANGE, "Skill-B": AlertLevel.RED})

    def test_dependency_shift_is_yellow_until_deadline_is_breached(self) -> None:
        result = evaluate_risk_signals(
            (
                RiskSignal("T-1", dependency_shift_sprints=1),
                RiskSignal("T-2", dependency_shift_sprints=2),
            ),
            completed_through_sprint=1,
        )
        levels = {alert.entity_id: alert.level for alert in result}
        self.assertEqual(levels, {"T-1": AlertLevel.YELLOW, "T-2": AlertLevel.YELLOW})

    def test_alert_policy_rejects_non_positive_thresholds(self) -> None:
        with self.assertRaises(ValueError):
            AlertPolicy(urgent_horizon_sprints=0)


class ReferenceCatalogTests(unittest.TestCase):
    def test_unknown_reference_values_are_reported(self) -> None:
        catalog = ReferenceCatalog(("planned",), ("capacity",), ("done",))
        current = plan(
            1,
            (item("T-1", "I-1", 5, 1, decision_status="invented"),),
        )
        actual = facts(
            1,
            ActualResult("T-1", "I-1", 1, 5, True, closure_status="not-in-source"),
        )
        issues = validate_reference_usage(current, actual, catalog)
        self.assertEqual(len(issues), 2)

    def test_source_reference_values_pass_unchanged(self) -> None:
        catalog = ReferenceCatalog(("planned",), ("capacity",), ("done",))
        current = plan(
            1,
            (
                item(
                    "T-1",
                    "I-1",
                    5,
                    1,
                    decision_status="planned",
                    mismatch_reason="capacity",
                ),
            ),
        )
        actual = facts(
            1,
            ActualResult(
                "T-1", "I-1", 1, 5, True, closure_status="done", mismatch_reason="capacity"
            ),
        )
        self.assertEqual(validate_reference_usage(current, actual, catalog), ())


class SnapshotTests(unittest.TestCase):
    def test_snapshot_composes_kpis_diff_and_alerts(self) -> None:
        original = plan(1, (item("T-1", "I-1", 5, 1), item("T-2", "I-2", 8, 2)))
        current = plan(
            2,
            (item("T-1", "I-1", 5, 1, PlanState.COMPLETED), item("T-2", "I-2", 8, 7)),
            1,
        )
        actual = facts(1, ActualResult("T-1", "I-1", 1, 5, True, True))
        snapshot = build_replanning_snapshot(
            original,
            original,
            current,
            actual,
            (RiskSignal("T-2", forecast_completion_sprint=7),),
        )
        self.assertEqual(snapshot.pi_predictability_fact.percentage, 50)
        self.assertIsNotNone(snapshot.pi_predictability_forecast)
        self.assertEqual(snapshot.pi_predictability_forecast.percentage, 50)
        self.assertEqual(len(snapshot.sprint_say_do), 6)
        self.assertEqual(len(snapshot.plan_diff.entries), 2)
        self.assertIn(AlertCode.DEADLINE_OUTSIDE_QUARTER, {a.code for a in snapshot.alerts})
        self.assertEqual(snapshot.alerts[0].level, AlertLevel.RED)

    def test_snapshot_rejects_plan_fact_time_mismatch(self) -> None:
        original = plan(1, (item("T-1", "I-1", 5, 1),))
        stale_current = plan(2, original.items, as_of_sprint=0)
        with self.assertRaisesRegex(ValueError, "as_of_sprint"):
            build_replanning_snapshot(original, original, stale_current, facts(1))


if __name__ == "__main__":
    unittest.main()
