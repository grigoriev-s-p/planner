from __future__ import annotations

from copy import deepcopy

import pytest

from src.web import api as web_api
from src.web.api import PlanDraftStore
from src.web.catalog import CatalogRepository
from src.web.constructor import build_constructor_plan, scenario_tables
from src.web.replanning import _snapshot_diff
from tests.test_replanning_workflow import approved_plan, scenario


def _facts() -> list[dict]:
    return [
        {"task_id": "A", "task_completed": True},
        {
            "task_id": "C",
            "task_completed": False,
            "remaining_role_hours": {"Backend": 40},
            "incomplete_reason": "Не хватило времени",
        },
    ]


def test_close_is_idempotent_and_keeps_previous_approved_until_review() -> None:
    store = PlanDraftStore()
    approved = approved_plan(store)

    first = store.close_sprint(1, _facts())
    second = store.close_sprint(1, _facts())

    assert first["workflow_id"] == second["workflow_id"]
    assert first["status"] == "READY_FOR_REVIEW"
    assert first["actual_persisted"] is True
    assert store.approved() == approved


def test_invalid_actual_is_rejected_before_persistence() -> None:
    store = PlanDraftStore()
    approved_plan(store)

    with pytest.raises(ValueError, match="Неизвестные task_id"):
        store.close_sprint(1, [{"task_id": "UNKNOWN", "task_completed": True}])

    assert store.replan_workflow() is None


def test_approve_persists_exact_reviewed_draft_with_provenance() -> None:
    store = PlanDraftStore()
    approved_plan(store)
    workflow = store.close_sprint(1, _facts())
    reviewed = deepcopy(workflow["draft_plan"])

    saved = store.approve_replanning(workflow["workflow_id"])

    assert saved["tasks"] == reviewed["tasks"]
    assert saved["plan"] == reviewed["plan"]
    assert saved["revision_context"]["trigger"] == "REPLAN_APPROVE"
    assert saved["revision_context"]["workflow_id"] == workflow["workflow_id"]
    assert store.replan_workflow(workflow["workflow_id"])["status"] == "APPROVED"


def test_actual_survives_planner_failure_and_retry(monkeypatch) -> None:
    store = PlanDraftStore()
    approved_plan(store)
    original = web_api.build_remaining_horizon_plan

    def fail(*_args, **_kwargs):
        raise RuntimeError("optimizer unavailable")

    monkeypatch.setattr(web_api, "build_remaining_horizon_plan", fail)
    failed = store.close_sprint(1, _facts())

    assert failed["status"] == "REPLANNING_FAILED"
    assert failed["actual_persisted"] is True
    assert failed["actual"]["results"] == _facts()
    assert store.approved()["_planning_state"]["completed_through_sprint"] == 0

    monkeypatch.setattr(web_api, "build_remaining_horizon_plan", original)
    retried = store.retry_replanning(failed["workflow_id"])
    assert retried["status"] == "READY_FOR_REVIEW"
    assert retried["actual"] == failed["actual"]


def test_pending_replan_and_actual_survive_restart(tmp_path) -> None:
    repository = CatalogRepository(
        tmp_path / "catalog.sqlite3", seed_tables=scenario_tables(scenario())
    )
    first = PlanDraftStore(repository_provider=lambda: repository)
    draft = first.create(build_constructor_plan(scenario()))
    approved = first.approve(draft["draft"]["id"])
    workflow = first.close_sprint(1, _facts())

    restarted = PlanDraftStore(repository_provider=lambda: repository)
    restored = restarted.replan_workflow(workflow["workflow_id"])

    assert restored["status"] == "READY_FOR_REVIEW"
    assert restored["actual"] == workflow["actual"]
    assert restarted.approved()["approved_snapshot"]["revision"] == approved["approved_snapshot"]["revision"]

    saved = restarted.approve_replanning(workflow["workflow_id"])
    after_approval = PlanDraftStore(repository_provider=lambda: repository)
    assert after_approval.approved()["approved_snapshot"]["revision"] == saved["approved_snapshot"]["revision"]
    assert after_approval.replan_workflow(workflow["workflow_id"])["status"] == "APPROVED"


