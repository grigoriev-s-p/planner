from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from src.web import api as web_api
from src.web.api import PlanDraftStore, app
from src.web.catalog import CatalogRepository
from src.web.constructor import ConstructorScenario, build_constructor_plan, scenario_tables
from src.web.entity_projections import build_engineer_projection, build_team_projection
from src.web.lifecycle import project_lifecycle
from src.web.replanning import build_remaining_horizon_plan
from tests.test_replanning_workflow import scenario


def _approved(store: PlanDraftStore | None = None) -> tuple[PlanDraftStore, dict]:
    store = store or PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    return store, store.approve(draft["draft"]["id"])


def _after_sprint_one(results: list[dict] | None = None) -> tuple[dict, dict]:
    _, approved = _approved()
    replanned = build_remaining_horizon_plan(approved, 1, results or [])
    return approved, project_lifecycle(replanned, [approved, replanned])


def test_t1_sprint_one_is_the_only_current_sprint_initially() -> None:
    _, approved = _approved()
    projected = project_lifecycle(approved, [approved])

    assert projected["plan"]["quarter"]["current_sprint"] == 1
    assert [row["sprint_number"] for row in projected["plan"]["sprints"] if row["status"] == "CURRENT"] == [1]


def test_t2_t3_after_close_sprint_two_is_current_and_sprint_one_is_closed() -> None:
    _, projected = _after_sprint_one()

    assert projected["plan"]["quarter"]["current_sprint"] == 2
    assert [row["sprint_number"] for row in projected["plan"]["sprints"] if row["status"] == "CURRENT"] == [2]
    assert projected["plan"]["sprints"][0]["presentation_state"] == "CLOSED"
    assert projected["plan"]["sprints"][0]["status"] == "COMPLETED"


def test_authoritative_dashboard_projects_approved_lifecycle_once(monkeypatch) -> None:
    store, _ = _approved()
    store.close_sprint(1, [], all_completed=True)
    monkeypatch.setattr(web_api, "plan_drafts", store)

    dashboard = web_api._current_dashboard()
    current = [
        row["sprint_number"]
        for row in dashboard["plan"]["sprints"]
        if row["status"] == "CURRENT"
    ]

    assert dashboard["plan"]["quarter"]["current_sprint"] == 2
    assert current == [2]
    assert dashboard["plan"]["sprints"][0]["status"] == "COMPLETED"
    assert next(row for row in dashboard["tasks"] if row["task_id"] == "A")["current_status"] == "DONE"


