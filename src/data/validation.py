"""Validation for normalized preprocessing output."""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Iterable

from .loader import EMPTY_ALLOWED_PROCESSED_FILES, REQUIRED_PROCESSED_FILES
from .normalization import NormalizedDataset


class DataValidationError(RuntimeError):
    """Critical preprocessing validation failures."""


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def require_success(self) -> None:
        if self.errors:
            details = "\n- ".join(self.errors)
            raise DataValidationError(f"Dataset validation failed:\n- {details}")


def is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def validate_capacity_rate(records: Iterable[dict[str, Any]]) -> list[str]:
    """Return violations for capacity rates outside the usable (0, 1] range."""

    violations: list[str] = []
    for index, record in enumerate(records, start=1):
        value = record.get("capacity_rate")
        engineer_id = record.get("engineer_id", f"row {index}")
        if not is_number(value) or not 0 < float(value) <= 1:
            violations.append(
                f"engineers row {index} ({engineer_id}) has invalid capacity_rate {value!r}; "
                "expected a number in (0, 1]."
            )
    return violations


def dependency_reference_issues(
    dependencies: Iterable[dict[str, Any]], task_ids: set[str]
) -> list[str]:
    """Return violations for raw dependency IDs absent from normalized tasks."""

    issues: list[str] = []
    for index, dependency in enumerate(dependencies, start=1):
        for column in ("blocking_task_id", "blocked_task_id"):
            task_id = dependency.get(column)
            if task_id not in task_ids:
                issues.append(
                    f"task_dependencies row {index}: {column}={task_id!r} is absent from tasks.csv."
                )
    return issues


def _validate_required_tables(dataset: NormalizedDataset, report: ValidationReport) -> None:
    for filename in REQUIRED_PROCESSED_FILES:
        if filename not in dataset.tables:
            report.errors.append(f"Required table {filename} is absent.")
        elif not dataset.tables.get(filename) and filename not in EMPTY_ALLOWED_PROCESSED_FILES:
            report.errors.append(f"Required table {filename} is empty.")


def validate_reference_list(filename: str, records: list[dict[str, Any]]) -> list[str]:
    """Validate the lossless ordered representation of a source reference list."""

    issues: list[str] = []
    expected_order = list(range(1, len(records) + 1))
    actual_order = [record.get("source_order") for record in records]
    if actual_order != expected_order:
        issues.append(
            f"{filename} source_order must be consecutive and preserve source order; "
            f"got {actual_order!r}."
        )
    for index, record in enumerate(records, start=1):
        value = record.get("value")
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{filename} row {index} has an empty or non-text value {value!r}.")
    return issues


