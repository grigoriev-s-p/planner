import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import src.web.api as web_api
from src.development.service import DevelopmentRecommendationService
from src.planning.capacity import calculate_team_capacity
from src.web.catalog import CatalogConflictError, CatalogRepository
from src.web.constructor import build_catalog_snapshot


class CatalogCrudRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.sqlite3"
        self.repository = CatalogRepository(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_empty_team_and_unassigned_engineer_are_persistent(self) -> None:
        self.repository.create_team(team_id="Team-ML", name="ML Platform")
        self.repository.create_engineer(
            engineer_id="E-100",
            name="Ada",
            role="  ML   Engineer ",
            grade="Senior",
            capacity_rate=1,
            team_id=None,
            skills=["Python", " python "],
        )

        reopened = CatalogRepository(self.path)
        snapshot = build_catalog_snapshot(
            reopened.active_planning_tables(), revision=reopened.revision()
        )
        team = next(item for item in snapshot["teams"] if item["team_id"] == "Team-ML")
        engineer = next(item for item in snapshot["engineers"] if item["engineer_id"] == "E-100")

        self.assertIsNone(team["expected_velocity_sp"])
        self.assertEqual(engineer["assignments"], [])
        self.assertEqual(engineer["free_assignment"]["role"], "ML Engineer")
        self.assertIn("ML Engineer", snapshot["options"]["roles"])
        self.assertEqual(engineer["skills"], ["Python"])

    def test_membership_is_canonical_and_capacity_cannot_exceed_one(self) -> None:
        self.repository.create_team(team_id="Team-A", name="A")
        self.repository.create_team(team_id="Team-B", name="B")
        self.repository.create_engineer(
            engineer_id="E-1", name="One", role="Backend", grade=None,
            capacity_rate=1, team_id=None, skills=[],
        )
        self.repository.add_engineer_membership(
            engineer_id="E-1", team_id="Team-A", capacity_rate=0.5
        )
        self.repository.add_engineer_membership(
            engineer_id="E-1", team_id="Team-B", capacity_rate=0.5
        )
        rows = [
            row for row in self.repository.active_planning_tables()["engineers"]
            if row["engineer_id"] == "E-1"
        ]
        self.assertEqual({row["team_id"] for row in rows}, {"Team-A", "Team-B"})
        self.assertAlmostEqual(sum(float(row["capacity_rate"]) for row in rows), 1.0)
        with self.assertRaises(CatalogConflictError):
            self.repository.add_engineer_membership(
                engineer_id="E-1", team_id="Team-A", capacity_rate=0.1
            )

    def test_duplicate_active_id_is_rejected(self) -> None:
        self.repository.create_team(team_id="Team-A", name="A")
        with self.assertRaisesRegex(CatalogConflictError, "уже существует"):
            self.repository.create_team(team_id="Team-A", name="Duplicate")

    def test_role_spelling_is_reused_without_duplicate_option(self) -> None:
        for engineer_id, role in (("E-1", "ML Engineer"), ("E-2", " ml   engineer ")):
            self.repository.create_engineer(
                engineer_id=engineer_id, name=None, role=role, grade=None,
                capacity_rate=1, team_id=None,
            )
        catalog = build_catalog_snapshot(self.repository.active_planning_tables())
        self.assertEqual([role for role in catalog["options"]["roles"] if role.casefold() == "ml engineer"], ["ML Engineer"])

    def test_permanent_delete_removes_entity_rows_and_team_links(self) -> None:
        self.repository.create_team(team_id="Team-A", name="A")
        self.repository.create_engineer(
            engineer_id="E-1", name="One", role="Backend", grade=None,
            capacity_rate=1, team_id="Team-A", skills=["Python"],
        )
        self.repository.create_task(
            task={"task_id": "TASK-1", "summary": "Task", "team_id": "Team-A", "status": "ToDo"},
            role_requirements=[{"role": "Backend", "required_hours": 8}],
            skill_requirements=[{"technology": "Python", "role": "Backend"}],
        )
        self.repository.delete_entity("task", "TASK-1")
        self.repository.delete_entity("team", "Team-A")
        tables = self.repository.tables()
        self.assertFalse(any(row.get("task_id") == "TASK-1" for row in tables["tasks"]))
        self.assertFalse(any(row.get("team_id") == "Team-A" for row in tables["team_history"]))
        self.assertEqual(tables["engineers"][0].get("team_id"), None)
        self.assertFalse(self.repository.lifecycle("task", "TASK-1")["archived"])
        self.assertFalse(self.repository.lifecycle("team", "Team-A")["archived"])

    def test_manual_capacity_is_used_without_fabricating_historical_velocity(self) -> None:
        result = calculate_team_capacity("Team-New", [{
            "team_id": "Team-New", "team_name": "New", "capacity_sp": 12,
        }])
        self.assertEqual(result.history_samples, 0)
        self.assertEqual(result.average_velocity_sp, 0)
        self.assertEqual(result.capacity_sp, 12)

    def test_manual_team_capacity_survives_catalog_projection(self) -> None:
        self.repository.create_team(team_id="Team-New", name="New", capacity_sp=12)
        snapshot = build_catalog_snapshot(self.repository.active_planning_tables())
        team = next(item for item in snapshot["teams"] if item["team_id"] == "Team-New")
        self.assertIsNone(team["expected_velocity_sp"])
        self.assertEqual(team["capacity_sp"], 12)


class CatalogCrudApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = CatalogRepository(Path(self.temp.name) / "catalog.sqlite3")
        self.provider = patch("src.web.api.get_catalog_repository", return_value=self.repository)
        self.provider.start()
        web_api.plan_drafts.reset()
        self.client = TestClient(web_api.app)

    def tearDown(self) -> None:
        web_api.plan_drafts.reset()
        self.provider.stop()
        self.temp.cleanup()

    def test_create_entities_uses_catalog_and_keeps_approved_plan_empty(self) -> None:
        team = self.client.post("/api/teams", json={
            "team_id": "Team-A", "name": "Alpha", "engineer_ids": [],
        })
        engineer = self.client.post("/api/engineers", json={
            "engineer_id": "E-1", "name": "Ada", "team_id": "Team-A",
            "role": "ML Engineer", "grade": "Senior", "capacity_rate": 1,
            "skills": ["Python"],
        })
        task = self.client.post("/api/tasks", json={
            "task_id": "TASK-ML", "title": "ML inference", "team_id": "Team-A",
            "status": "ToDo", "rung": 10, "story_points": 5,
            "role_requirements": [{"role": "ML Engineer", "required_hours": 24}],
            "required_skills": [{"technology": "Python", "role": "ML Engineer"}],
            "depends_on": [],
        })

        self.assertEqual(team.status_code, 201, team.text)
        self.assertEqual(engineer.status_code, 201, engineer.text)
        self.assertEqual(task.status_code, 201, task.text)
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(dashboard["status"], "EMPTY")
        self.assertIsNone(web_api.plan_drafts.approved())
        self.assertIn("E-1", {item["engineer_id"] for item in dashboard["engineers"]})
        self.assertIn("Team-A", {item["team_id"] for item in dashboard["teams"]})
        self.assertIn("TASK-ML", {item["task_id"] for item in dashboard["tasks"]})
        self.assertEqual(self.client.get("/api/engineers/E-1").status_code, 200)
        self.assertEqual(self.client.get("/api/teams/Team-A").status_code, 200)
        self.assertEqual(self.client.get("/api/tasks/TASK-ML").status_code, 200)
        star = self.client.get("/api/star-map").json()
        team_star = next(item for item in star["teams"] if item["team_id"] == "Team-A")
        role = next(item for item in team_star["competency_modes"]["all"]["roles"] if item["role"] == "ML Engineer")
        self.assertEqual(role["team_bus_factor"], 1)

    def test_manual_team_capacity_is_visible_without_approved_plan(self) -> None:
        response = self.client.post("/api/teams", json={
            "team_id": "Team-Capacity", "name": "Capacity Team",
            "capacity_sp": 18,
        })
        self.assertEqual(response.status_code, 201, response.text)
        dashboard = self.client.get("/api/dashboard").json()
        team = next(item for item in dashboard["teams"] if item["team_id"] == "Team-Capacity")
        self.assertEqual(team["capacity_sp"], 18)
        self.assertEqual(team["sp_by_sprint"]["1"]["capacity"], 18)
        self.assertEqual(team["sp_by_sprint"]["1"]["remaining"], 18)

    def test_duplicate_api_id_returns_conflict(self) -> None:
        payload = {"team_id": "Team-A", "name": "Alpha", "engineer_ids": []}
        self.assertEqual(self.client.post("/api/teams", json=payload).status_code, 201)
        duplicate = self.client.post("/api/teams", json=payload)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("уже существует", duplicate.json()["detail"])

    def test_permanent_delete_endpoint_removes_entity_from_active_catalog(self) -> None:
        self.client.post("/api/teams", json={"team_id": "Team-A", "name": "A"})
        self.client.post("/api/engineers", json={
            "engineer_id": "E-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1,
        })
        deleted = self.client.delete("/api/engineers/E-1/permanent")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn("E-1", {row["engineer_id"] for row in self.client.get("/api/engineers").json()})
        self.assertEqual(self.client.get("/api/engineers/E-1").status_code, 404)
        self.assertEqual(self.client.delete("/api/engineers/E-1/permanent").status_code, 404)

    def test_membership_archive_and_bf_use_active_catalog(self) -> None:
        self.client.post("/api/teams", json={"team_id": "Team-A", "name": "A"})
        for engineer_id in ("E-1", "E-2"):
            self.assertEqual(self.client.post("/api/engineers", json={
                "engineer_id": engineer_id,
                "team_id": "Team-A",
                "role": "Backend",
                "capacity_rate": 1,
                "skills": ["Python"],
            }).status_code, 201)

        def bf() -> int:
            star = self.client.get("/api/star-map").json()
            team = next(item for item in star["teams"] if item["team_id"] == "Team-A")
            role = next(item for item in team["competency_modes"]["all"]["roles"] if item["role"] == "Backend")
            return role["team_bus_factor"]

        self.assertEqual(bf(), 2)
        archived = self.client.post("/api/engineers/E-2/archive", json={
            "reason_code": "CREATED_BY_MISTAKE", "confirm_replan": False,
        })
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(bf(), 1)
        self.assertIsNone(web_api.plan_drafts.approved())
        self.assertEqual({row["engineer_id"] for row in self.client.get("/api/engineers").json()}, {"E-1"})
        team = self.client.get("/api/teams/Team-A").json()
        self.assertEqual({row["engineer_id"] for row in team["members"]}, {"E-1"})
        self.assertTrue(self.repository.lifecycle("engineer", "E-2")["archived"])
        last_archived = self.client.post("/api/engineers/E-1/archive", json={
            "reason_code": "CREATED_BY_MISTAKE", "confirm_replan": False,
        })
        self.assertEqual(last_archived.status_code, 200, last_archived.text)
        empty_team = self.client.get("/api/teams/Team-A")
        self.assertEqual(empty_team.status_code, 200)
        self.assertEqual(empty_team.json()["members"], [])

    def test_team_creation_memberships_are_bidirectional_and_atomic(self) -> None:
        self.client.post("/api/engineers", json={
            "engineer_id": "E-1", "role": "ML Engineer", "capacity_rate": 1,
        })
        response = self.client.post("/api/teams", json={
            "team_id": "Team-ML", "name": "ML", "engineer_ids": ["E-1"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        engineer = self.client.get("/api/engineers/E-1").json()
        team = self.client.get("/api/teams/Team-ML").json()
        self.assertEqual(engineer["memberships"][0]["team_id"], "Team-ML")
        self.assertEqual(team["members"][0]["engineer_id"], "E-1")
        failed = self.client.post("/api/teams", json={
            "team_id": "Team-Wrong", "name": "Wrong", "engineer_ids": ["E-1"],
        })
        self.assertEqual(failed.status_code, 409)
        self.assertNotIn("Team-Wrong", {row["team_id"] for row in self.client.get("/api/teams").json()})

    def test_new_role_flows_from_catalog_to_next_build_without_mutating_approval(self) -> None:
        self.assertEqual(self.client.post("/api/teams", json={
            "team_id": "Team-ML", "name": "ML", "engineer_ids": [],
        }).status_code, 201)
        self.assertEqual(self.client.post("/api/engineers", json={
            "engineer_id": "E-ML", "name": "Ada", "team_id": "Team-ML",
            "role": "ML Engineer", "capacity_rate": 1, "skills": ["Python"],
        }).status_code, 201)
        self.assertEqual(self.client.post("/api/tasks", json={
            "task_id": "TASK-ML", "title": "ML inference", "team_id": "Team-ML",
            "status": "ToDo", "rung": 100, "story_points": 5,
            "role_requirements": [{"role": "ML Engineer", "required_hours": 24}],
            "required_skills": [{"technology": "Python", "role": "ML Engineer"}],
        }).status_code, 201)
        catalog = self.client.get("/api/plan-constructor/catalog").json()
        self.assertIn("ML Engineer", catalog["options"]["roles"])
        team = {**catalog["teams"][0], "expected_velocity_sp": 10}
        scenario = {
            "teams": [team], "engineers": catalog["engineers"],
            "tasks": catalog["tasks"],
        }
        response = self.client.post("/api/plan-constructor/plan", json=scenario)
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        planned = next(row for row in draft["tasks"] if row["task_id"] == "TASK-ML")
        self.assertEqual(planned["status"], "PLANNED")
        approved = self.client.post("/api/plan-constructor/drafts/approve", json={
            "draft_id": draft["draft"]["id"],
        })
        self.assertEqual(approved.status_code, 200, approved.text)
        plan_before = self.client.get("/api/plan").json()
        recommendations_before = self.client.get("/api/development/recommendations")
        self.assertEqual(recommendations_before.status_code, 200)
        self.assertTrue(any(
            row["category"] == "TEAM_WEAKNESS"
            and row["team_id"] == "Team-ML"
            and row["deficit"]["role"] == "ML Engineer"
            for row in recommendations_before.json()["recommendations"]
        ))

        self.assertEqual(self.client.post("/api/engineers", json={
            "engineer_id": "E-ML-2", "team_id": "Team-ML",
            "role": "ML Engineer", "capacity_rate": 1, "skills": ["Python"],
        }).status_code, 201)
        self.assertEqual(self.client.get("/api/plan").json(), plan_before)
        star = self.client.get("/api/star-map").json()
        team_star = next(row for row in star["teams"] if row["team_id"] == "Team-ML")
        role = next(row for row in team_star["competency_modes"]["all"]["roles"] if row["role"] == "ML Engineer")
        self.assertEqual(role["team_bus_factor"], 2)
        recommendations_after = self.client.get("/api/development/recommendations")
        self.assertEqual(recommendations_after.status_code, 200)
        self.assertFalse(any(
            row["category"] == "TEAM_WEAKNESS"
            and row["team_id"] == "Team-ML"
            and row["deficit"]["role"] == "ML Engineer"
            for row in recommendations_after.json()["recommendations"]
        ))

    def test_catalog_change_invalidates_unapproved_draft(self) -> None:
        self.client.post("/api/teams", json={"team_id": "T", "name": "T"})
        self.client.post("/api/engineers", json={
            "engineer_id": "E", "team_id": "T", "role": "Backend", "capacity_rate": 1,
        })
        self.client.post("/api/tasks", json={
            "task_id": "TASK", "title": "Task", "team_id": "T", "story_points": 3,
            "role_requirements": [{"role": "Backend", "required_hours": 8}],
        })
        catalog = self.client.get("/api/plan-constructor/catalog").json()
        response = self.client.post("/api/plan-constructor/plan", json={
            "teams": [{**catalog["teams"][0], "expected_velocity_sp": 10}],
            "engineers": catalog["engineers"], "tasks": catalog["tasks"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        draft_id = response.json()["draft"]["id"]
        self.client.post("/api/engineers", json={
            "engineer_id": "E2", "role": "Backend", "capacity_rate": 1,
        })
        self.assertTrue(self.client.get(f"/api/plan-constructor/drafts/{draft_id}").json()["draft"]["catalog_stale"])
        approval = self.client.post("/api/plan-constructor/drafts/approve", json={"draft_id": draft_id})
        self.assertEqual(approval.status_code, 409)
        self.assertIsNone(web_api.plan_drafts.approved())

    def test_team_role_coverage_uses_each_actual_membership_role(self) -> None:
        for team_id in ("Team-A", "Team-B"):
            self.client.post("/api/teams", json={"team_id": team_id, "name": team_id})
        self.client.post("/api/engineers", json={
            "engineer_id": "E-1", "team_id": "Team-A", "role": "Backend",
            "capacity_rate": 0.5,
        })
        self.assertEqual(self.client.post("/api/engineers/E-1/memberships", json={
            "team_id": "Team-B", "role": "Analyst", "capacity_rate": 0.5,
        }).status_code, 201)
        dashboard = self.client.get("/api/dashboard").json()
        team_a = next(item for item in dashboard["teams"] if item["team_id"] == "Team-A")
        self.assertEqual(team_a["roles"], ["Backend"])
        service = DevelopmentRecommendationService(self.repository.active_planning_tables(), dashboard)
        self.assertEqual(service._role_bf[("Team-A", "Backend")][0], 1)
        self.assertEqual(service._role_bf[("Team-B", "Analyst")][0], 1)
        self.assertNotIn(("Team-A", "Analyst"), service._role_bf)


if __name__ == "__main__":
    unittest.main()
