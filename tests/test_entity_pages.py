from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient

from src.web.api import app
from src.web.api import PlanDraftStore
from src.web.catalog import CatalogRepository
from src.web.constructor import build_constructor_plan, scenario_tables
from src.web.entity_projections import (
    build_engineer_history,
    build_engineer_projection,
    build_team_history,
    build_team_projection,
)
from src.web.replanning import build_remaining_horizon_plan
from tests.test_replanning_workflow import scenario


def _approved_history() -> tuple[dict, list[dict]]:
    store = PlanDraftStore()
    draft = store.create(build_constructor_plan(scenario()))
    approved = store.approve(draft["draft"]["id"])
    return approved, store.history()


def test_team_projection_uses_approved_plan_and_separates_sp_from_role_hours() -> None:
    approved, history = _approved_history()

    team = build_team_projection(approved, history, "Team-A")

    assert team is not None
    assert team["identity"] == {"team_id": "Team-A", "name": "A"}
    assert team["approved_revision"] is None
    assert team["source"] == "LATEST_APPROVED_PLAN"
    assert team["capacity"]["sp"] == {"used": 2.8, "capacity": 8.0}
    assert team["capacity"]["roles"] == [
        {"role": "Backend", "used_hours": 80.0, "capacity_hours": 80.0}
    ]
    assert {item["task_id"] for item in team["current_tasks"]} == {"A", "C"}
    assert all(item["participation_type"] == "OWNER" for item in team["current_tasks"])
    assert team["members"][0]["engineer_id"] == "E-1"
    star_team = next(item for item in approved["star_map"]["teams"] if item["team_id"] == "Team-A")
    assert team["competencies"] == star_team["competencies"]


def test_team_projection_marks_cross_team_work_without_moving_owner_sp() -> None:
    approved, history = _approved_history()
    dashboard = deepcopy(approved)
    dashboard["teams"].append({
        "team_id": "Team-B",
        "name": "B",
        "member_ids": ["E-2"],
        "roles": ["Backend"],
        "sp_by_sprint": {"1": {"capacity": 8, "used": 0, "remaining": 8}},
    })
    dashboard["engineers"].append({
        "engineer_id": "E-2",
        "name": "Helper",
        "roles": ["Backend"],
        "team_ids": ["Team-B"],
        "capacity_buckets": [{"team_id": "Team-B", "capacity_rate": 1, "capacity_hours": 80}],
        "skills": [],
        "allocations": [{
            "task_id": "A", "engineer_id": "E-2", "role": "Backend",
            "source_team_id": "Team-B", "assigned_task_team_id": "Team-A",
            "sprint": 1, "allocated_hours": 4, "cross_team": True,
        }],
    })
    dashboard["tasks"][0]["allocations"].append({
        "engineer_id": "E-2", "role": "Backend", "source_team_id": "Team-B",
        "assigned_task_team_id": "Team-A", "sprint": 1,
        "allocated_hours": 4, "cross_team": True,
    })
    dashboard["_planning_state"]["tables"]["engineers"].append({
        "engineer_id": "E-2", "team_id": "Team-B", "role": "Backend",
        "capacity_rate": 1,
    })

    owner = build_team_projection(dashboard, history, "Team-A")
    helper = build_team_projection(dashboard, history, "Team-B")

    assert owner is not None and helper is not None
    assert owner["capacity"]["sp"]["used"] == 2.8
    assert len(owner["cross_team"]["received"]) == 1
    assert owner["cross_team"]["received"][0]["task_id"] == "A"
    assert owner["cross_team"]["received"][0]["planned_hours"] == 4
    assert helper["capacity"]["sp"]["used"] == 0
    assert helper["cross_team"]["provided"][0]["task_id"] == "A"
    assert helper["cross_team"]["provided"][0]["planned_hours"] == 4


def test_team_history_uses_actual_result_and_keeps_unfinished_reason() -> None:
    approved, _ = _approved_history()
    replanned = build_remaining_horizon_plan(approved, 1, [
        {"task_id": "A", "task_completed": True},
        {
            "task_id": "C",
            "task_completed": False,
            "remaining_role_hours": {"Backend": 40},
            "incomplete_reason": "Не хватило времени",
        },
    ])
    history = build_team_history([approved, replanned], "Team-A")

    assert len(history["closed_sprints"]) == 1
    sprint = history["closed_sprints"][0]
    assert {item["task_id"] for item in sprint["completed_tasks"]} == {"A"}
    assert {item["task_id"] for item in sprint["unfinished_tasks"]} == {"C"}
    unfinished = sprint["unfinished_tasks"][0]["outcome"]
    assert unfinished["incomplete_reason"] == "Не хватило времени"
    assert unfinished["remaining_role_hours"] == {"Backend": 40.0}
    assert unfinished["actual_hours"] is None


