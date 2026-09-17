"""Immutable domain structures shared by KPI and replanning calculations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from numbers import Real


QUARTER_SPRINTS = 6


class PlanState(str, Enum):
    """Stable internal meaning; source-dataset status text stays separate."""

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DEFERRED = "deferred"
    MOVED = "moved"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class MeasureKind(str, Enum):
    FACT = "fact"
    FORECAST = "forecast"


def _validate_hours(value: float, field_name: str) -> None:
    if (
        not isinstance(value, Real)
        or isinstance(value, bool)
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field_name} must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class RoleHoursAllocation:
    role: str
    hours: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", self.role.strip())
        if not self.role:
            raise ValueError("allocation role must not be empty")
        _validate_hours(self.hours, "role allocation hours")


@dataclass(frozen=True, slots=True)
class EngineerHoursAllocation:
    engineer_id: str
    role: str
    hours: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "engineer_id", self.engineer_id.strip())
        object.__setattr__(self, "role", self.role.strip())
        if not self.engineer_id or not self.role:
            raise ValueError("engineer allocation engineer_id and role must not be empty")
        _validate_hours(self.hours, "engineer allocation hours")


@dataclass(frozen=True, slots=True)
class SprintAllocation:
    """Task effort assigned to one sprint; SP and hours remain separate."""

    sprint_number: int
    role_hours: tuple[RoleHoursAllocation, ...] = ()
    engineer_hours: tuple[EngineerHoursAllocation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_hours", tuple(self.role_hours))
        object.__setattr__(self, "engineer_hours", tuple(self.engineer_hours))
        if self.sprint_number < 1:
            raise ValueError("allocation sprint_number must be positive")
        roles = [allocation.role for allocation in self.role_hours]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate role allocation in one sprint")
        engineer_keys = [
            (allocation.engineer_id, allocation.role)
            for allocation in self.engineer_hours
        ]
        if len(engineer_keys) != len(set(engineer_keys)):
            raise ValueError("duplicate engineer/role allocation in one sprint")


@dataclass(frozen=True, slots=True)
class PlanItem:
    """One task in a version of a quarterly plan.

    ``completion_sprint`` may be greater than six in a current plan. This is how
    the model preserves a forecast that has slipped outside the quarter instead
    of silently dropping it.
    """

    task_id: str
    initiative_id: str
    story_points: float
    completion_sprint: int | None
    state: PlanState = PlanState.PLANNED
    team_id: str | None = None
    decision_status: str | None = None
    mismatch_reason: str | None = None
    allocations: tuple[SprintAllocation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", self.task_id.strip())
        object.__setattr__(self, "initiative_id", self.initiative_id.strip())
        object.__setattr__(self, "state", PlanState(self.state))
        object.__setattr__(self, "allocations", tuple(self.allocations))
        if not self.task_id:
            raise ValueError("task_id must not be empty")
        if not self.initiative_id:
            raise ValueError("initiative_id must not be empty")
        if (
            not isinstance(self.story_points, Real)
            or isinstance(self.story_points, bool)
            or not isfinite(self.story_points)
            or self.story_points < 0
        ):
            raise ValueError("story_points must be a finite non-negative number")
        if self.completion_sprint is not None and self.completion_sprint < 1:
            raise ValueError("completion_sprint must be positive when specified")
        if (
            self.state in {PlanState.PLANNED, PlanState.IN_PROGRESS}
            and self.completion_sprint is None
        ):
            raise ValueError("a planned or in-progress item must have completion_sprint")
        if self.state is PlanState.CANCELLED and self.completion_sprint is not None:
            raise ValueError("a cancelled item cannot have completion_sprint")
        allocation_sprints = [allocation.sprint_number for allocation in self.allocations]
        if len(allocation_sprints) != len(set(allocation_sprints)):
            raise ValueError("a task can have at most one allocation per sprint")
        if (
            allocation_sprints
            and self.completion_sprint is not None
            and max(allocation_sprints) > self.completion_sprint
        ):
            raise ValueError("task allocation cannot be later than completion_sprint")

    @property
    def is_in_quarter(self) -> bool:
        return (
            self.state
            in {PlanState.PLANNED, PlanState.IN_PROGRESS, PlanState.COMPLETED}
            and self.completion_sprint is not None
            and self.completion_sprint <= QUARTER_SPRINTS
        )


@dataclass(frozen=True, slots=True)
class QuarterPlan:
    """A versioned, immutable plan snapshot."""

    quarter_id: str
    version: int
    as_of_sprint: int
    items: tuple[PlanItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "quarter_id", self.quarter_id.strip())
        object.__setattr__(self, "items", tuple(self.items))
        if not self.quarter_id:
            raise ValueError("quarter_id must not be empty")
        if self.version < 1:
            raise ValueError("version must be positive")
        if not 0 <= self.as_of_sprint <= QUARTER_SPRINTS:
            raise ValueError(f"as_of_sprint must be between 0 and {QUARTER_SPRINTS}")
        task_ids = [item.task_id for item in self.items]
        duplicate_ids = sorted({task_id for task_id in task_ids if task_ids.count(task_id) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate task_id values in plan: {', '.join(duplicate_ids)}")

    def by_task_id(self) -> dict[str, PlanItem]:
        return {item.task_id: item for item in self.items}

    def baseline_items(self) -> tuple[PlanItem, ...]:
        """Items promised inside the quarter in this plan version."""

        return tuple(item for item in self.items if item.is_in_quarter)


@dataclass(frozen=True, slots=True)
class ActualResult:
    """Observed delivery in one closed sprint.

    ``initiative_completed`` is explicit because finishing one task does not
    always mean that the business initiative is complete.
    """

    task_id: str
    initiative_id: str
    sprint_number: int
    completed_story_points: float
    task_completed: bool
    initiative_completed: bool = False
    closure_status: str | None = None
    mismatch_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", self.task_id.strip())
        object.__setattr__(self, "initiative_id", self.initiative_id.strip())
        if not self.task_id or not self.initiative_id:
            raise ValueError("actual result task_id and initiative_id must not be empty")
        if not 1 <= self.sprint_number <= QUARTER_SPRINTS:
            raise ValueError(f"sprint_number must be between 1 and {QUARTER_SPRINTS}")
        if (
            not isinstance(self.completed_story_points, Real)
            or isinstance(self.completed_story_points, bool)
            or not isfinite(self.completed_story_points)
            or self.completed_story_points < 0
        ):
            raise ValueError("completed_story_points must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class ActualResults:
    """Append-only factual snapshot through the last closed sprint."""

    quarter_id: str
    completed_through_sprint: int
    entries: tuple[ActualResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "quarter_id", self.quarter_id.strip())
        object.__setattr__(self, "entries", tuple(self.entries))
        if not self.quarter_id:
            raise ValueError("quarter_id must not be empty")
        if not 0 <= self.completed_through_sprint <= QUARTER_SPRINTS:
            raise ValueError(
                f"completed_through_sprint must be between 0 and {QUARTER_SPRINTS}"
            )
        future_entries = [
            entry.task_id
            for entry in self.entries
            if entry.sprint_number > self.completed_through_sprint
        ]
        if future_entries:
            raise ValueError(
                "actual results cannot belong to an open sprint: "
                + ", ".join(sorted(set(future_entries)))
            )
        result_keys = [(entry.task_id, entry.sprint_number) for entry in self.entries]
        duplicates = sorted({key for key in result_keys if result_keys.count(key) > 1})
        if duplicates:
            readable = ", ".join(f"{task_id}/sprint-{sprint}" for task_id, sprint in duplicates)
            raise ValueError(f"duplicate actual result rows: {readable}")

    def for_sprint(self, sprint_number: int) -> tuple[ActualResult, ...]:
        return tuple(entry for entry in self.entries if entry.sprint_number == sprint_number)


@dataclass(frozen=True, slots=True)
class ReferenceCatalog:
    """Verbatim allowed values extracted from the three source reference lists."""

    planning_results: tuple[str, ...]
    mismatch_reasons: tuple[str, ...]
    closure_results: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("planning_results", "mismatch_reasons", "closure_results"):
            values = tuple(value.strip() for value in getattr(self, field_name))
            if not values or any(not value for value in values):
                raise ValueError(f"{field_name} must contain non-empty reference values")
            object.__setattr__(self, field_name, values)


def validate_reference_usage(
    plan: QuarterPlan,
    actual_results: ActualResults,
    catalog: ReferenceCatalog,
) -> tuple[str, ...]:
    """Return all source-dictionary violations without mutating or guessing values."""

    issues: list[str] = []
    allowed_plan = set(catalog.planning_results)
    allowed_reasons = set(catalog.mismatch_reasons)
    allowed_closure = set(catalog.closure_results)
    for item in plan.items:
        if item.decision_status is not None and item.decision_status not in allowed_plan:
            issues.append(f"task {item.task_id}: unknown decision_status {item.decision_status!r}")
        if item.mismatch_reason is not None and item.mismatch_reason not in allowed_reasons:
            issues.append(f"task {item.task_id}: unknown mismatch_reason {item.mismatch_reason!r}")
    for entry in actual_results.entries:
        if entry.closure_status is not None and entry.closure_status not in allowed_closure:
            issues.append(f"task {entry.task_id}: unknown closure_status {entry.closure_status!r}")
        if entry.mismatch_reason is not None and entry.mismatch_reason not in allowed_reasons:
            issues.append(f"task {entry.task_id}: unknown mismatch_reason {entry.mismatch_reason!r}")
    return tuple(issues)
