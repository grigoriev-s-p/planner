"""Business-level acceptance scenarios for the six-sprint planner."""

from __future__ import annotations

import pytest

from scripts.data_audit import AuditSeverity
from src.planning import PlanStatus
from src.planning.plan_models import RecommendationType
from src.planning.reasons import ReasonCode

from .builders import SyntheticQuarter, result_by_id


def _single_team_quarter(*, velocity: float = 10, capacity_rate: float = 1.0) -> SyntheticQuarter:
    return (
        SyntheticQuarter()
        .team("Team-A", velocity=velocity)
        .engineer("ENG-A", "Team-A", "Backend", capacity_rate=capacity_rate)
    )


def _reason_codes(result) -> set[ReasonCode]:
    return {reason.code for reason in result.final_reasons}


def test_prefers_more_completed_initiatives_over_more_tasks() -> None:
    quarter = _single_team_quarter()
    for number in range(1, 4):
        quarter.task(
            f"A-{number}", initiative_id="INIT-A", story_points=2,
            role_hours={"Backend": 80}, rung=10,
        )
    for number in range(1, 9):
        quarter.task(
            f"B-{number}", initiative_id="INIT-B", story_points=1,
            role_hours={"Backend": 80}, rung=100,
        )

    plan = quarter.plan()
    b_only = _single_team_quarter()
    for number in range(1, 9):
        b_only.task(
            f"B-{number}", initiative_id="INIT-B", story_points=1,
            role_hours={"Backend": 80}, rung=100,
        )

    assert plan.completed_initiative_ids == ("INIT-A",)
    assert all(result_by_id(plan, f"A-{number}").status == PlanStatus.PLANNED for number in range(1, 4))
    assert len(b_only.plan().planned_tasks) > len(
        [result for result in plan.planned_tasks if result.initiative_id == "INIT-B"]
    )


def test_primary_initiative_objective_beats_high_rung_resource_mix() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A")
        .engineer("ENG-A", "Team-A", "Role-A")
        .engineer("ENG-B", "Team-A", "Role-B")
        .task("MIXED", initiative_id="INIT-MIXED", story_points=1,
              role_hours={"Role-A": 240, "Role-B": 240}, rung=100)
        .task("ROLE-A", initiative_id="INIT-A", story_points=2,
              role_hours={"Role-A": 320}, rung=10)
        .task("ROLE-B", initiative_id="INIT-B", story_points=2,
              role_hours={"Role-B": 320}, rung=10)
    )

    plan = quarter.plan()

    assert plan.completed_initiative_ids == ("INIT-A", "INIT-B")
    assert result_by_id(plan, "MIXED").status == PlanStatus.MOVED


def test_primary_initiative_objective_holds_above_exact_search_threshold() -> None:
    quarter = SyntheticQuarter().team("Team-A", velocity=20)
    for role in ("Role-A", "Role-B", "Role-C", "Role-D", "Role-E"):
        quarter.engineer(f"ENG-{role}", "Team-A", role)
    quarter.task(
        "MIXED",
        initiative_id="INIT-MIXED",
        story_points=1,
        role_hours={"Role-A": 240, "Role-B": 240},
        rung=100,
    )
    quarter.task(
        "ROLE-A",
        initiative_id="INIT-A",
        story_points=2,
        role_hours={"Role-A": 320},
        rung=10,
    )
    quarter.task(
        "ROLE-B",
        initiative_id="INIT-B",
        story_points=2,
        role_hours={"Role-B": 320},
        rung=10,
    )
    for role in ("Role-C", "Role-D", "Role-E"):
        quarter.task(
            role,
            initiative_id=f"INIT-{role}",
            story_points=3,
            role_hours={role: 480},
            rung=10,
        )

    plan = quarter.plan()

    assert plan.completed_initiative_ids == (
        "INIT-A",
        "INIT-B",
        "INIT-Role-C",
        "INIT-Role-D",
        "INIT-Role-E",
    )
    assert result_by_id(plan, "MIXED").status == PlanStatus.MOVED


def test_infeasible_high_rung_does_not_block_lower_rung() -> None:
    quarter = _single_team_quarter()
    quarter.task("HIGH", story_points=2, role_hours={"Backend": 500}, rung=100)
    quarter.task("LOW", story_points=2, role_hours={"Backend": 80}, rung=10)

    plan = quarter.plan()

    assert result_by_id(plan, "HIGH").status == PlanStatus.MOVED
    assert result_by_id(plan, "LOW").status == PlanStatus.PLANNED
    assert plan.sprints[0].engineer_hours_used["ENG-A"] == 80


