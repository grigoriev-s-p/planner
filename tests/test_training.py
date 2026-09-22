from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.planning.availability import availability_rate_index
from src.planning.feasibility import FeasibilityContext, FeasibilityService
from src.planning.models import SprintState
from src.planning.planner import build_quarter_plan
from src.web import api
from src.web.api import PlanDraftStore, TrainingRequest
from src.web.catalog import CatalogRepository
from src.web.service import PlanningAPIService
from src.web.service import build_bus_factor_payload
from src.web.constructor import build_catalog_snapshot


def _tables():
    return {
        "tasks": [{"task_id": "T", "title": "Backend work", "team_id": "A",
                   "initiative_id": "I", "story_points": 2, "rung": 80,
                   "status": "ToDo", "is_placeholder": False}],
        "task_role_effort": [{"task_id": "T", "role": "Backend", "required_hours": 40}],
        "task_dependencies": [],
        "task_skill_requirements": [],
        "engineers": [
            {"engineer_id": "E1", "team_id": "A", "role": "Backend", "capacity_rate": 1},
            {"engineer_id": "E2", "team_id": "A", "role": "Backend", "capacity_rate": 1},
        ],
        "engineer_skills": [],
        "team_history": [{"team_id": "A", "velocity_achieved": 10,
                          "planned_sp": 10, "snapshot_date": "2026-01-01"}],
    }


def _recommendation():
    return {"recommendations": [{
        "recommendation_id": "development-test", "team_id": "A",
        "deficit": {"type": "SKILL", "role": "Backend", "skill": "Kafka",
                    "skills": ["Kafka"]},
        "recommendation": {"candidates": [{"engineer_id": "E1",
                                           "target_role": "Backend",
                                           "target_skill": "Kafka"}]},
    }]}


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    repository = CatalogRepository(tmp_path / "catalog.sqlite3", seed_tables=_tables())
    store = PlanDraftStore(repository_provider=lambda: repository)
    approved = PlanningAPIService.from_tables(
        repository.active_planning_tables(),
        planner_options={"start_sprint": 3},
    ).dashboard
    approved["_planning_state"] = {
        "tables": deepcopy(repository.active_planning_tables()),
        "baseline_tables": deepcopy(repository.active_planning_tables()),
        "completed_through_sprint": 0,
    }
    store.replace_approved(approved)
    monkeypatch.setattr(api, "get_catalog_repository", lambda: repository)
    monkeypatch.setattr(api, "plan_drafts", store)
    monkeypatch.setattr(api, "development_recommendations", _recommendation)
    return repository, store


def _request(mode="HALF", sprints=None, resolutions=None):
    return TrainingRequest(
        recommendation_id="development-test", engineer_id="E1",
        selected_sprints=sprints or [3], availability_mode=mode,
        resolutions=resolutions or {},
    )


def test_no_conflict_training_persists_canonical_constraint(scenario):
    repository, store = scenario
    approved = store.approved()
    for task in approved["tasks"]:
        task["allocations"] = []
    store.replace_approved(approved)
    result = api.confirm_training(_request())
    assert result["status"] == "SCHEDULED"
    assert len(repository.training_plans()) == 1
    assert availability_rate_index(repository.availability_constraints())[3]["E1"] == .5
    assert repository.capability_overlays() == []


def test_conflict_is_staged_without_changing_official_data(scenario):
    repository, store = scenario
    request = _request("BLOCKED")
    analysis = api.analyze_training(request)
    assert analysis["requires_replanning"] is True
    assert analysis["conflicts"][0]["task_id"] == "T"
    with pytest.raises(Exception):
        api.confirm_training(request)
    assert repository.training_plans() == []
    assert repository.availability_constraints() == []
    assert store.approved()["approved_snapshot"]["revision"] == 1


