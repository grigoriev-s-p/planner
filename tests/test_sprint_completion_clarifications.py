from __future__ import annotations

from tests.test_replanning_workflow import scenario
from src.web.api import PlanDraftStore
from src.web.constructor import ConstructorScenario, build_constructor_plan
from src.web.replanning import build_remaining_horizon_plan
from src.web import api as web_api


def _approved() -> dict:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    return store.approve(draft["draft"]["id"])


def _two_engineer_approved() -> dict:
    scenario = ConstructorScenario(**{
        "teams": [{"team_id": "A", "name": "A", "expected_velocity_sp": 10, "focus_factor": 0.8}],
        "engineers": [
            {"engineer_id": "E-1", "name": "One", "assignments": [{"team_id": "A", "role": "Backend", "capacity_rate": 1}], "skills": []},
            {"engineer_id": "E-2", "name": "Two", "assignments": [{"team_id": "A", "role": "Backend", "capacity_rate": 1}], "skills": []},
        ],
        "tasks": [{"task_id": "T", "title": "Task", "initiative_id": "I", "team_id": "A", "status": "ToDo", "rung": 90, "story_points": 2, "role_requirements": [{"role": "Backend", "required_hours": 160, "skills": []}], "depends_on": []}],
    })
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario))
    return store.approve(draft["draft"]["id"])


def test_default_completion_only_finishes_tasks_planned_to_close() -> None:
    approved = _approved()
    replanned = build_remaining_horizon_plan(approved, 1, [])
    ids = {task["task_id"] for task in replanned["tasks"]}
    assert "A" not in ids  # completion_sprint == 1
    assert "C" in ids  # multi-sprint work continues beyond Sprint 1


def test_employee_carry_over_is_allocated_before_new_work() -> None:
    approved = _approved()
    replanned = build_remaining_horizon_plan(approved, 1, [
        {"task_id": "A", "task_completed": True},
        {
            "task_id": "C",
            "task_completed": False,
            "remaining_role_hours": {"Backend": 40},
            "remaining_engineer_allocations": {"E-1": 20},
        },
    ])
    task = next(item for item in replanned["tasks"] if item["task_id"] == "C")
    sprint_two = [row for row in task["allocations"] if row["sprint"] == 2]
    assert sprint_two
    assert sum(row["allocated_hours"] for row in sprint_two if row["engineer_id"] == "E-1") >= 20


def test_unavailable_next_sprint_is_limited_to_next_sprint() -> None:
    approved = _approved()
    replanned = build_remaining_horizon_plan(approved, 1, [
        {"task_id": "A", "task_completed": True},
        {
            "task_id": "C",
            "task_completed": False,
            "remaining_role_hours": {"Backend": 40},
            "remaining_engineer_allocations": {"E-1": 20},
            "unavailable_next_sprint": True,
        },
    ])
    task = next(item for item in replanned["tasks"] if item["task_id"] == "C")
    assert not any(row["sprint"] == 2 and row["engineer_id"] == "E-1" for row in task["allocations"])
    assert any(row["sprint"] >= 3 and row["engineer_id"] == "E-1" for row in task["allocations"])


def test_unavailable_employee_can_be_replaced_by_same_role_engineer() -> None:
    approved = _two_engineer_approved()
    replanned = build_remaining_horizon_plan(approved, 1, [{
        "task_id": "T",
        "task_completed": False,
        "remaining_role_hours": {"Backend": 80},
        "remaining_engineer_allocations": {"E-1": 40},
        "unavailable_next_sprint": True,
    }])
    task = replanned["tasks"][0]
    assert any(row["sprint"] == 2 and row["engineer_id"] == "E-2" for row in task["allocations"])


def test_sprint_six_closes_quarter_without_sprint_seven() -> None:
    approved = _approved()
    current = approved
    for sprint in range(1, 7):
        current = build_remaining_horizon_plan(current, sprint, [])
    assert current["replanning"]["quarter_status"] == "COMPLETED"
    assert current["replanning"]["remaining_sprints"] == []
    assert current["plan"]["quarter"]["current_sprint"] == 6
    assert len(current["plan"]["sprints"]) == 6


