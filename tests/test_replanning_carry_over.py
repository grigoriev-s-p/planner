from __future__ import annotations

from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from src.web.api import PlanDraftStore
from src.web.catalog import CatalogRepository
from src.web.constructor import ConstructorScenario, build_constructor_plan, scenario_tables
from src.web.lifecycle import project_lifecycle
from src.web.replanning import build_remaining_horizon_plan
from tests.test_replanning_workflow import scenario as dependency_scenario


INCOMPLETE_REASONS = (
    "Не хватило времени",
    "Недоступность необходимых ресурсов",
    "Зависимость не была завершена",
    "Объём задачи оказался больше запланированного",
    "Изменились приоритеты",
    "Задача была приостановлена",
    "Другая организационная причина",
)


def _competing_scenario() -> ConstructorScenario:
    return ConstructorScenario(**{
        "teams": [{
            "team_id": "Team-A", "name": "A",
            "expected_velocity_sp": 5, "focus_factor": 0.8,
        }],
        "engineers": [{
            "engineer_id": "E-QA", "name": "QA",
            "assignments": [{
                "team_id": "Team-A", "role": "QA", "capacity_rate": 1,
            }],
            "skills": [],
        }],
        "tasks": [
            {
                "task_id": "A-CARRY", "title": "Carry-over", "initiative_id": "I",
                "team_id": "Team-A", "status": "ToDo", "rung": 80,
                "story_points": 4,
                "role_requirements": [{
                    "role": "QA", "required_hours": 80, "skills": [],
                }],
                "depends_on": [],
            },
            {
                "task_id": "Z-NEW", "title": "New task", "initiative_id": "I",
                "team_id": "Team-A", "status": "ToDo", "rung": 80,
                "story_points": 4,
                "role_requirements": [{
                    "role": "QA", "required_hours": 80, "skills": [],
                }],
                "depends_on": [],
            },
        ],
    })


def _approved(scenario: ConstructorScenario | None = None) -> dict:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario or _competing_scenario()))
    return store.approve(draft["draft"]["id"])


def _unfinished(reason: str = "Не хватило времени", hours: float = 40) -> dict:
    return {
        "task_id": "A-CARRY",
        "task_completed": False,
        "incomplete_reason": reason,
        "remaining_role_hours": {"QA": hours},
        "remaining_engineer_allocations": {},
        "unavailable_next_sprint": False,
        "unavailable_engineer_ids": [],
    }


def test_unfinished_task_is_carried_to_next_sprint_and_visible_end_to_end() -> None:
    approved = _approved()
    replanned = build_remaining_horizon_plan(approved, 1, [_unfinished()])
    projected = project_lifecycle(replanned, [approved, replanned])
    task = next(row for row in replanned["tasks"] if row["task_id"] == "A-CARRY")
    change = next(
        row for row in replanned["replanning"]["diff"]["changed_tasks"]
        if row["task_id"] == "A-CARRY"
    )
    history_task = next(
        row for row in projected["plan"]["sprints"][0]["tasks"]
        if row["task_id"] == "A-CARRY"
    )
    current_ids = {
        row["task_id"] for row in projected["plan"]["sprints"][1]["tasks"]
    }

    assert task["planned_sprints"] == [1, 2]
    assert task["role_requirements"] == {"QA": 40.0}
    assert task["remaining_story_points"] == 2.0
    assert task["assigned_team_id"] == "Team-A"
    assert next(row for row in projected["tasks"] if row["task_id"] == "A-CARRY")["current_status"] == "IN_PROGRESS"
    assert history_task["outcome"]["status"] == "UNFINISHED"
    assert history_task["outcome"]["incomplete_reason"] == "Не хватило времени"
    assert "A-CARRY" in current_ids
    assert change["change"] == "CARRY_OVER"
    assert change["carry_over"] == {
        "closed_sprint": 1,
        "earliest_remaining_sprint": 2,
        "incomplete_reason": "Не хватило времени",
        "remaining_role_hours": {"QA": 40.0},
        "remaining_total_hours": 40.0,
        "remaining_story_points": 2.0,
        "outcome": "SCHEDULED",
    }