def test_training_completion_adds_capability_without_raw_rewrite(scenario):
    repository, store = scenario
    plan = {
        "training_id": "TR-1", "engineer_id": "E1", "target_role": "Architect",
        "target_skill": "Kafka", "selected_sprints": [2, 3],
        "availability_mode": "HALF", "availability_rate": .5,
        "status": "SCHEDULED", "effective_from_sprint": 4,
    }
    repository.save_training_plan(plan)
    assert repository.capability_overlays() == []
    payload = deepcopy(store.approved())
    payload["_planning_state"]["completed_through_sprint"] = 3
    store.replace_approved(payload)
    overlays = repository.capability_overlays()
    assert {(row["capability_type"], row["capability_value"],
             row["effective_from_sprint"]) for row in overlays} == {
                 ("ROLE", "Architect", 4), ("SKILL", "Kafka", 4),
             }
    active = repository.active_planning_tables()
    assert any(row.get("role") == "Architect" and row["engineer_id"] == "E1"
               for row in active["engineers"])
    assert any(row["technology"] == "Kafka" for row in active["engineer_skills"])
    assert all(row.get("role") != "Architect" for row in repository.tables()["engineers"])
    assert len(repository.training_plans()) == 1
    bus = build_bus_factor_payload(active)
    team = next(row for row in bus["teams"] if row["team_id"] == "A")
    kafka = next(row for row in team["competencies"] if row["skill"] == "Kafka")
    assert kafka["bus_factor"] == 1
    catalog = build_catalog_snapshot(active)
    profile = next(row for row in catalog["engineers"] if row["engineer_id"] == "E1")
    assert profile["membership_rate"] == 1
    dashboard = PlanningAPIService.from_tables(active).dashboard
    assert next(row for row in dashboard["engineers"]
                if row["engineer_id"] == "E1")["capacity_hours"] == 80
    star = api._active_star_map(active, catalog, None, [])
    star_team = next(row for row in star["teams"] if row["team_id"] == "A")
    assert any(row["role"] == "Architect" for row in
               star_team["competency_modes"]["all"]["roles"])


def test_training_conflict_move_is_atomic_on_approval(scenario):
    repository, store = scenario
    impact = api.analyze_training(_request("BLOCKED"))
    key = impact["conflicts"][0]["key"]
    result = api.confirm_training(_request("BLOCKED", resolutions={
        key: {"action": "MOVE"},
    }))
    assert result["status"] == "READY_FOR_REVIEW"
    assert repository.training_plans() == []
    assert repository.availability_constraints() == []
    before_revision = store.approved()["approved_snapshot"]["revision"]
    approved = store.approve_replanning(result["workflow"]["workflow_id"])
    assert approved["approved_snapshot"]["revision"] == before_revision + 1
    assert len(repository.training_plans()) == 1
    assert availability_rate_index(repository.availability_constraints())[3]["E1"] == 0
    task = next(row for row in approved["tasks"] if row["task_id"] == "T")
    assert all(row["sprint"] != 3 for row in task["allocations"])


def test_training_conflict_replacement_is_reviewed_before_persist(scenario):
    repository, store = scenario
    impact = api.analyze_training(_request("BLOCKED"))
    key = impact["conflicts"][0]["key"]
    result = api.confirm_training(_request("BLOCKED", resolutions={
        key: {"action": "REPLACE", "engineer_id": "E2"},
    }))
    assert result["status"] == "READY_FOR_REVIEW"
    assert repository.training_plans() == []
    task = next(row for row in result["workflow"]["draft_plan"]["tasks"]
                if row["task_id"] == "T")
    assert any(row["engineer_id"] == "E2" and row["sprint"] == 3
               for row in task["allocations"])
    assert store.approved()["approved_snapshot"]["revision"] == 1


def test_manual_and_training_use_min_not_product(scenario):
    repository, store = scenario
    repository.save_availability_constraints([{
        "engineer_id": "E1", "sprint": 4, "availability_rate": .5,
        "source_type": "MANUAL", "source_id": "manual",
    }])
    result = api.confirm_training(_request("HALF", sprints=[4, 5]))
    assert result["status"] == "SCHEDULED"
    rates = availability_rate_index(repository.availability_constraints())
    assert rates[4]["E1"] == .5
    assert rates[5]["E1"] == .5
    profile = api.engineer_trainings("E1")["trainings"][0]["effective_availability"]["4"]
    assert profile["training_rate"] == .5
    assert profile["other_rate"] == .5
    assert profile["hours"] == 40
    assert store.approved()["approved_snapshot"]["revision"] == 1