def test_backend_current_sprint_moves_after_close(monkeypatch) -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    approved = store.approve(draft["draft"]["id"])
    store.replace_approved(build_remaining_horizon_plan(approved, 1, []))
    monkeypatch.setattr(web_api, "plan_drafts", store)
    payload = web_api.current_sprint()
    assert payload["sprint_number"] == 2
    assert payload["can_close"] is True


def test_task_incomplete_reason_is_stored_for_audit_only() -> None:
    approved = _approved()
    fact = {
        "task_id": "C",
        "task_completed": False,
        "remaining_role_hours": {"Backend": 40},
        "incomplete_reason": "Не хватило времени",
    }
    with_reason = build_remaining_horizon_plan(approved, 1, [fact])
    without_reason = build_remaining_horizon_plan(approved, 1, [{
        "task_id": "C",
        "task_completed": False,
        "remaining_role_hours": {"Backend": 40},
    }])
    assert with_reason["tasks"] == without_reason["tasks"]
    assert with_reason["_planning_state"]["actual_results"][-1]["incomplete_reason"] == "Не хватило времени"


def test_early_multisprint_completion_frees_future_work_and_can_keep_schedule() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    approved = store.approve(draft["draft"]["id"])
    long_task = next(row for row in approved["tasks"] if row["task_id"] == "C")
    assert long_task["completion_sprint"] > 1
    starting_sp = float(long_task["remaining_story_points"])

    workflow = store.close_sprint(1, [{
        "task_id": "C",
        "task_completed": True,
        "completed_early": True,
        "completed_story_points": starting_sp,
        "remaining_story_points": 0,
        "remaining_role_hours": {},
    }])

    assert workflow["status"] == "READY_FOR_REVIEW"
    assert set(workflow["selected_change_task_ids"]) == set(workflow["selectable_change_task_ids"])
    kpi = workflow["sprint_result"]["kpi"]
    assert kpi["baseline_confirmed"] is True
    assert kpi["completion_percent"] > 100
    assert all(row["task_id"] != "C" for row in workflow["draft_plan"]["tasks"])
    assert not any(
        row["task_id"] == "C"
        for sprint in workflow["draft_plan"]["plan"]["sprints"]
        if sprint["sprint_number"] > 1
        for row in sprint["tasks"]
    )
    assert not any(
        row["task_id"] == "C" and int(row["sprint"]) > 1
        for row in workflow["draft_plan"]["plan"]["allocations"]
    )

    review = store.select_replan_task_changes(workflow["workflow_id"], [])
    assert review["selected_change_task_ids"] == []
    before_b = next(row for row in approved["tasks"] if row["task_id"] == "B")
    review_b = next(row for row in review["draft_plan"]["tasks"] if row["task_id"] == "B")
    assert review_b["allocations"] == before_b["allocations"]
    saved = store.approve_replanning(workflow["workflow_id"])
    assert saved["_planning_state"]["sprint_baselines"]["1"]["planned_sp"] == kpi["baseline_sp"]
    assert saved["_planning_state"]["closed_sprint_history"][0]["sprint_baseline"]["planned_sp"] == kpi["baseline_sp"]
    assert not any(
        row["task_id"] == "C"
        for sprint in saved["plan"]["sprints"]
        if sprint["sprint_number"] > 1
        for row in sprint["tasks"]
    )
    assert not any(
        row["task_id"] == "C" and int(row["sprint"]) > 1
        for row in saved["plan"]["allocations"]
    )


def test_closed_sprint_has_confirmed_baseline_while_future_kpi_is_unavailable() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    store.approve(draft["draft"]["id"])

    workflow = store.close_sprint(1, [], all_completed=True)
    sprint_one = workflow["sprint_result"]["kpi"]
    sprint_two = workflow["draft_plan"]["kpis"]["sprint_kpis"][1]

    assert sprint_one["baseline_confirmed"] is True
    assert sprint_one["completion_percent"] is not None
    assert sprint_two["completion_percent"] is None
    assert sprint_two["unavailable_reason"] == "UNAVAILABLE_NO_ACTUAL"
