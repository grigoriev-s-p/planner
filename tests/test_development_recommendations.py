from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.development import DevelopmentRecommendationService, skill_affinity
from src.web.api import app, plan_drafts


def engineer(
    engineer_id: str,
    *,
    role: str = "Backend",
    skills: tuple[str, ...] = (),
    team: str = "TEAM-A",
    used: float = 0,
    grade: str = "Middle",
    archived: bool = False,
) -> dict:
    return {
        "engineer_id": engineer_id,
        "name": engineer_id,
        "roles": [role],
        "skills": list(skills),
        "team_ids": [team],
        "used_hours_by_sprint": {str(sprint): used for sprint in range(1, 7)},
        "grade": grade,
        "archived": archived,
    }


def task(
    task_id: str = "T-1",
    *,
    role: str = "Backend",
    skill: str | None = "Kafka",
    team: str = "TEAM-A",
    status: str = "PLANNED",
) -> dict:
    planned = status == "PLANNED"
    return {
        "task_id": task_id,
        "title": f"Title {task_id}",
        "assigned_team_id": team,
        "original_team_id": team,
        "status": status,
        "current_status": status,
        "rung": 100,
        "planned_sprints": [1] if planned else [],
        "role_requirements": {role: 40},
        "role_hours_by_sprint": {"1": {role: 40}} if planned else {},
        "required_skills": ([{
            "technology": skill,
            "role": role,
            "source": "explicit",
            "confidence": "high",
            "confirmed": True,
        }] if skill else []),
    }


def dashboard(tasks: list[dict], engineers: list[dict], *, revision: int = 1) -> dict:
    teams: dict[str, dict] = {}
    for person in engineers:
        if person.get("archived"):
            continue
        for team_id in person.get("team_ids") or []:
            team = teams.setdefault(team_id, {"team_id": team_id, "competencies": []})
            for skill in person.get("skills") or []:
                competency = next((row for row in team["competencies"] if row["skill"] == skill), None)
                if competency is None:
                    competency = {"skill": skill, "bus_factor": 0, "engineers": []}
                    team["competencies"].append(competency)
                competency["engineers"].append({"engineer_id": person["engineer_id"]})
                competency["bus_factor"] = len(competency["engineers"])
    tables = {
        "engineers": [],
        "task_skill_requirements": [
            {"task_id": row["task_id"], **requirement}
            for row in tasks for requirement in row.get("required_skills") or []
        ],
    }
    return {
        "approved_snapshot": {"revision": revision},
        "plan": {"quarter": {"current_sprint": 1, "sprint_count": 6}},
        "tasks": tasks,
        "engineers": engineers,
        "star_map": {"teams": list(teams.values())},
        "_planning_state": {"tables": tables},
    }


def calculate(tasks: list[dict], engineers: list[dict]) -> dict:
    payload = dashboard(tasks, engineers)
    return DevelopmentRecommendationService(payload["_planning_state"]["tables"], payload).calculate()


def with_blockers(row: dict, *, roles: tuple[str, ...] = (), skills: tuple[str, ...] = ()) -> dict:
    row["reasons"] = [
        *[
            {"code": "MISSING_SPECIALIST", "details": {"role": role}}
            for role in roles
        ],
        *([{
            "code": "INSUFFICIENT_SKILL_CAPACITY",
            "details": {"role": "Backend", "missing_skills": list(skills)},
        }] if skills else []),
    ]
    return row


def by_category(result: dict, category: str) -> list[dict]:
    return [row for row in result["recommendations"] if row["category"] == category]


def test_unplanned_task_creates_separate_role_and_skill_requirements() -> None:
    blocked = with_blockers(
        task(status="UNPLANNABLE"),
        roles=("Руководитель проекта",),
        skills=("Kafka",),
    )
    result = calculate([blocked], [engineer("JAVA", skills=("Java",))])
    critical = by_category(result, "PLAN_CRITICAL")
    assert {(row["deficit"]["type"], row["deficit"].get("role") or row["deficit"].get("skill")) for row in critical} == {
        ("ROLE", "Руководитель проекта"),
        ("SKILL", "Kafka"),
    }
    assert all(row["demand_basis"] == "UNPLANNED_TASK_RESOURCE_BLOCKERS" for row in critical)


def test_unplanned_task_does_not_recommend_non_blocking_required_role() -> None:
    blocked = with_blockers(
        task(status="UNPLANNABLE", role="Аналитик", skill=None),
        roles=("Руководитель проекта", "Специалист поддержки"),
    )
    result = calculate([blocked], [engineer("ANALYST", role="Аналитик")])
    critical = by_category(result, "PLAN_CRITICAL")
    assert {row["deficit"]["role"] for row in critical} == {
        "Руководитель проекта",
        "Специалист поддержки",
    }
    assert all(row["deficit"]["role"] != "Аналитик" for row in critical)


