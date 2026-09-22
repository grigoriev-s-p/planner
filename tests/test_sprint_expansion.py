from copy import deepcopy

from src.web.expansion import build_proposal, merge_additions, simulate_additions
from src.web.service import PlanningAPIService
from src.web import api
from src.web.api import PlanDraftStore
from src.web.catalog import CatalogRepository
from src.web.kpi_projection import freeze_sprint_baseline, project_management_kpis
from fastapi.testclient import TestClient
import pytest


def _tables():
    return {
        "tasks": [
            {"task_id": "A", "title": "Original", "team_id": "Team-A",
             "initiative_id": "I-A", "story_points": 2, "rung": 90,
             "status": "ToDo", "is_placeholder": False},
            {"task_id": "B", "title": "Additional", "team_id": "Team-A",
             "initiative_id": "I-B", "story_points": 2, "rung": 80,
             "status": "ToDo", "is_placeholder": False},
            {"task_id": "C", "title": "Another", "team_id": "Team-A",
             "initiative_id": "I-C", "story_points": 2, "rung": 70,
             "status": "ToDo", "is_placeholder": False},
        ],
        "task_role_effort": [
            {"task_id": task_id, "role": "Backend", "required_hours": 40}
            for task_id in ("A", "B", "C")
        ],
        "task_dependencies": [], "task_skill_requirements": [],
        "engineers": [{"engineer_id": "E1", "team_id": "Team-A",
                       "role": "Backend", "capacity_rate": 1}],
        "engineer_skills": [],
        "team_history": [{"team_id": "Team-A", "velocity_achieved": 10,
                          "planned_sp": 10, "snapshot_date": "2026-01-01"}],
    }


def _approved():
    tables = _tables()
    original = deepcopy(tables)
    original["tasks"] = original["tasks"][:1]
    original["task_role_effort"] = original["task_role_effort"][:1]
    approved = PlanningAPIService.from_tables(original).dashboard
    approved["approved_snapshot"] = {"revision": 1}
    approved["_planning_state"] = {"completed_through_sprint": 1,
                                    "actual_completed_tasks": {"A": 1},
                                    "tables": deepcopy(original),
                                    "baseline_tables": deepcopy(original),
                                    "week0_baseline": deepcopy(approved)}
    return approved, tables


def test_additive_proposal_preserves_original_assignments_and_separates_tasks():
    approved, tables = _approved()
    proposal = build_proposal(approved, tables, catalog_revision=1,
                              availability_constraints=[], workflow_id="close-1")
    assert proposal["status"] == "OPEN"
    assert len(proposal["recommendations"]) == 2
    assert all(row["type"] == "ADDITIONAL_TASK" for row in proposal["recommendations"])
    simulation = simulate_additions(approved, tables, set(proposal["best_task_ids"]))
    assert set(simulation["selected_ids"]) == {"B", "C"}
    merged = merge_additions(approved, simulation, tables, triggered_by_sprint=1)
    assert next(row for row in merged["tasks"] if row["task_id"] == "A") == approved["tasks"][0]
    assert {row["task_id"]: row["start_sprint"] for row in merged["tasks"]
            if row["task_id"] in {"B", "C"}} == {"B": 2, "C": 2}
    assert all(row["expansion_origin"]["triggered_by_sprint"] == 1
               for row in merged["tasks"] if row["task_id"] in {"B", "C"})