def test_team_page_has_stable_route_and_tabs() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (root / "frontend" / "app.js").read_text(encoding="utf-8")

    assert 'id="page-team-detail"' in html
    assert 'data-team-detail-tab="history"' in html
    assert "return `/teams/${encodeURIComponent(state.teamId)}`" in script
    assert "getTeamHistory(teamId)" in script


def test_team_history_reports_owner_reassignment_between_approved_revisions() -> None:
    approved, _ = _approved_history()
    approved["approved_snapshot"] = {"revision": 1}
    changed = deepcopy(approved)
    changed["approved_snapshot"] = {"revision": 2}
    changed["_planning_state"]["completed_through_sprint"] = 1
    task = next(item for item in changed["tasks"] if item["task_id"] == "C")
    task["assigned_team_id"] = "Team-B"

    history = build_team_history([approved, changed], "Team-A")

    assert history["closed_sprints"][0]["ownership_changes"] == [{
        "task_id": "C",
        "title": "Remaining",
        "from_team_id": "Team-A",
        "to_team_id": "Team-B",
        "direction": "TRANSFERRED_OUT",
    }]


def test_team_history_separates_provided_and_received_cross_team_help() -> None:
    approved, _ = _approved_history()
    approved["approved_snapshot"] = {"revision": 1}
    task = next(item for item in approved["tasks"] if item["task_id"] == "A")
    task["allocations"].append({
        "engineer_id": "E-2", "role": "Backend", "source_team_id": "Team-B",
        "assigned_task_team_id": "Team-A", "sprint": 1,
        "allocated_hours": 4, "cross_team": True,
    })
    after = deepcopy(approved)
    after["approved_snapshot"] = {"revision": 2}
    after["_planning_state"]["completed_through_sprint"] = 1
    after["_planning_state"]["actual_completed_tasks"] = {"A": 1}

    owner = build_team_history([approved, after], "Team-A")["closed_sprints"][0]
    helper = build_team_history([approved, after], "Team-B")["closed_sprints"][0]

    assert owner["cross_team_received"][0]["planned_hours"] == 4
    assert helper["cross_team_provided"][0]["planned_hours"] == 4
    assert helper["owner_tasks"] == []


def test_engineer_projection_has_stable_identity_memberships_and_planned_work() -> None:
    approved, history = _approved_history()

    engineer = build_engineer_projection(approved, history, "E-1")

    assert engineer is not None
    assert engineer["identity"] == {"engineer_id": "E-1", "name": "Engineer"}
    assert engineer["source"] == "LATEST_APPROVED_PLAN"
    assert engineer["memberships"] == [{
        "team_id": "Team-A", "capacity_rate": 1.0, "capacity_hours": 80.0,
        "planned_hours": 80.0, "cross_team_planned_hours": 0,
    }]
    assert {item["task_id"] for item in engineer["current_allocations"]} == {"A", "C"}
    assert {item["task_id"] for item in engineer["future_allocations"]} == {"B", "C"}
    assert all(item["hours_semantics"] == "PLANNED" for item in engineer["current_allocations"])
    star_engineer = next(item for item in approved["star_map"]["engineers"] if item["engineer_id"] == "E-1")
    assert engineer["roles"] == star_engineer["roles"]
    assert engineer["skills"] == star_engineer["skills"]


def test_engineer_projection_infers_cross_team_from_source_and_owner() -> None:
    approved, history = _approved_history()
    dashboard = deepcopy(approved)
    task = next(item for item in dashboard["tasks"] if item["task_id"] == "A")
    task["assigned_team_id"] = "Team-B"
    task["allocations"][0]["assigned_task_team_id"] = "Team-B"
    task["allocations"][0]["cross_team"] = False
    allocation = next(item for item in dashboard["engineers"][0]["allocations"] if item["task_id"] == "A")
    allocation["assigned_task_team_id"] = "Team-B"
    allocation["cross_team"] = False

    engineer = build_engineer_projection(dashboard, history, "E-1")

    assert engineer is not None
    row = next(item for item in engineer["current_allocations"] if item["task_id"] == "A")
    assert row["participation_type"] == "CROSS_TEAM"
    assert row["source_team_id"] == "Team-A"
    assert row["owner_team_id"] == "Team-B"


def test_engineer_projection_keeps_membership_capacity_buckets_separate() -> None:
    approved, history = _approved_history()
    dashboard = deepcopy(approved)
    profile = dashboard["engineers"][0]
    profile["capacity_buckets"][0]["capacity_rate"] = 0.5
    profile["capacity_buckets"][0]["capacity_hours"] = 40
    profile["team_ids"].append("Team-B")
    profile["capacity_buckets"].append({
        "team_id": "Team-B", "capacity_rate": 0.5, "capacity_hours": 40,
    })
    star_profile = dashboard["star_map"]["engineers"][0]
    star_profile["memberships"][0]["capacity_rate"] = 0.5
    star_profile["memberships"].append({"team_id": "Team-B", "capacity_rate": 0.5})

    engineer = build_engineer_projection(dashboard, history, "E-1")

    assert engineer is not None
    assert [(row["team_id"], row["capacity_hours"]) for row in engineer["memberships"]] == [
        ("Team-A", 40), ("Team-B", 40),
    ]
    assert engineer["capacity_hours"] == 80.0


