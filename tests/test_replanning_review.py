from __future__ import annotations

from pathlib import Path

from src.web.replanning import _snapshot_diff


FRONTEND = Path("frontend")


def _task(
    task_id: str,
    *,
    status: str = "PLANNED",
    team: str = "Team-A",
    sprints: tuple[int, ...] = (2,),
    allocations: list[dict] | None = None,
    sp: float = 2,
    **metadata,
) -> dict:
    return {
        "task_id": task_id,
        "title": task_id,
        "status": status,
        "assigned_team_id": team,
        "planned_sprints": list(sprints),
        "completion_sprint": max(sprints, default=None),
        "sp_load_by_sprint": {str(sprint): sp for sprint in sprints},
        "allocations": allocations or [],
        **metadata,
    }


def _allocation(
    engineer: str,
    hours: float,
    *,
    sprint: int = 2,
    source: str = "Team-A",
    owner: str = "Team-A",
    cross_team: bool = False,
) -> dict:
    return {
        "sprint": sprint,
        "engineer_id": engineer,
        "role": "Backend",
        "source_team_id": source,
        "assigned_task_team_id": owner,
        "allocated_hours": hours,
        "cross_team": cross_team,
    }


def test_global_navigation_has_no_replanning_destination_but_view_remains() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    navigation = html[html.index('<nav class="nav"'):html.index("</nav>")]

    assert 'data-page="recalc"' not in navigation
    assert "Пересчёт плана" not in navigation
    assert 'id="page-recalc"' in html


def test_replanning_route_is_guarded_and_pending_review_has_contextual_entry() -> None:
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "page === 'recalc' && data.ready && !hasPendingReplan()" in source
    assert "show('current-sprint', 'replace')" in source
    assert "Требуется подтверждение факта спринта" in source
    assert 'data-page="recalc">Открыть проверку' in source
    assert "data.replanWorkflow = {workflow_id: workflowId, status: 'APPROVED'}" in source
    assert "show('current-sprint')" in source


def test_close_sprint_navigation_requires_review_for_new_workflows() -> None:
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    close_flow = source[
        source.index("async function closeCurrentSprint"):
        source.index("function renderStar")
    ]

    fast_path = close_flow.index("result.status === 'SPRINT_CLOSED'")
    current_sprint = close_flow.index("show('current-sprint')", fast_path)
    review = close_flow.index("show('recalc')", current_sprint)
    assert fast_path < current_sprint < review


def test_legacy_replanning_input_is_removed() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")

    assert "Загрузите фактические результаты" not in html
    assert "submitReplanningFile" not in source
    assert "selectedFactFile" not in source
    assert "chooseFact" not in source
    assert "runDemo" not in source


def test_normal_planned_completion_is_not_an_affected_element() -> None:
    before = {"tasks": [_task("DONE", sprints=(1,))]}
    after = {
        "tasks": [],
        "_planning_state": {"actual_completed_tasks": {"DONE": 1}},
    }

    diff = _snapshot_diff(before, after, closed_sprint=1)

    assert diff["affected_elements"] == []
    assert diff["changed_count"] == 0
    assert "DONE" in diff["unchanged_tasks"]


def test_business_equivalent_task_ignores_order_and_technical_metadata() -> None:
    allocations = [_allocation("E-1", 20), _allocation("E-2", 20)]
    before_task = _task("SAME", sprints=(2, 3), allocations=allocations, generated_at="old", revision=1)
    after_task = _task(
        "SAME",
        sprints=(3, 2),
        allocations=list(reversed(allocations)),
        generated_at="new",
        revision=99,
    )
    after_task["sp_load_by_sprint"]["2"] += 0.0000001
    before = {"tasks": [before_task]}
    after = {"tasks": [after_task]}

    diff = _snapshot_diff(before, after, closed_sprint=1)

    assert diff["affected_elements"] == []
    assert diff["summary"]["has_changes"] is False


def test_canonical_affected_elements_cover_every_manager_visible_change() -> None:
    before = {"tasks": [
        _task("UNCHANGED", allocations=[_allocation("E-0", 20)]),
        _task("SPRINT", sprints=(2,), allocations=[_allocation("E-1", 20)]),
        _task("TEAM", team="Team-A", allocations=[_allocation("E-2", 20)]),
        _task("PEOPLE", allocations=[_allocation("Anna", 20), _allocation("Ivan", 20)]),
        _task("HOURS", allocations=[_allocation("Ivan", 40)]),
        _task("ADD", status="MOVED", sprints=(), allocations=[]),
        _task("REMOVE", sprints=(3,), allocations=[_allocation("E-3", 20, sprint=3)]),
    ]}
    after = {"tasks": [
        _task("UNCHANGED", allocations=[_allocation("E-0", 20)]),
        _task("SPRINT", sprints=(3,), allocations=[_allocation("E-1", 20, sprint=3)]),
        _task("TEAM", team="Team-B", allocations=[_allocation("E-2", 20, owner="Team-B")]),
        _task("PEOPLE", allocations=[
            _allocation("Ivan", 20),
            _allocation("Petr", 20, source="Platform", cross_team=True),
        ]),
        _task("HOURS", allocations=[_allocation("Ivan", 20)]),
        _task("ADD", status="PLANNED", sprints=(4,), allocations=[_allocation("E-4", 20, sprint=4)]),
        _task("REMOVE", status="MOVED", sprints=(), allocations=[]),
    ]}

    diff = _snapshot_diff(before, after, closed_sprint=1)
    affected = {row["task_id"]: row for row in diff["affected_elements"]}

    assert set(affected) == {"SPRINT", "TEAM", "PEOPLE", "HOURS", "ADD", "REMOVE"}
    assert "SPRINT_CHANGED" in affected["SPRINT"]["fields"]
    assert "TEAM_CHANGED" in affected["TEAM"]["fields"]
    assert {"ENGINEERS_CHANGED", "CROSS_TEAM_CHANGED", "ALLOCATION_CHANGED"}.issubset(affected["PEOPLE"]["fields"])
    assert "HOURS_CHANGED" in affected["HOURS"]["fields"]
    assert "ADDED_TO_REMAINING_QUARTER" in affected["ADD"]["fields"]
    assert "REMOVED_FROM_REMAINING_QUARTER" in affected["REMOVE"]["fields"]
    assert "UNCHANGED" in diff["unchanged_tasks"]
    assert [row["kind"] for row in affected["PEOPLE"]["allocation_changes"]] == ["REMOVED", "ADDED"]
    assert sum(row["task_id"] == "PEOPLE" for row in diff["affected_elements"]) == 1


def test_review_ui_uses_one_canonical_changed_task_list_without_unchanged_summary() -> None:
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    review = source[source.index("function renderReplanWorkflow"):source.index("async function retryReplan")]

    assert "diff.affected_elements || diff.changed_tasks" in review
    assert "changes.map(replanChangeCard)" in review
    assert "Внимание, следующие элементы будут затронуты:" in review
    assert "Изменений в плане не требуется" in review
    assert "unchanged_tasks" not in review
    assert "replan-summary" not in review
    assert "affected_team_ids" not in review