def test_apply_all_approves_additions_atomically_and_freezes_next_baseline(tmp_path, monkeypatch):
    approved, tables = _approved()
    repository = CatalogRepository(tmp_path / "catalog.sqlite3", seed_tables=tables)
    store = PlanDraftStore(repository_provider=lambda: repository)
    store.replace_approved(approved)
    monkeypatch.setattr(api, "get_catalog_repository", lambda: repository)
    monkeypatch.setattr(api, "plan_drafts", store)
    proposal = api.refresh_sprint_expansion()
    assert len(proposal["proposal"]["recommendations"]) == 2
    selected = api.apply_all_sprint_expansion()
    assert selected["effect"]["added_tasks"] == 2
    result = api.approve_sprint_expansion()
    assert result["proposal"]["status"] == "APPROVED"
    assert api.approve_sprint_expansion()["proposal"]["status"] == "APPROVED"
    assert len(repository.approved_plan_history()) == 2
    current = repository.load_approved_plan()
    assert {row["task_id"] for row in current["tasks"]} == {"A", "B", "C"}
    assert current["_planning_state"]["sprint_baselines"]["2"]["planned_sp"] == 4
    assert current["_planning_state"]["sprint_baselines"]["2"]["source_revision"] == current["approved_snapshot"]["revision"]
    assert repository.latest_expansion_proposal()["status"] == "APPROVED"
    dashboard = api._dashboard_with_lifecycle()
    assert {row["task_id"] for row in dashboard["tasks"]} == {"A", "B", "C"}
    assert {row["initiative_id"] for row in dashboard["initiatives"]} == {"I-A", "I-B", "I-C"}
    assert dashboard["kpis"]["sprint_kpis"][1]["baseline_sp"] == 4
    next_workflow = store.close_sprint(2, [
        {"task_id": "B", "task_completed": False,
         "remaining_role_hours": {"Backend": 20},
         "incomplete_reason": "Не успели"},
        {"task_id": "C", "task_completed": True},
    ])
    assert next_workflow["status"] == "READY_FOR_REVIEW"


def test_ignore_keeps_approved_unchanged_but_persists_next_sprint_baseline(tmp_path, monkeypatch):
    approved, tables = _approved()
    repository = CatalogRepository(tmp_path / "catalog.sqlite3", seed_tables=tables)
    store = PlanDraftStore(repository_provider=lambda: repository)
    store.replace_approved(approved)
    monkeypatch.setattr(api, "get_catalog_repository", lambda: repository)
    monkeypatch.setattr(api, "plan_drafts", store)
    api.refresh_sprint_expansion()
    before = repository.load_approved_plan()
    decision = api.ignore_sprint_expansion()
    assert decision["status"] == "IGNORED"
    assert repository.load_approved_plan() == before
    assert len(repository.approved_plan_history()) == 1
    projected = api._current_dashboard()
    assert projected["kpis"]["sprint_kpis"][1]["baseline_confirmed"] is True
    workflow = store.close_sprint(2, [], all_completed=True)
    saved = store.approve_replanning(workflow["workflow_id"])
    assert saved["_planning_state"]["sprint_baselines"]["2"]["planned_sp"] == 0


def test_reserved_sp_keeps_existing_future_task_and_pushes_addition_later():
    tables = _tables()
    tables["tasks"] = [{**tables["tasks"][0], "story_points": 7}, tables["tasks"][1]]
    tables["task_role_effort"] = tables["task_role_effort"][:2]
    original = deepcopy(tables)
    original["tasks"] = original["tasks"][:1]
    original["task_role_effort"] = original["task_role_effort"][:1]
    approved = PlanningAPIService.from_tables(original,
        planner_options={"start_sprint": 2}).dashboard
    approved["_planning_state"] = {"completed_through_sprint": 1,
                                    "actual_completed_tasks": {}}
    assert approved["tasks"][0]["start_sprint"] == 2
    simulation = simulate_additions(approved, tables)
    assert simulation["tasks"][0]["task_id"] == "B"
    assert simulation["tasks"][0]["sp_load_by_sprint"] == {"2": 1.0, "3": 1.0}
    merged = merge_additions(approved, simulation, tables)
    assert next(row for row in merged["tasks"] if row["task_id"] == "A") == approved["tasks"][0]