def test_unplanned_requirements_do_not_calculate_headcount_hours_or_plan_impact() -> None:
    result = calculate([with_blockers(task(status="MOVED"), roles=("Backend",))], [engineer("JAVA", skills=("Java",))])
    assert result["calculation_mode"] == "COMPETENCY_LOOKUP_ONLY"
    assert "quarter_capacity_per_fte" not in result
    for row in result["recommendations"]:
        assert "required_hours" not in row["deficit"]
        assert "deficit_hours" not in row["deficit"]
        assert "hire_count" not in row["recommendation"]
        assert "residual_deficit_hours" not in row["recommendation"]


def test_non_resource_unplanned_task_does_not_create_hiring_advice() -> None:
    row = task(status="UNPLANNABLE")
    row["reasons"] = [{"code": "DEPENDENCY_NOT_COMPLETED"}]
    assert by_category(calculate([row], []), "PLAN_CRITICAL") == []


def test_same_requirement_aggregates_its_task_list() -> None:
    result = calculate([
        with_blockers(task("BLOCKED-1", status="UNPLANNABLE"), skills=("Kafka",)),
        with_blockers(task("BLOCKED-2", status="MOVED"), skills=("Kafka",)),
    ], [])
    kafka = next(row for row in by_category(result, "PLAN_CRITICAL") if row["deficit"].get("skill") == "Kafka")
    assert {row["task_id"] for row in kafka["tasks"]} == {"BLOCKED-1", "BLOCKED-2"}
    assert kafka["priority"]["covered_task_count"] == 2


def test_team_weakness_contains_only_demanded_bf_one_competencies() -> None:
    result = calculate(
        [task("KAFKA", skill="Kafka"), task("REACT", role="Frontend", skill="React")],
        [
            engineer("KAFKA-1", skills=("Kafka",)),
            engineer("REACT-1", role="Frontend", skills=("React",)),
            engineer("REACT-2", role="Frontend", skills=("React",)),
        ],
    )
    weaknesses = by_category(result, "TEAM_WEAKNESS")
    assert weaknesses
    assert all(row["deficit"]["bus_factor"] == 1 for row in weaknesses)
    assert all(row["deficit"].get("skill") != "React" for row in weaknesses)


def test_bf_one_role_and_skill_are_separate_cards_with_task_lists() -> None:
    result = calculate([task()], [engineer("HOLDER", role="Backend", skills=("Kafka",))])
    weaknesses = by_category(result, "TEAM_WEAKNESS")
    assert {(row["deficit"]["type"], row["deficit"].get("role") or row["deficit"].get("skill")) for row in weaknesses} == {
        ("ROLE", "Backend"),
        ("SKILL", "Kafka"),
    }
    assert all(row["tasks"][0]["task_id"] == "T-1" for row in weaknesses)


def test_candidates_are_ranked_for_training_without_capacity_prediction() -> None:
    result = calculate(
        [with_blockers(task(status="UNPLANNABLE"), skills=("Kafka",))],
        [
            engineer("BUSY-JAVA", skills=("Java",), used=40),
            engineer("IDLE-JAVA", skills=("Java",), used=0),
            engineer("UNRELATED", role="Designer", skills=("Figma",), used=0),
        ],
    )
    kafka = next(row for row in by_category(result, "PLAN_CRITICAL") if row["deficit"].get("skill") == "Kafka")
    candidates = kafka["recommendation"]["candidates"]
    assert [row["engineer_id"] for row in candidates[:2]] == ["IDLE-JAVA", "BUSY-JAVA"]
    assert "effective_quarter_hours" not in candidates[0]
    assert "covered_task_count" not in candidates[0]
    assert "role_plan" not in candidates[0]


def test_existing_holder_archived_and_other_team_are_not_candidates() -> None:
    result = calculate(
        [task()],
        [
            engineer("HOLDER", skills=("Kafka",)),
            engineer("CANDIDATE", skills=("Java",)),
            engineer("OTHER", skills=("Java",), team="TEAM-B"),
            engineer("ARCHIVED", skills=("Java",), archived=True),
        ],
    )
    kafka = next(row for row in by_category(result, "TEAM_WEAKNESS") if row["deficit"].get("skill") == "Kafka")
    assert [row["engineer_id"] for row in kafka["recommendation"]["candidates"]] == ["CANDIDATE"]


def test_hiring_is_an_unquantified_alternative_for_every_requirement() -> None:
    result = calculate([with_blockers(task(status="UNPLANNABLE"), roles=("Backend",))], [])
    for row in result["recommendations"]:
        assert row["recommendation"]["primary_action"] == "UPSKILL_OR_HIRE"
        assert row["recommendation"]["hire_profile"]["label"]
        assert "count" not in row["recommendation"]["hire_profile"]


def test_skill_affinity_uses_shared_skill_groups() -> None:
    affinity = skill_affinity("Kafka", {"Java", "RabbitMQ"})
    assert affinity is not None
    assert affinity.score > 0
    assert affinity.related_skills


