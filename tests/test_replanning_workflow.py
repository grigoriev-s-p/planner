from __future__ import annotations

import tempfile
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from src.web.api import app
from src.web.api import PlanDraftStore
from src.web.catalog import CatalogRepository
from src.web.constructor import ConstructorScenario, build_constructor_plan, scenario_tables
from src.web.replanning import build_remaining_horizon_plan


def scenario() -> ConstructorScenario:
    return ConstructorScenario(**{
        "teams": [{
            "team_id": "Team-A", "name": "A", "expected_velocity_sp": 10,
            "focus_factor": 0.8,
        }],
        "engineers": [{
            "engineer_id": "E-1", "name": "Engineer",
            "assignments": [{
                "team_id": "Team-A", "role": "Backend", "capacity_rate": 1,
            }],
            "skills": [],
        }],
        "tasks": [
            {
                "task_id": "A", "title": "Blocker", "initiative_id": "I-A",
                "team_id": "Team-A", "status": "ToDo", "rung": 100,
                "story_points": 1,
                "role_requirements": [{"role": "Backend", "required_hours": 8, "skills": []}],
                "depends_on": [],
            },
            {
                "task_id": "B", "title": "Blocked", "initiative_id": "I-B",
                "team_id": "Team-A", "status": "ToDo", "rung": 90,
                "story_points": 1,
                "role_requirements": [{"role": "Backend", "required_hours": 8, "skills": []}],
                "depends_on": ["A"],
            },
            {
                "task_id": "C", "title": "Remaining", "initiative_id": "I-C",
                "team_id": "Team-A", "status": "InProgress", "rung": 80,
                "story_points": 2,
                "role_requirements": [{"role": "Backend", "required_hours": 80, "skills": []}],
                "depends_on": [],
            },
        ],
    })


def approved_plan(store: PlanDraftStore) -> dict:
    draft = store.create(build_constructor_plan(scenario()))
    return store.approve(draft["draft"]["id"])


def test_remaining_horizon_freezes_past_uses_actual_dependency_and_keeps_baseline() -> None:
    store = PlanDraftStore()
    approved = approved_plan(store)
    frozen_sprint = deepcopy(approved["plan"]["sprints"][0])
    baseline = deepcopy(approved["_planning_state"]["week0_baseline"]["kpis"])

    replanned = build_remaining_horizon_plan(approved, 1, [
        {"task_id": "A", "task_completed": True, "remaining_role_hours": {}},
        {"task_id": "C", "task_completed": False, "remaining_role_hours": {"Backend": 40}},
    ])

    assert replanned["plan"]["sprints"][0] == frozen_sprint
    by_id = {task["task_id"]: task for task in replanned["tasks"]}
    assert "A" not in by_id
    assert by_id["B"]["start_sprint"] == 2
    assert 1 in by_id["C"]["planned_sprints"]
    future_c_hours = sum(
        hours for sprint, hours in by_id["C"]["hours_by_sprint"].items()
        if int(sprint) > 1
    )
    assert future_c_hours == 40
    assert all(
        int(row["sprint"]) >= 2
        for row in by_id["C"]["allocations"]
        if int(row["sprint"]) > 1
    )
    assert replanned["kpis"]["expected_kpi"]["baseline_initiatives"] == baseline["expected_kpi"]["baseline_initiatives"]
    assert [row["sprint_number"] for row in replanned["plan"]["sprints"]] == [1, 2, 3, 4, 5, 6]


def test_approved_plan_and_history_survive_store_restart() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = CatalogRepository(
            Path(directory) / "catalog.sqlite3", seed_tables=scenario_tables(scenario())
        )
        first_store = PlanDraftStore(repository_provider=lambda: repository)
        approved = approved_plan(first_store)

        restarted_store = PlanDraftStore(repository_provider=lambda: repository)
        restored = restarted_store.approved()

        assert restored == approved
        assert restored["approved_snapshot"]["revision"] == 1
        assert len(repository.approved_plan_history()) == 1

        replanned = build_remaining_horizon_plan(restored, 1, [
            {"task_id": "A", "task_completed": True, "remaining_role_hours": {}},
        ])
        saved = restarted_store.replace_approved(replanned)
        second_restart = PlanDraftStore(repository_provider=lambda: repository)

        assert second_restart.approved() == saved
        history = repository.approved_plan_history()
        assert [row["approved_snapshot"]["revision"] for row in history] == [1, 2]
        assert history[0]["plan"]["sprints"][0] == approved["plan"]["sprints"][0]

        historical_first = deepcopy(history[0])
        repository.archive("engineer", "E-1", "EMPLOYEE_LEFT")
        assert not repository.active_planning_tables()["engineers"]
        assert repository.approved_plan_history()[0] == historical_first
        repository.merge_tables({
            "engineers": [{
                "engineer_id": "E-1", "team_id": "Team-A",
                "role": "Backend", "capacity_rate": 1,
            }]
        })
        assert repository.lifecycle("engineer", "E-1")["archived"]


def test_manual_frontend_build_keeps_task_payload_fields() -> None:
    source = (Path(__file__).resolve().parents[1] / "frontend" / "constructor.js").read_text(encoding="utf-8")

    assert "const tasks = draft.tasks.map((task) => ({...task}));" in source
    assert "draft.tasks.map((task) => ({...task, team_id: null}))" not in source
    tables = scenario_tables(scenario())
    tasks = {row["task_id"]: row for row in tables["tasks"]}
    assert tasks["A"]["team_id"] == "Team-A"
    assert {row["task_id"] for row in tables["tasks"]} == {"A", "B", "C"}


def test_close_sprint_api_creates_review_draft_before_replacing_approved(monkeypatch) -> None:
    store = PlanDraftStore()
    approved = approved_plan(store)
    monkeypatch.setattr("src.web.api.plan_drafts", store)

    response = TestClient(app).post("/api/replanning/close-sprint", json={
        "sprint_number": 1,
        "results": [
            {"task_id": "A", "task_completed": True},
            {
                "task_id": "C", "task_completed": False,
                "remaining_role_hours": {"Backend": 40},
                "incomplete_reason": "Не хватило времени",
            },
        ],
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "READY_FOR_REVIEW"
    assert payload["replanning_required"] is True
    assert payload["actual_persisted"] is True
    assert payload["draft_plan"]["replanning"]["completed_through_sprint"] == 1
    assert next(task for task in payload["draft_plan"]["tasks"] if task["task_id"] == "B")["start_sprint"] == 2
    assert store.approved() == approved

    approved_response = TestClient(app).post(
        f"/api/replanning/drafts/{payload['workflow_id']}/approve"
    )
    assert approved_response.status_code == 200, approved_response.text
    assert store.approved() == approved_response.json()
    assert store.approved()["revision_context"]["trigger"] == "REPLAN_APPROVE"
