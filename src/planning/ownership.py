"""Task-owner validation derived from final engineer allocations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .plan_models import TaskAllocation


EPSILON = 1e-9


@dataclass(frozen=True)
class SprintOwnership:
    sprint: int
    owner_hours: float
    external_hours: float

    @property
    def valid(self) -> bool:
        return self.external_hours <= EPSILON or self.owner_hours > EPSILON


@dataclass(frozen=True)
class TaskOwnership:
    owner_team_id: str
    owner_hours: float
    external_hours: float
    owner_engineer_ids: tuple[str, ...]
    external_engineer_ids: tuple[str, ...]
    sprint_ownership: tuple[SprintOwnership, ...]

    @property
    def total_hours(self) -> float:
        return self.owner_hours + self.external_hours

    @property
    def owner_share(self) -> float | None:
        if self.total_hours <= EPSILON:
            return None
        return self.owner_hours / self.total_hours

    @property
    def owner_engineer_count(self) -> int:
        return len(self.owner_engineer_ids)

    @property
    def external_engineer_count(self) -> int:
        return len(self.external_engineer_ids)

    @property
    def hours_valid(self) -> bool:
        return self.total_hours <= EPSILON or self.owner_hours > self.external_hours + EPSILON

    @property
    def headcount_valid(self) -> bool:
        return (
            self.total_hours <= EPSILON
            or self.owner_engineer_count >= self.external_engineer_count
        )

    @property
    def sprint_participation_valid(self) -> bool:
        return all(item.valid for item in self.sprint_ownership)

    @property
    def valid(self) -> bool:
        return self.hours_valid and self.headcount_valid and self.sprint_participation_valid

    @property
    def status_message(self) -> str:
        if not self.hours_valid:
            return (
                f"{self.owner_team_id} не может быть основной командой: большую часть "
                "трудозатрат пришлось бы выполнять специалистам других команд."
            )
        if not self.headcount_valid:
            return (
                "Для выполнения задачи потребовалось бы больше внешних специалистов, "
                f"чем специалистов {self.owner_team_id}."
            )
        if not self.sprint_participation_valid:
            return (
                f"{self.owner_team_id} не участвует в одном из спринтов, где работу "
                "выполняют внешние специалисты."
            )
        if self.total_hours <= EPSILON:
            return "Для задачи не указаны человеко-часы."
        return f"Основная часть работы остаётся у {self.owner_team_id}."

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_team_id": self.owner_team_id,
            "total_hours": self.total_hours,
            "owner_hours": self.owner_hours,
            "external_hours": self.external_hours,
            "owner_share": self.owner_share,
            "owner_share_percent": (
                self.owner_share * 100 if self.owner_share is not None else None
            ),
            "owner_engineer_ids": list(self.owner_engineer_ids),
            "external_engineer_ids": list(self.external_engineer_ids),
            "owner_engineer_count": self.owner_engineer_count,
            "external_engineer_count": self.external_engineer_count,
            "hours_valid": self.hours_valid,
            "headcount_valid": self.headcount_valid,
            "sprint_participation_valid": self.sprint_participation_valid,
            "valid": self.valid,
            "status_message": self.status_message,
            "sprints": [
                {**asdict(item), "valid": item.valid}
                for item in self.sprint_ownership
            ],
        }


def _evaluate(
    owner_team_id: str,
    contributions: Iterable[tuple[int, str, str | None, float]],
) -> TaskOwnership:
    owner_hours = 0.0
    external_hours = 0.0
    owner_engineers: set[str] = set()
    external_engineers: set[str] = set()
    by_sprint: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])

    for sprint, engineer_id, source_team_id, raw_hours in contributions:
        hours = max(float(raw_hours), 0.0)
        if hours <= EPSILON:
            continue
        if source_team_id == owner_team_id:
            owner_hours += hours
            owner_engineers.add(engineer_id)
            by_sprint[int(sprint)][0] += hours
        else:
            external_hours += hours
            external_engineers.add(engineer_id)
            by_sprint[int(sprint)][1] += hours

    return TaskOwnership(
        owner_team_id=owner_team_id,
        owner_hours=owner_hours,
        external_hours=external_hours,
        owner_engineer_ids=tuple(sorted(owner_engineers)),
        external_engineer_ids=tuple(sorted(external_engineers)),
        sprint_ownership=tuple(
            SprintOwnership(sprint, values[0], values[1])
            for sprint, values in sorted(by_sprint.items())
        ),
    )


def evaluate_task_ownership(
    owner_team_id: str,
    allocations: Iterable[TaskAllocation],
) -> TaskOwnership:
    """Evaluate owner share once from a task's final domain allocations."""

    return _evaluate(
        owner_team_id,
        (
            (
                allocation.sprint_number,
                engineer.engineer_id,
                engineer.source_team_id,
                engineer.allocated_hours,
            )
            for allocation in allocations
            for engineer in allocation.engineer_allocations
        ),
    )


def evaluate_ownership_rows(
    owner_team_id: str,
    allocations: Iterable[Mapping[str, Any]],
) -> TaskOwnership:
    """Evaluate the same rule from serialized allocations used by API layers."""

    def contributions() -> Iterable[tuple[int, str, str | None, float]]:
        for row in allocations:
            engineer_id = str(row.get("engineer_id") or "").strip()
            if not engineer_id:
                continue
            source_team = row.get("source_team_id")
            yield (
                int(row.get("sprint") or row.get("sprint_number") or 0),
                engineer_id,
                str(source_team).strip() if source_team is not None else None,
                float(row.get("allocated_hours") or row.get("hours") or 0.0),
            )

    return _evaluate(owner_team_id, contributions())