def test_addition_never_double_books_existing_engineer_hours():
    tables = _tables()
    tables["tasks"] = tables["tasks"][:2]
    tables["task_role_effort"] = [tables["task_role_effort"][0],
                                   {"task_id": "B", "role": "Backend", "required_hours": 50}]
    original = deepcopy(tables)
    original["tasks"] = original["tasks"][:1]
    original["task_role_effort"] = original["task_role_effort"][:1]
    approved = PlanningAPIService.from_tables(original,
        planner_options={"start_sprint": 2}).dashboard
    approved["_planning_state"] = {"completed_through_sprint": 1,
                                    "actual_completed_tasks": {}}
    additional = simulate_additions(approved, tables)["tasks"][0]
    for sprint in range(2, 7):
        original_hours = sum(row["allocated_hours"] for row in approved["tasks"][0]["allocations"]
                             if row["sprint"] == sprint and row["engineer_id"] == "E1")
        new_hours = sum(row["allocated_hours"] for row in additional["allocations"]
                        if row["sprint"] == sprint and row["engineer_id"] == "E1")
        assert original_hours + new_hours <= 80


def test_new_role_makes_additional_task_feasible_without_rewriting_original():
    approved, tables = _approved()
    tables["tasks"] = tables["tasks"][:2]
    tables["task_role_effort"] = [tables["task_role_effort"][0],
                                   {"task_id": "B", "role": "QA", "required_hours": 40}]
    assert simulate_additions(approved, tables)["tasks"] == []
    tables["engineers"].append({"engineer_id": "E-QA", "team_id": "Team-A",
                                 "role": "QA", "capacity_rate": 1})
    simulation = simulate_additions(approved, tables)
    assert [row["task_id"] for row in simulation["tasks"]] == ["B"]
    merged = merge_additions(approved, simulation, tables)
    assert any(row["engineer_id"] == "E-QA" for row in merged["engineers"])
    assert any(row["engineer_id"] == "E-QA"
               for row in next(task for task in merged["tasks"] if task["task_id"] == "B")["allocations"])


def test_new_engineer_is_visible_in_current_projections_after_approval(tmp_path, monkeypatch):
    approved, tables = _approved()
    tables["tasks"] = tables["tasks"][:2]
    tables["task_role_effort"] = [tables["task_role_effort"][0],
                                   {"task_id": "B", "role": "QA", "required_hours": 40}]
    tables["engineers"].append({"engineer_id": "E-QA", "team_id": "Team-A",
                                 "role": "QA", "capacity_rate": 1})
    repository = CatalogRepository(tmp_path / "catalog.sqlite3", seed_tables=tables)
    store = PlanDraftStore(repository_provider=lambda: repository)
    store.replace_approved(approved)
    monkeypatch.setattr(api, "get_catalog_repository", lambda: repository)
    monkeypatch.setattr(api, "plan_drafts", store)
    api.refresh_sprint_expansion()
    api.apply_all_sprint_expansion()
    api.approve_sprint_expansion()
    dashboard = api._dashboard_with_lifecycle()
    engineer = next(row for row in dashboard["engineers"] if row["engineer_id"] == "E-QA")
    assert any(row["task_id"] == "B" for row in engineer["allocations"])
    task = next(row for row in dashboard["tasks"] if row["task_id"] == "B")
    assert task["status"] == "PLANNED"
    assert task["expansion_origin"]["source"] == "sprint_expansion"
    team = next(row for row in dashboard["teams"] if row["team_id"] == "Team-A")
    assert team["sp_by_sprint"]["2"]["used"] == 2
    star = next(row for row in dashboard["star_map"]["teams"] if row["team_id"] == "Team-A")
    assert any(row.get("role") == "QA" and row["team_bus_factor"] == 1
               for row in star["competency_modes"]["all"]["roles"])


def test_new_cross_team_helper_uses_source_bucket_and_keeps_owner_sp():
    approved, tables = _approved()
    tables["tasks"] = tables["tasks"][:2]
    tables["task_role_effort"] = [tables["task_role_effort"][0],
                                   {"task_id": "B", "role": "Backend", "required_hours": 60},
                                   {"task_id": "B", "role": "QA", "required_hours": 20}]
    tables["engineers"].append({"engineer_id": "E-QA", "team_id": "Team-B",
                                 "role": "QA", "capacity_rate": 1})
    tables["team_history"].append({"team_id": "Team-B", "velocity_achieved": 10,
                                   "planned_sp": 10, "snapshot_date": "2026-01-01"})
    simulation = simulate_additions(approved, tables)
    task = simulation["tasks"][0]
    assert task["assigned_team_id"] == "Team-A"
    assert task["ownership"]["valid"] is True
    assert [(row["engineer_id"], row["source_team_id"], row["allocated_hours"])
            for row in task["allocations"]] == [("E1", "Team-A", 60.0),
                                                 ("E-QA", "Team-B", 20.0)]
    merged = merge_additions(approved, simulation, tables)
    owner = next(row for row in merged["teams"] if row["team_id"] == "Team-A")
    assert owner["sp_by_sprint"]["2"]["used"] == 2
    helper = next(row for row in merged["engineers"] if row["engineer_id"] == "E-QA")
    assert any(row["task_id"] == "B" and row["cross_team"] for row in helper["allocations"])


