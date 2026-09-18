"""Focused regression checks added by the strict initial-planner audit."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

import pytest

from src.planning import FeasibilityContext, PlanStatus, build_quarter_plan
from src.planning.reasons import ReasonCode
from src.web.service import PlanningAPIService
from tests.acceptance.builders import SyntheticQuarter, result_by_id


def test_inprogress_role_effort_is_already_remaining_work() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("ENG-A", "Team-A", "Backend")
        .task(
            "ACTIVE",
            status="InProgress",
            story_points=4,
            role_hours={"Backend": 40},
            estimated_hh=999,
        )
    )
    quarter.tasks[0]["spent_time"] = 1_000

    result = result_by_id(quarter.plan(), "ACTIVE")

    assert result.status == PlanStatus.PLANNED
    assert sum(item.allocated_hours for item in result.allocations) == pytest.approx(40)


def test_task_title_does_not_infer_a_required_skill() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .engineer("ENG-A", "Team-A", "Backend")
        .task("KAFKA", story_points=2, role_hours={"Backend": 40})
    )
    quarter.tasks[0]["title"] = "Kafka migration"

    result = result_by_id(quarter.plan(), "KAFKA")

    assert result.status == PlanStatus.PLANNED
    assert result.allocations[0].engineer_allocations[0].engineer_id == "ENG-A"


def test_semantic_plan_is_independent_of_input_row_order() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=10)
        .team("Team-B", velocity=10)
        .engineer("ENG-A", "Team-A", "Backend")
        .engineer("ENG-B", "Team-B", "QA")
        .task(
            "A-1",
            initiative_id="INIT-A",
            story_points=4,
            role_hours={"Backend": 120},
            rung=90,
        )
        .task(
            "A-2",
            initiative_id="INIT-A",
            story_points=2,
            role_hours={"QA": 40},
            rung=80,
        )
        .task(
            "B-1",
            initiative_id="INIT-B",
            team_id="Team-B",
            story_points=3,
            role_hours={"QA": 80},
            rung=70,
        )
        .depends_on("A-2", "A-1")
    )
    baseline = json.dumps(quarter.plan().to_dict(), sort_keys=True, ensure_ascii=False)

    for seed in range(5):
        rng = random.Random(seed)
        shuffled = {}
        for name in (
            "tasks",
            "effort",
            "dependencies",
            "engineers",
            "engineer_skills",
            "team_history",
        ):
            rows = list(getattr(quarter, name))
            rng.shuffle(rows)
            shuffled[name] = rows
        context = FeasibilityContext.from_records(
            tasks=shuffled["tasks"],
            task_role_effort=shuffled["effort"],
            dependencies=shuffled["dependencies"],
            engineers=shuffled["engineers"],
            engineer_skills=shuffled["engineer_skills"],
            team_history=shuffled["team_history"],
        )

        actual = json.dumps(
            build_quarter_plan(context).to_dict(), sort_keys=True, ensure_ascii=False
        )
        assert actual == baseline


def test_committed_plan_state_obeys_reference_hour_and_sp_invariants() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A", velocity=20)
        .team("Team-B", velocity=20)
        .engineer("ENG-A", "Team-A", "Backend")
        .engineer("ENG-B", "Team-B", "QA")
        .task(
            "DONE",
            initiative_id="INIT-DONE",
            team_id="Team-A",
            story_points=5,
            status="Done",
        )
        .task(
            "BUILD",
            initiative_id="INIT-WORK",
            team_id="Team-A",
            story_points=9,
            role_hours={"Backend": 216},
            rung=90,
        )
        .task(
            "VERIFY",
            initiative_id="INIT-WORK",
            team_id="Team-A",
            story_points=3,
            role_hours={"QA": 80},
            rung=80,
        )
        .task(
            "TOO-LARGE",
            initiative_id="INIT-LATER",
            team_id="Team-A",
            story_points=20,
            role_hours={"Backend": 1_000},
            rung=10,
        )
        .depends_on("VERIFY", "BUILD")
    )
    plan = quarter.plan()
    tasks = {row["task_id"]: row for row in quarter.tasks}
    teams = {row["team_id"] for row in quarter.team_history}
    engineers = {row["engineer_id"] for row in quarter.engineers}
    role_requirements: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for row in quarter.effort:
        role_requirements[row["task_id"]][row["role"]] += row["required_hours"]

    assert [sprint.sprint_number for sprint in plan.sprints] == [1, 2, 3, 4, 5, 6]
    committed_task_ids: set[str] = set()
    for sprint in plan.sprints:
        for team_id, used in sprint.team_sp_used.items():
            assert team_id in teams
            assert used <= sprint.team_capacity_sp[team_id] + 1e-9
        for allocation in sprint.task_allocations:
            committed_task_ids.add(allocation.task_id)
            assert allocation.task_id in tasks
            assert 1 <= allocation.sprint_number <= 6
            result = result_by_id(plan, allocation.task_id)
            assert result.status == PlanStatus.PLANNED
            assert result.assigned_team_id in teams
            for engineer_allocation in allocation.engineer_allocations:
                assert engineer_allocation.engineer_id in engineers
                assert engineer_allocation.source_team_id in teams
                assert engineer_allocation.task_team in teams

        task_sp_by_team: dict[str, float] = defaultdict(float)
        for allocation in sprint.task_allocations:
            assigned_team = result_by_id(plan, allocation.task_id).assigned_team_id
            assert assigned_team is not None
            task_sp_by_team[assigned_team] += allocation.capacity_sp_load
        assert dict(task_sp_by_team) == pytest.approx(sprint.team_sp_used)

        engineer_hours: dict[str, float] = defaultdict(float)
        bucket_hours: dict[tuple[str, str], float] = defaultdict(float)
        for allocation in sprint.task_allocations:
            for item in allocation.engineer_allocations:
                engineer_hours[item.engineer_id] += item.hours
                assert item.source_team_id is not None
                bucket_hours[(item.engineer_id, item.source_team_id)] += item.hours
        assert dict(engineer_hours) == pytest.approx(sprint.engineer_hours_used)
        for engineer_id, per_team in sprint.engineer_team_hours_used.items():
            for team_id, hours in per_team.items():
                assert bucket_hours[(engineer_id, team_id)] == pytest.approx(hours)
                assert hours <= 80 + 1e-9

    for result in plan.task_results:
        if result.status == PlanStatus.PLANNED:
            allocated_by_role: dict[str, float] = defaultdict(float)
            for allocation in result.allocations:
                for role, hours in allocation.allocated_role_hours.items():
                    allocated_by_role[role] += hours
            assert dict(allocated_by_role) == pytest.approx(
                role_requirements[result.task_id]
            )
            assert sum(item.capacity_sp_load for item in result.allocations) == pytest.approx(
                tasks[result.task_id]["story_points"]
            )
        else:
            assert not result.allocations

    assert committed_task_ids == {
        result.task_id for result in plan.task_results if result.status == PlanStatus.PLANNED
    }
    json.dumps(plan.to_dict(), allow_nan=False)


def test_current_processed_dataset_smoke_invariants() -> None:
    processed_dir = Path(__file__).resolve().parents[1] / "data" / "processed"
    required_files = {
        "tasks.csv",
        "task_role_effort.csv",
        "task_dependencies.csv",
        "engineers.csv",
        "engineer_skills.csv",
        "team_history.csv",
    }
    if not all((processed_dir / name).is_file() for name in required_files):
        pytest.skip("Current processed dataset is not available.")

    service = PlanningAPIService(processed_dir)
    plan = service.plan
    context = service.context
    tasks = {row["task_id"]: row for row in context.tasks}
    engineers = {row["engineer_id"] for row in context.engineers}
    teams = {
        row["team_id"]
        for row in (*context.team_history, *context.engineers)
        if row.get("team_id")
    }
    bucket_rates: dict[tuple[str, str], float] = {}
    for row in context.engineers:
        key = (row["engineer_id"], row["team_id"])
        rate = float(row["capacity_rate"])
        bucket_rates[key] = min(bucket_rates.get(key, rate), rate)
    required_by_task: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for row in context.task_role_effort:
        hours = float(row["required_hours"])
        if hours > 0:
            required_by_task[row["task_id"]][row["role"]] += hours

    completions = {
        result.task_id: result.completion_sprint for result in plan.task_results
    }
    committed_ids: set[str] = set()
    for sprint in plan.sprints:
        assert 1 <= sprint.sprint_number <= 6
        sp_by_team: dict[str, float] = defaultdict(float)
        hours_by_engineer: dict[str, float] = defaultdict(float)
        hours_by_bucket: dict[tuple[str, str], float] = defaultdict(float)
        for allocation in sprint.task_allocations:
            committed_ids.add(allocation.task_id)
            assert allocation.task_id in tasks
            result = result_by_id(plan, allocation.task_id)
            assert result.status == PlanStatus.PLANNED
            assert result.assigned_team_id in teams
            sp_by_team[result.assigned_team_id] += allocation.capacity_sp_load
            for item in allocation.engineer_allocations:
                assert item.engineer_id in engineers
                assert item.source_team_id in teams
                assert item.task_team in teams
                hours_by_engineer[item.engineer_id] += item.hours
                hours_by_bucket[(item.engineer_id, item.source_team_id)] += item.hours
        assert dict(sp_by_team) == pytest.approx(sprint.team_sp_used)
        assert dict(hours_by_engineer) == pytest.approx(sprint.engineer_hours_used)
        for team_id, used_sp in sprint.team_sp_used.items():
            assert used_sp <= sprint.team_capacity_sp[team_id] + 1e-9
        for key, used_hours in hours_by_bucket.items():
            assert key in bucket_rates
            assert used_hours <= 80 * bucket_rates[key] + 1e-9
            engineer_id, team_id = key
            assert sprint.engineer_team_hours_used[engineer_id][team_id] == pytest.approx(
                used_hours
            )

    explanations = {item.task_id: item for item in plan.task_explanations}
    for result in plan.task_results:
        if result.status == PlanStatus.PLANNED:
            by_role: dict[str, float] = defaultdict(float)
            for allocation in result.allocations:
                for role, hours in allocation.allocated_role_hours.items():
                    by_role[role] += hours
            assert dict(by_role) == pytest.approx(required_by_task[result.task_id])
            expected_sp = float(tasks[result.task_id].get("story_points") or 0)
            assert result.capacity_sp_load == pytest.approx(expected_sp)
            for blocker in result.dependencies:
                blocker_completion = completions[blocker]
                assert blocker_completion is not None
                assert result.start_sprint is not None
                assert result.start_sprint >= blocker_completion + 1
        else:
            assert not result.allocations

        explanation = explanations[result.task_id]
        reason_codes = {reason.code for reason in explanation.reasons}
        has_cross_team_allocation = any(
            item.cross_team
            for allocation in result.allocations
            for item in allocation.engineer_allocations
        )
        assert (ReasonCode.MULTI_SPRINT in reason_codes) == result.is_multi_sprint
        assert (ReasonCode.CROSS_TEAM_HELP in reason_codes) == has_cross_team_allocation
        assert (ReasonCode.TEAM_REASSIGNED in reason_codes) == result.team_reassigned
        assert (ReasonCode.QUARTER_OVERFLOW in reason_codes) == result.outside_quarter
        if ReasonCode.DEPENDENCY_DELAY in reason_codes:
            assert result.start_sprint is not None and result.start_sprint > 1

    assert committed_ids == {
        result.task_id for result in plan.task_results if result.status == PlanStatus.PLANNED
    }
    assert len(plan.sprints) == 6
    json.dumps(plan.to_dict(), allow_nan=False)
