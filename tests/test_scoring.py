from __future__ import annotations

import unittest

from src.planning.scoring import (
    PlanningContext,
    ScoringConfig,
    calculate_task_score,
    downstream_task_ids,
)
from src.planning.planner import sort_tasks_for_planning


def task(
    task_id: str,
    *,
    status: str = "ToDo",
    rung: float | None = None,
    planned_end: str | None = None,
    initiative_id: str | None = None,
    placeholder: bool = False,
    **extra,
) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "rung": rung,
        "planned_end": planned_end,
        "initiative_id": initiative_id,
        "is_placeholder": placeholder,
        **extra,
    }


def scoring_context(tasks: list[dict], dependencies: list[dict] | None = None) -> PlanningContext:
    return PlanningContext.from_records(tasks, dependencies or [])


class ScoringTests(unittest.TestCase):
    def test_in_progress_receives_status_bonus(self) -> None:
        tasks = [task("TODO", status="ToDo"), task("ACTIVE", status="InProgress")]
        context = scoring_context(tasks)
        todo = calculate_task_score(tasks[0], context)
        active = calculate_task_score(tasks[1], context)
        self.assertEqual(todo.components["status_score"], 0)
        self.assertEqual(active.components["status_score"], 25)

    def test_in_progress_bonus_is_not_absolute_priority(self) -> None:
        tasks = [
            task("HIGH", status="ToDo", rung=100),
            task("ACTIVE", status="InProgress", rung=0),
        ]
        context = scoring_context(tasks)
        high = calculate_task_score(tasks[0], context)
        active = calculate_task_score(tasks[1], context)
        self.assertGreater(high.total_score, active.total_score)

    def test_best_rung_receives_higher_score(self) -> None:
        tasks = [task("HIGH", rung=100), task("LOW", rung=50)]
        context = scoring_context(tasks)
        self.assertGreater(
            calculate_task_score(tasks[0], context).components["rung_score"],
            calculate_task_score(tasks[1], context).components["rung_score"],
        )

    def test_confirmed_default_uses_higher_rung_and_largest_individual_weight(self) -> None:
        config = ScoringConfig()
        self.assertEqual(config.rung_direction, "higher_is_better")
        self.assertGreater(config.rung_weight, max(
            config.in_progress_bonus, config.deadline_weight,
            config.initiative_completion_weight, config.dependency_unlock_weight,
        ))
        tasks = [task("LOW", rung=50), task("HIGH", rung=100)]
        self.assertEqual(
            [row["task_id"] for row in sort_tasks_for_planning(tasks, [])],
            ["HIGH", "LOW"],
        )

    def test_equal_scores_have_deterministic_task_id_tie_break(self) -> None:
        tasks = [task("B", rung=50), task("A", rung=50)]
        context = scoring_context(tasks)
        self.assertEqual(
            calculate_task_score(tasks[0], context).total_score,
            calculate_task_score(tasks[1], context).total_score,
        )
        for rows in (tasks, tasks[::-1]):
            self.assertEqual(
                [row["task_id"] for row in sort_tasks_for_planning(rows, [])], ["A", "B"]
            )

    def test_additional_factors_can_outweigh_a_small_rung_difference(self) -> None:
        tasks = [
            task("HIGH", rung=100), task("ACTIVE", rung=90, status="InProgress"),
            task("LOW", rung=0),
        ]
        context = scoring_context(tasks)
        high, active = [calculate_task_score(row, context) for row in tasks[:2]]
        self.assertGreater(high.components["rung_score"], active.components["rung_score"])
        self.assertGreater(active.total_score, high.total_score)
        self.assertEqual(sort_tasks_for_planning(tasks, [])[0]["task_id"], "ACTIVE")

    def test_superseded_lower_rung_direction_is_rejected(self) -> None:
        tasks = [task("HIGH", rung=100), task("LOW", rung=10)]
        context = scoring_context(tasks)
        higher = ScoringConfig(rung_direction="higher_is_better")
        self.assertGreater(
            calculate_task_score(tasks[0], context, higher).components["rung_score"],
            calculate_task_score(tasks[1], context, higher).components["rung_score"],
        )
        with self.assertRaisesRegex(ValueError, "higher_is_better"):
            ScoringConfig(rung_direction="lower_is_better")  # type: ignore[arg-type]

    def test_missing_rung_is_zero_and_does_not_break_scoring(self) -> None:
        tasks = [task("KNOWN", rung=10), task("MISSING")]
        context = scoring_context(tasks)
        score = calculate_task_score(tasks[1], context)
        self.assertTrue(score.eligible)
        self.assertEqual(score.components["rung_score"], 0)

    def test_planned_end_is_metadata_and_does_not_change_score_or_order(self) -> None:
        tasks = [
            task("Z-EARLY", planned_end="2026-01-15"),
            task("A-LATE", planned_end="2026-03-15"),
        ]
        context = scoring_context(tasks)
        early = calculate_task_score(tasks[0], context)
        late = calculate_task_score(tasks[1], context)
        self.assertEqual(early.components["deadline_score"], 0)
        self.assertEqual(early.total_score, late.total_score)
        self.assertEqual(
            [row["task_id"] for row in sort_tasks_for_planning(tasks, [])],
            ["A-LATE", "Z-EARLY"],
        )

    def test_planned_end_is_not_an_eligibility_constraint(self) -> None:
        tasks = [task("PAST", planned_end="2000-01-01")]
        score = calculate_task_score(tasks[0], scoring_context(tasks))
        self.assertTrue(score.eligible)

    def test_last_initiative_task_receives_maximum_completion_bonus(self) -> None:
        tasks = [
            task("DONE", status="Done", initiative_id="I-1"),
            task("LAST", initiative_id="I-1"),
        ]
        context = scoring_context(tasks)
        score = calculate_task_score(tasks[1], context)
        self.assertEqual(score.components["initiative_completion_score"], 20)

    def test_initiative_bonus_is_recalculated_after_commit(self) -> None:
        tasks = [task("A", initiative_id="I-1"), task("B", initiative_id="I-1")]
        before = scoring_context(tasks)
        before_score = calculate_task_score(tasks[1], before)
        after_score = calculate_task_score(tasks[1], before.with_committed("A"))
        self.assertEqual(before_score.components["initiative_completion_score"], 10)
        self.assertEqual(after_score.components["initiative_completion_score"], 20)

    def test_dependency_blocker_receives_unlock_bonus(self) -> None:
        tasks = [task("A"), task("B")]
        dependencies = [{"blocking_task_id": "A", "blocked_task_id": "B"}]
        context = scoring_context(tasks, dependencies)
        self.assertGreater(
            calculate_task_score(tasks[0], context).components["dependency_unlock_score"],
            calculate_task_score(tasks[1], context).components["dependency_unlock_score"],
        )

    def test_more_downstream_tasks_increase_bonus_up_to_maximum(self) -> None:
        tasks = [task(letter) for letter in "ABCDEF"]
        dependencies = [
            {"blocking_task_id": "A", "blocked_task_id": "B"},
            {"blocking_task_id": "B", "blocked_task_id": "C"},
            {"blocking_task_id": "C", "blocked_task_id": "D"},
            {"blocking_task_id": "D", "blocked_task_id": "E"},
            {"blocking_task_id": "E", "blocked_task_id": "E"},
            {"blocking_task_id": "F", "blocked_task_id": "B"},
        ]
        context = scoring_context(tasks, dependencies)
        self.assertGreater(len(downstream_task_ids("A", context)), len(downstream_task_ids("D", context)))
        a_bonus = calculate_task_score(tasks[0], context).components["dependency_unlock_score"]
        self.assertLessEqual(a_bonus, ScoringConfig().dependency_unlock_weight)
        self.assertEqual(a_bonus, ScoringConfig().dependency_unlock_weight)

    def test_placeholder_is_not_eligible_for_business_scoring(self) -> None:
        tasks = [task("MISSING", placeholder=True)]
        score = calculate_task_score(tasks[0], scoring_context(tasks))
        self.assertFalse(score.eligible)
        self.assertEqual(score.not_eligible_reason, "PLACEHOLDER_TASK")
        self.assertEqual(score.total_score, 0)

    def test_done_task_is_not_eligible(self) -> None:
        tasks = [task("DONE", status="Done", rung=100)]
        score = calculate_task_score(tasks[0], scoring_context(tasks))
        self.assertFalse(score.eligible)
        self.assertEqual(score.not_eligible_reason, "ALREADY_COMPLETED")

    def test_score_does_not_depend_on_capacity_fields(self) -> None:
        first = task("A", rung=10, free_engineer_hours=0, free_story_points=0)
        second = task("A", rung=10, free_engineer_hours=999, free_story_points=999)
        first_score = calculate_task_score(first, scoring_context([first]))
        second_score = calculate_task_score(second, scoring_context([second]))
        self.assertEqual(first_score.to_dict(), second_score.to_dict())


if __name__ == "__main__":
    unittest.main()