def test_non_contiguous_training_only_constrains_selected_sprints(scenario):
    repository, _ = scenario
    result = api.confirm_training(_request("HALF", sprints=[2, 4]))
    assert result["status"] == "SCHEDULED"
    rates = availability_rate_index(repository.availability_constraints())
    assert rates[2]["E1"] == .5
    assert 3 not in rates
    assert rates[4]["E1"] == .5
    assert result["plan"]["effective_from_sprint"] == 5


def test_current_sprint_rejected_even_if_submitted_directly(scenario):
    with pytest.raises(Exception):
        api.analyze_training(_request("NO_IMPACT", sprints=[1]))


def test_atomic_write_rolls_back_snapshot_on_training_failure(scenario):
    repository, store = scenario
    plan = {"training_id": "duplicate", "engineer_id": "E1",
            "selected_sprints": [4], "availability_rate": .5,
            "availability_mode": "HALF", "status": "SCHEDULED"}
    repository.save_training_plan(plan)
    revision = store.approved()["approved_snapshot"]["revision"]
    with pytest.raises(Exception):
        repository.save_approved_with_training(
            store.approved(), plan,
            {"workflow_id": "W", "status": "READY_FOR_REVIEW",
             "base_approved_revision": revision},
        )
    assert repository.load_approved_plan()["approved_snapshot"]["revision"] == revision
    assert len(repository.training_plans()) == 1


def test_training_completes_only_after_last_sprint_close(scenario):
    repository, store = scenario
    plan = {"training_id": "TR-close", "engineer_id": "E1",
            "target_skill": "Kafka", "target_role": None,
            "selected_sprints": [2, 3], "availability_rate": 1,
            "availability_mode": "NO_IMPACT", "status": "SCHEDULED"}
    repository.save_training_plan(plan)
    for sprint in (1, 2, 3):
        workflow = store.close_sprint(sprint, [], all_completed=True)
        assert workflow["status"] == "READY_FOR_REVIEW"
        if sprint < 3:
            assert repository.capability_overlays() == []
        store.approve_replanning(workflow["workflow_id"])
    assert repository.training_plans()[0]["status"] == "COMPLETED"
    assert repository.capability_overlays()[0]["effective_from_sprint"] == 4


def test_acquired_role_and_skill_not_usable_before_effective_sprint():
    context = FeasibilityContext.from_records(
        tasks=[{"task_id": "T", "team_id": "A", "story_points": 1,
                "status": "ToDo", "rung": 80}],
        task_role_effort=[{"task_id": "T", "role": "Architect", "required_hours": 20}],
        dependencies=[],
        engineers=[{"engineer_id": "E1", "team_id": "A", "role": "Backend",
                    "capacity_rate": 1},
                   {"engineer_id": "E1", "team_id": "A", "role": "Architect",
                    "capacity_rate": 1, "effective_from_sprint": 4}],
        engineer_skills=[{"engineer_id": "E1", "technology": "Kafka",
                          "effective_from_sprint": 4}],
        team_history=[{"team_id": "A", "velocity_achieved": 10, "planned_sp": 10}],
        task_skill_requirements=[{"task_id": "T", "role": "Architect",
                                  "technology": "Kafka"}],
    )
    service = FeasibilityService(context)
    before = service.check("T", SprintState(sprint_number=3))
    after = service.check("T", SprintState(sprint_number=4))
    assert before.role_capacity[0].available_hours == 0
    assert after.role_capacity[0].available_hours == 80
    planned = next(row for row in build_quarter_plan(context).task_results
                   if row.task_id == "T")
    assert planned.allocations[0].sprint_number >= 4


def test_training_http_flow_and_engineer_profile(scenario):
    repository, store = scenario
    client = TestClient(api.app)
    payload = _request("NO_IMPACT", sprints=[4, 5]).model_dump()
    analyzed = client.post("/api/development/trainings/analyze", json=payload)
    assert analyzed.status_code == 200
    assert analyzed.json()["requires_replanning"] is False
    saved = client.post("/api/development/trainings/confirm", json=payload)
    assert saved.status_code == 200
    assert saved.json()["status"] == "SCHEDULED"
    profile = client.get("/api/engineers/E1/trainings")
    assert profile.status_code == 200
    assert profile.json()["trainings"][0]["effective_from_sprint"] == 6
    assert store.approved()["approved_snapshot"]["revision"] == 1
