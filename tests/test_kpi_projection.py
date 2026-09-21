from __future__ import annotations

from copy import deepcopy

import pytest

from src.web.api import PlanDraftStore
from src.web.constructor import build_constructor_plan
from src.web.kpi_projection import project_management_kpis
from tests.test_replanning_workflow import scenario


def _payload() -> dict:
    sprint_rows = [
        {
            "sprint": sprint,
            "baseline_sp": 10.0 if sprint == 1 else 0.0,
            "actual_sp": None,
            "completion_percent": None,
            "has_actual": False,
        }
        for sprint in range(1, 7)
    ]
    return {
        "kpis": {
            "expected_kpi": {
                "completed_initiatives": 1.0,
                "baseline_initiatives": 2.0,
                "completion_percent": 50.0,
            },
            "quarter_kpi": {},
            "sprint_kpis": deepcopy(sprint_rows),
        },
        "_planning_state": {
            "week0_baseline": {"kpis": {"sprint_kpis": sprint_rows}},
            "baseline_tables": {
                "tasks": [
                    {"task_id": "T-1", "initiative_id": "I-1", "status": "ToDo"},
                    {"task_id": "T-2", "initiative_id": "I-1", "status": "ToDo"},
                    {"task_id": "T-3", "initiative_id": "I-2", "status": "InProgress"},
                ]
            },
            "original_task_baseline": {
                "T-1": {
                    "original_story_points": 8.0,
                    "original_total_required_hours": 80.0,
                },
                "T-2": {
                    "original_story_points": 2.0,
                    "original_total_required_hours": 20.0,
                },
            },
            "completed_through_sprint": 1,
            "actual_completed_tasks": {"T-2": 1},
            "closed_sprint_history": [{
                "sprint_number": 1,
                "tasks": [
                    {
                        "task_id": "T-1",
                        "story_points": 8.0,
                        "sprint_capacity_sp_load": 8.0,
                        "outcome": {
                            "completed": False,
                            "remaining_role_hours": {"Backend": 20.0},
                        },
                    },
                    {
                        "task_id": "T-2",
                        "story_points": 2.0,
                        "sprint_capacity_sp_load": 2.0,
                        "outcome": {"completed": True, "remaining_role_hours": {}},
                    },
                ],
            }],
        },
    }


def test_say_do_uses_frozen_sprint_commitment_and_reports_transferred_sp() -> None:
    payload = _payload()
    payload["kpis"]["sprint_kpis"][0]["baseline_sp"] = 99.0

    result = project_management_kpis(payload)
    sprint = result["kpis"]["sprint_kpis"][0]

    assert sprint["baseline_sp"] == 10.0
    assert sprint["actual_sp"] == 8.0
    assert sprint["transferred_sp"] == 2.0
    assert sprint["remaining_sp"] == 2.0
    assert sprint["completion_percent"] == 80.0
    assert sprint["status"] == "BELOW_TARGET"


def test_replanned_work_in_an_initially_empty_sprint_is_counted() -> None:
    payload = _payload()
    payload["_planning_state"]["completed_through_sprint"] = 3
    payload["_planning_state"]["closed_sprint_history"].append({
        "sprint_number": 3,
        "tasks": [
            {
                "task_id": "R-1",
                "sprint_capacity_sp_load": 6.0,
                "outcome": {"completed": True, "remaining_role_hours": {}},
            },
            {
                "task_id": "R-2",
                "sprint_capacity_sp_load": 5.0,
                "outcome": {"completed": True, "remaining_role_hours": {}},
            },
            {
                "task_id": "R-3",
                "sprint_capacity_sp_load": 2.4,
                "outcome": {"completed": True, "remaining_role_hours": {}},
            },
            {
                "task_id": "R-4",
                "sprint_capacity_sp_load": 3.0,
                "outcome": {"completed": True, "remaining_role_hours": {}},
            },
        ],
    })

    sprint = project_management_kpis(payload)["kpis"]["sprint_kpis"][2]

    assert sprint["baseline_sp"] == 16.4
    assert sprint["actual_sp"] == 16.4
    assert sprint["transferred_sp"] == 0.0
    assert sprint["remaining_sp"] == 0.0
    assert sprint["completion_percent"] == 100.0
    assert sprint["status"] == "ON_TARGET"


def test_pi_predictability_counts_only_fully_completed_week0_initiatives() -> None:
    payload = _payload()
    payload["_planning_state"]["actual_completed_tasks"] = {"T-1": 1, "T-2": 1}

    quarter = project_management_kpis(payload)["kpis"]["quarter_kpi"]

    assert quarter["completed_initiatives"] == 1.0
    assert quarter["baseline_initiatives"] == 2.0
    assert quarter["completion_percent"] == 50.0
    assert quarter["status"] == "BELOW_TARGET"