def test_engineer_history_labels_plan_as_planned_and_uses_task_actual_outcome() -> None:
    approved, _ = _approved_history()
    replanned = build_remaining_horizon_plan(approved, 1, [
        {"task_id": "A", "task_completed": True},
        {
            "task_id": "C", "task_completed": False,
            "remaining_role_hours": {"Backend": 40},
            "incomplete_reason": "Изменились приоритеты",
        },
    ])

    history = build_engineer_history([approved, replanned], "E-1")

    sprint = history["closed_sprints"][0]
    assert {item["task_id"] for item in sprint["participations"]} == {"A", "C"}
    assert all(item["hours_semantics"] == "PLANNED" for item in sprint["participations"])
    by_task = {item["task_id"]: item for item in sprint["participations"]}
    assert by_task["A"]["outcome"]["status"] == "COMPLETED"
    assert by_task["C"]["outcome"]["status"] == "UNFINISHED"
    assert by_task["C"]["outcome"]["incomplete_reason"] == "Изменились приоритеты"
    assert by_task["C"]["outcome"]["actual_hours"] is None
    assert by_task["C"]["planned_hours"] == 72.0
    assert sprint["allocation_changes"]


def test_engineer_page_has_stable_route_and_tabs() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    script = (root / "frontend" / "app.js").read_text(encoding="utf-8")

    assert 'id="page-engineer-detail"' in html
    assert 'data-engineer-detail-tab="history"' in html
    assert "return `/engineers/${encodeURIComponent(state.engineerId)}`" in script
    assert "getEngineerHistory(engineerId)" in script
    assert "Плановое участие" in script
    assert "data.engineerDetails = {};" in script
    assert "data.teamDetails = {};" in script


def test_team_and_engineer_deep_links_serve_spa_shell() -> None:
    client = TestClient(app)

    team_response = client.get("/teams/Team-A")
    engineer_response = client.get("/engineers/E-1")

    assert team_response.status_code == 200
    assert engineer_response.status_code == 200
    assert 'id="page-team-detail"' in team_response.text
    assert 'id="page-engineer-detail"' in engineer_response.text


def test_entity_projections_survive_catalog_repository_restart() -> None:
    directory = Path.cwd() / f".entity_pages_test_{uuid4().hex}"
    directory.mkdir()
    try:
        repository = CatalogRepository(
            directory / "catalog.sqlite3", seed_tables=scenario_tables(scenario())
        )
        first_store = PlanDraftStore(repository_provider=lambda: repository)
        draft = first_store.create(build_constructor_plan(scenario()))
        first_store.approve(draft["draft"]["id"])

        restarted_store = PlanDraftStore(repository_provider=lambda: repository)
        restored = restarted_store.approved()
        assert restored is not None
        team = build_team_projection(restored, restarted_store.history(), "Team-A")
        engineer = build_engineer_projection(restored, restarted_store.history(), "E-1")

        assert team is not None and team["approved_revision"] == 1
        assert engineer is not None and engineer["approved_revision"] == 1
        assert team["identity"]["team_id"] == "Team-A"
        assert engineer["identity"]["engineer_id"] == "E-1"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_team_and_engineer_projections_follow_replanned_approved_state_and_keep_history() -> None:
    approved, _ = _approved_history()
    replanned = build_remaining_horizon_plan(approved, 1, [
        {"task_id": "A", "task_completed": True},
        {
            "task_id": "C", "task_completed": False,
            "remaining_role_hours": {"Backend": 40},
            "incomplete_reason": "Не хватило времени",
        },
    ])
    snapshots = [approved, replanned]

    team = build_team_projection(replanned, snapshots, "Team-A")
    engineer = build_engineer_projection(replanned, snapshots, "E-1")
    team_history = build_team_history(snapshots, "Team-A")
    engineer_history = build_engineer_history(snapshots, "E-1")

    assert team is not None and team["current_sprint"] == 2
    current_c = next(item for item in team["current_tasks"] if item["task_id"] == "C")
    assert current_c["remaining_role_hours"] == {"Backend": 40.0}
    assert all(item["task_id"] != "A" for item in team["current_tasks"])
    assert engineer is not None
    assert all(item["task_id"] != "A" for item in engineer["current_allocations"])
    assert team_history["closed_sprints"][0]["completed_tasks"][0]["task_id"] == "A"
    assert team_history["closed_sprints"][0]["unfinished_tasks"][0]["task_id"] == "C"
    assert {item["task_id"] for item in engineer_history["closed_sprints"][0]["participations"]} == {"A", "C"}