def test_dependent_tasks_form_only_one_required_package():
    approved, tables = _approved()
    tables["task_dependencies"] = [{"blocking_task_id": "B", "blocked_task_id": "C",
                                    "dependency_type": "depends on"}]
    proposal = build_proposal(approved, tables, catalog_revision=1,
                              availability_constraints=[], workflow_id="close-1")
    assert len(proposal["recommendations"]) == 1
    assert proposal["recommendations"][0]["type"] == "ATOMIC_PACKAGE"
    assert set(proposal["recommendations"][0]["task_ids"]) == {"B", "C"}


def test_factually_completed_blocker_unlocks_next_sprint_without_history_rewrite():
    approved, tables = _approved()
    tables["tasks"] = tables["tasks"][:2]
    tables["task_role_effort"] = tables["task_role_effort"][:2]
    tables["task_dependencies"] = [{"blocking_task_id": "A", "blocked_task_id": "B",
                                    "dependency_type": "depends on"}]
    simulation = simulate_additions(approved, tables)
    assert [row["task_id"] for row in simulation["tasks"]] == ["B"]
    assert simulation["tasks"][0]["start_sprint"] == 2
    assert next(row for row in approved["tasks"] if row["task_id"] == "A")["start_sprint"] == 1


def test_sprint_baseline_denominators_are_frozen_per_sprint():
    payload = {"plan": {"sprints": [
        {"sprint_number": sprint, "tasks": [{"task_id": f"T{sprint}",
                                               "capacity_sp_load": sp}]}
        for sprint, sp in ((1, 100), (2, 80), (3, 60))]},
        "_planning_state": {"completed_through_sprint": 0,
                            "week0_baseline": {"kpis": {"sprint_kpis": [
                                {"sprint": sprint, "baseline_sp": sp}
                                for sprint, sp in ((1, 100), (2, 80), (3, 60))]}}}}
    freeze_sprint_baseline(payload, 1)
    payload["plan"]["sprints"][1]["tasks"][0]["capacity_sp_load"] = 90
    freeze_sprint_baseline(payload, 2)
    payload["plan"]["sprints"][2]["tasks"][0]["capacity_sp_load"] = 80
    freeze_sprint_baseline(payload, 3)
    payload["plan"]["sprints"][0]["tasks"][0]["capacity_sp_load"] = 999
    projected = project_management_kpis(payload)
    assert [row["baseline_sp"] for row in projected["kpis"]["sprint_kpis"][:3]] == [100, 90, 80]
    assert projected["kpis"]["sprint_kpis"][3]["completion_percent"] is None
    payload["_planning_state"]["completed_through_sprint"] = 2
    payload["_planning_state"]["closed_sprint_history"] = [{
        "sprint_number": 2,
        "tasks": [{"task_id": "T2", "sprint_capacity_sp_load": 90,
                   "sp_load_by_sprint": {"2": 90},
                   "outcome": {"completed": False,
                               "remaining_story_points": 18,
                               "starting_story_points": 90}}],
    }]
    actual = project_management_kpis(payload)["kpis"]["sprint_kpis"][1]
    assert actual["actual_sp"] == 72
    assert actual["completion_percent"] == 80


