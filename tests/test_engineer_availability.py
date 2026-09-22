from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from src.planning.availability import (
    availability_rate_index,
    effective_capacity_hours,
    validate_availability_rate,
)
from src.planning.feasibility import FeasibilityContext, FeasibilityService
from src.planning.models import SprintState
from src.planning.planner import build_quarter_plan
from src.web.catalog import CatalogRepository
from src.web.api import PlanDraftStore
from src.web import api as web_api
from src.web.replanning import _preferred_engineer_hours_by_task
from src.web.replanning import build_remaining_horizon_plan


def _context(engineers: list[dict], *, required_hours: float = 80) -> FeasibilityContext:
    return FeasibilityContext.from_records(
        tasks=[{
            "task_id": "T", "team_id": "A", "story_points": 1,
            "status": "ToDo", "rung": 80, "is_placeholder": False,
        }],
        task_role_effort=[{"task_id": "T", "role": "Backend", "required_hours": required_hours}],
        dependencies=[],
        engineers=engineers,
        engineer_skills=[],
        team_history=[{"team_id": "A", "velocity_achieved": 10, "planned_sp": 10}],
    )


def test_default_and_supported_availability_rates() -> None:
    assert availability_rate_index([]) == {}
    assert effective_capacity_hours(80, 1) == 80
    assert effective_capacity_hours(80, 0.5) == 40
    assert effective_capacity_hours(80, 0) == 0
    assert effective_capacity_hours(40, 1) == 40
    for value in (0, 0.5, 1):
        assert validate_availability_rate(value) == value
    assert availability_rate_index([
        {"engineer_id": "E", "sprint": 3, "availability_rate": 0.5, "source_type": "MANUAL"},
        {"engineer_id": "E", "sprint": 3, "availability_rate": 0, "source_type": "TRAINING"},
    ]) == {3: {"E": 0}}


def test_split_membership_buckets_are_reduced_independently() -> None:
    service = FeasibilityService(_context([
        {"engineer_id": "E", "team_id": "A", "role": "Backend", "capacity_rate": 0.5},
        {"engineer_id": "E", "team_id": "B", "role": "Backend", "capacity_rate": 0.5},
    ], required_hours=40))
    result = service.check(
        "T", SprintState(sprint_number=2), availability_rates={"E": 0.5}
    )
    buckets = sorted(row.nominal_hours for row in result.engineers)
    assert buckets == [20, 20]
    assert result.role_capacity[0].available_hours == 40


def test_planner_uses_effective_capacity_in_every_sprint() -> None:
    context = _context([
        {"engineer_id": "E", "team_id": "A", "role": "Backend", "capacity_rate": 1},
    ], required_hours=80)
    plan = build_quarter_plan(
        context,
        engineer_availability_rates_by_sprint={1: {"E": 0.5}, 2: {"E": 0.5}},
    )
    task = next(row for row in plan.task_results if row.task_id == "T")
    by_sprint = {
        allocation.sprint_number: sum(
            row.hours for row in allocation.engineer_allocations
        )
        for allocation in task.allocations
    }
    assert by_sprint == {1: 40, 2: 40}


def test_availability_constraints_survive_repository_restart() -> None:
    path = Path(f"availability-test-{uuid4().hex}.sqlite3")
    try:
        repository = CatalogRepository(path)
        repository.save_availability_constraints([{
            "engineer_id": "E-1",
            "sprint": 4,
            "availability_rate": 0.5,
            "source_type": "MANUAL",
            "source_id": "manual",
            "reason": "Отпуск",
        }])
        restarted = CatalogRepository(path)
        rows = restarted.availability_constraints()
        assert len(rows) == 1
        assert rows[0]["engineer_id"] == "E-1"
        assert rows[0]["availability_rate"] == 0.5
        restarted.save_availability_constraints([{
            "engineer_id": "E-1", "sprint": 4, "availability_rate": 1,
            "source_type": "MANUAL", "source_id": "manual",
        }])
        assert restarted.availability_constraints() == []
    finally:
        path.unlink(missing_ok=True)


def test_zero_remaining_engineers_are_not_kept_as_carry_over_preferences() -> None:
    tasks = {
        "T": {"allocations": [
            {"sprint": 2, "engineer_id": "A", "allocated_hours": 20},
            {"sprint": 2, "engineer_id": "B", "allocated_hours": 30},
            {"sprint": 3, "engineer_id": "A", "allocated_hours": 10},
        ]},
        "OTHER": {"allocations": [
            {"sprint": 3, "engineer_id": "C", "allocated_hours": 8},
        ]},
    }
    preferred = _preferred_engineer_hours_by_task(tasks, 2, {"T"})
    assert "T" not in preferred
    assert preferred["OTHER"] == {"C": 8}


