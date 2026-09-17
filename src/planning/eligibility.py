"""Pre-planning eligibility checks for structural role availability."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.data.normalization import normalize_role_name

from .reasons import ReasonCode


def _text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def _positive_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


@dataclass(frozen=True)
class EligibilityContext:
    task_role_effort: tuple[dict[str, Any], ...]
    engineers: tuple[dict[str, Any], ...]

    @classmethod
    def from_records(
        cls,
        *,
        task_role_effort: Iterable[Mapping[str, Any]],
        engineers: Iterable[Mapping[str, Any]],
    ) -> "EligibilityContext":
        return cls(
            task_role_effort=tuple(dict(row) for row in task_role_effort),
            engineers=tuple(dict(row) for row in engineers),
        )


@dataclass(frozen=True)
class EligibilityReason:
    code: ReasonCode
    role: str
    required_hours: float
    available_specialists: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "role": self.role,
            "required_hours": self.required_hours,
            "available_specialists": self.available_specialists,
        }


@dataclass(frozen=True)
class EligibilityResult:
    task_id: str
    eligible: bool
    reasons: tuple[EligibilityReason, ...] = ()

    @property
    def missing_roles(self) -> tuple[str, ...]:
        return tuple(reason.role for reason in self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "eligible": self.eligible,
            "reasons": [reason.to_dict() for reason in self.reasons],
        }


@dataclass(frozen=True)
class EligibilityReport:
    results: tuple[EligibilityResult, ...]

    @property
    def eligible(self) -> tuple[EligibilityResult, ...]:
        return tuple(result for result in self.results if result.eligible)

    @property
    def blocked_by_missing_specialists(self) -> tuple[EligibilityResult, ...]:
        return tuple(result for result in self.results if not result.eligible)

    def summary(self) -> dict[str, int]:
        return {
            "total_tasks": len(self.results),
            "eligible": len(self.eligible),
            "blocked_by_missing_specialists": len(self.blocked_by_missing_specialists),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "results": [result.to_dict() for result in self.results],
        }


class EligibilityService:
    """Check canonical roles across all teams; sprint capacity is checked later."""

    def __init__(self, context: EligibilityContext):
        self.context = context
        specialists_by_role: dict[str, set[str]] = defaultdict(set)
        for row in context.engineers:
            engineer_id = _text(row.get("engineer_id"))
            role = normalize_role_name(_text(row.get("role")))
            if engineer_id and role:
                specialists_by_role[role].add(engineer_id)
        self._specialists_by_role = specialists_by_role

        requirements_by_task: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for row in context.task_role_effort:
            task_id = _text(row.get("task_id"))
            role = normalize_role_name(_text(row.get("role")))
            required_hours = _positive_number(row.get("required_hours"))
            if task_id and role and required_hours is not None:
                requirements_by_task[task_id][role] += required_hours
        self._requirements_by_task = requirements_by_task

    def check(self, task: Mapping[str, Any]) -> EligibilityResult:
        task_id = _text(task.get("task_id")) or ""
        reasons = tuple(
            EligibilityReason(
                code=ReasonCode.NO_AVAILABLE_ROLE_SPECIALIST,
                role=role,
                required_hours=required_hours,
                available_specialists=0,
            )
            for role, required_hours in sorted(
                self._requirements_by_task.get(task_id, {}).items()
            )
            if not self._specialists_by_role.get(role)
        )
        return EligibilityResult(task_id=task_id, eligible=not reasons, reasons=reasons)

    def check_all(self, tasks: Iterable[Mapping[str, Any]]) -> EligibilityReport:
        return EligibilityReport(tuple(self.check(task) for task in tasks))


def check_task_eligibility(
    task: Mapping[str, Any], context: EligibilityContext
) -> EligibilityResult:
    """Public convenience API for checking one task."""

    return EligibilityService(context).check(task)
