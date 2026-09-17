"""Audit the processed planning dataset without changing source data.

The script intentionally performs detection and classification only.  It does
not normalize spellings, repair references, fill missing values, or invoke the
planner.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "processed"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "reports" / "data_audit_report.md"

REQUIRED_FILES = (
    "tasks.csv",
    "task_role_effort.csv",
    "task_dependencies.csv",
    "engineers.csv",
    "engineer_skills.csv",
    "team_history.csv",
)


@dataclass(frozen=True)
class IssueDefinition:
    section: str
    category: str
    planning_impact: str
    recommendation: str
    priority: int
    ask_organizers: bool = False


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    task_ids: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    engineer_ids: tuple[str, ...] = ()


class AuditSeverity(str, Enum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True)
class AuditIssueResult:
    issue_code: str
    severity: AuditSeverity
    message: str
    affected_entity_type: str
    affected_entity_ids: tuple[str, ...]
    planning_impact: str
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_code": self.issue_code,
            "severity": self.severity.value,
            "message": self.message,
            "affected_entity_type": self.affected_entity_type,
            "affected_entity_ids": list(self.affected_entity_ids),
            "planning_impact": self.planning_impact,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class DatasetAuditResult:
    status: str
    planning_allowed: bool
    issues: tuple[AuditIssueResult, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            severity.value: sum(issue.severity == severity for issue in self.issues)
            for severity in AuditSeverity
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "planning_allowed": self.planning_allowed,
            "counts": self.counts,
            "issues": [issue.to_dict() for issue in self.issues],
        }


# The order is also the order used in the full issue-count table.
ISSUE_DEFINITIONS: dict[str, IssueDefinition] = {
    "UNRESOLVED_PLACEHOLDER_TASK": IssueDefinition(
        "critical",
        "Source task coverage",
        "The planner has effort or dependency information but no business attributes, SP, team, status, or dates for the task.",
        "Ask the organizers for the missing Tasks rows; until clarified, keep these tasks unplannable placeholders.",
        10,
        True,
    ),
    "DEPENDENCY_REFERENCES_UNKNOWN_TASK": IssueDefinition(
        "critical",
        "Dependencies",
        "A dependency cannot be evaluated because an endpoint is absent even from processed tasks.",
        "Request the missing task or correct dependency identifier; do not silently drop the edge.",
        11,
        True,
    ),
    "DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK": IssueDefinition(
        "critical",
        "Dependencies",
        "The dependency is formally connected to a processed placeholder, but the source task is missing, so readiness and completion cannot be established.",
        "Ask the organizers for the missing task record and treat the dependency as unresolved until then.",
        12,
        True,
    ),
    "SELF_DEPENDENCY": IssueDefinition(
        "critical",
        "Dependencies",
        "A task can never satisfy a dependency on itself.",
        "Confirm and correct the dependency row with the organizers.",
        13,
        True,
    ),
    "DEPENDENCY_CYCLE": IssueDefinition(
        "critical",
        "Dependencies",
        "Tasks in the cycle cannot be placed in a valid predecessor order.",
        "Clarify or remove the incorrect edge; otherwise mark the cycle as impossible to plan.",
        14,
        True,
    ),
    "DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED": IssueDefinition(
        "critical",
        "Dependencies",
        "A wrong interpretation of blocking_task_id and blocked_task_id reverses prerequisite order and invalidates the plan.",
        "Ask the organizers to confirm the direction of every dependency_type before using dates or sprint order as hard constraints.",
        15,
        True,
    ),
    "DONE_DEPENDENCY_CONFLICT": IssueDefinition(
        "data_quality",
        "Dependencies",
        "A source task is already Done although its recorded blocker is not Done; the factual status and dependency graph disagree.",
        "Keep the task completed for planning and ask the organizers to correct or explain the dependency.",
        16,
        True,
    ),
    "TASK_WITHOUT_EXISTING_TEAM": IssueDefinition(
        "critical",
        "Teams",
        "The task has no team capacity, velocity, or eligible engineer pool and therefore cannot be scheduled reliably.",
        "Ask for the correct team; until then, treat the task as unplannable rather than assigning a default team.",
        20,
        True,
    ),
    "NO_AVAILABLE_SPECIALISTS_FOR_ROLE": IssueDefinition(
        "critical",
        "Roles",
        "Tasks require positive hours for a role with zero exact-match engineers, making their role-hours infeasible.",
        "Confirm role naming or staffing; if no mapping or specialist exists, mark affected tasks infeasible.",
        30,
        True,
    ),
    "TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE": IssueDefinition(
        "critical",
        "Skills",
        "Technology fit cannot be checked, so a plan may assign engineers who do not have required competencies.",
        "Ask for task-level skill requirements or a documented role/task-to-skill mapping; do not infer skills from summaries.",
        31,
        True,
    ),
    "SKILL_WITHOUT_AVAILABLE_ENGINEER": IssueDefinition(
        "critical",
        "Skills",
        "No engineer declares an exact-match skill required by the affected tasks.",
        "Confirm spelling/mapping or add staffing; otherwise mark affected tasks infeasible.",
        32,
        True,
    ),
    "INITIATIVE_DEPENDS_ON_MISSING_TASK": IssueDefinition(
        "critical",
        "Initiatives",
        "The initiative touches a dependency whose other endpoint is a source-missing placeholder, so completion cannot be certified.",
        "Ask which initiative owns the placeholder and restore the missing task record before assessing initiative completion.",
        33,
        True,
    ),
    "DEPENDENCY_REQUIRED_FIELD_MISSING": IssueDefinition(
        "data_quality",
        "Dependencies",
        "The dependency row cannot be interpreted as a complete directed relationship.",
        "Ask for the missing endpoint or dependency type; preserve the raw row until clarified.",
        100,
        True,
    ),
    "DUPLICATE_DEPENDENCY_EDGE": IssueDefinition(
        "data_quality",
        "Dependencies",
        "Repeated edges can double-count constraints or encode contradictory relationship types.",
        "Confirm whether repeated edges are intentional and choose one documented semantic representation.",
        101,
        True,
    ),
    "TASK_ROLE_EFFORT_UNKNOWN_TASK": IssueDefinition(
        "data_quality",
        "Hours",
        "Effort cannot be joined to a task and may be ignored or assigned incorrectly.",
        "Request the task row or correct the task_id; do not discard the effort record.",
        102,
        True,
    ),
    "ENGINEER_SKILL_UNKNOWN_ENGINEER": IssueDefinition(
        "data_quality",
        "Skills",
        "A skill cannot contribute to capacity because its engineer profile is absent.",
        "Restore the engineer profile or correct engineer_id.",
        103,
        True,
    ),
    "TASK_SKILL_UNKNOWN_TASK": IssueDefinition(
        "data_quality",
        "Skills",
        "A task skill requirement cannot be joined to a task.",
        "Restore the task row or correct task_id.",
        104,
        True,
    ),
    "TEAM_WITHOUT_ENGINEERS": IssueDefinition(
        "critical",
        "Teams",
        "The team has historical identity but no engineer capacity, so its tasks cannot be resourced by the current profiles.",
        "Clarify the team roster or treat the team as having zero role capacity.",
        34,
        True,
    ),
    "ENGINEER_WITHOUT_TEAM": IssueDefinition(
        "data_quality",
        "Teams",
        "The engineer cannot be assigned to a team-scoped plan without an explicit allocation rule.",
        "Ask for team membership; do not allocate the engineer globally by default.",
        110,
        True,
    ),
    "ENGINEER_UNKNOWN_TEAM": IssueDefinition(
        "data_quality",
        "Teams",
        "The engineer references a team absent from the team registry proxy, so team capacity may be incomplete.",
        "Confirm the team identifier or extend the authoritative team registry.",
        111,
        True,
    ),
    "BLANK_TASK_ID": IssueDefinition(
        "data_quality",
        "Identifiers",
        "Rows without task_id cannot be joined across tables or traced in planner output.",
        "Request stable task identifiers; do not generate business IDs automatically.",
        120,
        True,
    ),
    "BLANK_ENGINEER_ID": IssueDefinition(
        "data_quality",
        "Identifiers",
        "An engineer row without engineer_id cannot be deduplicated or capacity-limited per person.",
        "Request a stable engineer identifier.",
        121,
        True,
    ),
    "DUPLICATE_TASK_ID": IssueDefinition(
        "data_quality",
        "Identifiers",
        "Multiple task records make status, SP, team, and dates ambiguous.",
        "Ask which task row is authoritative; do not merge automatically.",
        122,
        True,
    ),
    "DUPLICATE_ENGINEER_ID": IssueDefinition(
        "data_quality",
        "Identifiers",
        "Repeated engineer IDs identify one person with separate team-specific capacity buckets.",
        "Keep each unique engineer-team bucket and deduplicate only repeated rows of the same pair.",
        123,
        True,
    ),
    "DUPLICATE_INITIATIVE_ID": IssueDefinition(
        "data_quality",
        "Identifiers",
        "Duplicate rows in an initiative registry make initiative attributes ambiguous.",
        "Ask which initiative registry row is authoritative.",
        124,
        True,
    ),
    "INITIATIVE_REGISTRY_NOT_AVAILABLE": IssueDefinition(
        "data_quality",
        "Initiatives",
        "Initiatives without tasks and duplicate initiative entities cannot be detected from task foreign keys alone.",
        "Ask for an authoritative initiative registry; do not treat repeated task initiative_id values as duplicate initiatives.",
        125,
        True,
    ),
    "BLANK_INITIATIVE_ID": IssueDefinition(
        "data_quality",
        "Initiatives",
        "The task cannot contribute to initiative completion or PI Predictability.",
        "Ask for the owning PRODF initiative or explicitly classify the task as initiative-independent.",
        126,
        True,
    ),
    "INITIATIVE_WITHOUT_TASKS": IssueDefinition(
        "data_quality",
        "Initiatives",
        "The initiative has no work items from which completion can be calculated.",
        "Confirm whether tasks are missing or the initiative should be excluded from the quarter baseline.",
        127,
        True,
    ),
    "MISSING_STORY_POINTS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "Team SP capacity and prioritization cannot be evaluated for the task.",
        "Request an SP estimate or route the task through an explicit, documented fallback.",
        130,
        True,
    ),
    "INVALID_STORY_POINTS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "A non-numeric SP value cannot be used in capacity calculations.",
        "Correct the source value after confirmation; do not coerce arbitrary text.",
        131,
        True,
    ),
    "NON_POSITIVE_STORY_POINTS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "Zero or negative SP can bypass team-capacity constraints or invert totals.",
        "Confirm whether zero is intentional; negative values must be corrected before planning.",
        132,
        True,
    ),
    "MISSING_REQUIRED_HOURS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "Role capacity cannot be fully checked for the effort row.",
        "Request the estimate; preserve missing as unknown rather than zero.",
        133,
        True,
    ),
    "INVALID_REQUIRED_HOURS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "A non-numeric effort value cannot be allocated.",
        "Correct the source value after confirmation.",
        134,
        True,
    ),
    "NEGATIVE_REQUIRED_HOURS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "Negative effort creates capacity instead of consuming it.",
        "Correct the source estimate before planning.",
        135,
        True,
    ),
    "STORY_POINTS_WITHOUT_HOURS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "The task fits SP capacity but cannot be checked against roles and engineer hours.",
        "Request role-hour estimates or apply an explicit fallback that keeps the uncertainty visible.",
        136,
        True,
    ),
    "HOURS_WITHOUT_STORY_POINTS": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "Role-hours exist, but the task cannot be constrained by team velocity or included reliably in SP KPIs.",
        "Request SP; until then, keep the task out of normal SP-based planning or use a visible fallback.",
        137,
        True,
    ),
    "TASK_HOURS_TOTAL_MISMATCH": IssueDefinition(
        "data_quality",
        "Story points and hours",
        "Task estimated_hh and the sum of role-hours imply different workload totals.",
        "Ask which estimate is authoritative; do not scale role-hours automatically.",
        138,
        True,
    ),
    "CAPACITY_RATE_NULL": IssueDefinition(
        "data_quality",
        "Capacity rate",
        "The engineer's available hours cannot be calculated.",
        "Request capacity_rate or exclude the engineer from available capacity until clarified.",
        140,
        True,
    ),
    "CAPACITY_RATE_INVALID": IssueDefinition(
        "data_quality",
        "Capacity rate",
        "A non-numeric capacity rate cannot be converted to available hours.",
        "Correct the source value after confirmation.",
        141,
        True,
    ),
    "CAPACITY_RATE_NEGATIVE": IssueDefinition(
        "data_quality",
        "Capacity rate",
        "Negative availability corrupts capacity totals.",
        "Correct the source value before planning.",
        142,
        True,
    ),
    "CAPACITY_RATE_ABOVE_ONE": IssueDefinition(
        "data_quality",
        "Capacity rate",
        "Availability above one exceeds the documented full-time capacity model.",
        "Confirm whether overtime is intended; otherwise correct the value.",
        143,
        True,
    ),
    "CAPACITY_RATE_UNEXPECTED": IssueDefinition(
        "data_quality",
        "Capacity rate",
        "The value is inside 0..1 but outside the allowed catalog {0, 0.5, 1}.",
        "Ask whether the allowed catalog should be expanded; do not round automatically.",
        144,
        True,
    ),
    "STATUS_REFERENCE_NOT_AVAILABLE": IssueDefinition(
        "data_quality",
        "Statuses",
        "Unknown statuses cannot be distinguished from valid source statuses without an independent catalog.",
        "Ask for the official task-status catalog and add it to processed data.",
        150,
        True,
    ),
    "TASK_STATUS_MISSING": IssueDefinition(
        "data_quality",
        "Statuses",
        "The planner cannot decide whether the task is eligible, completed, or already in progress.",
        "Request a status; do not assume ToDo.",
        151,
        True,
    ),
    "UNKNOWN_TASK_STATUS": IssueDefinition(
        "data_quality",
        "Statuses",
        "The planner has no documented behavior for this status.",
        "Map the value only after organizers confirm its meaning.",
        152,
        True,
    ),
    "PLANNED_START_MISSING": IssueDefinition(
        "data_quality",
        "Planned dates",
        "Baseline schedule comparisons and date-quality checks are incomplete.",
        "Request the planned start or keep it explicitly unknown; do not use dates as hard constraints.",
        160,
        False,
    ),
    "PLANNED_END_MISSING": IssueDefinition(
        "data_quality",
        "Planned dates",
        "Deadline and interval analysis are incomplete.",
        "Request the planned end or keep it explicitly unknown; do not use dates as hard constraints.",
        161,
        False,
    ),
    "TASK_WITHOUT_PLANNED_DATES": IssueDefinition(
        "data_quality",
        "Planned dates",
        "The task has no usable baseline interval for schedule-risk reporting.",
        "Ask for both dates if baseline comparison is required.",
        162,
        False,
    ),
    "INVALID_PLANNED_DATE": IssueDefinition(
        "data_quality",
        "Planned dates",
        "The date cannot be ordered or compared with the quarter horizon.",
        "Correct the date format in the source after confirmation.",
        163,
        True,
    ),
    "PLANNED_END_BEFORE_START": IssueDefinition(
        "data_quality",
        "Planned dates",
        "The baseline interval is chronologically impossible.",
        "Ask which date is correct; do not swap dates automatically.",
        164,
        True,
    ),
    "SUSPICIOUS_PLANNED_INTERVAL": IssueDefinition(
        "data_quality",
        "Planned dates",
        "The baseline interval exceeds the 12-week planning horizon and may distort deadline-risk interpretation.",
        "Confirm whether the interval intentionally spans quarters; keep it informational rather than a hard constraint.",
        165,
        False,
    ),
    "IN_PROGRESS_MISSING_SPENT_TIME": IssueDefinition(
        "data_quality",
        "In progress and spent time",
        "Remaining effort for active work cannot be calculated from actual consumption.",
        "Request spent_time or define an explicit remaining-effort fallback.",
        170,
        True,
    ),
    "INVALID_SPENT_TIME": IssueDefinition(
        "data_quality",
        "In progress and spent time",
        "Actual effort cannot be compared with the estimate.",
        "Correct the source value after confirmation.",
        171,
        True,
    ),
    "NEGATIVE_SPENT_TIME": IssueDefinition(
        "data_quality",
        "In progress and spent time",
        "Negative actual effort would increase the remaining estimate.",
        "Correct the source value before replanning.",
        172,
        True,
    ),
    "SPENT_TIME_EXCEEDS_ESTIMATE": IssueDefinition(
        "data_quality",
        "In progress and spent time",
        "A naïve remaining-effort calculation becomes negative and hides overrun.",
        "Keep the overrun explicit and ask whether a revised estimate is available.",
        173,
        False,
    ),
    "SPENT_TIME_WITHOUT_ESTIMATE": IssueDefinition(
        "data_quality",
        "In progress and spent time",
        "Actual effort exists but remaining effort cannot be derived.",
        "Request an estimate or use a documented fallback.",
        174,
        True,
    ),
    "POSSIBLE_ROLE_SPELLING_VARIANT": IssueDefinition(
        "data_quality",
        "Roles",
        "Visually similar role labels remain unmatched, which may create a false staffing deficit or hide a real taxonomy difference.",
        "Ask organizers to approve an explicit role mapping; keep exact matching until then.",
        180,
        True,
    ),
}


class AuditError(RuntimeError):
    """Raised when the audit cannot safely read its required inputs."""


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_true(value: Any) -> bool:
    return clean(value).casefold() in {"1", "true", "yes", "да"}


def parse_decimal(value: Any) -> tuple[str, Decimal | None]:
    text = clean(value)
    if not text:
        return "blank", None
    try:
        return "ok", Decimal(text.replace(",", "."))
    except InvalidOperation:
        return "invalid", None


def parse_datetime(value: Any) -> tuple[str, datetime | None]:
    text = clean(value)
    if not text:
        return "blank", None
    try:
        return "ok", datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return "invalid", None


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                raise AuditError(f"{path} has no CSV header.")
            return [dict(row) for row in reader]
    except OSError as error:
        raise AuditError(f"Could not read {path}: {error}") from error


def require_columns(filename: str, rows: list[dict[str, str]], columns: Sequence[str]) -> None:
    if not rows:
        raise AuditError(f"Required processed table {filename} is empty.")
    missing = [column for column in columns if column not in rows[0]]
    if missing:
        raise AuditError(f"{filename} is missing columns: {', '.join(missing)}.")


def split_list(value: Any) -> list[str]:
    """Split comma/semicolon lists, preserving separators inside brackets."""

    text = clean(value)
    if not text:
        return []
    result: list[str] = []
    current: list[str] = []
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = set(pairs.values())
    for character in text:
        if character in pairs:
            depth += 1
        elif character in closing and depth:
            depth -= 1
        if character in {",", ";"} and depth == 0:
            item = " ".join("".join(current).split())
            if item:
                result.append(item)
            current = []
        else:
            current.append(character)
    item = " ".join("".join(current).split())
    if item:
        result.append(item)
    return result


def format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")


def exact_ids(rows: Iterable[dict[str, str]], field: str) -> list[str]:
    return [clean(row.get(field)) for row in rows if clean(row.get(field))]


def duplicate_values(values: Iterable[str]) -> dict[str, int]:
    counts = Counter(values)
    return {value: count for value, count in sorted(counts.items()) if count > 1}


def compact(values: Iterable[str], limit: int = 10) -> str:
    unique = sorted({clean(value) for value in values if clean(value)})
    if not unique:
        return "—"
    if len(unique) <= limit:
        return ", ".join(f"`{value}`" for value in unique)
    shown = ", ".join(f"`{value}`" for value in unique[:limit])
    return f"{shown}, … (+{len(unique) - limit})"


def md_escape(value: Any) -> str:
    return clean(value).replace("|", "\\|").replace("\n", " ")


def normalized_role_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def strongly_connected_components(edges: Iterable[tuple[str, str]]) -> list[list[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for source, target in edges:
        if source and target:
            graph[source].add(target)
            nodes.update((source, target))

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in sorted(graph.get(node, ())):
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])

        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while stack:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return sorted((component for component in components if len(component) > 1), key=lambda item: item)


def find_optional_file(input_dir: Path, candidates: Sequence[str]) -> Path | None:
    for candidate in candidates:
        path = input_dir / candidate
        if path.is_file():
            return path
    return None


def first_existing_column(rows: list[dict[str, str]], candidates: Sequence[str]) -> str | None:
    if not rows:
        return None
    for candidate in candidates:
        if candidate in rows[0]:
            return candidate
    return None


def add_issue(
    issues: list[Issue],
    code: str,
    message: str,
    task_ids: Iterable[str] = (),
    teams: Iterable[str] = (),
    engineer_ids: Iterable[str] = (),
) -> None:
    if code not in ISSUE_DEFINITIONS:
        raise AuditError(f"Internal audit error: undefined issue code {code}.")
    issues.append(
        Issue(
            code=code,
            message=message,
            task_ids=tuple(sorted({clean(value) for value in task_ids if clean(value)})),
            teams=tuple(sorted({clean(value) for value in teams if clean(value)})),
            engineer_ids=tuple(
                sorted({clean(value) for value in engineer_ids if clean(value)})
            ),
        )
    )


def done_dependency_conflicts(
    tasks: Sequence[dict[str, Any]],
    dependencies: Sequence[dict[str, Any]],
) -> list[Issue]:
    """Return data-quality findings without changing factual Done statuses."""

    task_by_id = {
        clean(task.get("task_id")): task
        for task in tasks
        if clean(task.get("task_id"))
    }
    findings: list[Issue] = []
    for dependency in dependencies:
        blocking = clean(dependency.get("blocking_task_id"))
        blocked = clean(dependency.get("blocked_task_id"))
        blocked_status = clean(task_by_id.get(blocked, {}).get("status"))
        blocking_status = clean(task_by_id.get(blocking, {}).get("status"))
        if blocked_status.casefold() != "done" or blocking_status.casefold() == "done":
            continue
        add_issue(
            findings,
            "DONE_DEPENDENCY_CONFLICT",
            (
                f"Done task {blocked} is recorded as blocked by {blocking}, whose "
                f"status is {blocking_status or '<missing>'}."
            ),
            [blocking, blocked],
        )
    return findings


def audit_dataset(input_dir: Path, suspicious_date_days: int) -> dict[str, Any]:
    for filename in REQUIRED_FILES:
        if not (input_dir / filename).is_file():
            raise AuditError(f"Required processed file is missing: {input_dir / filename}")

    tasks = read_csv(input_dir / "tasks.csv")
    effort = read_csv(input_dir / "task_role_effort.csv")
    dependencies = read_csv(input_dir / "task_dependencies.csv")
    engineers = read_csv(input_dir / "engineers.csv")
    engineer_skills = read_csv(input_dir / "engineer_skills.csv")
    team_history = read_csv(input_dir / "team_history.csv")

    require_columns(
        "tasks.csv",
        tasks,
        (
            "task_id",
            "initiative_id",
            "team_id",
            "status",
            "story_points",
            "spent_time",
            "estimated_hh",
            "planned_start",
            "planned_end",
            "is_placeholder",
        ),
    )
    require_columns("task_role_effort.csv", effort, ("task_id", "role", "required_hours"))
    require_columns(
        "task_dependencies.csv",
        dependencies,
        ("blocking_task_id", "blocked_task_id", "dependency_type"),
    )
    require_columns(
        "engineers.csv",
        engineers,
        ("engineer_id", "team_id", "role", "capacity_rate"),
    )
    require_columns("engineer_skills.csv", engineer_skills, ("engineer_id", "technology"))
    require_columns("team_history.csv", team_history, ("team_id",))

    issues: list[Issue] = []
    task_ids = exact_ids(tasks, "task_id")
    task_id_set = set(task_ids)
    task_rows_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for task in tasks:
        task_rows_by_id[clean(task.get("task_id"))].append(task)
    task_by_id = {task_id: rows[0] for task_id, rows in task_rows_by_id.items() if task_id}
    placeholder_ids = {
        clean(task.get("task_id"))
        for task in tasks
        if clean(task.get("task_id")) and is_true(task.get("is_placeholder"))
    }
    issues.extend(done_dependency_conflicts(tasks, dependencies))

    # 5. Identifiers and duplicates.
    blank_task_rows = [str(index) for index, row in enumerate(tasks, start=2) if not clean(row.get("task_id"))]
    if blank_task_rows:
        add_issue(
            issues,
            "BLANK_TASK_ID",
            f"tasks.csv contains {len(blank_task_rows)} rows with blank task_id (CSV rows {', '.join(blank_task_rows)}).",
        )
    duplicate_task_ids = duplicate_values(task_ids)
    for task_id, count in duplicate_task_ids.items():
        add_issue(
            issues,
            "DUPLICATE_TASK_ID",
            f"task_id {task_id} occurs {count} times in tasks.csv.",
            [task_id],
        )

    engineer_ids = exact_ids(engineers, "engineer_id")
    engineer_id_set = set(engineer_ids)
    blank_engineer_rows = [
        str(index) for index, row in enumerate(engineers, start=2) if not clean(row.get("engineer_id"))
    ]
    if blank_engineer_rows:
        add_issue(
            issues,
            "BLANK_ENGINEER_ID",
            f"engineers.csv contains {len(blank_engineer_rows)} rows with blank engineer_id (CSV rows {', '.join(blank_engineer_rows)}).",
        )
    duplicate_engineer_ids = duplicate_values(engineer_ids)
    for engineer_id, count in duplicate_engineer_ids.items():
        memberships = sorted(
            {
                clean(row.get("team_id"))
                for row in engineers
                if clean(row.get("engineer_id")) == engineer_id and clean(row.get("team_id"))
            }
        )
        add_issue(
            issues,
            "DUPLICATE_ENGINEER_ID",
            f"engineer_id {engineer_id} occurs {count} times; teams: {', '.join(memberships) or 'none'}.",
            teams=memberships,
            engineer_ids=[engineer_id],
        )

    for task_id in sorted(placeholder_ids):
        add_issue(
            issues,
            "UNRESOLVED_PLACEHOLDER_TASK",
            f"{task_id} exists only as a processed placeholder and is absent from the source Tasks block.",
            [task_id],
        )

    # 4. Teams. team_history is the only available team registry proxy unless
    # a future teams.csv is supplied.
    teams_file = find_optional_file(input_dir, ("teams.csv",))
    if teams_file:
        team_rows = read_csv(teams_file)
        team_column = first_existing_column(team_rows, ("team_id", "id"))
        if not team_column:
            raise AuditError(f"{teams_file.name} has no team_id column.")
        known_teams = set(exact_ids(team_rows, team_column))
        team_registry_source = teams_file.name
    else:
        known_teams = set(exact_ids(team_history, "team_id"))
        team_registry_source = "team_history.csv (registry proxy)"

    task_team_problems: list[dict[str, str]] = []
    for task in tasks:
        task_id = clean(task.get("task_id"))
        team_id = clean(task.get("team_id"))
        if not team_id or team_id not in known_teams:
            reason = "blank team_id" if not team_id else "team absent from registry"
            task_team_problems.append({"task_id": task_id, "team_id": team_id, "reason": reason})
            add_issue(
                issues,
                "TASK_WITHOUT_EXISTING_TEAM",
                f"Task {task_id or '<blank task_id>'} has {reason}: {team_id or '<blank>'}.",
                [task_id],
                [team_id],
            )

    engineer_team_ids = set(exact_ids(engineers, "team_id"))
    for team_id in sorted(known_teams - engineer_team_ids):
        affected = [
            clean(task.get("task_id"))
            for task in tasks
            if clean(task.get("team_id")) == team_id
        ]
        add_issue(
            issues,
            "TEAM_WITHOUT_ENGINEERS",
            f"Team {team_id} has no engineer profiles.",
            affected,
            [team_id],
        )
    for index, engineer in enumerate(engineers, start=2):
        engineer_id = clean(engineer.get("engineer_id"))
        team_id = clean(engineer.get("team_id"))
        if not team_id:
            add_issue(
                issues,
                "ENGINEER_WITHOUT_TEAM",
                f"Engineer {engineer_id or f'CSV row {index}'} has blank team_id.",
            )
        elif team_id not in known_teams:
            add_issue(
                issues,
                "ENGINEER_UNKNOWN_TEAM",
                f"Engineer {engineer_id or f'CSV row {index}'} references unknown team {team_id}.",
                teams=[team_id],
            )

    # 1. Roles and specialists. Processed ``role`` is canonicalized by
    # preprocessing, then matched exactly here. The audit applies no additional
    # implicit aliasing; optional role_raw remains diagnostic context only.
    available_role_engineers: dict[str, set[str]] = defaultdict(set)
    for engineer in engineers:
        role = clean(engineer.get("role"))
        engineer_id = clean(engineer.get("engineer_id"))
        if role and engineer_id:
            available_role_engineers[role].add(engineer_id)

    role_demands: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"task_ids": set(), "teams": set(), "hours": Decimal("0")}
    )
    task_positive_hours: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    task_has_valid_effort: set[str] = set()
    role_labels = set()
    for index, row in enumerate(effort, start=2):
        task_id = clean(row.get("task_id"))
        role = clean(row.get("role"))
        role_labels.add(role)
        if not task_id:
            add_issue(
                issues,
                "BLANK_TASK_ID",
                f"task_role_effort.csv row {index} has blank task_id.",
            )
        elif task_id not in task_id_set:
            add_issue(
                issues,
                "TASK_ROLE_EFFORT_UNKNOWN_TASK",
                f"task_role_effort.csv row {index} references absent task {task_id}.",
                [task_id],
            )
        number_status, hours = parse_decimal(row.get("required_hours"))
        if number_status == "blank":
            add_issue(
                issues,
                "MISSING_REQUIRED_HOURS",
                f"task_role_effort.csv row {index} ({task_id}, {role}) has missing required_hours.",
                [task_id],
            )
            continue
        if number_status == "invalid" or hours is None:
            add_issue(
                issues,
                "INVALID_REQUIRED_HOURS",
                f"task_role_effort.csv row {index} ({task_id}, {role}) has non-numeric required_hours={row.get('required_hours')!r}.",
                [task_id],
            )
            continue
        task_has_valid_effort.add(task_id)
        if hours < 0:
            add_issue(
                issues,
                "NEGATIVE_REQUIRED_HOURS",
                f"Task {task_id}, role {role}, has required_hours={format_decimal(hours)}.",
                [task_id],
            )
        if hours > 0:
            task_positive_hours[task_id] += hours
            demand = role_demands[role]
            demand["task_ids"].add(task_id)
            task = task_by_id.get(task_id, {})
            team_id = clean(task.get("team_id"))
            if team_id:
                demand["teams"].add(team_id)
            demand["hours"] += hours

    missing_role_rows: list[dict[str, Any]] = []
    engineer_roles = set(available_role_engineers)
    for role in sorted(role_demands):
        demand = role_demands[role]
        available = available_role_engineers.get(role, set())
        if available:
            continue
        task_list = sorted(demand["task_ids"])
        team_list = sorted(demand["teams"])
        missing_role_rows.append(
            {
                "role": role,
                "hours": demand["hours"],
                "task_ids": task_list,
                "teams": team_list,
                "available": 0,
            }
        )
        add_issue(
            issues,
            "NO_AVAILABLE_SPECIALISTS_FOR_ROLE",
            f"Role {role} requires {format_decimal(demand['hours'])} hours across {len(task_list)} tasks, but has 0 exact-match engineers.",
            task_list,
            team_list,
        )

        role_key = normalized_role_key(role)
        candidates = sorted(
            candidate
            for candidate in engineer_roles
            if candidate != role and normalized_role_key(candidate) == role_key
        )
        if candidates:
            add_issue(
                issues,
                "POSSIBLE_ROLE_SPELLING_VARIANT",
                f"Required role {role!r} has no exact match; possible engineer-role variants: {', '.join(repr(item) for item in candidates)}. No additional mapping was applied during audit.",
                task_list,
                team_list,
            )

    # 2. Skills. Use task_skills.csv when present, otherwise inspect explicit
    # task fields. Never infer requirements from role or summary.
    declared_engineer_skills = {
        clean(row.get("technology")) for row in engineer_skills if clean(row.get("technology"))
    }
    for index, skill_row in enumerate(engineer_skills, start=2):
        engineer_id = clean(skill_row.get("engineer_id"))
        if engineer_id and engineer_id not in engineer_id_set:
            add_issue(
                issues,
                "ENGINEER_SKILL_UNKNOWN_ENGINEER",
                f"engineer_skills.csv row {index} references absent engineer {engineer_id}.",
            )

    task_skill_requirements: dict[str, set[str]] = defaultdict(set)
    task_skills_source = "not available"
    task_skills_file = find_optional_file(input_dir, ("task_skills.csv", "task_required_skills.csv"))
    if task_skills_file:
        task_skill_rows = read_csv(task_skills_file)
        task_column = first_existing_column(task_skill_rows, ("task_id",))
        skill_column = first_existing_column(
            task_skill_rows,
            ("technology", "skill", "required_skill", "skills", "skills_required"),
        )
        if not task_column or not skill_column:
            raise AuditError(
                f"{task_skills_file.name} must contain task_id and a skill/technology column."
            )
        task_skills_source = task_skills_file.name
        for index, row in enumerate(task_skill_rows, start=2):
            task_id = clean(row.get(task_column))
            if task_id and task_id not in task_id_set:
                add_issue(
                    issues,
                    "TASK_SKILL_UNKNOWN_TASK",
                    f"{task_skills_file.name} row {index} references absent task {task_id}.",
                    [task_id],
                )
            for skill in split_list(row.get(skill_column)):
                task_skill_requirements[task_id].add(skill)
    else:
        skills_field = first_existing_column(
            tasks,
            ("skills_required", "required_skills", "skills", "technologies", "technology"),
        )
        if skills_field:
            task_skills_source = f"tasks.csv:{skills_field}"
            for task in tasks:
                task_id = clean(task.get("task_id"))
                for skill in split_list(task.get(skills_field)):
                    task_skill_requirements[task_id].add(skill)
        else:
            add_issue(
                issues,
                "TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE",
                "No task_skills.csv and no explicit task skill/technology column exist in the processed dataset.",
                [task_id for task_id in task_ids if task_id not in placeholder_ids],
                [clean(task.get("team_id")) for task in tasks],
            )

    missing_skill_rows: list[dict[str, Any]] = []
    skill_to_tasks: dict[str, set[str]] = defaultdict(set)
    for task_id, skills in task_skill_requirements.items():
        for skill in skills:
            skill_to_tasks[skill].add(task_id)
    for skill in sorted(skill_to_tasks):
        if skill in declared_engineer_skills:
            continue
        affected_tasks = sorted(skill_to_tasks[skill])
        affected_teams = sorted(
            {
                clean(task_by_id.get(task_id, {}).get("team_id"))
                for task_id in affected_tasks
                if clean(task_by_id.get(task_id, {}).get("team_id"))
            }
        )
        missing_skill_rows.append(
            {"skill": skill, "task_ids": affected_tasks, "teams": affected_teams}
        )
        add_issue(
            issues,
            "SKILL_WITHOUT_AVAILABLE_ENGINEER",
            f"Skill {skill} is required by {len(affected_tasks)} tasks and declared by 0 engineers (exact match).",
            affected_tasks,
            affected_teams,
        )

    # 3. Dependencies.
    dependency_problem_rows: list[dict[str, str]] = []
    dependency_edges: list[tuple[str, str]] = []
    edge_rows: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for index, dependency in enumerate(dependencies, start=2):
        blocking = clean(dependency.get("blocking_task_id"))
        blocked = clean(dependency.get("blocked_task_id"))
        dependency_type = clean(dependency.get("dependency_type"))
        row_problem_codes: list[str] = []
        if not blocking or not blocked or not dependency_type:
            row_problem_codes.append("DEPENDENCY_REQUIRED_FIELD_MISSING")
            add_issue(
                issues,
                "DEPENDENCY_REQUIRED_FIELD_MISSING",
                f"Dependency CSV row {index}: type={dependency_type or '<blank>'}, blocking={blocking or '<blank>'}, blocked={blocked or '<blank>'}.",
                [blocking, blocked],
            )
        missing_processed = [task_id for task_id in (blocking, blocked) if task_id and task_id not in task_id_set]
        if missing_processed:
            row_problem_codes.append("DEPENDENCY_REFERENCES_UNKNOWN_TASK")
            add_issue(
                issues,
                "DEPENDENCY_REFERENCES_UNKNOWN_TASK",
                f"Dependency {dependency_type or '<blank>'}: {blocking or '<blank>'} -> {blocked or '<blank>'}; absent processed endpoints: {', '.join(missing_processed)}.",
                [blocking, blocked],
            )
        missing_source = [task_id for task_id in (blocking, blocked) if task_id in placeholder_ids]
        if missing_source:
            row_problem_codes.append("DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK")
            add_issue(
                issues,
                "DEPENDENCY_REFERENCES_MISSING_SOURCE_TASK",
                f"Dependency type={dependency_type!r}: {blocking} -> {blocked}; source-missing placeholder endpoints: {', '.join(missing_source)}.",
                [blocking, blocked],
            )
        if blocking and blocked and blocking == blocked:
            row_problem_codes.append("SELF_DEPENDENCY")
            add_issue(
                issues,
                "SELF_DEPENDENCY",
                f"Dependency {dependency_type}: {blocking} -> {blocked} is a self-dependency.",
                [blocking],
            )
        if blocking and blocked:
            dependency_edges.append((blocking, blocked))
            edge_rows[(blocking, blocked)].append(dependency)
        if row_problem_codes:
            dependency_problem_rows.append(
                {
                    "dependency_type": dependency_type,
                    "blocking_task_id": blocking,
                    "blocked_task_id": blocked,
                    "issues": ", ".join(row_problem_codes),
                }
            )

    duplicate_dependency_edges: list[dict[str, Any]] = []
    for (blocking, blocked), rows in sorted(edge_rows.items()):
        if len(rows) <= 1:
            continue
        types = sorted({clean(row.get("dependency_type")) for row in rows})
        duplicate_dependency_edges.append(
            {"blocking": blocking, "blocked": blocked, "count": len(rows), "types": types}
        )
        add_issue(
            issues,
            "DUPLICATE_DEPENDENCY_EDGE",
            f"Dependency edge {blocking} -> {blocked} occurs {len(rows)} times with types: {', '.join(types)}.",
            [blocking, blocked],
        )

    cycles = strongly_connected_components(dependency_edges)
    for component in cycles:
        add_issue(
            issues,
            "DEPENDENCY_CYCLE",
            f"Dependency cycle/SCC contains: {', '.join(component)}.",
            component,
        )
    dependency_types = sorted(
        {clean(row.get("dependency_type")) for row in dependencies if clean(row.get("dependency_type"))}
    )
    if dependencies:
        add_issue(
            issues,
            "DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED",
            "Processed data preserves blocking_task_id -> blocked_task_id, but organizer semantics are not independently confirmed for types: "
            + ", ".join(dependency_types)
            + ".",
            [
                value
                for dependency in dependencies
                for value in (
                    clean(dependency.get("blocking_task_id")),
                    clean(dependency.get("blocked_task_id")),
                )
            ],
        )

    # 6. Story points and hours.
    task_sp: dict[str, Decimal | None] = {}
    for index, task in enumerate(tasks, start=2):
        task_id = clean(task.get("task_id"))
        status, value = parse_decimal(task.get("story_points"))
        task_sp[task_id] = value if status == "ok" else None
        if status == "blank":
            add_issue(
                issues,
                "MISSING_STORY_POINTS",
                f"Task {task_id or f'CSV row {index}'} has missing story_points.",
                [task_id],
                [clean(task.get("team_id"))],
            )
        elif status == "invalid":
            add_issue(
                issues,
                "INVALID_STORY_POINTS",
                f"Task {task_id or f'CSV row {index}'} has non-numeric story_points={task.get('story_points')!r}.",
                [task_id],
                [clean(task.get("team_id"))],
            )
        elif value is not None and value <= 0:
            add_issue(
                issues,
                "NON_POSITIVE_STORY_POINTS",
                f"Task {task_id or f'CSV row {index}'} has story_points={format_decimal(value)}.",
                [task_id],
                [clean(task.get("team_id"))],
            )

    tasks_with_positive_sp = {task_id for task_id, value in task_sp.items() if value is not None and value > 0}
    tasks_with_positive_hours = {task_id for task_id, value in task_positive_hours.items() if value > 0}
    story_points_without_hours = sorted(tasks_with_positive_sp - tasks_with_positive_hours)
    hours_without_story_points = sorted(tasks_with_positive_hours - tasks_with_positive_sp)
    for task_id in story_points_without_hours:
        add_issue(
            issues,
            "STORY_POINTS_WITHOUT_HOURS",
            f"Task {task_id} has positive SP but no positive role-hour estimate.",
            [task_id],
            [clean(task_by_id.get(task_id, {}).get("team_id"))],
        )
    for task_id in hours_without_story_points:
        add_issue(
            issues,
            "HOURS_WITHOUT_STORY_POINTS",
            f"Task {task_id} has {format_decimal(task_positive_hours[task_id])} positive role-hours but no positive SP.",
            [task_id],
            [clean(task_by_id.get(task_id, {}).get("team_id"))],
        )

    task_hours_mismatches: list[dict[str, Any]] = []
    for task in tasks:
        task_id = clean(task.get("task_id"))
        estimate_status, estimated_hh = parse_decimal(task.get("estimated_hh"))
        if estimate_status != "ok" or estimated_hh is None or task_id not in task_has_valid_effort:
            continue
        role_total = task_positive_hours.get(task_id, Decimal("0"))
        if estimated_hh != role_total:
            task_hours_mismatches.append(
                {
                    "task_id": task_id,
                    "estimated_hh": estimated_hh,
                    "role_total": role_total,
                    "delta": role_total - estimated_hh,
                }
            )
            add_issue(
                issues,
                "TASK_HOURS_TOTAL_MISMATCH",
                f"Task {task_id}: estimated_hh={format_decimal(estimated_hh)}, sum of positive role-hours={format_decimal(role_total)}, delta(role-total minus task)={format_decimal(role_total - estimated_hh)}.",
                [task_id],
                [clean(task.get("team_id"))],
            )

    fully_linked_sp_hours = sorted(tasks_with_positive_sp & tasks_with_positive_hours)
    fallback_sp_hours = sorted(task_id_set - set(fully_linked_sp_hours))

    # 7. Capacity rate.
    capacity_distribution: Counter[str] = Counter()
    for index, engineer in enumerate(engineers, start=2):
        engineer_id = clean(engineer.get("engineer_id"))
        value_text = clean(engineer.get("capacity_rate"))
        capacity_distribution[value_text or "<NULL>"] += 1
        status, value = parse_decimal(value_text)
        if status == "blank":
            add_issue(
                issues,
                "CAPACITY_RATE_NULL",
                f"Engineer {engineer_id or f'CSV row {index}'} has NULL capacity_rate.",
            )
        elif status == "invalid" or value is None:
            add_issue(
                issues,
                "CAPACITY_RATE_INVALID",
                f"Engineer {engineer_id or f'CSV row {index}'} has non-numeric capacity_rate={value_text!r}.",
            )
        elif value < 0:
            add_issue(
                issues,
                "CAPACITY_RATE_NEGATIVE",
                f"Engineer {engineer_id or f'CSV row {index}'} has capacity_rate={format_decimal(value)}.",
            )
        elif value > 1:
            add_issue(
                issues,
                "CAPACITY_RATE_ABOVE_ONE",
                f"Engineer {engineer_id or f'CSV row {index}'} has capacity_rate={format_decimal(value)}.",
            )
        elif value not in {Decimal("0"), Decimal("0.5"), Decimal("1")}:
            add_issue(
                issues,
                "CAPACITY_RATE_UNEXPECTED",
                f"Engineer {engineer_id or f'CSV row {index}'} has capacity_rate={format_decimal(value)}; allowed: 0, 0.5, 1.",
            )

    # 8. Statuses.
    status_distribution = Counter(clean(task.get("status")) or "<blank>" for task in tasks)
    status_file = find_optional_file(input_dir, ("task_statuses.csv", "statuses.csv"))
    status_reference: set[str] = set()
    if status_file:
        status_rows = read_csv(status_file)
        status_column = first_existing_column(status_rows, ("status", "value", "name"))
        if not status_column:
            raise AuditError(f"{status_file.name} has no status/value/name column.")
        status_reference = set(exact_ids(status_rows, status_column))
        status_reference_source = status_file.name
    else:
        status_reference_source = "not available"
        add_issue(
            issues,
            "STATUS_REFERENCE_NOT_AVAILABLE",
            "No task_statuses.csv or statuses.csv exists; only observed task values can be distributed, not validated as official.",
        )
    for task in tasks:
        task_id = clean(task.get("task_id"))
        status = clean(task.get("status"))
        if not status:
            add_issue(
                issues,
                "TASK_STATUS_MISSING",
                f"Task {task_id or '<blank task_id>'} has blank status.",
                [task_id],
                [clean(task.get("team_id"))],
            )
        elif status_reference and status not in status_reference:
            add_issue(
                issues,
                "UNKNOWN_TASK_STATUS",
                f"Task {task_id or '<blank task_id>'} has status {status!r}, absent from {status_reference_source}.",
                [task_id],
                [clean(task.get("team_id"))],
            )

    # 9. Initiatives.
    task_initiative_counts = Counter(
        clean(task.get("initiative_id"))
        for task in tasks
        if clean(task.get("initiative_id"))
    )
    for task in tasks:
        task_id = clean(task.get("task_id"))
        if not clean(task.get("initiative_id")):
            add_issue(
                issues,
                "BLANK_INITIATIVE_ID",
                f"Task {task_id or '<blank task_id>'} has blank initiative_id.",
                [task_id],
                [clean(task.get("team_id"))],
            )

    initiative_file = find_optional_file(input_dir, ("initiatives.csv",))
    initiative_registry_ids: list[str] = []
    initiatives_without_tasks: list[str] = []
    if initiative_file:
        initiative_rows = read_csv(initiative_file)
        initiative_column = first_existing_column(initiative_rows, ("initiative_id", "id"))
        if not initiative_column:
            raise AuditError(f"{initiative_file.name} has no initiative_id column.")
        initiative_registry_ids = exact_ids(initiative_rows, initiative_column)
        for initiative_id, count in duplicate_values(initiative_registry_ids).items():
            add_issue(
                issues,
                "DUPLICATE_INITIATIVE_ID",
                f"initiative_id {initiative_id} occurs {count} times in {initiative_file.name}.",
            )
        initiatives_without_tasks = sorted(set(initiative_registry_ids) - set(task_initiative_counts))
        for initiative_id in initiatives_without_tasks:
            add_issue(
                issues,
                "INITIATIVE_WITHOUT_TASKS",
                f"Initiative {initiative_id} has no tasks in tasks.csv.",
            )
        initiative_registry_source = initiative_file.name
    else:
        initiative_registry_source = "not available"
        add_issue(
            issues,
            "INITIATIVE_REGISTRY_NOT_AVAILABLE",
            "No initiatives.csv exists; repeated initiative_id values in tasks are treated as a one-to-many relation, not duplicates.",
        )

    affected_initiative_dependencies: list[dict[str, Any]] = []
    for dependency in dependencies:
        blocking = clean(dependency.get("blocking_task_id"))
        blocked = clean(dependency.get("blocked_task_id"))
        if not ({blocking, blocked} & placeholder_ids):
            continue
        present_endpoints = [task_id for task_id in (blocking, blocked) if task_id and task_id not in placeholder_ids]
        initiatives = sorted(
            {
                clean(task_by_id.get(task_id, {}).get("initiative_id"))
                for task_id in present_endpoints
                if clean(task_by_id.get(task_id, {}).get("initiative_id"))
            }
        )
        for initiative_id in initiatives:
            affected_initiative_dependencies.append(
                {
                    "initiative_id": initiative_id,
                    "blocking": blocking,
                    "blocked": blocked,
                    "dependency_type": clean(dependency.get("dependency_type")),
                }
            )
            add_issue(
                issues,
                "INITIATIVE_DEPENDS_ON_MISSING_TASK",
                f"Initiative {initiative_id} touches dependency {clean(dependency.get('dependency_type'))}: {blocking} -> {blocked}, which includes a source-missing placeholder.",
                [blocking, blocked],
            )

    # 10. Planned dates.
    date_problem_rows: list[dict[str, str]] = []
    for task in tasks:
        task_id = clean(task.get("task_id"))
        start_status, start = parse_datetime(task.get("planned_start"))
        end_status, end = parse_datetime(task.get("planned_end"))
        codes: list[str] = []
        if start_status == "blank":
            codes.append("PLANNED_START_MISSING")
            add_issue(
                issues,
                "PLANNED_START_MISSING",
                f"Task {task_id or '<blank task_id>'} has blank planned_start.",
                [task_id],
            )
        elif start_status == "invalid":
            codes.append("INVALID_PLANNED_DATE")
            add_issue(
                issues,
                "INVALID_PLANNED_DATE",
                f"Task {task_id or '<blank task_id>'} has invalid planned_start={task.get('planned_start')!r}.",
                [task_id],
            )
        if end_status == "blank":
            codes.append("PLANNED_END_MISSING")
            add_issue(
                issues,
                "PLANNED_END_MISSING",
                f"Task {task_id or '<blank task_id>'} has blank planned_end.",
                [task_id],
            )
        elif end_status == "invalid":
            codes.append("INVALID_PLANNED_DATE")
            add_issue(
                issues,
                "INVALID_PLANNED_DATE",
                f"Task {task_id or '<blank task_id>'} has invalid planned_end={task.get('planned_end')!r}.",
                [task_id],
            )
        if start_status == "blank" and end_status == "blank":
            codes.append("TASK_WITHOUT_PLANNED_DATES")
            add_issue(
                issues,
                "TASK_WITHOUT_PLANNED_DATES",
                f"Task {task_id or '<blank task_id>'} has neither planned_start nor planned_end.",
                [task_id],
            )
        if start is not None and end is not None:
            interval_days = (end - start).total_seconds() / 86400
            if interval_days < 0:
                codes.append("PLANNED_END_BEFORE_START")
                add_issue(
                    issues,
                    "PLANNED_END_BEFORE_START",
                    f"Task {task_id}: planned_end is {abs(interval_days):.1f} days before planned_start.",
                    [task_id],
                )
            elif interval_days > suspicious_date_days:
                codes.append("SUSPICIOUS_PLANNED_INTERVAL")
                add_issue(
                    issues,
                    "SUSPICIOUS_PLANNED_INTERVAL",
                    f"Task {task_id}: planned interval is {interval_days:.1f} days, above the {suspicious_date_days}-day threshold.",
                    [task_id],
                )
        if codes:
            date_problem_rows.append(
                {
                    "task_id": task_id,
                    "planned_start": clean(task.get("planned_start")),
                    "planned_end": clean(task.get("planned_end")),
                    "issues": ", ".join(codes),
                }
            )

    # 11. InProgress and spent time.
    in_progress_rows: list[dict[str, str]] = []
    for task in tasks:
        task_id = clean(task.get("task_id"))
        task_status = clean(task.get("status"))
        spent_status, spent = parse_decimal(task.get("spent_time"))
        estimate_status, estimate = parse_decimal(task.get("estimated_hh"))
        in_progress_issues: list[str] = []
        if task_status == "InProgress" and spent_status == "blank":
            in_progress_issues.append("IN_PROGRESS_MISSING_SPENT_TIME")
            add_issue(
                issues,
                "IN_PROGRESS_MISSING_SPENT_TIME",
                f"InProgress task {task_id} has missing spent_time; estimated_hh={clean(task.get('estimated_hh')) or '<blank>'}.",
                [task_id],
                [clean(task.get("team_id"))],
            )
        if spent_status == "invalid":
            in_progress_issues.append("INVALID_SPENT_TIME")
            add_issue(
                issues,
                "INVALID_SPENT_TIME",
                f"Task {task_id} has non-numeric spent_time={task.get('spent_time')!r}.",
                [task_id],
            )
        elif spent is not None and spent < 0:
            in_progress_issues.append("NEGATIVE_SPENT_TIME")
            add_issue(
                issues,
                "NEGATIVE_SPENT_TIME",
                f"Task {task_id} has spent_time={format_decimal(spent)}.",
                [task_id],
            )
        if spent is not None and estimate_status == "blank":
            in_progress_issues.append("SPENT_TIME_WITHOUT_ESTIMATE")
            add_issue(
                issues,
                "SPENT_TIME_WITHOUT_ESTIMATE",
                f"Task {task_id} has spent_time={format_decimal(spent)} but missing estimated_hh.",
                [task_id],
            )
        elif spent is not None and estimate is not None and spent > estimate:
            in_progress_issues.append("SPENT_TIME_EXCEEDS_ESTIMATE")
            add_issue(
                issues,
                "SPENT_TIME_EXCEEDS_ESTIMATE",
                f"Task {task_id}: spent_time={format_decimal(spent)} exceeds estimated_hh={format_decimal(estimate)}.",
                [task_id],
            )
        if task_status == "InProgress":
            in_progress_rows.append(
                {
                    "task_id": task_id,
                    "team_id": clean(task.get("team_id")),
                    "spent_time": clean(task.get("spent_time")),
                    "estimated_hh": clean(task.get("estimated_hh")),
                    "issues": ", ".join(in_progress_issues) or "none",
                }
            )

    manifest: dict[str, Any] = {}
    manifest_path = input_dir / "_dataset_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}

    statistics = {
        "task_rows": len(tasks),
        "source_tasks": len(tasks) - len(placeholder_ids),
        "placeholder_tasks": len(placeholder_ids),
        "initiatives": len(task_initiative_counts),
        "teams": len(known_teams),
        "engineer_rows": len(engineers),
        "unique_engineers": len(engineer_id_set),
        "effort_role_labels": len({role for role in role_labels if role}),
        "required_positive_role_labels": len(role_demands),
        "engineer_role_labels": len(engineer_roles),
        "engineer_skill_labels": len(declared_engineer_skills),
        "task_required_skill_labels": len(skill_to_tasks),
        "dependencies": len(dependencies),
        "fully_linked_sp_hours": len(fully_linked_sp_hours),
        "fallback_sp_hours": len(fallback_sp_hours),
    }

    return {
        "input_dir": input_dir,
        "manifest": manifest,
        "issues": issues,
        "statistics": statistics,
        "task_ids": task_ids,
        "placeholder_ids": sorted(placeholder_ids),
        "missing_role_rows": missing_role_rows,
        "role_demands": role_demands,
        "available_role_engineers": available_role_engineers,
        "task_skills_source": task_skills_source,
        "task_skill_requirements": task_skill_requirements,
        "declared_engineer_skills": declared_engineer_skills,
        "missing_skill_rows": missing_skill_rows,
        "dependency_problem_rows": dependency_problem_rows,
        "duplicate_dependency_edges": duplicate_dependency_edges,
        "cycles": cycles,
        "dependency_types": dependency_types,
        "known_teams": sorted(known_teams),
        "team_registry_source": team_registry_source,
        "task_team_problems": task_team_problems,
        "duplicate_task_ids": duplicate_task_ids,
        "duplicate_engineer_ids": duplicate_engineer_ids,
        "initiative_registry_source": initiative_registry_source,
        "initiative_registry_ids": initiative_registry_ids,
        "initiatives_without_tasks": initiatives_without_tasks,
        "story_points_without_hours": story_points_without_hours,
        "hours_without_story_points": hours_without_story_points,
        "task_hours_mismatches": task_hours_mismatches,
        "fully_linked_sp_hours": fully_linked_sp_hours,
        "fallback_sp_hours": fallback_sp_hours,
        "capacity_distribution": capacity_distribution,
        "status_distribution": status_distribution,
        "status_reference_source": status_reference_source,
        "status_reference": sorted(status_reference),
        "initiative_counts": task_initiative_counts,
        "affected_initiative_dependencies": affected_initiative_dependencies,
        "date_problem_rows": date_problem_rows,
        "suspicious_date_days": suspicious_date_days,
        "in_progress_rows": in_progress_rows,
    }


def issue_summary_rows(issues: Sequence[Issue]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        grouped[issue.code].append(issue)
    rows: list[dict[str, Any]] = []
    for code, definition in ISSUE_DEFINITIONS.items():
        code_issues = grouped.get(code, [])
        rows.append(
            {
                "code": code,
                "section": definition.section,
                "category": definition.category,
                "count": len(code_issues),
                "task_ids": sorted({task_id for issue in code_issues for task_id in issue.task_ids}),
                "teams": sorted({team for issue in code_issues for team in issue.teams}),
                "priority": definition.priority,
            }
        )
    return rows


INFO_ISSUE_CODES = {
    "DUPLICATE_ENGINEER_ID",
    "INITIATIVE_REGISTRY_NOT_AVAILABLE",
    "STATUS_REFERENCE_NOT_AVAILABLE",
}

WARNING_ISSUE_CODES = {
    "DONE_DEPENDENCY_CONFLICT",
    "DEPENDENCY_DIRECTION_SEMANTICS_UNCONFIRMED",
    "TASK_SKILL_REQUIREMENTS_NOT_AVAILABLE",
    "TASK_HOURS_TOTAL_MISMATCH",
    "PLANNED_START_MISSING",
    "PLANNED_END_MISSING",
    "TASK_WITHOUT_PLANNED_DATES",
    "SUSPICIOUS_PLANNED_INTERVAL",
    "IN_PROGRESS_MISSING_SPENT_TIME",
    "SPENT_TIME_EXCEEDS_ESTIMATE",
    "POSSIBLE_ROLE_SPELLING_VARIANT",
}


def machine_readable_audit(result: dict[str, Any]) -> DatasetAuditResult:
    """Aggregate raw findings into UI-safe issues without parsing Markdown."""

    grouped: dict[str, list[Issue]] = defaultdict(list)
    for issue in result["issues"]:
        grouped[issue.code].append(issue)

    structured: list[AuditIssueResult] = []
    for code, code_issues in sorted(
        grouped.items(),
        key=lambda item: (ISSUE_DEFINITIONS[item[0]].priority, item[0]),
    ):
        definition = ISSUE_DEFINITIONS[code]
        task_ids = tuple(
            sorted({task_id for issue in code_issues for task_id in issue.task_ids})
        )
        engineer_ids = tuple(
            sorted(
                {
                    engineer_id
                    for issue in code_issues
                    for engineer_id in issue.engineer_ids
                }
            )
        )
        teams = tuple(sorted({team for issue in code_issues for team in issue.teams}))
        if task_ids:
            entity_type, entity_ids = "task", task_ids
        elif engineer_ids:
            entity_type, entity_ids = "engineer", engineer_ids
        elif teams:
            entity_type, entity_ids = "team", teams
        else:
            entity_type, entity_ids = "dataset", ()

        if code in INFO_ISSUE_CODES:
            severity = AuditSeverity.INFO
        elif code in WARNING_ISSUE_CODES:
            severity = AuditSeverity.WARNING
        else:
            severity = AuditSeverity.BLOCKING
        message = code_issues[0].message
        if len(code_issues) > 1:
            message = f"{len(code_issues)} related findings. {message}"
        structured.append(
            AuditIssueResult(
                issue_code=code,
                severity=severity,
                message=message,
                affected_entity_type=entity_type,
                affected_entity_ids=entity_ids,
                planning_impact=definition.planning_impact,
                # Entity-level blockers are excluded by planner rules; they do
                # not make every other valid task unusable.
                blocking=False,
            )
        )

    has_findings = any(
        issue.severity in {AuditSeverity.BLOCKING, AuditSeverity.WARNING}
        for issue in structured
    )
    return DatasetAuditResult(
        status="ACCEPTED_WITH_WARNINGS" if has_findings else "ACCEPTED",
        planning_allowed=True,
        issues=tuple(structured),
    )


def structural_audit_failure(message: str) -> DatasetAuditResult:
    """Return a planning gate for an unreadable or structurally invalid dataset."""

    issue = AuditIssueResult(
        issue_code="STRUCTURAL_DATASET_ERROR",
        severity=AuditSeverity.BLOCKING,
        message=message,
        affected_entity_type="dataset",
        affected_entity_ids=(),
        planning_impact="The dataset cannot be preprocessed or validated reliably.",
        blocking=True,
    )
    return DatasetAuditResult(
        status="CANNOT_PLAN",
        planning_allowed=False,
        issues=(issue,),
    )


def top_concrete_issues(issues: Sequence[Issue], limit: int = 10) -> list[Issue]:
    grouped: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        if issue.task_ids:
            grouped[issue.code].append(issue)

    candidates: list[Issue] = []
    for code, code_issues in grouped.items():
        representative = sorted(
            code_issues,
            key=lambda issue: (-len(issue.task_ids), issue.message),
        )[0]
        all_task_ids = tuple(
            sorted({task_id for issue in code_issues for task_id in issue.task_ids})
        )
        all_teams = tuple(sorted({team for issue in code_issues for team in issue.teams}))
        message = representative.message
        if len(code_issues) > 1:
            message = (
                f"{len(code_issues)} occurrences. Highest-impact example: "
                f"{representative.message}"
            )
        candidates.append(
            Issue(
                code=code,
                message=message,
                task_ids=all_task_ids,
                teams=all_teams,
                engineer_ids=tuple(
                    sorted(
                        {
                            engineer_id
                            for issue in code_issues
                            for engineer_id in issue.engineer_ids
                        }
                    )
                ),
            )
        )

    candidates.sort(
        key=lambda issue: (
            0 if ISSUE_DEFINITIONS[issue.code].section == "critical" else 1,
            ISSUE_DEFINITIONS[issue.code].priority,
            -len(issue.task_ids),
            issue.code,
            issue.message,
        )
    )
    return candidates[:limit]


def render_issue_group_table(rows: Sequence[dict[str, Any]], section: str) -> list[str]:
    selected = [row for row in rows if row["section"] == section and row["count"] > 0]
    if not selected:
        return ["No issues detected in this section.", ""]
    lines = [
        "| Issue type | Problems | Affected tasks | Affected teams |",
        "|---|---:|---:|---:|",
    ]
    for row in sorted(selected, key=lambda item: (item["priority"], item["code"])):
        lines.append(
            f"| `{row['code']}` | {row['count']} | {len(row['task_ids'])} | {len(row['teams'])} |"
        )
    lines.append("")
    return lines


def render_report(result: dict[str, Any]) -> str:
    issues: list[Issue] = result["issues"]
    statistics = result["statistics"]
    summary_rows = issue_summary_rows(issues)
    grouped: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        grouped[issue.code].append(issue)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = result["manifest"]
    source_hash = clean(manifest.get("raw_file_hash")) or "not available"
    preprocessing_version = clean(manifest.get("preprocessing_version")) or "not available"

    lines: list[str] = [
        "# Dataset Audit Report",
        "",
        f"Generated: `{generated_at}`  ",
        f"Processed dataset: `{result['input_dir'].as_posix()}`  ",
        f"Raw source hash from manifest: `{source_hash}`  ",
        f"Preprocessing version: `{preprocessing_version}`",
        "",
        "> Detection-only audit: no processed data, planner code, preprocessing code, or business logic was changed. Canonical processed role values and exact skill strings were compared without additional implicit mapping.",
        "",
        "## Summary",
        "",
        "### General statistics",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Task rows | {statistics['task_rows']} |",
        f"| Source Tasks rows | {statistics['source_tasks']} |",
        f"| Placeholder tasks | {statistics['placeholder_tasks']} |",
        f"| Initiatives observed in tasks | {statistics['initiatives']} |",
        f"| Teams ({md_escape(result['team_registry_source'])}) | {statistics['teams']} |",
        f"| Engineer profile rows | {statistics['engineer_rows']} |",
        f"| Unique engineers | {statistics['unique_engineers']} |",
        f"| Role labels in effort matrix | {statistics['effort_role_labels']} |",
        f"| Roles with positive task demand | {statistics['required_positive_role_labels']} |",
        f"| Engineer role labels | {statistics['engineer_role_labels']} |",
        f"| Unique engineer skill labels | {statistics['engineer_skill_labels']} |",
        f"| Unique task-required skill labels | {statistics['task_required_skill_labels']} |",
        f"| Dependencies | {statistics['dependencies']} |",
        f"| Tasks fully linkable SP ↔ positive role-hours | {statistics['fully_linked_sp_hours']} |",
        f"| Tasks requiring SP/hours fallback | {statistics['fallback_sp_hours']} |",
        "",
        "### Issue counts by type",
        "",
        "A problem count is the number of independently reported occurrences. For grouped role/skill issues, one occurrence is one missing role/skill; affected-task counts are shown separately in the critical/data-quality summaries.",
        f"Total occurrences: **{len(issues)}** ({sum(1 for issue in issues if ISSUE_DEFINITIONS[issue.code].section == 'critical')} critical and {sum(1 for issue in issues if ISSUE_DEFINITIONS[issue.code].section == 'data_quality')} data-quality). Counts overlap when one source gap causes several downstream checks to fail.",
        "",
        "| Issue type | Classification | Problems | Affected tasks |",
        "|---|---|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            f"| `{row['code']}` | {row['section'].replace('_', ' ')} | {row['count']} | {len(row['task_ids'])} |"
        )
    lines.extend(["", "## Critical issues", ""])
    lines.extend(render_issue_group_table(summary_rows, "critical"))
    lines.extend(
        [
            "### 10 highest-priority concrete problems",
            "",
            "| # | Issue | Task IDs | Details |",
            "|---:|---|---|---|",
        ]
    )
    top_issues = top_concrete_issues(issues)
    if top_issues:
        for index, issue in enumerate(top_issues, start=1):
            lines.append(
                f"| {index} | `{issue.code}` | {compact(issue.task_ids)} | {md_escape(issue.message)} |"
            )
    else:
        lines.append("| — | — | — | No task-specific issues detected. |")

    lines.extend(["", "## Data quality issues", ""])
    lines.extend(render_issue_group_table(summary_rows, "data_quality"))

    lines.extend(["## Planning impact", ""])
    nonzero_codes = [row["code"] for row in summary_rows if row["count"] > 0]
    for code in nonzero_codes:
        definition = ISSUE_DEFINITIONS[code]
        count = len(grouped[code])
        task_count = len({task_id for issue in grouped[code] for task_id in issue.task_ids})
        problem_label = "problem" if count == 1 else "problems"
        task_label = "task" if task_count == 1 else "tasks"
        suffix = (
            f" ({count} {problem_label}, {task_count} affected {task_label})"
            if task_count
            else f" ({count} {problem_label})"
        )
        lines.append(f"- `{code}`{suffix}: {definition.planning_impact}")

    lines.extend(["", "## Recommendations", ""])
    for code in nonzero_codes:
        lines.append(f"- `{code}`: {ISSUE_DEFINITIONS[code].recommendation}")
    lines.extend(["", "### Questions requiring organizer clarification", ""])
    organizer_codes = [
        code for code in nonzero_codes if ISSUE_DEFINITIONS[code].ask_organizers
    ]
    if organizer_codes:
        for code in organizer_codes:
            lines.append(
                f"- `{code}` — {ISSUE_DEFINITIONS[code].recommendation}"
            )
    else:
        lines.append("No organizer questions were identified.")

    lines.extend(["", "## Detailed checks", "", "### 1. Roles and specialists", ""])
    missing_roles = result["missing_role_rows"]
    if missing_roles:
        lines.extend(
            [
                "Exact string matching found the following required roles with zero engineers:",
                "",
                "| Role | Required hours | Tasks | Task IDs | Teams | Available engineers |",
                "|---|---:|---:|---|---|---:|",
            ]
        )
        for row in missing_roles:
            lines.append(
                f"| {md_escape(row['role'])} | {format_decimal(row['hours'])} | {len(row['task_ids'])} | {compact(row['task_ids'], 100)} | {compact(row['teams'], 100)} | 0 |"
            )
    else:
        lines.append("Every role with positive task demand has at least one exact-match engineer role.")

    variant_issues = grouped.get("POSSIBLE_ROLE_SPELLING_VARIANT", [])
    if variant_issues:
        lines.extend(["", "Possible spelling/case variants were reported but **not mapped**:", ""])
        for issue in variant_issues:
            lines.append(f"- {issue.message}")

    lines.extend(["", "### 2. Skills / competencies", ""])
    lines.append(f"Task skill source: `{result['task_skills_source']}`.")
    lines.append(
        f"Engineer profiles declare {len(result['declared_engineer_skills'])} unique exact skill labels."
    )
    if result["task_skills_source"] == "not available":
        lines.append(
            "Task-level skill requirements are absent, so missing-skill and team skill-deficit checks are not computable. Roles and task summaries were not treated as skills."
        )
    elif result["missing_skill_rows"]:
        lines.extend(
            [
                "",
                "| Missing skill | Tasks | Task IDs | Teams |",
                "|---|---:|---|---|",
            ]
        )
        for row in result["missing_skill_rows"]:
            lines.append(
                f"| {md_escape(row['skill'])} | {len(row['task_ids'])} | {compact(row['task_ids'], 100)} | {compact(row['teams'], 100)} |"
            )
    else:
        lines.append("All explicit task-required skill labels have an exact match in engineer skills.")

    lines.extend(["", "### 3. Dependencies", ""])
    lines.extend(
        [
            f"Dependencies checked: {statistics['dependencies']}.",
            f"Dependency types observed: {compact(result['dependency_types'], 100)}.",
            f"Cycles found: {len(result['cycles'])}; self-dependencies: {len(grouped.get('SELF_DEPENDENCY', []))}; duplicate directed edges: {len(result['duplicate_dependency_edges'])}.",
            "",
        ]
    )
    dependency_rows = result["dependency_problem_rows"]
    if dependency_rows:
        lines.extend(
            [
                "| dependency_type | blocking_task_id | blocked_task_id | Issue |",
                "|---|---|---|---|",
            ]
        )
        for row in dependency_rows:
            lines.append(
                f"| {md_escape(row['dependency_type'])} | `{md_escape(row['blocking_task_id'])}` | `{md_escape(row['blocked_task_id'])}` | {md_escape(row['issues'])} |"
            )
    else:
        lines.append("No missing endpoints, placeholders, blank required fields, or self-dependencies were found.")
    if result["duplicate_dependency_edges"]:
        lines.extend(["", "Repeated directed edges:", ""])
        for row in result["duplicate_dependency_edges"]:
            lines.append(
                f"- `{row['blocking']}` → `{row['blocked']}` occurs {row['count']} times; types: {', '.join(row['types'])}."
            )

    lines.extend(["", "### 4. Teams", ""])
    lines.append(
        f"Team registry source: `{result['team_registry_source']}`; known teams: {compact(result['known_teams'], 100)}."
    )
    if result["task_team_problems"]:
        lines.extend(
            [
                "",
                "| Task ID | team_id | Problem |",
                "|---|---|---|",
            ]
        )
        for row in result["task_team_problems"]:
            lines.append(
                f"| `{md_escape(row['task_id'])}` | `{md_escape(row['team_id']) or '<blank>'}` | {md_escape(row['reason'])} |"
            )
    else:
        lines.append("All tasks reference a known team.")
    lines.append(
        f"Teams without engineers: {len(grouped.get('TEAM_WITHOUT_ENGINEERS', []))}; engineer rows without a team: {len(grouped.get('ENGINEER_WITHOUT_TEAM', []))}."
    )

    lines.extend(["", "### 5. Task IDs and duplicates", ""])
    lines.extend(
        [
            f"Duplicate task_id values: {len(result['duplicate_task_ids'])}.",
            f"Duplicate engineer_id values: {len(result['duplicate_engineer_ids'])}: {compact(result['duplicate_engineer_ids'].keys(), 100)}.",
            f"Initiative registry: `{result['initiative_registry_source']}`. Duplicate initiative entities are not inferred from repeated task foreign keys.",
            f"Blank task_id problems: {len(grouped.get('BLANK_TASK_ID', []))}; blank engineer_id problems: {len(grouped.get('BLANK_ENGINEER_ID', []))}.",
        ]
    )

    lines.extend(["", "### 6. Story points and hours", ""])
    lines.extend(
        [
            f"Tasks fully linkable SP ↔ positive role-hours: **{statistics['fully_linked_sp_hours']}**.",
            f"Tasks requiring fallback: **{statistics['fallback_sp_hours']}**: {compact(result['fallback_sp_hours'], 100)}.",
            f"Tasks with positive SP but without positive role-hours: {len(result['story_points_without_hours'])}: {compact(result['story_points_without_hours'], 100)}.",
            f"Tasks with positive role-hours but without positive SP: {len(result['hours_without_story_points'])}: {compact(result['hours_without_story_points'], 100)}.",
            f"Missing required_hours cells: {len(grouped.get('MISSING_REQUIRED_HOURS', []))}; negative required_hours: {len(grouped.get('NEGATIVE_REQUIRED_HOURS', []))}.",
        ]
    )
    if result["task_hours_mismatches"]:
        lines.extend(
            [
                "",
                "Task-level estimated_hh versus role-hour totals:",
                "",
                "| Task ID | estimated_hh | Sum role-hours | Delta (role − task) |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in result["task_hours_mismatches"]:
            lines.append(
                f"| `{row['task_id']}` | {format_decimal(row['estimated_hh'])} | {format_decimal(row['role_total'])} | {format_decimal(row['delta'])} |"
            )

    lines.extend(["", "### 7. Capacity rate", ""])
    lines.extend(
        [
            "Allowed values: `0`, `0.5`, `1`.",
            "",
            "| Value | Engineer rows |",
            "|---|---:|",
        ]
    )
    for value, count in sorted(result["capacity_distribution"].items()):
        lines.append(f"| `{md_escape(value)}` | {count} |")
    lines.append(
        f"NULL: {len(grouped.get('CAPACITY_RATE_NULL', []))}; negative: {len(grouped.get('CAPACITY_RATE_NEGATIVE', []))}; above 1: {len(grouped.get('CAPACITY_RATE_ABOVE_ONE', []))}; unexpected allowed-range values: {len(grouped.get('CAPACITY_RATE_UNEXPECTED', []))}."
    )

    lines.extend(["", "### 8. Task statuses", ""])
    lines.append(f"Official status reference source: `{result['status_reference_source']}`.")
    lines.extend(["", "| Status | Tasks |", "|---|---:|"])
    for status, count in sorted(result["status_distribution"].items()):
        lines.append(f"| `{md_escape(status)}` | {count} |")
    if result["status_reference"]:
        lines.append(f"Known reference values: {compact(result['status_reference'], 100)}.")
    else:
        lines.append(
            "Unknown-status validation is not claimable because no independent status catalog exists in processed data."
        )

    lines.extend(["", "### 9. Initiative / PRODF", ""])
    lines.extend(
        [
            f"Initiative registry source: `{result['initiative_registry_source']}`.",
            "",
            "| Initiative | Tasks |",
            "|---|---:|",
        ]
    )
    for initiative_id, count in sorted(result["initiative_counts"].items()):
        lines.append(f"| `{md_escape(initiative_id)}` | {count} |")
    lines.append(
        f"Tasks without initiative: {len(grouped.get('BLANK_INITIATIVE_ID', []))}: {compact((task_id for issue in grouped.get('BLANK_INITIATIVE_ID', []) for task_id in issue.task_ids), 100)}."
    )
    if result["initiative_registry_source"] == "not available":
        lines.append(
            "Initiatives without tasks cannot be detected without a separate registry. Repeated PRODF values in tasks are expected one-to-many links, not duplicate initiative records."
        )
    if result["affected_initiative_dependencies"]:
        lines.extend(["", "Initiatives whose completion cannot be certified because a dependency includes a source-missing task:", ""])
        for row in result["affected_initiative_dependencies"]:
            lines.append(
                f"- `{row['initiative_id']}`: `{row['blocking']}` → `{row['blocked']}` ({row['dependency_type']})."
            )

    lines.extend(["", "### 10. Planned dates", ""])
    lines.append(
        f"A suspicious interval is defined for this audit as more than {result['suspicious_date_days']} calendar days (the documented 12-week quarter). Dates remain informational and are not used as planning constraints."
    )
    lines.extend(
        [
            f"Missing planned_start: {len(grouped.get('PLANNED_START_MISSING', []))}; missing planned_end: {len(grouped.get('PLANNED_END_MISSING', []))}; tasks missing both: {len(grouped.get('TASK_WITHOUT_PLANNED_DATES', []))}.",
            f"End before start: {len(grouped.get('PLANNED_END_BEFORE_START', []))}; suspiciously long intervals: {len(grouped.get('SUSPICIOUS_PLANNED_INTERVAL', []))}.",
        ]
    )
    if result["date_problem_rows"]:
        lines.extend(
            [
                "",
                "| Task ID | planned_start | planned_end | Issues |",
                "|---|---|---|---|",
            ]
        )
        for row in result["date_problem_rows"]:
            lines.append(
                f"| `{md_escape(row['task_id'])}` | {md_escape(row['planned_start']) or '<blank>'} | {md_escape(row['planned_end']) or '<blank>'} | {md_escape(row['issues'])} |"
            )

    lines.extend(["", "### 11. InProgress and spent time", ""])
    lines.extend(
        [
            f"InProgress tasks: {len(result['in_progress_rows'])}.",
            "",
            "| Task ID | Team | spent_time | estimated_hh | Issues |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in result["in_progress_rows"]:
        lines.append(
            f"| `{md_escape(row['task_id'])}` | `{md_escape(row['team_id'])}` | {md_escape(row['spent_time']) or '<blank>'} | {md_escape(row['estimated_hh']) or '<blank>'} | {md_escape(row['issues'])} |"
        )
    lines.append(
        f"spent_time > estimated_hh: {len(grouped.get('SPENT_TIME_EXCEEDS_ESTIMATE', []))}; spent_time without estimated_hh: {len(grouped.get('SPENT_TIME_WITHOUT_ESTIMATE', []))}."
    )

    lines.extend(["", "### 12. Overall statistics", ""])
    lines.append(
        "Counts are reported in the Summary. Unique engineers are counted by engineer_id; engineer profile rows are also shown because multi-team assignments repeat IDs. Role and skill counts use exact source labels."
    )
    lines.append("")
    return "\n".join(lines)


def write_text_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        temporary.write(content)
    os.replace(temporary_path, path)


def print_console_summary(result: dict[str, Any], output_path: Path) -> None:
    statistics = result["statistics"]
    rows = issue_summary_rows(result["issues"])
    print("Dataset Audit Report")
    print(f"Input: {result['input_dir']}")
    print(f"Report: {output_path}")
    print(
        "Statistics: "
        f"tasks={statistics['task_rows']}, initiatives={statistics['initiatives']}, "
        f"teams={statistics['teams']}, unique_engineers={statistics['unique_engineers']}, "
        f"roles={statistics['effort_role_labels']}, engineer_skills={statistics['engineer_skill_labels']}, "
        f"dependencies={statistics['dependencies']}"
    )
    print("Issue counts:")
    nonzero = [row for row in rows if row["count"]]
    for row in nonzero:
        print(
            f"- {row['code']}: {row['count']} problem(s), "
            f"{len(row['task_ids'])} affected task(s)"
        )
    zero_codes = [row["code"] for row in rows if not row["count"]]
    print(f"Checks with zero problems: {', '.join(zero_codes)}")
    print("Top 10 concrete problems:")
    for index, issue in enumerate(top_concrete_issues(result["issues"]), start=1):
        print(f"{index}. {issue.code} [{', '.join(issue.task_ids)}] {issue.message}")
    organizer_codes = [
        row["code"]
        for row in nonzero
        if ISSUE_DEFINITIONS[row["code"]].ask_organizers
    ]
    print(f"Require organizer clarification: {', '.join(organizer_codes) or 'none'}")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing processed CSV files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Markdown report path.",
    )
    parser.add_argument(
        "--suspicious-date-days",
        type=positive_int,
        default=84,
        help="Flag planned intervals longer than this many calendar days (default: 84).",
    )
    arguments = parser.parse_args()
    input_dir = arguments.input_dir.resolve()
    output_path = arguments.output.resolve()
    try:
        result = audit_dataset(input_dir, arguments.suspicious_date_days)
        report = render_report(result)
        write_text_atomically(output_path, report)
    except (AuditError, OSError, ValueError) as error:
        print(f"Data audit failed: {error}")
        return 1
    print_console_summary(result, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