def test_higher_rung_wins_when_equally_feasible() -> None:
    quarter = _single_team_quarter()
    quarter.task("HIGH", story_points=2, role_hours={"Backend": 80}, rung=100)
    quarter.task("LOW", story_points=2, role_hours={"Backend": 80}, rung=10)

    plan = quarter.plan()

    assert result_by_id(plan, "HIGH").priority_order < result_by_id(plan, "LOW").priority_order
    assert result_by_id(plan, "HIGH").start_sprint == 1


def test_multisprint_sp_is_proportional_to_allocated_hours() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("ENG-1", "Team-A", "Backend")
        .engineer("ENG-2", "Team-A", "Backend")
        .task("LOAD-1", story_points=1, role_hours={"Backend": 80}, rung=100)
        .task("LOAD-2", story_points=1, role_hours={"Backend": 40}, rung=99)
        .task("LOAD-3", story_points=1, role_hours={"Backend": 60}, rung=98)
        .task("BIG", story_points=10, role_hours={"Backend": 300}, rung=1)
        .depends_on("LOAD-2", "LOAD-1")
        .depends_on("LOAD-3", "LOAD-2")
    )

    big = result_by_id(quarter.plan(), "BIG")
    by_sprint = {allocation.sprint_number: allocation for allocation in big.allocations}

    assert big.is_multi_sprint
    assert [by_sprint[number].allocated_hours for number in (1, 2, 3)] == [80, 120, 100]
    assert by_sprint[1].capacity_sp_load == pytest.approx(80 / 30)
    assert by_sprint[2].capacity_sp_load == pytest.approx(4)
    assert by_sprint[3].capacity_sp_load == pytest.approx(100 / 30)
    assert sum(item.capacity_sp_load for item in big.allocations) == pytest.approx(10)


def test_sp_capacity_limits_hours_without_decoupling_sp_load() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("BE-1", "Team-A", "Backend")
        .engineer("BE-2", "Team-A", "Backend")
        .engineer("QA-1", "Team-A", "QA")
        .task("RESERVE", story_points=5, role_hours={"QA": 10}, rung=100)
        .task("AI-LIKE", story_points=9, role_hours={"Backend": 216}, rung=90)
    )

    plan = quarter.plan()
    target = result_by_id(plan, "AI-LIKE")

    assert [item.allocated_hours for item in target.allocations] == [72, 144]
    assert [item.capacity_sp_load for item in target.allocations] == pytest.approx([3, 6])
    assert all(
        sprint.team_sp_used.get("Team-A", 0) <= sprint.team_capacity_sp["Team-A"] + 1e-9
        for sprint in plan.sprints
    )


def test_cross_team_helper_keeps_task_sp_on_assigned_team() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A").team("Team-B")
        .engineer("LOCAL", "Team-A", "Backend", capacity_rate=0.5)
        .engineer("EXTERNAL", "Team-B", "Backend", capacity_rate=0.5)
        .task("T", team_id="Team-A", story_points=2, role_hours={"Backend": 60})
    )

    plan = quarter.plan()
    task = result_by_id(plan, "T")
    allocations = task.allocations[0].engineer_allocations
    external = next(item for item in allocations if item.engineer_id == "EXTERNAL")

    assert task.assigned_team_id == "Team-A"
    assert plan.sprints[0].team_sp_used == {"Team-A": 2}
    assert [(item.engineer_id, item.allocated_hours) for item in allocations] == [
        ("LOCAL", 40), ("EXTERNAL", 20)
    ]
    assert external.source_team_id == "Team-B"
    assert external.assigned_task_team_id == "Team-A"
    assert external.cross_team
    assert external.capacity_share == pytest.approx(0.5)


def test_engineer_team_capacity_buckets_are_independent_and_hard_limited() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A").team("Team-B")
        .engineer("ENG-1", "Team-A", "Backend", capacity_rate=0.5)
        .engineer("ENG-1", "Team-B", "Backend", capacity_rate=0.5)
        .task("A", team_id="Team-A", story_points=2, role_hours={"Backend": 40})
        .task("B", team_id="Team-B", story_points=2, role_hours={"Backend": 40})
    )

    plan = quarter.plan()
    first_sprint = plan.sprints[0]

    assert first_sprint.engineer_team_hours_used["ENG-1"] == {"Team-A": 40, "Team-B": 40}
    assert first_sprint.engineer_hours_used["ENG-1"] == 80
    assert all(
        hours <= 40
        for sprint in plan.sprints
        for hours in sprint.engineer_team_hours_used.get("ENG-1", {}).values()
    )

    restricted = (
        SyntheticQuarter()
        .team("Team-A").team("Team-B")
        .engineer("ENG-1", "Team-A", "Backend", capacity_rate=0.5)
        .engineer("ENG-1", "Team-B", "Backend", capacity_rate=0.5)
        .task("A-50", team_id="Team-A", story_points=2, role_hours={"Backend": 50})
        .task("B-30", team_id="Team-B", story_points=2, role_hours={"Backend": 30})
        .plan()
    )
    assert all(
        hours <= 40
        for sprint in restricted.sprints
        for hours in sprint.engineer_team_hours_used.get("ENG-1", {}).values()
    )