def test_stale_replan_draft_cannot_replace_a_newer_approved_plan() -> None:
    store = PlanDraftStore()
    approved = approved_plan(store)
    workflow = store.close_sprint(1, _facts())
    newer = deepcopy(approved)
    newer["plan"]["quarter"]["plan_version"] = "concurrent-change"
    store.replace_approved(newer)

    with pytest.raises(RuntimeError, match="устарел"):
        store.approve_replanning(workflow["workflow_id"])


def test_replan_diff_contains_resource_sp_and_affected_entity_details() -> None:
    store = PlanDraftStore()
    approved_plan(store)
    diff = store.close_sprint(1, _facts())["diff"]

    assert diff["closed_sprint"] == 1
    assert diff["changed_count"] > 0
    assert diff["summary"]["changed_tasks"] == diff["changed_count"]
    assert isinstance(diff["affected_team_ids"], list)
    assert isinstance(diff["affected_engineer_ids"], list)
    changed = next(item for item in diff["changed_tasks"] if item["task_id"] == "C")
    assert changed["before"]["allocations"]
    assert "sp_load_by_sprint" in changed["after"]
    assert "total_hours" in changed["after"]


def test_diff_classifies_added_removed_team_engineer_and_cross_team_changes() -> None:
    before = {
        "tasks": [
            {"task_id": "REM", "status": "PLANNED", "assigned_team_id": "A", "planned_sprints": [2], "sp_load_by_sprint": {"2": 2}, "allocations": []},
            {"task_id": "ADD", "status": "MOVED", "assigned_team_id": "A", "planned_sprints": [], "sp_load_by_sprint": {}, "allocations": []},
            {"task_id": "MOVE", "status": "PLANNED", "assigned_team_id": "A", "planned_sprints": [3], "completion_sprint": 3, "sp_load_by_sprint": {"3": 2}, "allocations": [{"sprint": 3, "engineer_id": "E-1", "role": "Backend", "source_team_id": "A", "assigned_task_team_id": "A", "allocated_hours": 40}]},
            {"task_id": "DONE", "status": "PLANNED", "assigned_team_id": "A", "planned_sprints": [1], "sp_load_by_sprint": {"1": 1}, "allocations": []},
        ]
    }
    after = {
        "tasks": [
            {"task_id": "REM", "status": "MOVED", "assigned_team_id": "A", "planned_sprints": [], "sp_load_by_sprint": {}, "allocations": []},
            {"task_id": "ADD", "status": "PLANNED", "assigned_team_id": "A", "planned_sprints": [2], "sp_load_by_sprint": {"2": 1}, "allocations": []},
            {"task_id": "MOVE", "status": "PLANNED", "assigned_team_id": "B", "planned_sprints": [2], "completion_sprint": 2, "sp_load_by_sprint": {"2": 2}, "allocations": [{"sprint": 2, "engineer_id": "E-2", "role": "Backend", "source_team_id": "C", "assigned_task_team_id": "B", "allocated_hours": 20, "cross_team": True}]},
        ],
        "_planning_state": {"actual_completed_tasks": {"DONE": 1}},
    }

    changes = {row["task_id"]: row for row in _snapshot_diff(before, after, 1)["changed_tasks"]}

    assert "REMOVED_FROM_REMAINING_QUARTER" in changes["REM"]["fields"]
    assert "ADDED_TO_REMAINING_QUARTER" in changes["ADD"]["fields"]
    assert {"TEAM_CHANGED", "SPRINT_CHANGED", "ENGINEERS_CHANGED", "CROSS_TEAM_CHANGED"}.issubset(changes["MOVE"]["fields"])
    assert "DONE" not in changes


def test_frontend_exposes_review_retry_and_explicit_approve_controls() -> None:
    source = (web_api.FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    api_source = (web_api.FRONTEND_DIR / "api.js").read_text(encoding="utf-8")

    assert "data-approve-replan" in source
    assert "data-retry-replan" in source
    assert "hydrate(result);" not in source[source.index("async function closeCurrentSprint"):source.index("function renderStar")]
    assert "getReplanDraft" in api_source
    assert "approveReplanDraft" in api_source
