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

    assert plan.completed_initiative_ids == ("INIT-A",)


def test_second_rung_breaks_equal_first_rung() -> None:
    quarter = _quarter()
    _add_initiative(quarter, "INIT-A", [10, 5, 2], 100)
    _add_initiative(quarter, "INIT-B", [10, 8, 1], 100)

    plan = quarter.plan()

    assert plan.completed_initiative_ids == ("INIT-B",)


def test_equal_prefix_does_not_reward_the_longer_profile() -> None:
    left = initiative_priority_profile([{"rung": 10}, {"rung": 8}])
    right = initiative_priority_profile(
        [{"rung": 10}, {"rung": 8}, {"rung": 3}, {"rung": 3}]
    )
    assert compare_priority_profiles(left, right) == 0
    assert compare_priority_profiles(right, left) == 0

    quarter = _quarter()
    _add_initiative(quarter, "INIT-Z-EARLY", [10, 8], 100)
    _add_initiative(quarter, "INIT-A-LATE", [10, 8, 3, 3], 80)

    plan = quarter.plan()

    # Equal profiles fall through to earlier completion. The initiative ID is
    # deliberately less favorable to prove that the tertiary criterion wins.
    assert plan.completed_initiative_ids == ("INIT-Z-EARLY",)


def test_infeasible_high_profile_does_not_block_feasible_lower_profile() -> None:
    quarter = _quarter()
    _add_initiative(quarter, "INIT-HIGH", [100], 500)
    _add_initiative(quarter, "INIT-LOW", [10], 80)

    plan = quarter.plan()

    assert plan.completed_initiative_ids == ("INIT-LOW",)
    assert result_by_id(plan, "INIT-HIGH-1").status == PlanStatus.MOVED
    assert result_by_id(plan, "INIT-LOW-1").status == PlanStatus.PLANNED


def test_completed_initiative_count_beats_rung_profile() -> None:
    quarter = _quarter()
    _add_initiative(quarter, "INIT-LOW-A", [10], 160)
    _add_initiative(quarter, "INIT-LOW-B", [9], 160)
    _add_initiative(quarter, "INIT-HIGH", [100], 400)

    plan = quarter.plan()

    assert plan.completed_initiative_ids == ("INIT-LOW-A", "INIT-LOW-B")


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