def test_future_sprints_do_not_show_zero_as_fact() -> None:
    sprints = project_management_kpis(_payload())["kpis"]["sprint_kpis"]

    assert sprints[1]["has_actual"] is False
    assert sprints[1]["actual_sp"] is None
    assert sprints[1]["transferred_sp"] is None
    assert sprints[1]["remaining_sp"] is None
    assert sprints[1]["completion_percent"] is None


def test_close_sprint_response_contains_frozen_say_do_summary() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    store.approve(draft["draft"]["id"])

    result = store.close_sprint(1, [], all_completed=True)
    kpi = result["sprint_result"]["kpi"]

    assert kpi["sprint"] == 1
    assert kpi["has_actual"] is True
    assert kpi["transferred_sp"] == 0.0
    assert kpi["remaining_sp"] == 0.0
    assert kpi["completion_percent"] == 100.0
    assert kpi["status"] == "ON_TARGET"


def test_replanning_response_reports_sp_left_after_an_incomplete_sprint() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    store.approve(draft["draft"]["id"])

    result = store.close_sprint(1, [{
        "task_id": "A",
        "task_completed": False,
        "incomplete_reason": "Не хватило времени",
        "remaining_story_points": 0.5,
        "remaining_role_hours": {"Backend": 4.0},
    }])
    kpi = result["sprint_result"]["kpi"]

    assert result["replanning_required"] is True
    assert kpi["baseline_sp"] == 2.8
    assert kpi["actual_sp"] == 2.3
    assert kpi["transferred_sp"] == 0.5
    assert kpi["remaining_sp"] == 0.5
    assert round(kpi["completion_percent"], 2) == 82.14
    assert kpi["status"] == "BELOW_TARGET"


def test_planned_future_part_of_multisprint_task_is_not_a_sprint_deviation() -> None:
    payload = _payload()
    payload["_planning_state"]["closed_sprint_history"][0]["tasks"] = [{
        "task_id": "T-1",
        "story_points": 5.0,
        "sprint_capacity_sp_load": 2.0,
        "sp_load_by_sprint": {"1": 2.0, "2": 3.0},
        "outcome": {
            "completed": False,
            "starting_story_points": 5.0,
            "completed_story_points": 1.0,
            "remaining_story_points": 4.0,
            "remaining_role_hours": {"Backend": 40.0},
        },
    }]

    sprint = project_management_kpis(payload)["kpis"]["sprint_kpis"][0]

    assert sprint["baseline_sp"] == 2.0
    assert sprint["actual_sp"] == 1.0
    assert sprint["transferred_sp"] == 1.0
    assert sprint["actual_sp"] + sprint["transferred_sp"] == sprint["baseline_sp"]


def test_explicit_remaining_sp_is_persisted_with_conservation_audit() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    store.approve(draft["draft"]["id"])

    workflow = store.close_sprint(1, [{
        "task_id": "A",
        "task_completed": False,
        "incomplete_reason": "Не хватило времени",
        "remaining_story_points": 0.75,
        "remaining_role_hours": {"Backend": 6.0},
    }])
    actual = workflow["draft_plan"]["_planning_state"]["actual_results"][-1]

    assert actual["starting_story_points"] == 1.0
    assert actual["completed_story_points"] == 0.25
    assert actual["remaining_story_points"] == 0.75
    assert actual["completed_story_points"] + actual["remaining_story_points"] == actual["starting_story_points"]


def test_remaining_sp_cannot_exceed_task_volume_at_sprint_start() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    store.approve(draft["draft"]["id"])

    with pytest.raises(ValueError, match="превышает текущий объём"):
        store.close_sprint(1, [{
            "task_id": "A",
            "task_completed": False,
            "incomplete_reason": "Не хватило времени",
            "remaining_story_points": 1.1,
            "remaining_role_hours": {"Backend": 8.0},
        }])


def test_frontend_exposes_pi_comparison_sprint_badges_and_result_dialog() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    render_kpis = app.split("function renderKpis()", 1)[1].split(
        "function updateKpiDetailsToggle()", 1
    )[0]

    assert "PI Predictability" in app
    assert "Sprint Say/Do Ratio" not in render_kpis
    assert "pi-kpi-comparison" in render_kpis
    assert "sprint-column-kpi" in app
    assert "transferred_sp" in app
    assert "Перенесено" in app
    assert "sprint-result-formula" not in app
    assert "Есть отклонения от плана?" in html
    assert 'name="hasPlanDeviations"' in html
    assert "data-fact-sp" in app
    assert 'id="sprintResultDialog"' in html
    assert 'method="dialog" class="sprint-result-shell"' in html
    assert 'id="confirmSprintResult"' in html
    assert "showSprintResult(result.sprint_result?.kpi)" in app