def test_reassignment_uses_existing_team_and_preserves_original_team() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=0).team("Team-B", velocity=10)
        .engineer("ENG-B", "Team-B", "Backend")
        .task("MOVE", team_id="Team-A", story_points=4, role_hours={"Backend": 40})
    )

    plan = quarter.plan()
    moved = result_by_id(plan, "MOVE")

    assert moved.original_team_id == "Team-A"
    assert moved.assigned_team_id == "Team-B"
    assert moved.team_reassigned
    assert plan.sprints[0].team_sp_used == {"Team-B": 4}
    assert {team for sprint in plan.sprints for team in sprint.team_capacity_sp} == {"Team-A", "Team-B"}


def test_task_stays_in_original_team_when_reassignment_does_not_improve_plan() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A").team("Team-B")
        .engineer("ENG-A", "Team-A", "Backend")
        .engineer("ENG-B", "Team-B", "Backend")
        .task("STAY", team_id="Team-A", story_points=2, role_hours={"Backend": 40})
    )

    task = result_by_id(quarter.plan(), "STAY")

    assert task.assigned_team_id == "Team-A"
    assert not task.team_reassigned


def test_cross_team_help_and_task_reassignment_remain_distinct() -> None:
    helper_quarter = (
        SyntheticQuarter()
        .team("Team-A").team("Team-B")
        .engineer("LOCAL", "Team-A", "Backend", capacity_rate=0.5)
        .engineer("HELPER", "Team-B", "Backend", capacity_rate=0.5)
        .task("HELP", team_id="Team-A", story_points=2, role_hours={"Backend": 60})
    )
    reassignment_quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=0).team("Team-B")
        .engineer("ENG-B", "Team-B", "Backend")
        .task("MOVE", team_id="Team-A", story_points=2, role_hours={"Backend": 40})
    )

    helper = result_by_id(helper_quarter.plan(), "HELP")
    reassigned = result_by_id(reassignment_quarter.plan(), "MOVE")

    assert helper.assigned_team_id == "Team-A"
    assert any(
        allocation.cross_team
        for allocation in helper.allocations[0].engineer_allocations
    )
    assert reassigned.assigned_team_id == "Team-B"
    assert reassigned.team_reassigned


def test_done_status_overrides_inconsistent_dependency_and_audit_warns(tmp_path) -> None:
    quarter = _single_team_quarter()
    quarter.task("B", story_points=1, role_hours={"Backend": 40}, status="ToDo")
    quarter.task("A", story_points=2, role_hours={"Backend": 80}, status="Done")
    quarter.depends_on("A", "B")

    plan = quarter.plan()
    audit = quarter.audit(tmp_path)
    done = result_by_id(plan, "A")

    assert done.status == PlanStatus.COMPLETED
    assert done.completion_sprint == 0
    assert done.allocations == ()
    assert all(
        allocation.task_id != "A"
        for sprint in plan.sprints
        for allocation in sprint.task_allocations
    )
    conflict = next(issue for issue in audit.issues if issue.issue_code == "DONE_DEPENDENCY_CONFLICT")
    assert conflict.severity == AuditSeverity.WARNING


def test_dependency_chain_starts_only_after_each_blocker_completes() -> None:
    quarter = _single_team_quarter()
    quarter.task("C", story_points=2, role_hours={"Backend": 100}, rung=100)
    quarter.task("B", story_points=1, role_hours={"Backend": 10}, rung=90)
    quarter.task("A", story_points=1, role_hours={"Backend": 10}, rung=80)
    quarter.depends_on("B", "C").depends_on("A", "B")

    plan = quarter.plan()

    assert result_by_id(plan, "C").completion_sprint == 2
    assert result_by_id(plan, "B").start_sprint == 3
    assert result_by_id(plan, "A").start_sprint == 4


def test_out_of_quarter_recommendations_distinguish_reassignment_from_move() -> None:
    reassigned = (
        SyntheticQuarter()
        .team("Team-A", velocity=0).team("Team-B")
        .engineer("ENG-B", "Team-B", "Backend")
        .task("REASSIGN", team_id="Team-A", story_points=2, role_hours={"Backend": 40})
        .plan()
    )
    impossible = _single_team_quarter().task(
        "LATER", story_points=2, role_hours={"Backend": 500}
    ).plan()

    reassignment_options = result_by_id(reassigned, "REASSIGN").recommendations
    move_options = {
        item.recommendation_type: item
        for item in result_by_id(impossible, "LATER").recommendations
    }

    assert reassignment_options[0].recommendation_type == RecommendationType.REASSIGN_TO_EXISTING_TEAM
    assert reassignment_options[0].available
    assert reassignment_options[0].target_team_id == "Team-B"
    assert not move_options[RecommendationType.REASSIGN_TO_EXISTING_TEAM].available
    assert move_options[RecommendationType.MOVE_TO_NEXT_QUARTER].available


