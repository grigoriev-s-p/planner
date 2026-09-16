"""Convert parsed organizer tables to stable normalized records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .parser import ParsedDataset, RawReferenceList, RawTable, is_blank, normalized_text


TASK_HEADER_MAPPING = {
    "номер инициативы": "initiative_id",
    "initiative_id": "initiative_id",
    "task_id": "task_id",
    "parent_id": "parent_id",
    "summary": "summary",
    "team_id": "team_id",
    "status": "status",
    "rung": "rung",
    "spent_time": "spent_time",
    "estimation_sp": "story_points",
    "story points": "story_points",
    "created_at": "created_at",
    "planned_start": "planned_start",
    "planned_end": "planned_end",
    "actual_start": "actual_start",
    "actual_end": "actual_end",
    "estimated_hh": "estimated_hh",
    "плановый результат": "planned_result",
    "будет включено в спринт": "included_in_sprint",
    "заказчик": "customer",
    "исполнитель": "executor",
    "итоговый результат": "final_result",
}

PLACEHOLDER_TASK_SUMMARY = "Unknown task"


@dataclass(frozen=True)
class NormalizedDataset:
    tables: dict[str, list[dict[str, Any]]]
    effort_task_ids: set[str]
    effort_roles: set[str]
    placeholder_sources: dict[str, tuple[str, ...]]


def clean_value(value: Any) -> Any:
    if isinstance(value, str):
        normalized = " ".join(value.split())
        return normalized or None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


NUMBER_TEXT = re.compile(r"^-?\d+(?:[.,]\d+)?$")


def coerce_number(value: Any) -> Any:
    """Parse a plainly numeric source string without inventing a missing value."""

    cleaned = clean_value(value)
    if not isinstance(cleaned, str) or not NUMBER_TEXT.fullmatch(cleaned):
        return cleaned
    numeric_text = cleaned.replace(",", ".")
    numeric_value = float(numeric_text)
    return int(numeric_value) if numeric_value.is_integer() else numeric_value


def canonical_task_headers(headers: list[Any]) -> list[str]:
    used: set[str] = set()
    normalized_headers: list[str] = []
    for index, header in enumerate(headers, start=1):
        canonical = TASK_HEADER_MAPPING.get(normalized_text(header), f"source_column_{index}")
        unique_name = canonical
        suffix = 2
        while unique_name in used:
            unique_name = f"{canonical}_{suffix}"
            suffix += 1
        used.add(unique_name)
        normalized_headers.append(unique_name)
    return normalized_headers


def records_from_table(table: RawTable, headers: list[str] | None = None) -> list[dict[str, Any]]:
    field_names = headers or [str(header) for header in table.headers]
    return [
        {
            field_name: clean_value(row[index] if index < len(row) else None)
            for index, field_name in enumerate(field_names)
        }
        for row in table.rows
    ]


def melt_effort(table: RawTable) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    """Convert the actual role-by-task estimate matrix into long records.

    Explicit zeroes stay zeroes. Empty matrix cells remain empty; they are never
    converted to a guessed zero value.
    """

    task_ids = {
        str(clean_value(task_id))
        for task_id in table.headers[1:]
        if not is_blank(clean_value(task_id))
    }
    records: list[dict[str, Any]] = []
    roles: set[str] = set()
    for row in table.rows:
        role = clean_value(row[0] if row else None)
        if is_blank(role):
            continue
        role_text = str(role)
        roles.add(role_text)
        for column_index, task_id in enumerate(table.headers[1:], start=1):
            task_id = clean_value(task_id)
            if is_blank(task_id):
                continue
            required_hours = coerce_number(row[column_index] if column_index < len(row) else None)
            records.append(
                {
                    "task_id": str(task_id),
                    "role": role_text,
                    "required_hours": required_hours,
                }
            )
    return records, task_ids, roles


def split_skills(value: Any) -> list[str]:
    """Split comma/semicolon lists while preserving commas inside parentheses."""

    if is_blank(value):
        return []
    skills: list[str] = []
    current: list[str] = []
    bracket_depth = 0
    opening = "([{"
    closing = ")] }".replace(" ", "")
    for character in str(value):
        if character in opening:
            bracket_depth += 1
        elif character in closing and bracket_depth:
            bracket_depth -= 1
        if character in {",", ";"} and bracket_depth == 0:
            candidate = " ".join("".join(current).split())
            if candidate:
                skills.append(candidate)
            current = []
            continue
        current.append(character)
    candidate = " ".join("".join(current).split())
    if candidate:
        skills.append(candidate)
    return skills


def normalize_engineers(table: RawTable) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_records = records_from_table(table)
    engineers: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    seen_skills: set[tuple[str, str]] = set()
    for source in source_records:
        engineer_id = source.get("engineer_id")
        if is_blank(engineer_id):
            continue
        engineer_id = str(engineer_id)
        engineers.append(
            {
                "engineer_id": engineer_id,
                "team_id": source.get("team_id"),
                "role": source.get("role"),
                "grade": source.get("grade"),
                "capacity_rate": coerce_number(source.get("capacity_rate")),
            }
        )
        for technology in split_skills(source.get("skills_declared")):
            key = (engineer_id, technology.casefold())
            if key not in seen_skills:
                seen_skills.add(key)
                skills.append({"engineer_id": engineer_id, "technology": technology})
    return engineers, skills


def normalize_reference_list(reference: RawReferenceList) -> list[dict[str, Any]]:
    """Give source values a stable order without interpreting or rewriting them."""

    return [
        {"source_order": source_order, "value": value}
        for source_order, value in enumerate(reference.values, start=1)
    ]


def add_missing_task_placeholders(
    tasks: list[dict[str, Any]],
    effort_task_ids: set[str],
    dependencies: list[dict[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """Append deterministic placeholders for task IDs absent from the Tasks block."""

    reference_sources: dict[str, set[str]] = {}
    for task_id in effort_task_ids:
        reference_sources.setdefault(task_id, set()).add("task_role_effort")
    for dependency in dependencies:
        for field in ("blocking_task_id", "blocked_task_id"):
            task_id = clean_value(dependency.get(field))
            if not is_blank(task_id):
                reference_sources.setdefault(str(task_id), set()).add("Task_dependencies")

    existing_task_ids = {
        str(task["task_id"])
        for task in tasks
        if not is_blank(task.get("task_id"))
    }
    missing_task_ids = sorted(set(reference_sources) - existing_task_ids)
    if not missing_task_ids:
        return {}

    task_fields = list(tasks[0].keys()) if tasks else ["task_id", "summary", "is_placeholder"]
    for task_id in missing_task_ids:
        placeholder = {field: None for field in task_fields}
        placeholder["task_id"] = task_id
        placeholder["summary"] = PLACEHOLDER_TASK_SUMMARY
        placeholder["is_placeholder"] = True
        tasks.append(placeholder)
    source_order = {"task_role_effort": 0, "Task_dependencies": 1}
    return {
        task_id: tuple(
            sorted(reference_sources[task_id], key=lambda source: source_order[source])
        )
        for task_id in missing_task_ids
    }


def normalize(parsed: ParsedDataset) -> NormalizedDataset:
    tasks = records_from_table(parsed.tasks, canonical_task_headers(parsed.tasks.headers))
    for task in tasks:
        for field in ("rung", "spent_time", "story_points", "estimated_hh"):
            task[field] = coerce_number(task.get(field))
        task.setdefault("summary", None)
        task["is_placeholder"] = False
    effort, effort_task_ids, effort_roles = melt_effort(parsed.effort)
    dependencies = records_from_table(
        parsed.dependencies,
        ["blocking_task_id", "blocked_task_id", "dependency_type"],
    )
    placeholder_sources = add_missing_task_placeholders(tasks, effort_task_ids, dependencies)
    engineers, engineer_skills = normalize_engineers(parsed.engineers)
    team_history = records_from_table(
        parsed.team_history,
        ["snapshot_date", "team_id", "velocity_achieved", "planned_sp"],
    )
    for row in team_history:
        row["velocity_achieved"] = coerce_number(row.get("velocity_achieved"))
        row["planned_sp"] = coerce_number(row.get("planned_sp"))
    return NormalizedDataset(
        tables={
            "tasks.csv": tasks,
            "task_role_effort.csv": effort,
            "task_dependencies.csv": dependencies,
            "engineers.csv": engineers,
            "engineer_skills.csv": engineer_skills,
            "team_history.csv": team_history,
            "planning_results.csv": normalize_reference_list(parsed.planning_results),
            "plan_mismatch_reasons.csv": normalize_reference_list(parsed.plan_mismatch_reasons),
            "task_closure_results.csv": normalize_reference_list(parsed.task_closure_results),
        },
        effort_task_ids=effort_task_ids,
        effort_roles=effort_roles,
        placeholder_sources=placeholder_sources,
    )
