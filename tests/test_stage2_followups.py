from __future__ import annotations

from pathlib import Path

from src.web.api import PlanDraftStore
from src.web.constructor import build_constructor_plan
from src.web.replanning import build_remaining_horizon_plan
from tests.test_replanning_workflow import scenario


def _approved() -> dict:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    return store.approve(draft["draft"]["id"])


def test_remaining_story_points_keep_week0_hours_denominator_across_replans() -> None:
    approved = _approved()
    first = build_remaining_horizon_plan(approved, 1, [
        {"task_id": "A", "task_completed": True},
        {"task_id": "C", "task_completed": False, "remaining_role_hours": {"Backend": 40}},
    ])
    c1 = next(task for task in first["tasks"] if task["task_id"] == "C")
    assert sum(value for sprint, value in c1["sp_load_by_sprint"].items() if int(sprint) > 1) == 1.0
    second = build_remaining_horizon_plan(first, 2, [
        {"task_id": "B", "task_completed": True},
        {"task_id": "C", "task_completed": False, "remaining_role_hours": {"Backend": 20}},
    ])
    c2 = next(task for task in second["tasks"] if task["task_id"] == "C")
    assert abs(sum(value for sprint, value in c2["sp_load_by_sprint"].items() if int(sprint) > 2) - 0.5) < 1e-9
    assert second["_planning_state"]["baseline_tables"] == approved["_planning_state"]["baseline_tables"]


def test_frontend_close_sprint_uses_public_api_namespace() -> None:
    source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "window.postcodeApi.closeSprint" in source
    assert "await api.closeSprint" not in source


def test_week0_kpi_denominator_is_not_shrunk_by_done_tasks() -> None:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    approved = store.approve(draft["draft"]["id"])
    baseline = approved["_planning_state"]["week0_baseline"]["kpis"]["expected_kpi"]["baseline_initiatives"]
    saved = store.replace_approved(build_remaining_horizon_plan(
        approved, 1, [{"task_id": "A", "task_completed": True}]
    ))
    assert saved["kpis"]["expected_kpi"]["baseline_initiatives"] == baseline
    assert saved["_planning_state"]["week0_baseline"]["kpis"]["expected_kpi"]["baseline_initiatives"] == baseline