def test_failed_task_simulation_rolls_back_all_resources() -> None:
    quarter = _single_team_quarter()
    quarter.task("TOO-LARGE", story_points=10, role_hours={"Backend": 500}, rung=100)
    quarter.task("NEXT", story_points=2, role_hours={"Backend": 80}, rung=10)

    plan = quarter.plan()
    failed = result_by_id(plan, "TOO-LARGE")

    assert failed.status == PlanStatus.MOVED
    assert failed.allocations == ()
    assert ReasonCode.ALLOCATION_ROLLED_BACK in _reason_codes(failed)
    assert result_by_id(plan, "NEXT").start_sprint == 1
    assert plan.sprints[0].engineer_hours_used == {"ENG-A": 80}
    assert plan.sprints[0].team_sp_used == {"Team-A": 2}


def test_data_quality_warning_does_not_replace_planning_feasibility(tmp_path) -> None:
    quarter = _single_team_quarter()
    quarter.task(
        "MISMATCH", story_points=2, role_hours={"Backend": 40}, estimated_hh=80
    )
    quarter.task("CAPACITY", story_points=2, role_hours={"Backend": 500})

    plan = quarter.plan()
    audit = quarter.audit(tmp_path)

    mismatch_issue = next(
        issue for issue in audit.issues if issue.issue_code == "TASK_HOURS_TOTAL_MISMATCH"
    )
    assert mismatch_issue.severity == AuditSeverity.WARNING
    assert result_by_id(plan, "MISMATCH").status == PlanStatus.PLANNED
    capacity = result_by_id(plan, "CAPACITY")
    assert capacity.status == PlanStatus.MOVED
    assert ReasonCode.INSUFFICIENT_ROLE_CAPACITY in _reason_codes(capacity)
    assert all(issue.issue_code != "INSUFFICIENT_ROLE_CAPACITY" for issue in audit.issues)


def test_invalid_task_is_unplannable_but_does_not_block_valid_dataset(tmp_path) -> None:
    quarter = _single_team_quarter()
    for number in range(1, 10):
        quarter.task(
            f"VALID-{number}", story_points=1, role_hours={"Backend": 1}, rung=10
        )
    quarter.task("INVALID", story_points=None, role_hours={"Backend": 1}, rung=100)

    plan = quarter.plan()
    audit = quarter.audit(tmp_path)

    assert result_by_id(plan, "INVALID").status == PlanStatus.MOVED
    assert ReasonCode.UNKNOWN_TASK_DATA in _reason_codes(result_by_id(plan, "INVALID"))
    assert all(result_by_id(plan, f"VALID-{number}").status == PlanStatus.PLANNED for number in range(1, 10))
    assert any(issue.issue_code == "MISSING_STORY_POINTS" for issue in audit.issues)
    assert audit.planning_allowed


def test_same_synthetic_input_produces_identical_plan() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A").team("Team-B")
        .engineer("ENG-A", "Team-A", "Backend")
        .engineer("ENG-B", "Team-B", "Backend")
        .task("A", initiative_id="I-1", team_id="Team-A", story_points=2, role_hours={"Backend": 80}, rung=80)
        .task("B", initiative_id="I-2", team_id="Team-B", story_points=2, role_hours={"Backend": 80}, rung=70)
        .task("C", initiative_id="I-2", team_id="Team-B", story_points=2, role_hours={"Backend": 80}, rung=70)
    )

    snapshots = [quarter.plan().to_dict() for _ in range(5)]
    assert all(snapshot == snapshots[0] for snapshot in snapshots[1:])


def test_plan_keeps_fields_needed_for_quarter_metrics() -> None:
    quarter = _single_team_quarter()
    quarter.task("T-1", initiative_id="INIT-1", story_points=3, role_hours={"Backend": 80})
    quarter.task("T-2", initiative_id="INIT-1", story_points=2, role_hours={"Backend": 80})

    plan = quarter.plan()
    result = result_by_id(plan, "T-1")

    assert plan.completed_initiative_ids == ("INIT-1",)
    assert result.initiative_id == "INIT-1"
    assert result.story_points == 3
    assert result.completion_sprint is not None
    assert sum(sprint.capacity_sp_load for sprint in plan.sprints) == pytest.approx(
        sum(task.story_points or 0 for task in plan.planned_tasks)
    )