def validate(dataset: NormalizedDataset) -> ValidationReport:
    report = ValidationReport()
    _validate_required_tables(dataset, report)

    tasks = dataset.tables["tasks.csv"]
    task_ids = [task.get("task_id") for task in tasks]
    blank_task_rows = [str(index) for index, task_id in enumerate(task_ids, start=1) if not task_id]
    if blank_task_rows:
        report.errors.append(f"tasks.csv has empty task_id values in rows: {', '.join(blank_task_rows)}.")
    nonblank_task_ids = [str(task_id) for task_id in task_ids if task_id]
    duplicates = sorted(
        {task_id for task_id in nonblank_task_ids if nonblank_task_ids.count(task_id) > 1}
    )
    if duplicates:
        report.errors.append(f"tasks.csv has duplicate task_id values: {', '.join(duplicates)}.")
    task_id_set = set(nonblank_task_ids)

    placeholder_task_ids: list[str] = []

    for index, task in enumerate(tasks, start=1):
        is_placeholder = task.get("is_placeholder")
        if not isinstance(is_placeholder, bool):
            report.errors.append(
                f"tasks.csv row {index} ({task.get('task_id')}) has invalid "
                f"is_placeholder {is_placeholder!r}."
            )
            continue
        if is_placeholder:
            placeholder_task_ids.append(str(task.get("task_id")))
            if task.get("summary") != "Unknown task":
                report.errors.append(
                    f"placeholder task {task.get('task_id')!r} must have summary 'Unknown task'."
                )
            story_points = task.get("story_points")
            if story_points is not None and (not is_number(story_points) or story_points < 0):
                report.errors.append(
                    f"placeholder task {task.get('task_id')!r} has invalid "
                    f"story_points {story_points!r}."
                )
            continue
        story_points = task.get("story_points")
        if not is_number(story_points) or story_points < 0:
            report.errors.append(
                f"tasks.csv row {index} ({task.get('task_id')}) has invalid story_points {story_points!r}."
            )

    for task_id in sorted(placeholder_task_ids):
        sources = dataset.placeholder_sources.get(task_id, ())
        if not sources:
            report.errors.append(f"placeholder task {task_id!r} has no recorded source reference.")
            continue
        source_text = " and ".join(sources)
        report.warnings.append(
            f"Placeholder task created for {task_id}: referenced by {source_text} "
            "but absent from Tasks."
        )

    effort = dataset.tables["task_role_effort.csv"]
    for index, row in enumerate(effort, start=1):
        required_hours = row.get("required_hours")
        if not row.get("task_id") or not row.get("role"):
            report.errors.append(f"task_role_effort.csv row {index} has an empty task_id or role.")
        if not is_number(required_hours) or required_hours < 0:
            report.errors.append(
                f"task_role_effort.csv row {index} has invalid required_hours {required_hours!r}."
            )
    effort_task_ids = {str(row["task_id"]) for row in effort if row.get("task_id")}
    effort_roles = {str(row["role"]) for row in effort if row.get("role")}
    if effort_task_ids != dataset.effort_task_ids:
        report.errors.append("Some task identifiers were lost while melting the effort matrix.")
    if effort_roles != dataset.effort_roles:
        report.errors.append("Some roles were lost while melting the effort matrix.")
    extra_effort_tasks = sorted(effort_task_ids - task_id_set)
    if extra_effort_tasks:
        report.errors.append(
            "task_role_effort.csv references task IDs absent from tasks.csv: "
            + ", ".join(extra_effort_tasks)
            + "."
        )

    dependencies = dataset.tables["task_dependencies.csv"]
    for index, dependency in enumerate(dependencies, start=1):
        blocking_task_id = dependency.get("blocking_task_id")
        blocked_task_id = dependency.get("blocked_task_id")
        if not blocking_task_id or not blocked_task_id or not dependency.get("dependency_type"):
            report.errors.append(f"task_dependencies row {index} has an empty required field.")
        if blocking_task_id == blocked_task_id and blocking_task_id:
            report.errors.append(
                f"task_dependencies row {index} contains a self-dependency {blocking_task_id!r}."
            )
    report.errors.extend(dependency_reference_issues(dependencies, task_id_set))

    engineers = dataset.tables["engineers.csv"]
    report.errors.extend(validate_capacity_rate(engineers))
    engineer_ids = {str(engineer["engineer_id"]) for engineer in engineers if engineer.get("engineer_id")}
    for index, skill in enumerate(dataset.tables["engineer_skills.csv"], start=1):
        if skill.get("engineer_id") not in engineer_ids:
            report.errors.append(
                f"engineer_skills row {index} references absent engineer {skill.get('engineer_id')!r}."
            )
        if not skill.get("technology"):
            report.errors.append(f"engineer_skills row {index} has an empty technology.")

    for index, requirement in enumerate(dataset.tables["task_skill_requirements.csv"], start=1):
        if requirement.get("task_id") not in task_id_set:
            report.errors.append(
                "task_skill_requirements row "
                f"{index} references absent task {requirement.get('task_id')!r}."
            )
        if not requirement.get("technology"):
            report.errors.append(
                f"task_skill_requirements row {index} has an empty technology."
            )
        if requirement.get("confidence") not in {"high", "medium", "low"}:
            report.errors.append(
                f"task_skill_requirements row {index} has invalid confidence."
            )

    for filename in (
        "planning_results.csv",
        "plan_mismatch_reasons.csv",
        "task_closure_results.csv",
    ):
        report.errors.extend(validate_reference_list(filename, dataset.tables[filename]))

    return report