def test_future_availability_rejects_current_sprint(monkeypatch) -> None:
    path = Path(f"availability-api-test-{uuid4().hex}.sqlite3")
    try:
        repository = CatalogRepository(path, seed_tables={
            "engineers": [{
                "engineer_id": "E-1", "team_id": "A", "role": "Backend",
                "capacity_rate": 1,
            }],
        })
        monkeypatch.setattr(web_api, "get_catalog_repository", lambda: repository)
        monkeypatch.setattr(web_api, "plan_drafts", PlanDraftStore())
        try:
            web_api._validate_future_availability([{
                "engineer_id": "E-1", "sprint": 1, "availability_rate": 0.5,
            }])
            assert False, "current sprint must be immutable"
        except ValueError:
            pass
        web_api._validate_future_availability([{
            "engineer_id": "E-1", "sprint": 2, "availability_rate": 0.5,
        }])
    finally:
        path.unlink(missing_ok=True)


def test_replacement_candidates_rank_same_team_before_freer_external(monkeypatch) -> None:
    path = Path(f"replacement-test-{uuid4().hex}.sqlite3")
    try:
        repository = CatalogRepository(path, seed_tables={
            "engineers": [
                {"engineer_id": "OLD", "team_id": "A", "role": "Backend", "capacity_rate": 1},
                {"engineer_id": "LOCAL", "team_id": "A", "role": "Backend", "capacity_rate": 0.5},
                {"engineer_id": "EXTERNAL", "team_id": "B", "role": "Backend", "capacity_rate": 1},
                {"engineer_id": "WRONG", "team_id": "A", "role": "QA", "capacity_rate": 1},
            ],
            "tasks": [{"task_id": "T", "team_id": "A", "story_points": 1}],
            "task_role_effort": [{"task_id": "T", "role": "Backend", "required_hours": 20}],
        })
        store = PlanDraftStore()
        store.replace_approved({
            "tasks": [{
                "task_id": "T", "assigned_team_id": "A",
                "allocations": [{
                    "sprint": 3, "engineer_id": "OLD", "role": "Backend",
                    "source_team_id": "A", "allocated_hours": 20,
                }],
            }],
            "_planning_state": {"completed_through_sprint": 1, "tables": repository.active_planning_tables()},
        })
        monkeypatch.setattr(web_api, "get_catalog_repository", lambda: repository)
        monkeypatch.setattr(web_api, "plan_drafts", store)
        payload = web_api.replacement_candidates("T", "OLD", 3)
        candidates = payload["roles"][0]["candidates"]
        assert [row["engineer_id"] for row in candidates] == ["LOCAL", "EXTERNAL"]
        assert candidates[0]["cross_team"] is False
        assert candidates[1]["cross_team"] is True
    finally:
        path.unlink(missing_ok=True)


def test_replacement_candidates_exclude_unavailable_and_insufficient_capacity(monkeypatch) -> None:
    path = Path(f"replacement-capacity-test-{uuid4().hex}.sqlite3")
    try:
        repository = CatalogRepository(path, seed_tables={
            "engineers": [
                {"engineer_id": "OLD", "team_id": "A", "role": "Backend", "capacity_rate": 1},
                {"engineer_id": "LOCAL", "team_id": "A", "role": "Backend", "capacity_rate": 0.5},
                {"engineer_id": "EXTERNAL", "team_id": "B", "role": "Backend", "capacity_rate": 1},
            ],
            "tasks": [
                {"task_id": "T", "team_id": "A", "story_points": 1},
                {"task_id": "BUSY", "team_id": "A", "story_points": 1},
            ],
            "task_role_effort": [
                {"task_id": "T", "role": "Backend", "required_hours": 12},
                {"task_id": "BUSY", "role": "Backend", "required_hours": 32},
            ],
        })
        repository.save_availability_constraints([{
            "engineer_id": "EXTERNAL", "sprint": 3, "availability_rate": 0,
            "source_type": "MANUAL", "source_id": "manual",
        }])
        store = PlanDraftStore()
        store.replace_approved({
            "tasks": [
                {
                    "task_id": "T", "assigned_team_id": "A",
                    "allocations": [{
                        "sprint": 3, "engineer_id": "OLD", "role": "Backend",
                        "source_team_id": "A", "allocated_hours": 12,
                    }],
                },
                {
                    "task_id": "BUSY", "assigned_team_id": "A",
                    "allocations": [{
                        "sprint": 3, "engineer_id": "LOCAL", "role": "Backend",
                        "source_team_id": "A", "allocated_hours": 32,
                    }],
                },
            ],
            "_planning_state": {
                "completed_through_sprint": 1,
                "tables": repository.active_planning_tables(),
            },
        })
        monkeypatch.setattr(web_api, "get_catalog_repository", lambda: repository)
        monkeypatch.setattr(web_api, "plan_drafts", store)

        payload = web_api.replacement_candidates("T", "OLD", 3)
        assert payload["roles"][0]["candidates"] == []
    finally:
        path.unlink(missing_ok=True)


