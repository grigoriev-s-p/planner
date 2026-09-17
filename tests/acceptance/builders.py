"""Small, readable builders for planner acceptance scenarios."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from scripts.data_audit import audit_dataset, machine_readable_audit
from src.planning import FeasibilityContext, build_quarter_plan


TASK_COLUMNS = (
    "task_id",
    "initiative_id",
    "team_id",
    "story_points",
    "status",
    "rung",
    "estimated_hh",
    "spent_time",
    "planned_start",
    "planned_end",
    "is_placeholder",
)


@dataclass
class SyntheticQuarter:
    """In-memory normalized input shared by acceptance tests."""

    tasks: list[dict] = field(default_factory=list)
    effort: list[dict] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)
    engineers: list[dict] = field(default_factory=list)
    engineer_skills: list[dict] = field(default_factory=list)
    team_history: list[dict] = field(default_factory=list)

    def team(self, team_id: str, *, velocity: float = 10) -> "SyntheticQuarter":
        self.team_history.append({"team_id": team_id, "velocity_achieved": velocity})
        return self

    def engineer(
        self,
        engineer_id: str,
        team_id: str,
        role: str,
        *,
        capacity_rate: float = 1.0,
        skills: tuple[str, ...] = (),
    ) -> "SyntheticQuarter":
        self.engineers.append(
            {
                "engineer_id": engineer_id,
                "team_id": team_id,
                "role": role,
                "capacity_rate": capacity_rate,
            }
        )
        self.engineer_skills.extend(
            {"engineer_id": engineer_id, "technology": skill} for skill in skills
        )
        return self

    def task(
        self,
        task_id: str,
        *,
        initiative_id: str | None = None,
        team_id: str | None = "Team-A",
        story_points: float | None = 1,
        role_hours: dict[str, float] | None = None,
        rung: float = 50,
        status: str = "ToDo",
        estimated_hh: float | None = None,
    ) -> "SyntheticQuarter":
        self.tasks.append(
            {
                "task_id": task_id,
                "initiative_id": initiative_id,
                "team_id": team_id,
                "story_points": story_points,
                "status": status,
                "rung": rung,
                "estimated_hh": estimated_hh,
                "spent_time": None,
                "planned_start": None,
                "planned_end": None,
                "is_placeholder": False,
            }
        )
        self.effort.extend(
            {"task_id": task_id, "role": role, "required_hours": hours}
            for role, hours in (role_hours or {}).items()
        )
        return self

    def depends_on(self, blocked_task_id: str, blocking_task_id: str) -> "SyntheticQuarter":
        self.dependencies.append(
            {
                "blocking_task_id": blocking_task_id,
                "blocked_task_id": blocked_task_id,
                "dependency_type": "has to be done before",
            }
        )
        return self

    def context(self) -> FeasibilityContext:
        return FeasibilityContext.from_records(
            tasks=self.tasks,
            task_role_effort=self.effort,
            dependencies=self.dependencies,
            engineers=self.engineers,
            engineer_skills=self.engineer_skills,
            team_history=self.team_history,
        )

    def plan(self):
        return build_quarter_plan(self.context())

    def audit(self, directory: Path):
        """Run the real audit against temporary, synthetic processed CSV files."""

        directory.mkdir(parents=True, exist_ok=True)
        # The processed-data contract currently requires each CSV to contain at
        # least one data row. These neutral rows exist only in the temporary
        # audit copy; they are never passed to the planner.
        audit_dependencies = self.dependencies or [
            {
                "blocking_task_id": self.tasks[0]["task_id"],
                "blocked_task_id": self.tasks[1]["task_id"],
                "dependency_type": "has to be done before",
            }
        ]
        audit_skills = self.engineer_skills or [
            {"engineer_id": self.engineers[0]["engineer_id"], "technology": "Synthetic"}
        ]
        _write_csv(directory / "tasks.csv", TASK_COLUMNS, self.tasks)
        _write_csv(
            directory / "task_role_effort.csv",
            ("task_id", "role", "required_hours"),
            self.effort,
        )
        _write_csv(
            directory / "task_dependencies.csv",
            ("blocking_task_id", "blocked_task_id", "dependency_type"),
            audit_dependencies,
        )
        _write_csv(
            directory / "engineers.csv",
            ("engineer_id", "team_id", "role", "capacity_rate"),
            self.engineers,
        )
        _write_csv(
            directory / "engineer_skills.csv",
            ("engineer_id", "technology"),
            audit_skills,
        )
        _write_csv(
            directory / "team_history.csv",
            ("team_id", "velocity_achieved"),
            self.team_history,
        )
        return machine_readable_audit(audit_dataset(directory, suspicious_date_days=84))


def result_by_id(plan, task_id: str):
    return next(result for result in plan.task_results if result.task_id == task_id)


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