def test_zero_sp_sprint_kpi_is_unavailable():
    payload = {"plan": {"sprints": [{"sprint_number": 1, "tasks": []}]},
               "_planning_state": {"completed_through_sprint": 1,
                                   "closed_sprint_history": [{"sprint_number": 1, "tasks": []}],
                                   "week0_baseline": {"kpis": {"sprint_kpis": []}}}}
    freeze_sprint_baseline(payload, 1)
    kpi = project_management_kpis(payload)["kpis"]["sprint_kpis"][0]
    assert kpi["baseline_sp"] == 0
    assert kpi["completion_percent"] is None
    assert kpi["status"] == "UNAVAILABLE"


def test_close_approval_creates_one_persistent_proposal_and_retry_is_idempotent(tmp_path, monkeypatch):
    approved, tables = _approved()
    approved["_planning_state"]["completed_through_sprint"] = 0
    approved["_planning_state"]["actual_completed_tasks"] = {}
    repository = CatalogRepository(tmp_path / "catalog.sqlite3", seed_tables=tables)
    store = PlanDraftStore(repository_provider=lambda: repository)
    store.replace_approved(approved)
    monkeypatch.setattr(api, "get_catalog_repository", lambda: repository)
    monkeypatch.setattr(api, "plan_drafts", store)
    client = TestClient(api.app)
    close = client.post("/api/replanning/close-sprint", json={
        "sprint_number": 1, "all_completed": True, "results": []})
    assert close.status_code == 200, close.text
    workflow_id = close.json()["workflow_id"]
    first = client.post(f"/api/replanning/drafts/{workflow_id}/approve")
    assert first.status_code == 200, first.text
    assert first.json()["sprint_expansion"]["status"] == "OPEN"
    again = client.post(f"/api/replanning/drafts/{workflow_id}/approve")
    assert again.status_code == 200, again.text
    assert again.json()["sprint_expansion"]["workflow_id"] == first.json()["sprint_expansion"]["workflow_id"]
    assert len(repository.approved_plan_history()) == 2


def test_sequential_apply_matches_apply_all_for_compatible_additions(tmp_path, monkeypatch):
    def run(name, sequential):
        approved, tables = _approved()
        repository = CatalogRepository(tmp_path / f"{name}.sqlite3", seed_tables=tables)
        store = PlanDraftStore(repository_provider=lambda: repository)
        store.replace_approved(approved)
        monkeypatch.setattr(api, "get_catalog_repository", lambda: repository)
        monkeypatch.setattr(api, "plan_drafts", store)
        proposal = api.refresh_sprint_expansion()["proposal"]
        if sequential:
            for row in proposal["recommendations"]:
                api.select_sprint_expansion(row["recommendation_id"], "apply")
        else:
            api.apply_all_sprint_expansion()
        result = api.approve_sprint_expansion()["approved"]
        return {row["task_id"]: (row["planned_sprints"], row["assigned_team_id"],
                                 row["allocations"]) for row in result["tasks"]}, result["summary"]

    assert run("sequential", True) == run("all", False)


def test_stale_availability_rejects_apply_without_changing_approved(tmp_path, monkeypatch):
    approved, tables = _approved()
    repository = CatalogRepository(tmp_path / "catalog.sqlite3", seed_tables=tables)
    store = PlanDraftStore(repository_provider=lambda: repository)
    store.replace_approved(approved)
    monkeypatch.setattr(api, "get_catalog_repository", lambda: repository)
    monkeypatch.setattr(api, "plan_drafts", store)
    api.refresh_sprint_expansion()
    original = repository.load_approved_plan()
    repository.save_availability_constraints([{"engineer_id": "E1", "sprint": 2,
                                              "availability_rate": .5}])
    with pytest.raises(Exception):
        api.apply_all_sprint_expansion()
    assert repository.load_approved_plan() == original


def test_closed_sprint_six_never_creates_sprint_seven():
    approved, tables = _approved()
    approved["_planning_state"]["completed_through_sprint"] = 6
    proposal = build_proposal(approved, tables, catalog_revision=1,
                              availability_constraints=[], workflow_id="close-6")
    assert proposal["status"] == "QUARTER_COMPLETE"
    assert proposal["recommendations"] == []