def test_acceptance_carry_over_releases_zero_rows_and_replaces_unavailable_engineer() -> None:
    from tests.test_sprint_completion_clarifications import _two_engineer_approved

    approved = _two_engineer_approved()
    replanned = build_remaining_horizon_plan(
        approved,
        1,
        [{
            "task_id": "T",
            "task_completed": False,
            "incomplete_reason": "Недоступность сотрудника",
            "remaining_role_hours": {"Backend": 12},
            "remaining_engineer_work": [
                {"engineer_id": "E-1", "role": "Backend", "remaining_hours": 12},
                {"engineer_id": "E-2", "role": "Backend", "remaining_hours": 0},
            ],
            "remaining_engineer_allocations": {"E-1": 12},
            "unavailable_engineer_ids": ["E-1"],
        }],
        availability_constraints=[{
            "engineer_id": "E-1", "sprint": 2, "availability_rate": 0,
            "source_type": "MANUAL", "source_id": "manual",
        }],
    )
    task = next(row for row in replanned["tasks"] if row["task_id"] == "T")
    future = [row for row in task["allocations"] if int(row["sprint"]) > 1]
    assert sum(float(row["allocated_hours"]) for row in future) == 12
    assert {row["engineer_id"] for row in future} == {"E-2"}


def test_user_selected_replacement_receives_exact_remaining_work() -> None:
    from tests.test_sprint_completion_clarifications import _two_engineer_approved

    replanned = build_remaining_horizon_plan(
        _two_engineer_approved(),
        1,
        [{
            "task_id": "T",
            "task_completed": False,
            "incomplete_reason": "Недоступность сотрудника",
            "remaining_role_hours": {"Backend": 12},
            "remaining_engineer_work": [{
                "engineer_id": "E-1", "role": "Backend", "remaining_hours": 12,
            }],
            "remaining_engineer_allocations": {"E-1": 12},
            "replacement_engineer_allocations": {"E-2": 12},
            "unavailable_engineer_ids": ["E-1"],
        }],
        availability_constraints=[{
            "engineer_id": "E-1", "sprint": 2, "availability_rate": 0,
            "source_type": "MANUAL", "source_id": "manual",
        }],
    )
    task = next(row for row in replanned["tasks"] if row["task_id"] == "T")
    sprint_two = [row for row in task["allocations"] if int(row["sprint"]) == 2]
    assert sum(float(row["allocated_hours"]) for row in sprint_two) == 12
    assert {row["engineer_id"] for row in sprint_two} == {"E-2"}


def test_user_move_choice_moves_remaining_task_to_next_feasible_sprint() -> None:
    from tests.test_sprint_completion_clarifications import _two_engineer_approved

    replanned = build_remaining_horizon_plan(
        _two_engineer_approved(),
        1,
        [{
            "task_id": "T",
            "task_completed": False,
            "incomplete_reason": "Недоступность сотрудника",
            "remaining_role_hours": {"Backend": 12},
            "remaining_engineer_work": [{
                "engineer_id": "E-1", "role": "Backend", "remaining_hours": 12,
            }],
            "remaining_engineer_allocations": {"E-1": 12},
            "move_remaining_task": True,
            "unavailable_engineer_ids": ["E-1"],
        }],
    )
    task = next(row for row in replanned["tasks"] if row["task_id"] == "T")
    future_sprints = sorted({
        int(row["sprint"]) for row in task["allocations"] if int(row["sprint"]) > 1
    })
    assert future_sprints == [3]