def test_t4_t5_all_completed_api_accepts_empty_unfinished_payload(monkeypatch) -> None:
    store, _ = _approved()
    monkeypatch.setattr(web_api, "plan_drafts", store)

    response = TestClient(app).post("/api/replanning/close-sprint", json={
        "sprint_number": 1,
        "all_completed": True,
        "results": [],
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "SPRINT_CLOSED"
    assert payload["actual"]["all_completed"] is True
    assert payload["replanning_required"] is False
    assert payload["current_sprint"] == 2
    assert payload["sprint_result"]["unfinished_task_ids"] == []
    assert TestClient(app).get("/api/replanning/draft").json() == {"status": "NONE"}


def test_all_completed_does_not_call_replanner_or_change_future_plan(monkeypatch) -> None:
    store, approved = _approved()
    future_before = deepcopy(approved["plan"]["sprints"][1:])

    def unexpected_replan(*_args, **_kwargs):
        raise AssertionError("replanner must not run")

    monkeypatch.setattr(web_api, "build_remaining_horizon_plan", unexpected_replan)
    response = store.close_sprint(1, [], all_completed=True)
    saved = store.approved()

    assert response["status"] == "SPRINT_CLOSED"
    assert response["draft_plan"] is None
    assert saved["plan"]["sprints"][1:] == future_before
    assert saved["revision_context"]["trigger"] == "SPRINT_CLOSE_NO_REPLAN"
    assert saved["_planning_state"]["actual_completed_tasks"] == {"A": 1}
    assert next(row for row in saved["tasks"] if row["task_id"] == "C")["status"] == "PLANNED"


def test_sprint_two_all_completed_finishes_due_task_only() -> None:
    store, _ = _approved()
    first = store.close_sprint(1, [], all_completed=True)
    assert first["sprint_result"]["completed_task_ids"] == ["A"]
    assert "C" not in first["sprint_result"]["completed_task_ids"]
    future_before = deepcopy(store.approved()["plan"]["sprints"][2:])

    second = store.close_sprint(2, [], all_completed=True)
    state = store.approved()["_planning_state"]

    assert second["status"] == "SPRINT_CLOSED"
    assert second["current_sprint"] == 3
    assert second["sprint_result"]["completed_task_ids"] == ["C"]
    assert state["actual_completed_tasks"] == {"A": 1, "C": 2}
    assert store.approved()["plan"]["sprints"][2:] == future_before


def test_all_completed_double_submit_is_controlled() -> None:
    store, _ = _approved()
    store.close_sprint(1, [], all_completed=True)

    with pytest.raises(ValueError, match="последовательно"):
        store.close_sprint(1, [], all_completed=True)


def test_deviation_requires_reason_and_creates_replan_draft() -> None:
    store, _ = _approved()
    with pytest.raises(ValueError, match="укажите причину"):
        store.close_sprint(1, [{
            "task_id": "A",
            "task_completed": False,
            "remaining_role_hours": {"Backend": 4},
        }])

    workflow = store.close_sprint(1, [{
        "task_id": "A",
        "task_completed": False,
        "remaining_role_hours": {"Backend": 4},
        "incomplete_reason": "Не хватило времени",
    }])
    assert workflow["status"] == "READY_FOR_REVIEW"
    assert workflow["replanning_required"] is True
    assert workflow["draft_plan"] is not None


def test_t6_t7_only_due_task_is_done_and_continuation_is_in_progress() -> None:
    _, projected = _after_sprint_one()
    tasks = {row["task_id"]: row for row in projected["tasks"]}
    closed = {row["task_id"]: row for row in projected["plan"]["sprints"][0]["tasks"]}

    assert tasks["A"]["current_status"] == "DONE"
    assert tasks["A"]["actual_completion_sprint"] == 1
    assert tasks["C"]["current_status"] == "IN_PROGRESS"
    assert closed["C"]["outcome"]["status"] == "CONTINUING"


def test_t8_t9_completed_task_remains_in_closed_sprint_after_future_replan() -> None:
    _, projected = _after_sprint_one()
    closed_ids = {row["task_id"] for row in projected["plan"]["sprints"][0]["tasks"]}

    assert closed_ids == {"A", "C"}
    assert next(row for row in projected["tasks"] if row["task_id"] == "A")["historical_only"] is True
    assert next(row for row in projected["plan"]["sprints"][1]["tasks"] if row["task_id"] == "B")


def test_t10_unfinished_task_stays_in_history_and_becomes_carry_over() -> None:
    _, projected = _after_sprint_one([{
        "task_id": "C",
        "task_completed": False,
        "remaining_role_hours": {"Backend": 40},
        "incomplete_reason": "Не хватило времени",
    }])
    closed = {row["task_id"]: row for row in projected["plan"]["sprints"][0]["tasks"]}
    current_ids = {row["task_id"] for row in projected["plan"]["sprints"][1]["tasks"]}

    assert closed["C"]["outcome"]["status"] == "UNFINISHED"
    assert closed["C"]["outcome"]["incomplete_reason"] == "Не хватило времени"
    assert "C" in current_ids
    assert next(row for row in projected["tasks"] if row["task_id"] == "C")["current_status"] == "IN_PROGRESS"


def test_t11_t12_task_status_moves_from_current_work_to_actual_done() -> None:
    _, approved = _approved()
    initial = project_lifecycle(approved, [approved])
    initial_tasks = {row["task_id"]: row for row in initial["tasks"]}
    replanned = build_remaining_horizon_plan(approved, 1, [])
    after = project_lifecycle(replanned, [approved, replanned])
    after_tasks = {row["task_id"]: row for row in after["tasks"]}

    assert initial_tasks["A"]["current_status"] == "IN_PROGRESS"
    assert initial_tasks["B"]["current_status"] == "PLANNED"
    assert after_tasks["A"]["current_status"] == "DONE"
    assert after_tasks["A"]["actual_completion_sprint"] == 1


def test_task_status_is_shared_with_team_and_engineer_current_views() -> None:
    approved, projected = _after_sprint_one()
    snapshots = [approved, projected]
    team = build_team_projection(projected, snapshots, "Team-A")
    engineer = build_engineer_projection(projected, snapshots, "E-1")

    assert team is not None and engineer is not None
    assert {row["current_status"] for row in team["current_tasks"]} == {"IN_PROGRESS"}
    assert {row["task_current_status"] for row in engineer["current_allocations"]} == {"IN_PROGRESS"}


def test_t13_t14_status_and_closed_history_survive_restart() -> None:
    directory = Path.cwd() / f".sprint_lifecycle_test_{uuid4().hex}"
    directory.mkdir()
    try:
        repository = CatalogRepository(
            directory / "catalog.sqlite3", seed_tables=scenario_tables(scenario())
        )
        store, _ = _approved(PlanDraftStore(repository_provider=lambda: repository))
        store.close_sprint(1, [], all_completed=True)

        restarted = PlanDraftStore(repository_provider=lambda: repository)
        restored = restarted.approved()
        assert restored is not None
        projected = project_lifecycle(restored, restarted.history())
        tasks = {row["task_id"]: row for row in projected["tasks"]}

        assert tasks["A"]["current_status"] == "DONE"
        assert tasks["A"]["actual_completion_sprint"] == 1
        assert {row["task_id"] for row in projected["plan"]["sprints"][0]["tasks"]} == {"A", "C"}
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_t15_sprint_six_closes_without_a_current_sprint_seven() -> None:
    _, current = _approved()
    snapshots = [current]
    for sprint in range(1, 7):
        current = build_remaining_horizon_plan(current, sprint, [])
        snapshots.append(current)
    projected = project_lifecycle(current, snapshots)

    assert projected["plan"]["quarter"]["current_sprint"] == 6
    assert not [row for row in projected["plan"]["sprints"] if row["status"] == "CURRENT"]
    assert all(row["presentation_state"] == "CLOSED" for row in projected["plan"]["sprints"])
    tasks = {row["task_id"]: row for row in projected["tasks"]}
    assert tasks["C"]["current_status"] == "DONE"
    assert tasks["C"]["actual_completion_sprint"] == 2
    sprint_one = {row["task_id"]: row for row in projected["plan"]["sprints"][0]["tasks"]}
    sprint_two = {row["task_id"]: row for row in projected["plan"]["sprints"][1]["tasks"]}
    assert sprint_one["C"]["outcome"]["status"] == "CONTINUING"
    assert sprint_two["C"]["outcome"]["status"] == "COMPLETED"


def test_all_completed_sprint_six_closes_quarter_without_sprint_seven() -> None:
    store, _ = _approved()
    result = None
    for sprint in range(1, 7):
        result = store.close_sprint(sprint, [], all_completed=True)

    assert result is not None
    assert result["status"] == "SPRINT_CLOSED"
    assert result["quarter_completed"] is True
    assert result["current_sprint"] == 6
    assert store.approved()["_planning_state"]["quarter_status"] == "COMPLETED"


def test_t16_sprint_with_zero_due_tasks_closes_successfully() -> None:
    long_task = ConstructorScenario(**{
        "teams": [{"team_id": "A", "name": "A", "expected_velocity_sp": 10, "focus_factor": 0.8}],
        "engineers": [{
            "engineer_id": "E", "name": "E", "assignments": [
                {"team_id": "A", "role": "Backend", "capacity_rate": 1}
            ], "skills": [],
        }],
        "tasks": [{
            "task_id": "LONG", "title": "Long", "initiative_id": "I", "team_id": "A",
            "status": "ToDo", "rung": 10, "story_points": 2,
            "role_requirements": [{"role": "Backend", "required_hours": 120, "skills": []}],
            "depends_on": [],
        }],
    })
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(long_task))
    approved = store.approve(draft["draft"]["id"])
    assert next(row for row in approved["tasks"] if row["task_id"] == "LONG")["completion_sprint"] == 2

    workflow = store.close_sprint(1, [], all_completed=True)

    assert workflow["status"] == "SPRINT_CLOSED"
    assert workflow["replanning_required"] is False
    assert workflow["sprint_result"]["completed_count"] == 0
    assert next(row for row in store.approved()["tasks"] if row["task_id"] == "LONG")


def test_t17_frontend_uses_backend_lifecycle_projection() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    styles = (root / "frontend" / "styles.css").read_text(encoding="utf-8")
    lifecycle = (root / "src" / "web" / "lifecycle.py").read_text(encoding="utf-8")
    markup = (root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "all_completed: completionAnswer === 'yes'" in source
    assert "sprintData.presentation_state === 'CLOSED'" in source
    assert "task?.current_status" in source
    assert "sprint === currentSprint() ? 'current' : ''" in source
    assert "const dashboard = await window.postcodeApi.getDashboard();" in source
    assert "index === 0 ? 'current'" not in source
    assert "class=\"sprint-card historical" in source
    assert "outcome.label" in source
    assert ".sprint-card.historical.completed" in styles
    assert 'label = "Завершено"' in lifecycle
    assert "result.replanning_required === false" in source
    assert "show('current-sprint')" in source
    assert "!hasPendingReplan()" in source
    assert "Загрузите фактические результаты" not in markup
    assert "Результаты перепланирования" in markup


def test_sprint_close_form_excludes_irrelevant_controls_from_native_validation() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "frontend" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'step="any" data-fact-role=' in source
    assert 'step="0.25" data-fact-role=' not in source
    assert '<select data-task-reason disabled>' in source
    assert 'data-task-unfinished disabled' in source
    assert 'value="${escapeHtml(hours)}" disabled' in source
    assert "function syncSprintCloseControls()" in source
    assert "checkbox.disabled = !unfinishedEnabled;" in source
    assert "input.disabled = !taskEnabled;" in source
