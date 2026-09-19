from __future__ import annotations

from src.planning import (
    PlanStatus,
    compare_priority_profiles,
    initiative_priority_profile,
)

from tests.acceptance.builders import SyntheticQuarter, result_by_id


def _quarter() -> SyntheticQuarter:
    return (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("ENG-A", "Team-A", "Backend")
    )


def _add_initiative(
    quarter: SyntheticQuarter,
    initiative_id: str,
    rungs: list[float],
    hours_per_task: float,
) -> None:
    for index, rung in enumerate(rungs, start=1):
        quarter.task(
            f"{initiative_id}-{index}",
            initiative_id=initiative_id,
            story_points=1,
            role_hours={"Backend": hours_per_task},
            rung=rung,
        )


def test_higher_first_rung_beats_a_larger_rung_sum() -> None:
    quarter = _quarter()
    _add_initiative(quarter, "INIT-A", [10, 2], 160)
    _add_initiative(quarter, "INIT-B", [7, 7, 7], 80)

    plan = quarter.plan()

    assert result_by_id(plan, "INIT-A-1").priority_order == 1
    assert result_by_id(plan, "INIT-A-1").status == PlanStatus.PLANNED


def test_only_maximum_task_rung_defines_initiative_priority() -> None:
    left = initiative_priority_profile([{"rung": 10}, {"rung": 1}])
    right = initiative_priority_profile([{"rung": 8}, {"rung": 8}])
    equal_max = initiative_priority_profile([{"rung": 10}, {"rung": 9}])

    assert left == (10,)
    assert right == (8,)
    assert compare_priority_profiles(left, right) == 1
    assert compare_priority_profiles(left, equal_max) == 0


def test_equal_maximum_does_not_reward_more_tasks_or_rung_sum() -> None:
    left = initiative_priority_profile([{"rung": 10}, {"rung": 8}])
    right = initiative_priority_profile(
        [{"rung": 10}, {"rung": 8}, {"rung": 3}, {"rung": 3}]
    )
    assert compare_priority_profiles(left, right) == 0
    assert compare_priority_profiles(right, left) == 0



def test_infeasible_high_profile_does_not_block_feasible_lower_profile() -> None:
    quarter = _quarter()
    _add_initiative(quarter, "INIT-HIGH", [100], 500)
    _add_initiative(quarter, "INIT-LOW", [10], 80)

    plan = quarter.plan()

    assert plan.completed_initiative_ids == ("INIT-LOW",)
    assert result_by_id(plan, "INIT-HIGH-1").status == PlanStatus.MOVED
    assert result_by_id(plan, "INIT-LOW-1").status == PlanStatus.PLANNED


def test_completed_lower_rung_initiatives_cannot_beat_higher_task_rung() -> None:
    quarter = _quarter()
    _add_initiative(quarter, "INIT-LOW-A", [10], 160)
    _add_initiative(quarter, "INIT-LOW-B", [9], 160)
    _add_initiative(quarter, "INIT-HIGH", [100], 400)

    plan = quarter.plan()

    assert result_by_id(plan, "INIT-HIGH-1").priority_order == 1
    assert result_by_id(plan, "INIT-HIGH-1").status == PlanStatus.PLANNED


def test_r10_task_wins_even_when_two_r9_tasks_would_complete_an_initiative() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("ENG-A", "Team-A", "Backend", capacity_rate=0.5)
    )
    _add_initiative(quarter, "INIT-HIGH", [10, 2], 240)
    _add_initiative(quarter, "INIT-LOW", [9, 9], 120)

    plan = quarter.plan()

    assert result_by_id(plan, "INIT-HIGH-1").priority_order == 1
    assert result_by_id(plan, "INIT-HIGH-1").status == PlanStatus.PLANNED
    assert result_by_id(plan, "INIT-LOW-1").status == PlanStatus.MOVED
    assert result_by_id(plan, "INIT-LOW-2").status == PlanStatus.MOVED


def test_same_rung_prefers_tasks_that_complete_one_initiative() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("ENG-A", "Team-A", "Backend", capacity_rate=0.5)
    )
    _add_initiative(quarter, "INIT-Z-COMPLETE", [8, 8], 120)
    _add_initiative(quarter, "INIT-A-INCOMPLETE", [8, 7], 120)
    _add_initiative(quarter, "INIT-B-INCOMPLETE", [8, 7], 120)

    plan = quarter.plan()

    assert plan.completed_initiative_ids == ("INIT-Z-COMPLETE",)
    assert result_by_id(plan, "INIT-Z-COMPLETE-1").priority_order == 1
    assert result_by_id(plan, "INIT-Z-COMPLETE-2").priority_order == 2


def test_same_rung_maximizes_number_of_completed_initiatives() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("ENG-A", "Team-A", "Backend", capacity_rate=0.5)
    )
    _add_initiative(quarter, "INIT-A", [7], 240)
    _add_initiative(quarter, "INIT-B", [7], 120)
    _add_initiative(quarter, "INIT-C", [7], 120)

    plan = quarter.plan()

    assert plan.completed_initiative_ids == ("INIT-B", "INIT-C")


def test_initiative_choice_is_deterministic() -> None:
    quarter = _quarter()
    _add_initiative(quarter, "INIT-A", [10, 5, 2], 100)
    _add_initiative(quarter, "INIT-B", [10, 8, 1], 100)

    snapshots = []
    for _ in range(4):
        plan = quarter.plan()
        snapshots.append(
            (
                plan.completed_initiative_ids,
                tuple(
                    result.task_id
                    for result in sorted(
                        plan.task_results,
                        key=lambda item: item.priority_order or 10_000,
                    )
                ),
            )
        )

    assert all(snapshot == snapshots[0] for snapshot in snapshots)
