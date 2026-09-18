"""In-memory plan-constructor contracts and planner adapter.

The constructor deliberately never writes the source Excel or processed CSV files.
Browser drafts arrive as JSON, are normalized into a temporary planning context,
and disappear after the request has been served.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, Field

from src.data.normalization import normalize
from src.data.parser import DatasetParsingError, parse_workbook
from src.data.validation import DataValidationError, validate

from .service import TABLE_FILES, PlanningAPIService, ServiceDataError


MAX_CONSTRUCTOR_UPLOAD_BYTES = 25 * 1024 * 1024


class ConstructorTeam(BaseModel):
    team_id: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=200)
    expected_velocity_sp: float = Field(ge=0)
    focus_factor: float = Field(default=0.8, ge=0, le=1)


class ConstructorEngineerAssignment(BaseModel):
    team_id: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=200)
    grade: str | None = Field(default=None, max_length=120)
    capacity_rate: float = Field(gt=0, le=1)


class ConstructorEngineer(BaseModel):
    engineer_id: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=200)
    assignments: list[ConstructorEngineerAssignment] = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)


class ConstructorRoleRequirement(BaseModel):
    role: str = Field(min_length=1, max_length=200)
    required_hours: float = Field(gt=0)
    skills: list[str] = Field(default_factory=list)


class ConstructorTask(BaseModel):
    task_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    initiative_id: str | None = Field(default=None, max_length=120)
    team_id: str | None = Field(default=None, max_length=120)
    status: Literal["ToDo", "InProgress", "Done"] = "ToDo"
    rung: float = 0
    story_points: float = Field(ge=0)
    role_requirements: list[ConstructorRoleRequirement] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class ConstructorScenario(BaseModel):
    teams: list[ConstructorTeam] = Field(min_length=1)
    engineers: list[ConstructorEngineer] = Field(min_length=1)
    tasks: list[ConstructorTask] = Field(min_length=1)


class ScenarioValidationError(ValueError):
    """The submitted draft is structurally valid JSON but not a valid scenario."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