def test_same_rung_new_task_does_not_displace_carry_over() -> None:
    replanned = build_remaining_horizon_plan(
        _approved(), 1, [_unfinished(hours=80)]
    )
    tasks = {row["task_id"]: row for row in replanned["tasks"]}

    assert 2 in tasks["A-CARRY"]["planned_sprints"]
    assert 2 not in tasks["Z-NEW"]["planned_sprints"]


def test_higher_rung_task_can_displace_carry_over_to_later_sprint() -> None:
    approved = _approved()
    for row in approved["_planning_state"]["tables"]["tasks"]:
        if row["task_id"] == "Z-NEW":
            row["rung"] = 90
    replanned = build_remaining_horizon_plan(
        approved, 1, [_unfinished(hours=80)]
    )
    tasks = {row["task_id"]: row for row in replanned["tasks"]}

    assert 2 in tasks["Z-NEW"]["planned_sprints"]
    assert min(sprint for sprint in tasks["A-CARRY"]["planned_sprints"] if sprint > 1) == 3


def test_carry_over_that_cannot_fit_is_explicitly_excluded() -> None:
    replanned = build_remaining_horizon_plan(
        _approved(), 1, [_unfinished(hours=500)]
    )
    task = next(row for row in replanned["tasks"] if row["task_id"] == "A-CARRY")
    change = next(
        row for row in replanned["replanning"]["diff"]["changed_tasks"]
        if row["task_id"] == "A-CARRY"
    )

    assert task["status"] == "MOVED"
    assert task["reasons"]
    assert change["change"] == "CARRY_OVER"
    assert change["carry_over"]["outcome"] == "EXCLUDED"
    assert change["after"]["reason_codes"]


@pytest.mark.parametrize("reason", INCOMPLETE_REASONS)
def test_incomplete_reason_is_audit_metadata_only(reason: str) -> None:
    replanned = build_remaining_horizon_plan(
        _approved(), 1, [_unfinished(reason=reason)]
    )
    task = next(row for row in replanned["tasks"] if row["task_id"] == "A-CARRY")
    actual = replanned["_planning_state"]["actual_results"][-1]

    assert actual["incomplete_reason"] == reason
    assert 2 in task["planned_sprints"]


def test_accepted_cross_team_help_remains_available_for_carry_over() -> None:
    scenario = ConstructorScenario(**{
        "teams": [
            {"team_id": "Owner", "name": "Owner", "expected_velocity_sp": 5, "focus_factor": 0.8},
            {"team_id": "Helper", "name": "Helper", "expected_velocity_sp": 5, "focus_factor": 0.8},
        ],
        "engineers": [
            {"engineer_id": "E-OWNER", "name": "Owner", "assignments": [{"team_id": "Owner", "role": "QA", "capacity_rate": 0.75}], "skills": []},
            {"engineer_id": "E-QA", "name": "QA", "assignments": [{"team_id": "Helper", "role": "QA", "capacity_rate": 1}], "skills": []},
        ],
        "tasks": [{
            "task_id": "A-CARRY", "title": "Carry-over", "initiative_id": "I",
            "team_id": "Owner", "status": "ToDo", "rung": 80, "story_points": 2,
            "role_requirements": [{"role": "QA", "required_hours": 40, "skills": []}],
            "depends_on": [],
        }],
    })
    approved = _approved(scenario)
    original = next(row for row in approved["tasks"] if row["task_id"] == "A-CARRY")
    original["allocations"].append({
        "engineer_id": "E-QA", "role": "QA", "source_team_id": "Helper",
        "assigned_task_team_id": "Owner", "sprint": 1,
        "allocated_hours": 10.0, "cross_team": True,
        "capacity_bucket_hours": 80.0, "capacity_used_share": 0.125,
    })
    assert any(row["cross_team"] for row in original["allocations"])

    replanned = build_remaining_horizon_plan(
        approved, 1, [_unfinished(hours=220)]
    )
    task = next(row for row in replanned["tasks"] if row["task_id"] == "A-CARRY")
    future = [row for row in task["allocations"] if row["sprint"] > 1]

    assert task["assigned_team_id"] == "Owner"
    assert future
    assert any(
        row["source_team_id"] == "Helper" and row["cross_team"]
        for row in future
    )
    assert task["ownership"]["valid"] is True


