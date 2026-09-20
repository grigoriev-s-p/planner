from __future__ import annotations

from pathlib import Path

from src.planning import PlanStatus
from src.planning.reasons import ReasonCode
from src.web.service import PlanningAPIService
from src.web.recommendations import RecommendationDraft
from tests.acceptance.builders import SyntheticQuarter, result_by_id


ROOT = Path(__file__).resolve().parents[1]


def _service(tasks: list[dict], effort: list[dict], engineers: list[dict]) -> PlanningAPIService:
    return PlanningAPIService.from_tables({
        "tasks": tasks,
        "task_role_effort": effort,
        "task_dependencies": [],
        "engineers": engineers,
        "engineer_skills": [],
        "team_history": [{"team_id": "Team-A", "velocity_achieved": 1.25}],
    })


def _task(task_id: str, initiative_id: str, rung: float, *, role: str = "Backend") -> tuple[dict, dict]:
    return (
        {
            "task_id": task_id,
            "summary": task_id,
            "initiative_id": initiative_id,
            "team_id": "Team-A",
            "story_points": 1,
            "status": "ToDo",
            "rung": rung,
            "is_placeholder": False,
        },
        {"task_id": task_id, "role": role, "required_hours": 1},
    )


def test_t01_story_points_are_visible_in_selection_preview_board_and_detail() -> None:
    constructor = (ROOT / "frontend" / "constructor.js").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    assert "R${esc(task.rung)} · ${number(task.story_points)} SP" in constructor
    assert "Всего задачи: ${number(task.story_points)} SP" in constructor
    assert "В Sprint ${sprint}: ${number(sprintSp)} SP" in constructor
    assert "Часы в спринте: ${number(sprintHours)} ч" in constructor
    assert "Всего: ${number(task.story_points)} SP" in app
    assert "Sprint ${sprint}: ${number(sprintSp)} SP" in app
    assert "Story Points задачи" in app
    assert "Нагрузка Sprint ${selectedSprint}" in app


def test_t02_kpi_uses_all_ten_initial_initiatives_when_only_four_complete() -> None:
    rows = [
        _task("A", "A", 100),
        _task("B", "B", 99),
        _task("C", "C", 98),
        _task("D", "D", 97),
        _task("E-1", "E", 96),
        _task("F-1", "F", 95),
        _task("E-2", "E", 1),
        _task("F-2", "F", 1),
        _task("G", "G", 0),
        _task("H", "H", 0),
        _task("I", "I", 0),
        _task("J", "J", 0),
    ]
    service = _service(
        [task for task, _ in rows],
        [effort for _, effort in rows],
        [{"engineer_id": "E-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1}],
    )

    assert len(service.plan.completed_initiative_ids) == 4
    assert service.kpis["expected_kpi"] == {
        "completed_initiatives": 4.0,
        "baseline_initiatives": 10.0,
        "incomplete_initiatives": 6.0,
        "completion_percent": 40.0,
    }


def test_t03_fully_excluded_initiative_remains_in_denominator() -> None:
    rows = [_task("A", "A", 3), _task("B", "B", 2), _task("C", "C", 1, role="Project Manager")]
    service = _service(
        [task for task, _ in rows],
        [effort for _, effort in rows],
        [{"engineer_id": "E-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1}],
    )

    expected = service.kpis["expected_kpi"]
    assert expected["completed_initiatives"] == 2
    assert expected["baseline_initiatives"] == 3
    assert expected["completion_percent"] == 2 / 3 * 100


def test_t10_t11_missing_project_manager_has_no_role_fallback() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A")
        .engineer("SENIOR-BE", "Team-A", "Backend")
        .task("PM-TASK", initiative_id="PM", role_hours={"Project Manager": 40})
    )

    result = result_by_id(quarter.plan(), "PM-TASK")

    assert result.status == PlanStatus.INELIGIBLE_DATA_CONSTRAINT
    assert ReasonCode.NO_AVAILABLE_ROLE_SPECIALIST in {reason.code for reason in result.final_reasons}
    assert result.allocations == ()


def test_t12_exact_cross_team_project_manager_can_help() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A")
        .team("Team-B")
        .engineer("LOCAL-BE", "Team-A", "Backend")
        .engineer("EXTERNAL-PM", "Team-B", "Project Manager")
        .task(
            "CROSS-PM",
            initiative_id="PM",
            team_id="Team-A",
            role_hours={"Backend": 80, "Project Manager": 40},
        )
    )

    result = result_by_id(quarter.plan(), "CROSS-PM")

    assert result.status == PlanStatus.PLANNED
    assert any(
        allocation.engineer_id == "EXTERNAL-PM" and allocation.cross_team
        for sprint in result.allocations
        for allocation in sprint.engineer_allocations
    )


def test_t13_missing_role_everywhere_stays_a_visible_planning_risk() -> None:
    quarter = (
        SyntheticQuarter()
        .team("Team-A")
        .team("Team-B")
        .engineer("BE-A", "Team-A", "Backend")
        .engineer("BE-B", "Team-B", "Backend")
        .task("NO-PM", initiative_id="PM", role_hours={"Project Manager": 40})
    )

    result = result_by_id(quarter.plan(), "NO-PM")

    assert result.status == PlanStatus.INELIGIBLE_DATA_CONSTRAINT
    assert result.recommendations == ()
    reason = next(
        reason
        for reason in result.final_reasons
        if reason.code == ReasonCode.NO_AVAILABLE_ROLE_SPECIALIST
    )
    assert reason.details["role"] == "Project Manager"

    task, effort = _task("NO-PM", "PM", 1, role="Project Manager")
    service = _service(
        [task],
        [effort],
        [
            {"engineer_id": "BE-A", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1},
            {"engineer_id": "BE-B", "team_id": "Team-B", "role": "Backend", "capacity_rate": 1},
        ],
    )
    draft = RecommendationDraft(service.dashboard)
    assert draft.recommendations == {}
    assert any(
        risk.get("object") == "NO-PM" and "Project Manager" in risk.get("cause", "")
        for risk in service.risks
    )