class ConstructorDatasetUploadError(ValueError):
    """An uploaded workbook cannot be used as a constructor catalog."""


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _cycle_nodes(tasks: list[ConstructorTask]) -> tuple[str, ...]:
    """Return a deterministic dependency cycle witness, if one exists."""

    graph = {task.task_id: tuple(task.depends_on) for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(task_id: str) -> tuple[str, ...]:
        if task_id in visiting:
            start = path.index(task_id)
            return tuple((*path[start:], task_id))
        if task_id in visited:
            return ()
        visiting.add(task_id)
        path.append(task_id)
        for dependency_id in graph.get(task_id, ()):
            cycle = visit(dependency_id)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(task_id)
        visited.add(task_id)
        return ()

    for task_id in sorted(graph):
        cycle = visit(task_id)
        if cycle:
            return cycle
    return ()


def validate_constructor_scenario(scenario: ConstructorScenario) -> None:
    """Validate cross-entity rules that cannot be expressed by field types."""

    errors: list[str] = []
    team_ids = [_clean_text(team.team_id) for team in scenario.teams]
    engineer_ids = [_clean_text(item.engineer_id) for item in scenario.engineers]
    task_ids = [_clean_text(item.task_id) for item in scenario.tasks]

    for entity, values in (
        ("команд", team_ids),
        ("инженеров", engineer_ids),
        ("задач", task_ids),
    ):
        for duplicate in sorted(_duplicates(values)):
            errors.append(f"Дублируется ID {entity}: {duplicate}.")
        if any(not value for value in values):
            errors.append(f"ID {entity} не может быть пустым.")

    known_teams = set(team_ids)
    known_tasks = set(task_ids)
    for team in scenario.teams:
        if abs(team.focus_factor - 0.8) > 1e-9:
            errors.append(
                f"Команда {team.team_id}: Focus Factor должен быть 0.8 "
                "по правилам кейса."
            )

    for engineer in scenario.engineers:
        assignment_teams: list[str] = []
        for assignment in engineer.assignments:
            assignment_teams.append(_clean_text(assignment.team_id))
            if not _clean_text(assignment.role):
                errors.append(f"Инженер {engineer.engineer_id}: роль не может быть пустой.")
            if assignment.team_id not in known_teams:
                errors.append(
                    f"Инженер {engineer.engineer_id}: команда "
                    f"{assignment.team_id} не добавлена в сценарий."
                )
        for duplicate in sorted(_duplicates(assignment_teams)):
            errors.append(
                f"Инженер {engineer.engineer_id}: команда {duplicate} "
                "указана более одного раза."
            )
        total_capacity_rate = sum(item.capacity_rate for item in engineer.assignments)
        if total_capacity_rate > 1 + 1e-9:
            errors.append(
                f"Инженер {engineer.engineer_id}: сумма Capacity Rate по командам "
                f"равна {total_capacity_rate:g}, но не может превышать 1."
            )

    for task in scenario.tasks:
        if not _clean_text(task.title):
            errors.append(f"Задача {task.task_id}: название не может быть пустым.")
        if task.team_id is not None and task.team_id not in known_teams:
            errors.append(
                f"Задача {task.task_id}: команда {task.team_id} "
                "не добавлена в сценарий."
            )
        role_names = [_clean_text(item.role) for item in task.role_requirements]
        if any(not role for role in role_names):
            errors.append(f"Задача {task.task_id}: роль не может быть пустой.")
        for duplicate in sorted(_duplicates(role_names)):
            errors.append(
                f"Задача {task.task_id}: роль {duplicate} указана "
                "более одного раза."
            )
        for dependency_id in task.depends_on:
            if dependency_id == task.task_id:
                errors.append(f"Задача {task.task_id} не может зависеть сама от себя.")
            elif dependency_id not in known_tasks:
                errors.append(
                    f"Задача {task.task_id}: блокирующая задача "
                    f"{dependency_id} не добавлена в сценарий."
                )

    cycle = _cycle_nodes(scenario.tasks)
    if cycle:
        errors.append(f"Обнаружен цикл зависимостей: {' → '.join(cycle)}.")
    if errors:
        raise ScenarioValidationError(errors)


def scenario_tables(scenario: ConstructorScenario) -> dict[str, list[dict[str, Any]]]:
    """Convert a validated browser draft into normalized in-memory tables."""

    validate_constructor_scenario(scenario)
    tasks: list[dict[str, Any]] = []
    role_effort: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    engineers: list[dict[str, Any]] = []
    engineer_skills: list[dict[str, Any]] = []
    team_history: list[dict[str, Any]] = []
    task_skill_requirements: list[dict[str, Any]] = []

    for team in scenario.teams:
        team_id = _clean_text(team.team_id)
        team_history.append(
            {
                "team_id": team_id,
                "team_name": _clean_text(team.name) or team_id,
                "velocity_achieved": team.expected_velocity_sp,
            }
        )

    for engineer in scenario.engineers:
        engineer_id = _clean_text(engineer.engineer_id)
        for assignment in engineer.assignments:
            engineers.append(
                {
                    "engineer_id": engineer_id,
                    "name": _clean_text(engineer.name) or engineer_id,
                    "team_id": _clean_text(assignment.team_id),
                    "role_raw": _clean_text(assignment.role),
                    "role": _clean_text(assignment.role),
                    "grade": _clean_text(assignment.grade) or None,
                    "capacity_rate": assignment.capacity_rate,
                }
            )
        for skill in sorted({_clean_text(skill) for skill in engineer.skills if _clean_text(skill)}):
            engineer_skills.append({"engineer_id": engineer_id, "technology": skill})

    for task in scenario.tasks:
        task_id = _clean_text(task.task_id)
        tasks.append(
            {
                "task_id": task_id,
                "summary": _clean_text(task.title),
                "initiative_id": _clean_text(task.initiative_id) or None,
                # No source team means the planner evaluates every selected team
                # and chooses the earliest feasible assignment deterministically.
                "team_id": _clean_text(task.team_id) or None,
                "status": task.status,
                "rung": task.rung,
                "story_points": task.story_points,
                "is_placeholder": False,
            }
        )
        for requirement in task.role_requirements:
            role = _clean_text(requirement.role)
            role_effort.append(
                {
                    "task_id": task_id,
                    "role_raw": role,
                    "role": role,
                    "required_hours": requirement.required_hours,
                }
            )
            for skill in sorted(
                {_clean_text(skill) for skill in requirement.skills if _clean_text(skill)}
            ):
                task_skill_requirements.append(
                    {"task_id": task_id, "role": role, "technology": skill}
                )
        for blocking_task_id in sorted(set(task.depends_on)):
            dependencies.append(
                {
                    "blocking_task_id": blocking_task_id,
                    "blocked_task_id": task_id,
                    "dependency_type": "depends on",
                }
            )

    return {
        "tasks": tasks,
        "task_role_effort": role_effort,
        "task_dependencies": dependencies,
        "engineers": engineers,
        "engineer_skills": engineer_skills,
        "team_history": team_history,
        "task_skill_requirements": task_skill_requirements,
    }


def build_constructor_plan(scenario: ConstructorScenario) -> dict[str, Any]:
    """Run the real quarter planner against an ephemeral constructor scenario."""

    service = PlanningAPIService.from_tables(
        scenario_tables(scenario), plan_version="constructor-draft"
    )
    return {
        **service.dashboard,
        "draft": {
            "storage": "browser_local_storage",
            "server_persisted": False,
            "source_dataset_modified": False,
            "team_count": len(scenario.teams),
            "engineer_count": len(scenario.engineers),
            "task_count": len(scenario.tasks),
        },
    }


def build_constructor_plan_from_workbook(
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    """Parse an uploaded workbook and immediately build its six-sprint plan."""

    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or suffix not in {".xlsx", ".xlsm"}:
        raise ConstructorDatasetUploadError(
            "Поддерживаются только Excel-файлы .xlsx и .xlsm."
        )
    if not content:
        raise ConstructorDatasetUploadError("Загруженный Excel-файл пуст.")
    if len(content) > MAX_CONSTRUCTOR_UPLOAD_BYTES:
        raise ConstructorDatasetUploadError(
            "Размер Excel-файла не должен превышать 25 МБ."
        )

    try:
        with tempfile.TemporaryDirectory(prefix="plan-constructor-upload-") as directory:
            workbook = Path(directory) / safe_name
            workbook.write_bytes(content)
            normalized = normalize(parse_workbook(workbook))
            validation = validate(normalized)
            validation.require_success()
            planning_tables = {
                table_name: normalized.tables[csv_name]
                for table_name, csv_name in TABLE_FILES.items()
            }
            fingerprint = hashlib.sha256(content).hexdigest()[:8]
            service = PlanningAPIService.from_tables(
                planning_tables,
                plan_version=f"constructor-upload-{fingerprint}",
            )
    except (
        DatasetParsingError,
        DataValidationError,
        BadZipFile,
        InvalidFileException,
        ServiceDataError,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        raise ConstructorDatasetUploadError(str(error)) from error

    return {
        **service.dashboard,
        "uploaded_dataset": {
            "name": safe_name,
            "size_bytes": len(content),
            "fingerprint": fingerprint,
            "warnings": list(validation.warnings),
            "server_persisted": False,
            "source_dataset_modified": False,
        },
    }


def build_constructor_catalog(service: PlanningAPIService) -> dict[str, Any]:
    """Expose editable copies of the immutable processed-data catalog."""

    skills_by_engineer: dict[str, set[str]] = defaultdict(set)
    for row in service.tables["engineer_skills"]:
        engineer_id = _clean_text(row.get("engineer_id"))
        skill = _clean_text(row.get("technology"))
        if engineer_id and skill:
            skills_by_engineer[engineer_id].add(skill)

    engineer_profiles: dict[str, dict[str, Any]] = {}
    seen_assignments: set[tuple[str, str, str]] = set()
    for row in service.tables["engineers"]:
        engineer_id = _clean_text(row.get("engineer_id"))
        team_id = _clean_text(row.get("team_id"))
        role = _clean_text(row.get("role"))
        if not engineer_id or not team_id or not role:
            continue
        target = engineer_profiles.setdefault(
            engineer_id,
            {
                "engineer_id": engineer_id,
                "name": _clean_text(row.get("name")) or engineer_id,
                "assignments": [],
                "skills": [],
                "source": "dataset",
            },
        )
        key = (engineer_id, team_id, role)
        if key not in seen_assignments:
            target["assignments"].append(
                {
                    "team_id": team_id,
                    "role": role,
                    "grade": _clean_text(row.get("grade")) or None,
                    "capacity_rate": float(row.get("capacity_rate") or 0),
                }
            )
            seen_assignments.add(key)
    for engineer_id, profile in engineer_profiles.items():
        profile["assignments"].sort(key=lambda item: (item["team_id"], item["role"]))
        profile["skills"] = sorted(skills_by_engineer[engineer_id])

    role_requirements: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skill_requirements: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in service.tables.get("task_skill_requirements", []):
        key = (_clean_text(row.get("task_id")), _clean_text(row.get("role")))
        technology = _clean_text(row.get("technology"))
        if all(key) and technology:
            skill_requirements[key].append(technology)
    for row in service.tables["task_role_effort"]:
        task_id = _clean_text(row.get("task_id"))
        role = _clean_text(row.get("role"))
        hours = row.get("required_hours")
        try:
            required_hours = float(hours)
        except (TypeError, ValueError):
            continue
        if task_id and role and required_hours > 0:
            role_requirements[task_id].append(
                {
                    "role": role,
                    "required_hours": required_hours,
                    "skills": sorted(set(skill_requirements[(task_id, role)])),
                }
            )

    dependencies: dict[str, list[str]] = defaultdict(list)
    for row in service.tables["task_dependencies"]:
        blocker = _clean_text(row.get("blocking_task_id"))
        blocked = _clean_text(row.get("blocked_task_id"))
        if blocker and blocked:
            dependencies[blocked].append(blocker)

    raw_tasks = {
        _clean_text(row.get("task_id")): row
        for row in service.tables["tasks"]
        if _clean_text(row.get("task_id"))
    }
    catalog_tasks = []
    for task in service.tasks:
        if task["is_placeholder"]:
            continue
        task_id = task["task_id"]
        raw = raw_tasks[task_id]
        team_id = _clean_text(raw.get("team_id")) or task["original_team_id"]
        if not team_id or not role_requirements[task_id]:
            continue
        status = _clean_text(raw.get("status")) or "ToDo"
        if status.casefold() not in {"todo", "inprogress", "done"}:
            status = "ToDo"
        status = {"todo": "ToDo", "inprogress": "InProgress", "done": "Done"}[
            status.casefold()
        ]
        catalog_tasks.append(
            {
                "task_id": task_id,
                "title": task["title"],
                "initiative_id": task["initiative_id"],
                "team_id": team_id,
                "status": status,
                "rung": task["rung"] or 0,
                "story_points": task["story_points"] or 0,
                "role_requirements": sorted(
                    role_requirements[task_id], key=lambda item: item["role"]
                ),
                "depends_on": sorted(set(dependencies[task_id])),
                "source": "dataset",
            }
        )

    catalog_task_ids = {task["task_id"] for task in catalog_tasks}
    for task in catalog_tasks:
        # A constructor dependency can only target another selectable task.
        # Missing source rows and placeholders remain data-audit concerns and
        # are not silently injected into an isolated browser scenario.
        task["depends_on"] = [
            task_id for task_id in task["depends_on"] if task_id in catalog_task_ids
        ]

    roles = sorted(
        {
            assignment["role"]
            for profile in engineer_profiles.values()
            for assignment in profile["assignments"]
        }
        | {
            item["role"]
            for requirements in role_requirements.values()
            for item in requirements
        }
    )
    skills = sorted(
        {skill for values in skills_by_engineer.values() for skill in values}
    )
    grades = sorted(
        {
            assignment["grade"]
            for profile in engineer_profiles.values()
            for assignment in profile["assignments"]
            if assignment["grade"]
        }
    )
    return {
        "teams": [
            {
                "team_id": team["team_id"],
                "name": team["name"],
                "expected_velocity_sp": team["average_velocity_sp"],
                "focus_factor": 0.8,
                "source": "dataset",
            }
            for team in service.teams
        ],
        "engineers": sorted(engineer_profiles.values(), key=lambda item: item["engineer_id"]),
        "tasks": sorted(catalog_tasks, key=lambda item: item["task_id"]),
        "options": {
            "roles": roles,
            "skills": skills,
            "grades": grades,
            "statuses": ["ToDo", "InProgress", "Done"],
            "focus_factor": 0.8,
        },
        "source_dataset_read_only": True,
    }