def test_carry_over_and_reason_survive_restart() -> None:
    scenario = _competing_scenario()
    directory = Path.cwd() / f".carry_over_test_{uuid4().hex}"
    directory.mkdir()
    try:
        repository = CatalogRepository(
            directory / "catalog.sqlite3", seed_tables=scenario_tables(scenario)
        )
        store = PlanDraftStore(repository_provider=lambda: repository)
        draft = store.create(build_constructor_plan(scenario))
        store.approve(draft["draft"]["id"])
        workflow = store.close_sprint(1, [_unfinished()])

        restarted = PlanDraftStore(repository_provider=lambda: repository)
        restored = restarted.replan_workflow(workflow["workflow_id"])
        assert restored is not None
        actual = restored["actual"]["results"][0]
        task = next(
            row for row in restored["draft_plan"]["tasks"]
            if row["task_id"] == "A-CARRY"
        )

        assert actual["incomplete_reason"] == "Не хватило времени"
        assert actual["remaining_role_hours"] == {"QA": 40.0}
        assert 2 in task["planned_sprints"]
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_approved_replan_keeps_history_and_places_carry_over_in_current_sprint() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(_competing_scenario()))
    store.approve(draft["draft"]["id"])
    workflow = store.close_sprint(1, [_unfinished()])
    approved = store.approve_replanning(workflow["workflow_id"])
    projected = project_lifecycle(approved, store.history())

    assert projected["plan"]["quarter"]["current_sprint"] == 2
    assert "A-CARRY" in {
        row["task_id"] for row in projected["plan"]["sprints"][1]["tasks"]
    }
    closed = next(
        row for row in projected["plan"]["sprints"][0]["tasks"]
        if row["task_id"] == "A-CARRY"
    )
    assert closed["outcome"]["status"] == "UNFINISHED"


def test_unfinished_blocker_does_not_open_dependent_as_completed() -> None:
    approved = _approved(dependency_scenario())
    replanned = build_remaining_horizon_plan(approved, 1, [{
        "task_id": "A",
        "task_completed": False,
        "incomplete_reason": "Не хватило времени",
        "remaining_role_hours": {"Backend": 8},
    }])
    tasks = {row["task_id"]: row for row in replanned["tasks"]}

    assert min(sprint for sprint in tasks["A"]["planned_sprints"] if sprint > 1) == 2
    assert tasks["B"]["start_sprint"] > 2


def test_sprint_six_unfinished_task_is_explicitly_moved_outside_quarter() -> None:
    current = _approved()
    for sprint in range(1, 7):
        current = build_remaining_horizon_plan(current, sprint, [_unfinished()])
    task = next(row for row in current["tasks"] if row["task_id"] == "A-CARRY")
    change = next(
        row for row in current["replanning"]["diff"]["changed_tasks"]
        if row["task_id"] == "A-CARRY"
    )

    assert current["replanning"]["quarter_status"] == "COMPLETED"
    assert task["status"] == "MOVED"
    assert task["outside_quarter"] is True
    assert task["reasons"][0]["code"] == "QUARTER_OVERFLOW"
    assert change["carry_over"]["outcome"] == "EXCLUDED"


def test_frontend_renders_carry_over_details() -> None:
    source = Path("frontend/app.js").read_text(encoding="utf-8")

    assert "CARRY_OVER: 'Перенос незавершённой задачи'" in source
    assert "Факт Sprint" in source
    assert "Остаток" in source