def test_done_task_is_not_attached_to_catalog_bus_factor_weakness() -> None:
    result = calculate([task(status="DONE")], [engineer("HOLDER", skills=("Kafka",))])
    kafka_rows = [
        row for row in by_category(result, "TEAM_WEAKNESS")
        if row["deficit"].get("skill") == "Kafka"
    ]
    assert kafka_rows
    assert all(row["tasks"] == [] for row in kafka_rows)


def test_company_bus_factor_deduplicates_holders_across_teams() -> None:
    result = calculate(
        [],
        [
            engineer("A-KAFKA", skills=("Kafka", "Figma"), team="TEAM-A"),
            engineer("B-KAFKA", skills=("Kafka",), team="TEAM-B"),
        ],
    )
    weaknesses = by_category(result, "TEAM_WEAKNESS")
    local_kafka = [
        row for row in weaknesses
        if row["scope"] == "team" and row["deficit"].get("skill") == "Kafka"
    ]
    company_kafka = [
        row for row in weaknesses
        if row["scope"] == "company" and row["deficit"].get("skill") == "Kafka"
    ]
    company_figma = next(
        row for row in weaknesses
        if row["scope"] == "company" and row["deficit"].get("skill") == "Figma"
    )

    assert {row["team_id"] for row in local_kafka} == {"TEAM-A", "TEAM-B"}
    assert company_kafka == []
    assert company_figma["team_id"] == "__company__"
    assert company_figma["deficit"]["bus_factor"] == 1


def test_bf_one_skill_is_visible_even_without_quarter_demand() -> None:
    result = calculate([], [engineer("HOLDER", skills=("Rare Skill",))])
    skill_rows = [
        row for row in by_category(result, "TEAM_WEAKNESS")
        if row["deficit"].get("skill") == "Rare Skill"
    ]
    assert {row["scope"] for row in skill_rows} == {"team", "company"}
    assert all(row["tasks"] == [] for row in skill_rows)


def test_category_order_and_summary_are_stable() -> None:
    result = calculate(
        [with_blockers(task("BLOCKED", status="UNPLANNABLE"), roles=("QA",)), task("PLANNED")],
        [engineer("HOLDER", skills=("Kafka",)), engineer("CANDIDATE", skills=("Java",))],
    )
    categories = [row["category"] for row in result["recommendations"]]
    assert categories == sorted(categories, key=lambda value: value != "PLAN_CRITICAL")
    assert result["summary"] == {
        "total": len(result["recommendations"]),
        "plan_critical": len(by_category(result, "PLAN_CRITICAL")),
        "team_weakness": len(by_category(result, "TEAM_WEAKNESS")),
        "roles": sum(row["deficit"]["type"] == "ROLE" for row in result["recommendations"]),
        "skills": sum(row["deficit"]["type"] == "SKILL" for row in result["recommendations"]),
    }


def test_service_and_endpoint_do_not_mutate_plan() -> None:
    payload = dashboard([task()], [engineer("HOLDER", skills=("Kafka",))])
    before = deepcopy(payload)
    DevelopmentRecommendationService(payload["_planning_state"]["tables"], payload).calculate()
    assert payload == before
    with (
        patch.object(plan_drafts, "approved", return_value=deepcopy(payload)),
        patch("src.web.api._current_dashboard", return_value=deepcopy(payload)),
    ):
        response = TestClient(app).get("/api/development/recommendations")
    assert response.status_code == 200
    assert response.json()["read_only"] is True


def test_dashboard_does_not_calculate_development_recommendations() -> None:
    with patch.object(DevelopmentRecommendationService, "calculate", side_effect=AssertionError("must stay lazy")):
        response = TestClient(app).get("/api/dashboard")
    assert response.status_code == 200


def test_frontend_renders_lightweight_two_category_model() -> None:
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    html = (frontend / "index.html").read_text(encoding="utf-8")
    app_js = (frontend / "app.js").read_text(encoding="utf-8")
    assert 'data-page="development"' in html
    assert 'id="developmentActionFilter"' not in html
    assert "Критично по плану" in app_js
    assert "Слабые места команд" in app_js
    assert "UPSKILL_OR_HIRE" in app_js
    assert '<details class="panel development-card' in app_js
    assert 'class="development-card-summary"' in app_js
    assert 'class="development-card-body"' in app_js
    assert 'class="development-card-summary-copy"' in app_js
    assert 'class="development-card-summary-status"' in app_js
    assert "item.scope !== 'company'" in app_js
    assert 'data-open-star-development' in html
    assert "selectedTeam?.scope === 'company' ? 'all'" in app_js
    assert "Количество специалистов и влияние на план не прогнозируются" in app_js
    assert "quarter_capacity_per_hire" not in app_js
    assert "hire_count" not in app_js
