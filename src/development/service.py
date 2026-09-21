"""Fast, read-only development hints for an approved quarterly plan.

The service intentionally does not simulate hiring, training, capacity or a
future plan. It only identifies demanded roles/skills, attaches the tasks
that demand them and ranks people who are closest to each competency.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from time import perf_counter
from typing import Any, Iterable, Mapping

from src.skills import canonicalize_skill

from .skill_groups import skill_affinity


WEAK_BUS_FACTOR = 1
DEFAULT_QUARTER_SPRINTS = 6
COMPANY_SCOPE_ID = "__company__"
ROLE_BLOCKER_CODES = {
    "MISSING_SPECIALIST",
    "NO_AVAILABLE_ROLE_SPECIALIST",
    "INSUFFICIENT_ROLE_CAPACITY",
    "INSUFFICIENT_ENGINEER_CAPACITY",
}
SKILL_BLOCKER_CODES = {"INSUFFICIENT_SKILL_CAPACITY"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    return value is True or _text(value).casefold() in {"1", "true", "yes", "да"}


def _grade_fit(grade: Any) -> float:
    value = _text(grade).casefold()
    if "middle" in value or "мид" in value:
        return 1.0
    if "senior" in value or "сень" in value:
        return 0.9
    if "lead" in value or "лид" in value:
        return 0.85
    if "junior" in value or "джун" in value:
        return 0.8
    return 0.75


class DevelopmentRecommendationService:
    """Build two lightweight lists of competency recommendations."""

    def __init__(
        self,
        tables: Mapping[str, Iterable[Mapping[str, Any]]],
        dashboard: Mapping[str, Any],
    ) -> None:
        self.tables = {name: tuple(dict(row) for row in rows) for name, rows in tables.items()}
        self.dashboard = dashboard
        quarter = (dashboard.get("plan") or {}).get("quarter", {})
        self.quarter_sprint_count = max(int(quarter.get("sprint_count") or DEFAULT_QUARTER_SPRINTS), 1)
        self.engineers = {
            _text(row.get("engineer_id")): dict(row)
            for row in dashboard.get("engineers", [])
            if _text(row.get("engineer_id")) and not _truthy(row.get("archived"))
        }
        self.all_tasks = [
            dict(row) for row in dashboard.get("tasks", [])
            if not _truthy(row.get("archived")) and not _truthy(row.get("is_placeholder"))
        ]
        self.tasks = [row for row in self.all_tasks if self._is_quarter_task(row)]
        self.unplanned_tasks = [row for row in self.all_tasks if self._is_unplanned_task(row)]
        self._table_requirements = self._requirements_by_task()
        self._skill_bf = self._bus_factor_by_team_skill()
        self._role_bf = self._bus_factor_by_team_role()
        self._company_skill_bf = self._bus_factor_by_company_skill()
        self._company_role_bf = self._bus_factor_by_company_role()

    def _is_quarter_task(self, task: Mapping[str, Any]) -> bool:
        status = _text(task.get("current_status") or task.get("status")).replace("_", "").casefold()
        source_status = _text(task.get("source_status")).replace(" ", "").casefold()
        if status in {"done", "completed", "cancelled", "canceled", "moved", "unplannable"}:
            return False
        if source_status in {"done", "completed", "cancelled", "canceled"}:
            return False
        return any(
            1 <= int(sprint or 0) <= self.quarter_sprint_count
            for sprint in task.get("planned_sprints") or []
        )

    @staticmethod
    def _is_unplanned_task(task: Mapping[str, Any]) -> bool:
        status = _text(task.get("current_status") or task.get("status")).upper()
        return status in {"MOVED", "UNPLANNABLE"}

    def _requirements_by_task(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.tables.get("task_skill_requirements", ()):
            task_id = _text(row.get("task_id"))
            skill = canonicalize_skill(row.get("technology") or row.get("skill"))
            confidence = _text(row.get("confidence") or "high").casefold()
            if (
                not task_id or not skill or _truthy(row.get("rejected"))
                or (confidence not in {"", "high"} and not _truthy(row.get("confirmed")))
            ):
                continue
            grouped[task_id].append({
                **row,
                "technology": skill,
                "role": _text(row.get("role")) or None,
            })
        return grouped

    def _task_requirements(self, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        source = task.get("required_skills")
        rows = source if isinstance(source, list) else self._table_requirements.get(_text(task.get("task_id")), [])
        result: dict[tuple[str, str | None], dict[str, Any]] = {}
        for raw in rows or []:
            row = dict(raw) if isinstance(raw, Mapping) else {"technology": raw}
            skill = canonicalize_skill(row.get("technology") or row.get("skill"))
            confidence = _text(row.get("confidence") or "high").casefold()
            if (
                not skill or _truthy(row.get("rejected"))
                or (confidence not in {"", "high"} and not _truthy(row.get("confirmed")))
            ):
                continue
            role = _text(row.get("role")) or None
            result[(skill, role)] = {**row, "technology": skill, "role": role}
        return [result[key] for key in sorted(result, key=lambda value: (value[0].casefold(), value[1] or ""))]

    def _task_roles(self, task: Mapping[str, Any]) -> list[str]:
        roles = {
            _text(role)
            for role, hours in (task.get("role_requirements") or {}).items()
            if _text(role) and _number(hours) > 0
        }
        for sprint_roles in (task.get("role_hours_by_sprint") or {}).values():
            if isinstance(sprint_roles, Mapping):
                roles.update(
                    _text(role) for role, hours in sprint_roles.items()
                    if _text(role) and _number(hours) > 0
                )
        return sorted(roles, key=str.casefold)

    @staticmethod
    def _task_summary(task: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "task_id": _text(task.get("task_id")),
            "title": task.get("title"),
            "rung": task.get("rung"),
        }

    def _bus_factor_by_team_skill(self) -> dict[tuple[str, str], tuple[int, list[str]]]:
        result: dict[tuple[str, str], tuple[int, list[str]]] = {}
        for team in (self.dashboard.get("star_map") or {}).get("teams", []) or []:
            team_id = _text(team.get("team_id"))
            for item in team.get("competencies") or []:
                skill = canonicalize_skill(item.get("skill") or item.get("technology"))
                if not team_id or not skill:
                    continue
                holders = sorted({
                    _text(engineer.get("engineer_id"))
                    for engineer in item.get("engineers") or []
                    if _text(engineer.get("engineer_id"))
                })
                result[(team_id, skill)] = (int(item.get("bus_factor") or len(holders)), holders)
        return result

    def _bus_factor_by_team_role(self) -> dict[tuple[str, str], tuple[int, list[str]]]:
        result: dict[tuple[str, str], set[str]] = defaultdict(set)
        for assignment in self.tables.get("engineers", ()):
            engineer_id = _text(assignment.get("engineer_id"))
            team_id = _text(assignment.get("team_id"))
            role = _text(assignment.get("role"))
            if engineer_id in self.engineers and team_id and role:
                result[(team_id, role)].add(engineer_id)
        return {key: (len(holders), sorted(holders)) for key, holders in result.items()}

    def _bus_factor_by_company_skill(self) -> dict[str, tuple[int, list[str]]]:
        result: dict[str, set[str]] = defaultdict(set)
        for engineer_id, engineer in self.engineers.items():
            for raw_skill in engineer.get("skills") or []:
                skill = canonicalize_skill(raw_skill)
                if skill:
                    result[skill].add(engineer_id)
        return {skill: (len(holders), sorted(holders)) for skill, holders in result.items()}

    def _bus_factor_by_company_role(self) -> dict[str, tuple[int, list[str]]]:
        result: dict[str, set[str]] = defaultdict(set)
        for engineer_id, engineer in self.engineers.items():
            for role in engineer.get("roles") or []:
                if _text(role):
                    result[_text(role)].add(engineer_id)
        return {role: (len(holders), sorted(holders)) for role, holders in result.items()}

    def _quarter_demand_index(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        """Tasks linked to a competency; BF itself is not limited by demand."""

        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for task in self.tasks:
            team_id = _text(task.get("assigned_team_id") or task.get("original_team_id"))
            if not team_id:
                continue
            task_summary = self._task_summary(task)
            for role in self._task_roles(task):
                for scope_id in (team_id, COMPANY_SCOPE_ID):
                    item = grouped.setdefault((scope_id, "ROLE", role), {"tasks": {}, "related_roles": set()})
                    item["tasks"][task_summary["task_id"]] = task_summary
            for requirement in self._task_requirements(task):
                skill = requirement["technology"]
                for scope_id in (team_id, COMPANY_SCOPE_ID):
                    item = grouped.setdefault((scope_id, "SKILL", skill), {"tasks": {}, "related_roles": set()})
                    item["tasks"][task_summary["task_id"]] = task_summary
                    if requirement.get("role"):
                        item["related_roles"].add(requirement["role"])
        return grouped

    def _group_bus_factor_weaknesses(self) -> list[dict[str, Any]]:
        """Return every role/skill with BF=1 for each team and the company."""

        demand = self._quarter_demand_index()
        sources: list[tuple[str, str, str, int, list[str]]] = []
        sources.extend(
            (team_id, "ROLE", role, bus_factor, holders)
            for (team_id, role), (bus_factor, holders) in self._role_bf.items()
        )
        sources.extend(
            (team_id, "SKILL", skill, bus_factor, holders)
            for (team_id, skill), (bus_factor, holders) in self._skill_bf.items()
        )
        sources.extend(
            (COMPANY_SCOPE_ID, "ROLE", role, bus_factor, holders)
            for role, (bus_factor, holders) in self._company_role_bf.items()
        )
        sources.extend(
            (COMPANY_SCOPE_ID, "SKILL", skill, bus_factor, holders)
            for skill, (bus_factor, holders) in self._company_skill_bf.items()
        )

        output = []
        for team_id, competency_type, name, bus_factor, holders in sorted(sources):
            if bus_factor != WEAK_BUS_FACTOR:
                continue
            linked = demand.get((team_id, competency_type, name), {"tasks": {}, "related_roles": set()})
            output.append({
                "category": "TEAM_WEAKNESS",
                "category_label": "Слабые места команд",
                "scope": "company" if team_id == COMPANY_SCOPE_ID else "team",
                "team_id": team_id,
                "severity": "CRITICAL",
                "deficit": {
                    "type": competency_type,
                    "role": name if competency_type == "ROLE" else None,
                    "skill": name if competency_type == "SKILL" else None,
                    "skills": [name] if competency_type == "SKILL" else [],
                    "bus_factor": bus_factor,
                },
                "tasks": sorted(linked["tasks"].values(), key=lambda row: row["task_id"]),
                "demand_basis": "FULL_COMPETENCY_CATALOG_WITH_BF_1",
                "risk_reasons": ["LOW_ROLE_BUS_FACTOR" if competency_type == "ROLE" else "LOW_SKILL_BUS_FACTOR"],
                "current_holders": holders,
                "_related_roles": sorted(linked["related_roles"], key=str.casefold),
            })
        return output

    def _group_demands(
        self,
        tasks: Iterable[Mapping[str, Any]],
        *,
        category: str,
        category_label: str,
        bf_one_only: bool,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for task in tasks:
            team_id = _text(task.get("assigned_team_id") or task.get("original_team_id"))
            if not team_id:
                continue
            task_summary = self._task_summary(task)
            for role in self._task_roles(task):
                key = (team_id, "ROLE", role)
                item = grouped.setdefault(key, {"tasks": {}, "related_roles": set()})
                item["tasks"][task_summary["task_id"]] = task_summary
            for requirement in self._task_requirements(task):
                skill = requirement["technology"]
                key = (team_id, "SKILL", skill)
                item = grouped.setdefault(key, {"tasks": {}, "related_roles": set()})
                item["tasks"][task_summary["task_id"]] = task_summary
                if requirement.get("role"):
                    item["related_roles"].add(requirement["role"])

        output = []
        for (team_id, competency_type, name), item in sorted(grouped.items()):
            if competency_type == "ROLE":
                bus_factor, holders = self._role_bf.get((team_id, name), (0, []))
                role, skills = name, []
                risk_reason = "LOW_ROLE_BUS_FACTOR"
            else:
                bus_factor, holders = self._skill_bf.get((team_id, name), (0, []))
                role, skills = None, [name]
                risk_reason = "LOW_SKILL_BUS_FACTOR"
            if bf_one_only and bus_factor != WEAK_BUS_FACTOR:
                continue
            output.append({
                "category": category,
                "category_label": category_label,
                "team_id": team_id,
                "severity": "CRITICAL" if bf_one_only else "WARNING",
                "deficit": {
                    "type": competency_type,
                    "role": role,
                    "skill": name if competency_type == "SKILL" else None,
                    "skills": skills,
                    "bus_factor": bus_factor if bf_one_only else None,
                },
                "tasks": sorted(item["tasks"].values(), key=lambda row: row["task_id"]),
                "demand_basis": (
                    "UNPLANNED_TASK_REQUIREMENTS"
                    if category == "PLAN_CRITICAL"
                    else "QUARTER_TASK_REQUIREMENTS_WITH_BF_1"
                ),
                "risk_reasons": [risk_reason] if bf_one_only else [],
                "current_holders": holders,
                "_related_roles": sorted(item["related_roles"], key=str.casefold),
            })
        return output

    def _group_unplanned_blockers(self) -> list[dict[str, Any]]:
        """Group only role/skill shortages that actually blocked planning."""

        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for task in self.unplanned_tasks:
            team_id = _text(task.get("assigned_team_id") or task.get("original_team_id"))
            if not team_id:
                continue
            task_summary = self._task_summary(task)
            for reason in task.get("reasons") or []:
                if not isinstance(reason, Mapping):
                    continue
                code = _text(reason.get("code")).upper()
                details = reason.get("details") if isinstance(reason.get("details"), Mapping) else {}
                role = _text(details.get("role"))
                raw_skills = details.get("missing_skills") or details.get("required_technologies") or []
                if isinstance(raw_skills, str):
                    raw_skills = [raw_skills]
                skills = sorted({
                    canonicalize_skill(value) for value in raw_skills
                    if canonicalize_skill(value)
                }, key=str.casefold)

                if code in ROLE_BLOCKER_CODES and role:
                    key = (team_id, "ROLE", role)
                    item = grouped.setdefault(key, {"tasks": {}, "related_roles": set(), "reason_codes": set()})
                    item["tasks"][task_summary["task_id"]] = task_summary
                    item["reason_codes"].add(code)
                if code in SKILL_BLOCKER_CODES:
                    for skill in skills:
                        key = (team_id, "SKILL", skill)
                        item = grouped.setdefault(key, {"tasks": {}, "related_roles": set(), "reason_codes": set()})
                        item["tasks"][task_summary["task_id"]] = task_summary
                        item["reason_codes"].add(code)
                        if role:
                            item["related_roles"].add(role)

        output = []
        for (team_id, competency_type, name), item in sorted(grouped.items()):
            if competency_type == "ROLE":
                _, holders = self._role_bf.get((team_id, name), (0, []))
                role, skills = name, []
            else:
                _, holders = self._skill_bf.get((team_id, name), (0, []))
                role, skills = None, [name]
            output.append({
                "category": "PLAN_CRITICAL",
                "category_label": "Критично по плану",
                "team_id": team_id,
                "severity": "CRITICAL",
                "deficit": {
                    "type": competency_type,
                    "role": role,
                    "skill": name if competency_type == "SKILL" else None,
                    "skills": skills,
                    "bus_factor": None,
                },
                "tasks": sorted(item["tasks"].values(), key=lambda row: row["task_id"]),
                "demand_basis": "UNPLANNED_TASK_RESOURCE_BLOCKERS",
                "risk_reasons": sorted(item["reason_codes"]),
                "current_holders": holders,
                "_related_roles": sorted(item["related_roles"], key=str.casefold),
            })
        return output

    def _quarter_unassigned(self, engineer: Mapping[str, Any]) -> bool:
        allocations = [
            row for row in engineer.get("allocations") or []
            if 1 <= int(row.get("sprint") or 0) <= self.quarter_sprint_count
        ]
        if allocations:
            return sum(_number(row.get("allocated_hours")) for row in allocations) <= 1e-6
        return sum(
            _number((engineer.get("used_hours_by_sprint") or {}).get(str(sprint)))
            for sprint in range(1, self.quarter_sprint_count + 1)
        ) <= 1e-6

    def _candidates(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        target_role = _text(row["deficit"].get("role")) or None
        target_skill = _text(row["deficit"].get("skill")) or None
        related_roles = {_text(role).casefold() for role in row.get("_related_roles") or []}
        holders = set(row.get("current_holders") or [])
        output = []
        for engineer_id, engineer in self.engineers.items():
            company_scope = row.get("scope") == "company"
            if engineer_id in holders or (not company_scope and row["team_id"] not in (engineer.get("team_ids") or [])):
                continue
            roles = sorted({_text(role) for role in engineer.get("roles") or [] if _text(role)}, key=str.casefold)
            skills = {canonicalize_skill(skill) for skill in engineer.get("skills") or [] if canonicalize_skill(skill)}
            role_relevance = bool(related_roles.intersection(role.casefold() for role in roles))
            affinity = skill_affinity(target_skill, skills) if target_skill else None
            if target_skill and (affinity is None or affinity.score <= 0) and not role_relevance:
                continue

            quarter_unassigned = self._quarter_unassigned(engineer)
            grade_fit = _grade_fit(engineer.get("grade"))
            if target_skill:
                score = 0.65 * (affinity.score if affinity else 0.0) + 0.20 * float(role_relevance) + 0.10 * grade_fit + 0.05 * float(quarter_unassigned)
            else:
                score = 0.55 * float(quarter_unassigned) + 0.25 * grade_fit + 0.20 / max(len(roles), 1)

            reasons = ["Работает в компании."] if company_scope else [f"Состоит в команде {row['team_id']}."]
            if quarter_unassigned:
                reasons.append("Не задействован в утверждённом плане на квартал — показан выше остальных.")
            if role_relevance:
                reasons.append("Текущая роль соответствует задачам, которым нужен этот навык.")
            if affinity and affinity.related_skills:
                reasons.append(
                    f"Связанные навыки: {', '.join(affinity.related_skills)} "
                    f"(группа «{affinity.group_label}»)."
                )

            output.append({
                "engineer_id": engineer_id,
                "name": engineer.get("name") or engineer_id,
                "roles": roles,
                "grade": engineer.get("grade"),
                "score": round(score, 4),
                "score_percent": round(score * 100),
                "target_skill": target_skill,
                "target_role": target_role,
                "related_skills": list(affinity.related_skills) if affinity else [],
                "skill_group": affinity.group_key if affinity else None,
                "skill_group_label": affinity.group_label if affinity else None,
                "quarter_unassigned": quarter_unassigned,
                "priority_group": "UNASSIGNED_QUARTER" if quarter_unassigned else "RELATED_PROFILE",
                "development_options": ["ДООБУЧИТЬ_НАВЫКУ"] if target_skill else ["ДОБАВИТЬ_РОЛЬ"],
                "reasons": reasons,
            })
        output.sort(key=lambda item: (
            not item["quarter_unassigned"],
            -item["score"],
            item["engineer_id"],
        ))
        return output

    def _recommendation(self, row: dict[str, Any]) -> dict[str, Any]:
        candidates = self._candidates(row)
        deficit = row["deficit"]
        role = deficit.get("role")
        skills = list(deficit.get("skills") or [])
        signature = "|".join([row["category"], row["team_id"], deficit["type"], _text(role), *skills])
        hire_label = role if role else f"Специалист со знанием {', '.join(skills)}"
        return {
            "recommendation_id": "development-" + hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12],
            "team_id": row["team_id"],
            "scope": row.get("scope", "team"),
            "category": row["category"],
            "category_label": row["category_label"],
            "severity": row["severity"],
            "priority": {
                "covered_task_count": len(row["tasks"]),
                "has_unassigned_candidates": any(item["quarter_unassigned"] for item in candidates),
            },
            "deficit": deficit,
            "tasks": row["tasks"],
            "demand_basis": row["demand_basis"],
            "risk_reasons": row["risk_reasons"],
            "current_holders": row["current_holders"],
            "recommendation": {
                "primary_action": "UPSKILL_OR_HIRE",
                "candidates": candidates,
                "candidate_count": len(candidates),
                "hire_profile": {
                    "role": role,
                    "skills": skills,
                    "label": hire_label,
                },
                "reasons": [
                    "Кандидаты показаны в порядке приоритета для дообучения.",
                    "Найм указан как альтернатива без расчёта количества и влияния на план.",
                ],
            },
        }

    def calculate(self) -> dict[str, Any]:
        started = perf_counter()
        rows = [
            *self._group_unplanned_blockers(),
            *self._group_bus_factor_weaknesses(),
        ]
        recommendations = [self._recommendation(row) for row in rows]
        recommendations.sort(key=lambda item: (
            item["category"] != "PLAN_CRITICAL",
            item.get("scope") == "company",
            -item["priority"]["covered_task_count"],
            not item["priority"]["has_unassigned_candidates"],
            item["team_id"],
            item["deficit"]["type"],
            _text(item["deficit"].get("role") or item["deficit"].get("skill")).casefold(),
        ))
        teams = sorted({item["team_id"] for item in recommendations if item["team_id"] != COMPANY_SCOPE_ID})
        roles = sorted({
            _text(item["deficit"].get("role"))
            for item in recommendations if item["deficit"].get("role")
        }, key=str.casefold)
        skills = sorted({
            skill for item in recommendations for skill in item["deficit"].get("skills") or []
        }, key=str.casefold)
        return {
            "status": "READY",
            "source": "LATEST_APPROVED_PLAN",
            "approved_revision": (self.dashboard.get("approved_snapshot") or {}).get("revision"),
            "scope": "FULL_QUARTER",
            "summary": {
                "total": len(recommendations),
                "plan_critical": sum(item["category"] == "PLAN_CRITICAL" for item in recommendations),
                "team_weakness": sum(item["category"] == "TEAM_WEAKNESS" for item in recommendations),
                "roles": sum(item["deficit"]["type"] == "ROLE" for item in recommendations),
                "skills": sum(item["deficit"]["type"] == "SKILL" for item in recommendations),
            },
            "filters": {
                "teams": teams,
                "roles": roles,
                "skills": skills,
                "categories": ["PLAN_CRITICAL", "TEAM_WEAKNESS"],
            },
            "recommendations": recommendations,
            "calculation_ms": round((perf_counter() - started) * 1000, 2),
            "read_only": True,
            "calculation_mode": "COMPETENCY_LOOKUP_ONLY",
        }
