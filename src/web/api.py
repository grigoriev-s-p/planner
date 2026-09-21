"""FastAPI application serving the product UI and planner API."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Callable, Literal
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .constructor import (
    ConstructorDatasetUploadError,
    ConstructorScenario,
    PreparedConstructorUpload,
    ScenarioValidationError,
    build_constructor_catalog,
    build_catalog_snapshot,
    build_constructor_plan,
    build_constructor_plan_from_prepared,
    prepare_constructor_workbook,
)
from src.skills import get_skill_registry, merge_task_skill_requirements
from src.development import DevelopmentRecommendationService
from .data_preflight import structural_preflight
from .service import (
    PlanningAPIService,
    ServiceDataError,
    build_bus_factor_payload,
    get_planning_service,
)
from .catalog import (
    ARCHIVE_REASON_LABELS,
    ArchiveConflictError,
    ArchiveValidationError,
    CatalogNotFoundError,
    CatalogConflictError,
    get_catalog_repository,
)
from src.star_map import StarMapService
from .recommendations import RecommendationDraft
from .replanning import build_remaining_horizon_plan, _immutable_task_baseline, _task_map
from .entity_projections import (
    build_engineer_history,
    build_engineer_projection,
    build_team_history,
    build_team_projection,
)
from .lifecycle import capture_closed_sprint, project_lifecycle
from .kpi_projection import project_management_kpis, sprint_kpi
from .engineer_workspace import build_engineer_workspace
from src.planning.task_states import is_historical_done


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(title="PostCode Quarter Planning API", version="1.0.0")


class ArchiveRequest(BaseModel):
    reason_code: str
    reason_text: str | None = None
    confirm_replan: bool = False
    team_engineer_action: Literal["UNASSIGN", "DEACTIVATE"] | None = None
    team_task_action: Literal["REMOVE", "REDISTRIBUTE"] | None = None
    engineer_allocation_action: Literal[
        "APPLY_PLANNER", "REPLAN_WITHOUT_REPLACEMENT"
    ] | None = None


class DraftApprovalRequest(BaseModel):
    draft_id: str


class RecommendationRequest(BaseModel):
    recommendation_id: str


class AcceptedChangeRequest(BaseModel):
    change_id: str


class RecommendationRefreshRequest(BaseModel):
    revision: int


class ActualTaskResultRequest(BaseModel):
    task_id: str
    task_completed: bool = False
    incomplete_reason: str | None = None
    remaining_story_points: float | None = Field(default=None, ge=0)
    remaining_role_hours: dict[str, float] = Field(default_factory=dict)
    remaining_engineer_allocations: dict[str, float] = Field(default_factory=dict)
    employee_deviation: dict[str, object] | None = None
    unavailable_next_sprint: bool = False
    unavailable_engineer_ids: list[str] = Field(default_factory=list)


class SprintActualRequest(BaseModel):
    sprint_number: int = Field(ge=1, le=6)
    all_completed: bool = False
    results: list[ActualTaskResultRequest] = Field(default_factory=list)


class ConstructorUploadDecisionRequest(BaseModel):
    decisions: dict[str, Literal["KEEP_ARCHIVED", "RESTORE", "RESTORE_UPDATE"]] = Field(default_factory=dict)
    existing_decisions: dict[str, Literal["KEEP_CURRENT", "UPDATE"]] = Field(default_factory=dict)


class SkillExtractionRequest(BaseModel):
    title: str = Field(default="", max_length=500)
    task_id: str = Field(default="preview", max_length=120)
    roles: list[str] = Field(default_factory=list)
    explicit: list[dict] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)


class CatalogEngineerCreateRequest(BaseModel):
    engineer_id: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=200)
    team_id: str | None = Field(default=None, max_length=120)
    role: str = Field(min_length=1, max_length=200)
    grade: str | None = Field(default=None, max_length=120)
    capacity_rate: float = Field(default=1, gt=0, le=1)
    skills: list[str] = Field(default_factory=list)


class CatalogTeamCreateRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=200)
    engineer_ids: list[str] = Field(default_factory=list)
    # A manually created team has no historical velocity yet, so its initial
    # per-sprint capacity is entered explicitly by the user.
    capacity_sp: float | None = Field(default=None, ge=0)


class CatalogMembershipRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=120)
    capacity_rate: float = Field(gt=0, le=1)
    role: str | None = Field(default=None, max_length=200)
    grade: str | None = Field(default=None, max_length=120)


class CatalogTaskCreateRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    initiative_id: str | None = Field(default=None, max_length=120)
    team_id: str | None = Field(default=None, max_length=120)
    status: Literal["ToDo", "InProgress", "Done"] = "ToDo"
    rung: float = 0
    story_points: float = Field(ge=0)
    planned_start: str | None = Field(default=None, max_length=50)
    deadline: str | None = Field(default=None, max_length=50)
    role_requirements: list[dict] = Field(default_factory=list)
    required_skills: list[dict] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


def _with_official_week0_kpi_denominator(payload: dict) -> dict:
    """Anchor KPI to every active initiative in the stored Week-0 task pool."""

    candidate = deepcopy(payload)
    state = candidate.get("_planning_state") or {}
    # KPI denominator is anchored to the immutable Week-0 pool.  Replanning
    # mutates current tables (for example marking a task Done), so those rows
    # must never shrink the baseline initiative set.
    tables = state.get("baseline_tables") or state.get("tables") or {}
    tasks = tables.get("tasks") or []
    initiative_ids = {
        str(task.get("initiative_id")).strip()
        for task in tasks
        if task.get("initiative_id")
        and str(task.get("status") or "").strip().casefold()
        in {"todo", "inprogress"}
        and not (
            task.get("is_placeholder") is True
            or str(task.get("is_placeholder") or "").strip().casefold()
            in {"true", "1", "yes"}
        )
    }
    if not initiative_ids:
        return candidate

    denominator = float(len(initiative_ids))
    holders = [candidate, state.get("week0_baseline") or {}]
    for holder in holders:
        kpis = holder.get("kpis") or {}
        expected = kpis.get("expected_kpi") or {}
        completed = expected.get("completed_initiatives")
        if completed is not None:
            expected["baseline_initiatives"] = denominator
            expected["incomplete_initiatives"] = max(
                denominator - float(completed), 0.0
            )
            expected["completion_percent"] = (
                float(completed) / denominator * 100 if denominator else None
            )
            kpis["expected_kpi"] = expected
        quarter = kpis.get("quarter_kpi") or {}
        if quarter:
            quarter["baseline_initiatives"] = denominator
            numerator = quarter.get("completed_initiatives")
            quarter["completion_percent"] = (
                float(numerator) / denominator * 100
                if numerator is not None and denominator
                else None
            )
            kpis["quarter_kpi"] = quarter
        holder["kpis"] = kpis
    candidate["_planning_state"] = state
    return candidate


class PlanDraftStore:
    """Keep review drafts in memory and approved snapshots persistently."""

    def __init__(self, repository_provider: Callable[[], object] | None = None) -> None:
        self._lock = RLock()
        self._drafts: dict[str, RecommendationDraft] = {}
        self._approved: dict | None = None
        self._replan_workflows: dict[str, dict] = {}
        self._repository_provider = repository_provider
        self._persistent_loaded = repository_provider is None
        self._draft_catalog_revisions: dict[str, int] = {}

    def _persist(self, payload: dict) -> dict:
        if self._repository_provider is None:
            return deepcopy(payload)
        repository = self._repository_provider()
        return repository.save_approved_plan(payload)

    def create(self, payload: dict) -> dict:
        draft_id = uuid4().hex
        draft = RecommendationDraft(payload)
        with self._lock:
            self._drafts = {draft_id: draft}
            revision = (
                int(self._repository_provider().revision())
                if self._repository_provider is not None
                else 0
            )
            self._draft_catalog_revisions = {draft_id: revision}
        snapshot = draft.snapshot(draft_id)
        snapshot.setdefault("draft", {})["catalog_revision"] = revision
        snapshot["draft"]["catalog_stale"] = False
        return snapshot

    def snapshot(self, draft_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            snapshot = draft.snapshot(draft_id)
            built_revision = self._draft_catalog_revisions.get(draft_id, 0)
            current_revision = (
                int(self._repository_provider().revision())
                if self._repository_provider is not None
                else built_revision
            )
            snapshot.setdefault("draft", {})["catalog_revision"] = built_revision
            snapshot["draft"]["catalog_stale"] = built_revision != current_revision
            return snapshot

    def apply(self, draft_id: str, recommendation_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            draft.apply(recommendation_id)
            return draft.snapshot(draft_id)

    def reject(self, draft_id: str, recommendation_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            draft.reject(recommendation_id)
            return draft.snapshot(draft_id)

    def undo(self, draft_id: str, change_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            draft.undo(change_id)
            return draft.snapshot(draft_id)

    def reset_to_base(self, draft_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            draft.reset()
            return draft.snapshot(draft_id)

    def apply_all(self, draft_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            draft.apply_all()
            return draft.snapshot(draft_id)

    def refresh_recommendations(self, draft_id: str, revision: int) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            draft.refresh_optimization(expected_revision=revision)
            return draft.snapshot(draft_id)

    def approve(self, draft_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            if self._repository_provider is not None:
                built_revision = self._draft_catalog_revisions.get(draft_id, 0)
                if built_revision != int(self._repository_provider().revision()):
                    raise ValueError(
                        "Каталог изменился после построения плана. Постройте план заново перед утверждением."
                    )
            approved = draft.snapshot(draft_id)
            approved["draft"] = {
                **approved.get("draft", {}),
                "approved": True,
            }
            approved = _current_plan_without_historical_done(approved)
            for task in approved.get("tasks", []):
                if task.get("remaining_story_points") is None:
                    task["remaining_story_points"] = task.get("story_points")
            for task in (approved.get("plan") or {}).get("tasks", []):
                if task.get("remaining_story_points") is None:
                    task["remaining_story_points"] = task.get("story_points")
            approved["_planning_state"] = {
                "week0_baseline": deepcopy(draft.base_plan),
                "tables": deepcopy(draft.tables),
                "baseline_tables": deepcopy(draft.tables),
                "original_task_baseline": _immutable_task_baseline(draft.tables),
                "completed_through_sprint": 0,
                "actual_completed_tasks": {},
            }
            approved["revision_context"] = {
                "previous_approved_revision": None,
                "closed_sprint": 0,
                "trigger": "APPROVE",
            }
            if self._repository_provider is not None and draft.tables:
                # Scenario entities stay draft-only until the plan is approved.
                # Approval also makes newly created entities part of the
                # persistent working catalog.
                self._repository_provider().merge_tables(deepcopy(draft.tables))
            self._approved = self._persist(
                _with_official_week0_kpi_denominator(approved)
            )
            return deepcopy(self._approved)

    def approved(self) -> dict | None:
        with self._lock:
            if not self._persistent_loaded and self._repository_provider is not None:
                loaded = self._repository_provider().load_approved_plan()
                self._approved = (
                    _with_official_week0_kpi_denominator(loaded)
                    if loaded is not None
                    else None
                )
                self._persistent_loaded = True
            return deepcopy(self._approved) if self._approved is not None else None

    def history(self) -> list[dict]:
        with self._lock:
            if self._repository_provider is not None:
                return [
                    deepcopy(item)
                    for item in self._repository_provider().approved_plan_history()
                ]
            return [deepcopy(self._approved)] if self._approved is not None else []

    def reset(self) -> None:
        with self._lock:
            self._drafts = {}
            self._approved = None
            self._replan_workflows = {}
            self._draft_catalog_revisions = {}
            self._persistent_loaded = True

    @staticmethod
    def _approved_token(payload: dict) -> str:
        revision = (payload.get("approved_snapshot") or {}).get("revision")
        if revision is not None:
            return f"revision:{revision}"
        source = {
            "plan": payload.get("plan"),
            "tasks": payload.get("tasks"),
            "completed_through_sprint": (
                payload.get("_planning_state") or {}
            ).get("completed_through_sprint"),
        }
        raw = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "fingerprint:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _actual_fingerprint(
        sprint_number: int, results: list[dict], all_completed: bool = False
    ) -> str:
        raw = json.dumps(
            {
                "sprint_number": sprint_number,
                "all_completed": all_completed,
                "results": results,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_sprint_actual(
        approved: dict,
        sprint_number: int,
        results: list[dict],
        all_completed: bool = False,
    ) -> None:
        state = approved.get("_planning_state") or {}
        tables = state.get("tables") or {}
        if not tables:
            raise ValueError("У утверждённого плана отсутствует planning context.")
        if sprint_number != int(state.get("completed_through_sprint") or 0) + 1:
            raise ValueError("Спринты должны закрываться последовательно.")
        task_ids = [str(row.get("task_id") or "") for row in results]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Факт одной задачи нельзя передать дважды.")
        known_ids = {str(row.get("task_id")) for row in tables.get("tasks", [])}
        unknown = sorted(set(task_ids) - known_ids)
        if unknown:
            raise ValueError("Неизвестные task_id в факте: " + ", ".join(unknown))
        current_tasks = _task_map(approved)
        sprint_task_ids = {
            str(row.get("task_id"))
            for row in next(
                (
                    sprint.get("tasks") or []
                    for sprint in (approved.get("plan") or {}).get("sprints") or []
                    if int(sprint.get("sprint_number") or 0) == sprint_number
                ),
                [],
            )
            if row.get("task_id")
        }
        outside_sprint = sorted(set(task_ids) - sprint_task_ids)
        if outside_sprint:
            raise ValueError(
                f"В факт Sprint {sprint_number} переданы задачи из других спринтов: "
                + ", ".join(outside_sprint)
            )
        if all_completed and any(not row.get("task_completed") for row in results):
            raise ValueError(
                "При all_completed=true нельзя передавать незавершённые задачи."
            )
        if all_completed:
            due_ids = {
                task_id
                for task_id, task in _task_map(approved).items()
                if not is_historical_done(task)
                and int(task.get("completion_sprint") or 0) == sprint_number
            }
            unexpected = sorted(set(task_ids) - due_ids)
            if unexpected:
                raise ValueError(
                    "При all_completed=true можно передать только задачи, "
                    f"завершающиеся в Sprint {sprint_number}: "
                    + ", ".join(unexpected)
                )
        for row in results:
            if not row.get("task_completed") and not row.get("remaining_role_hours"):
                raise ValueError(
                    f"Для незавершённой задачи {row.get('task_id')} укажите remaining_role_hours."
                )
            if not row.get("task_completed") and not row.get("incomplete_reason"):
                raise ValueError(
                    f"Для незавершённой задачи {row.get('task_id')} укажите причину."
                )
            remaining_sp = row.get("remaining_story_points")
            if not row.get("task_completed") and remaining_sp is not None:
                task = current_tasks.get(str(row.get("task_id"))) or {}
                remaining_sp_value = float(remaining_sp)
                available_sp = float(
                    task.get("remaining_story_points")
                    if task.get("remaining_story_points") is not None
                    else task.get("story_points") or 0.0
                )
                if remaining_sp_value <= 0:
                    raise ValueError(
                        f"Для задачи {row.get('task_id')} укажите положительный остаток SP."
                    )
                if remaining_sp_value > available_sp + 1e-9:
                    raise ValueError(
                        f"Остаток {remaining_sp_value:g} SP для {row.get('task_id')} "
                        f"превышает текущий объём {available_sp:g} SP."
                    )

    def _save_replan_workflow(self, workflow: dict) -> dict:
        saved = deepcopy(workflow)
        saved["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self._repository_provider is not None:
            saved = self._repository_provider().save_replan_workflow(saved)
        self._replan_workflows[saved["workflow_id"]] = deepcopy(saved)
        return deepcopy(saved)

    def replan_workflow(self, workflow_id: str | None = None) -> dict | None:
        with self._lock:
            workflow = self._replan_workflows.get(workflow_id) if workflow_id else None
            if workflow is None and self._repository_provider is not None:
                repository = self._repository_provider()
                workflow = (
                    repository.load_replan_workflow(workflow_id)
                    if workflow_id
                    else repository.latest_replan_workflow()
                )
                if workflow is not None:
                    self._replan_workflows[workflow["workflow_id"]] = deepcopy(workflow)
            if workflow is None and not workflow_id and self._replan_workflows:
                workflow = max(
                    self._replan_workflows.values(),
                    key=lambda item: item.get("updated_at") or item.get("created_at") or "",
                )
            return deepcopy(workflow) if workflow is not None else None

    def _build_replan_workflow(self, workflow: dict, approved: dict) -> dict:
        try:
            candidate = build_remaining_horizon_plan(
                approved,
                int(workflow["sprint_number"]),
                deepcopy(workflow["actual"]["results"]),
            )
            candidate.setdefault("replanning", {}).update({
                "workflow_id": workflow["workflow_id"],
                "review_status": "DRAFT",
            })
            actual_rows = [
                row for row in (candidate.get("_planning_state") or {}).get("actual_results", [])
                if int(row.get("sprint_number") or 0) == int(workflow["sprint_number"])
            ]
            completed_ids = sorted({
                str(row.get("task_id")) for row in actual_rows
                if row.get("task_id") and row.get("task_completed")
            })
            unfinished_ids = sorted({
                str(row.get("task_id")) for row in actual_rows
                if row.get("task_id") and not row.get("task_completed")
            })
            candidate["revision_context"] = {
                "previous_approved_revision": (
                    approved.get("approved_snapshot") or {}
                ).get("revision"),
                "closed_sprint": int(workflow["sprint_number"]),
                "actual_result_sprint": int(workflow["sprint_number"]),
                "trigger": "REPLAN_DRAFT",
            }
            workflow.update({
                "status": "READY_FOR_REVIEW",
                "replanning_required": True,
                "draft_plan": candidate,
                "diff": deepcopy((candidate.get("replanning") or {}).get("diff") or {}),
                "kind": (
                    "QUARTER_CLOSE_REVIEW"
                    if int(workflow["sprint_number"]) == 6
                    else "REPLAN_REVIEW"
                ),
                "sprint_result": {
                    "completed_task_ids": completed_ids,
                    "unfinished_task_ids": unfinished_ids,
                    "completed_count": len(completed_ids),
                    "unfinished_count": len(unfinished_ids),
                    "task_results": actual_rows,
                    "kpi": sprint_kpi(candidate, int(workflow["sprint_number"])),
                },
                "error": None,
            })
        except Exception as error:
            workflow.update({
                "status": "REPLANNING_FAILED",
                "replanning_required": True,
                "draft_plan": None,
                "diff": None,
                "error": str(error),
            })
        return self._save_replan_workflow(workflow)

    def _close_without_replanning(
        self,
        approved: dict,
        sprint_number: int,
        results: list[dict],
        workflow: dict,
    ) -> dict:
        """Close a successful sprint while preserving the approved future plan."""

        candidate = deepcopy(approved)
        state = deepcopy(candidate.get("_planning_state") or {})
        tasks = _task_map(approved)
        due_ids = {
            task_id
            for task_id, task in tasks.items()
            if not is_historical_done(task)
            and int(task.get("completion_sprint") or 0) == sprint_number
        }
        supplied = {str(row.get("task_id") or ""): row for row in results}

        actual_by_task = {
            task_id: {
                **deepcopy(supplied.get(task_id) or {}),
                "task_id": task_id,
                "task_completed": True,
                "starting_story_points": float(
                    tasks[task_id].get("remaining_story_points")
                    if tasks[task_id].get("remaining_story_points") is not None
                    else tasks[task_id].get("story_points") or 0.0
                ),
                "completed_story_points": float(
                    tasks[task_id].get("remaining_story_points")
                    if tasks[task_id].get("remaining_story_points") is not None
                    else tasks[task_id].get("story_points") or 0.0
                ),
                "remaining_story_points": 0.0,
                "remaining_role_hours": {},
                "remaining_engineer_allocations": {},
                "unavailable_next_sprint": False,
                "unavailable_engineer_ids": [],
                "auto_completed": task_id not in supplied,
            }
            for task_id in sorted(due_ids)
        }
        completed = {
            **{
                str(task_id): int(completed_sprint)
                for task_id, completed_sprint
                in (state.get("actual_completed_tasks") or {}).items()
            },
            **{task_id: sprint_number for task_id in due_ids},
        }

        tables = deepcopy(state.get("tables") or {})
        for task in tables.get("tasks", []):
            if str(task.get("task_id")) in due_ids:
                task["status"] = "Done"
        state["tables"] = tables
        state["completed_through_sprint"] = sprint_number
        state["actual_completed_tasks"] = completed
        state["actual_results"] = [
            *(state.get("actual_results") or []),
            *[
                {**deepcopy(actual_by_task[task_id]), "sprint_number": sprint_number}
                for task_id in sorted(actual_by_task)
            ],
        ]
        closed_history = [
            deepcopy(row)
            for row in state.get("closed_sprint_history") or []
            if int(row.get("sprint_number") or 0) != sprint_number
        ]
        closed_history.append(
            capture_closed_sprint(approved, sprint_number, actual_by_task)
        )
        closed_history.sort(key=lambda row: int(row.get("sprint_number") or 0))
        state["closed_sprint_history"] = closed_history
        if sprint_number == 6:
            state["quarter_status"] = "COMPLETED"
        candidate["_planning_state"] = state

        for task in candidate.get("tasks", []):
            if str(task.get("task_id")) in due_ids:
                task["status"] = "DONE"
                task["status_label"] = "Завершена"

        quarter_completed = sprint_number == 6
        current_sprint = 6 if quarter_completed else sprint_number + 1
        quarter = candidate.setdefault("plan", {}).setdefault("quarter", {})
        quarter["current_sprint"] = current_sprint
        quarter["current_sprint_source"] = "sprint_close_actual"
        candidate["replanning"] = {
            **deepcopy(candidate.get("replanning") or {}),
            "completed_through_sprint": sprint_number,
            "frozen_sprints": list(range(1, sprint_number + 1)),
            "remaining_sprints": list(range(sprint_number + 1, 7)),
            "quarter_status": "COMPLETED" if quarter_completed else "IN_PROGRESS",
            "replanning_required": False,
        }
        candidate["revision_context"] = {
            "previous_approved_revision": (
                approved.get("approved_snapshot") or {}
            ).get("revision"),
            "closed_sprint": sprint_number,
            "actual_result_sprint": sprint_number,
            "trigger": "SPRINT_CLOSE_NO_REPLAN",
            "workflow_id": workflow["workflow_id"],
        }
        candidate = project_management_kpis(candidate)
        workflow.update({
            "status": "READY_FOR_REVIEW",
            "kind": (
                "QUARTER_CLOSE_REVIEW"
                if quarter_completed
                else "SPRINT_CLOSE_REVIEW"
            ),
            "replanning_required": False,
            "review_required": True,
            "current_sprint": current_sprint,
            "quarter_completed": quarter_completed,
            "sprint_result": {
                "completed_task_ids": sorted(due_ids),
                "unfinished_task_ids": [],
                "completed_count": len(due_ids),
                "unfinished_count": 0,
                "task_results": [deepcopy(actual_by_task[key]) for key in sorted(actual_by_task)],
                "kpi": sprint_kpi(candidate, sprint_number),
            },
            "draft_plan": candidate,
            "diff": {
                "closed_sprint": sprint_number,
                "changed_count": 0,
                "changed_tasks": [],
                "affected_elements": [],
                "affected_team_ids": [],
                "affected_engineer_ids": [],
            },
            "error": None,
        })
        return self._save_replan_workflow(workflow)

    def close_sprint(
        self,
        sprint_number: int,
        results: list[dict],
        all_completed: bool = False,
    ) -> dict:
        """Close a successful sprint or create a review draft for deviations."""

        with self._lock:
            approved = self.approved()
            if approved is None:
                raise RuntimeError("Сначала утвердите квартальный план.")
            self._validate_sprint_actual(
                approved, sprint_number, results, all_completed=all_completed
            )
            base_token = self._approved_token(approved)
            actual_fingerprint = self._actual_fingerprint(
                sprint_number, results, all_completed=all_completed
            )
            latest = self.replan_workflow()
            if (
                latest
                and latest.get("base_approved_token") == base_token
                and int(latest.get("sprint_number") or 0) == sprint_number
                and latest.get("status") in {
                    "ACTUAL_SAVED", "READY_FOR_REVIEW", "REPLANNING_FAILED"
                }
            ):
                if latest.get("actual_fingerprint") == actual_fingerprint:
                    if latest.get("status") == "ACTUAL_SAVED":
                        if all_completed:
                            return self._close_without_replanning(
                                approved, sprint_number, results, latest
                            )
                        return self._build_replan_workflow(latest, approved)
                    return latest

                # Until explicit approval, a saved fact remains editable.  A
                # corrected fact replaces only its review draft; Approved Plan
                # remains untouched.
                latest.update({
                    "status": "ACTUAL_SAVED",
                    "actual_fingerprint": actual_fingerprint,
                    "actual": {
                        "sprint_number": sprint_number,
                        "all_completed": all_completed,
                        "results": deepcopy(results),
                    },
                    "actual_persisted": True,
                    "replanning_required": not all_completed,
                    "review_required": True,
                    "draft_plan": None,
                    "diff": None,
                    "error": None,
                })
                for key in (
                    "kind", "sprint_result", "current_sprint",
                    "quarter_completed", "approved_revision", "approved_plan",
                ):
                    latest.pop(key, None)
                latest = self._save_replan_workflow(latest)
                if all_completed:
                    return self._close_without_replanning(
                        approved, sprint_number, results, latest
                    )
                return self._build_replan_workflow(latest, approved)
            now = datetime.now(timezone.utc).isoformat()
            workflow = {
                "workflow_id": uuid4().hex,
                "status": "ACTUAL_SAVED",
                "sprint_number": sprint_number,
                "base_approved_token": base_token,
                "base_approved_revision": (
                    approved.get("approved_snapshot") or {}
                ).get("revision"),
                "actual_fingerprint": actual_fingerprint,
                "actual": {
                    "sprint_number": sprint_number,
                    "all_completed": all_completed,
                    "results": deepcopy(results),
                },
                "actual_persisted": True,
                "replanning_required": not all_completed,
                "review_required": True,
                "draft_plan": None,
                "diff": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            workflow = self._save_replan_workflow(workflow)
            if all_completed:
                return self._close_without_replanning(
                    approved, sprint_number, results, workflow
                )
            return self._build_replan_workflow(workflow, approved)

    def retry_replanning(self, workflow_id: str) -> dict:
        approved = self.approved()
        if approved is None:
            raise RuntimeError("Утверждённый план не найден.")
        with self._lock:
            workflow = self.replan_workflow(workflow_id)
            if workflow is None:
                raise KeyError(workflow_id)
            if workflow.get("status") == "APPROVED":
                return workflow
            if workflow.get("base_approved_token") != self._approved_token(approved):
                raise RuntimeError("Черновик устарел: утверждённый план уже изменился.")
            workflow["status"] = "ACTUAL_SAVED"
            workflow["error"] = None
            workflow = self._save_replan_workflow(workflow)
            if workflow.get("actual", {}).get("all_completed"):
                return self._close_without_replanning(
                    approved,
                    int(workflow["sprint_number"]),
                    deepcopy(workflow["actual"].get("results") or []),
                    workflow,
                )
            return self._build_replan_workflow(workflow, approved)

    def cancel_replanning(self, workflow_id: str) -> dict:
        """Discard an unapproved sprint fact without changing Approved Plan."""

        approved = self.approved()
        if approved is None:
            raise RuntimeError("Утверждённый план не найден.")
        with self._lock:
            workflow = self.replan_workflow(workflow_id)
            if workflow is None:
                raise KeyError(workflow_id)
            if workflow.get("status") == "CANCELLED":
                return workflow
            if workflow.get("status") not in {
                "ACTUAL_SAVED", "READY_FOR_REVIEW", "REPLANNING_FAILED"
            }:
                raise RuntimeError("Можно отменить только неподтверждённый факт спринта.")
            if workflow.get("base_approved_token") != self._approved_token(approved):
                raise RuntimeError("Черновик устарел: утверждённый план уже изменился.")
            workflow.update({
                "status": "CANCELLED",
                "draft_plan": None,
                "diff": None,
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            })
            return self._save_replan_workflow(workflow)

    def approve_replanning(self, workflow_id: str) -> dict:
        approved = self.approved()
        if approved is None:
            raise RuntimeError("Утверждённый план не найден.")
        with self._lock:
            workflow = self.replan_workflow(workflow_id)
            if workflow is None:
                raise KeyError(workflow_id)
            if workflow.get("status") == "APPROVED":
                return deepcopy(workflow.get("approved_plan"))
            if workflow.get("status") != "READY_FOR_REVIEW" or not workflow.get("draft_plan"):
                raise RuntimeError("Черновик пересчёта ещё не готов к утверждению.")
            if workflow.get("base_approved_token") != self._approved_token(approved):
                raise RuntimeError("Черновик устарел: утверждённый план уже изменился.")
            candidate = deepcopy(workflow["draft_plan"])
            candidate.setdefault("replanning", {})["review_status"] = "APPROVED"
            candidate["revision_context"] = {
                "previous_approved_revision": (
                    approved.get("approved_snapshot") or {}
                ).get("revision"),
                "closed_sprint": int(workflow["sprint_number"]),
                "actual_result_sprint": int(workflow["sprint_number"]),
                "trigger": (
                    "REPLAN_APPROVE"
                    if workflow.get("replanning_required")
                    else "SPRINT_CLOSE_APPROVE"
                ),
                "workflow_id": workflow_id,
            }
            saved = self.replace_approved(candidate)
            workflow.update({
                "status": "APPROVED",
                "approved_plan": saved,
                "approved_revision": (saved.get("approved_snapshot") or {}).get("revision"),
                "approved_at": datetime.now(timezone.utc).isoformat(),
            })
            self._save_replan_workflow(workflow)
            return saved

    def replace_approved(self, payload: dict) -> dict:
        with self._lock:
            candidate = deepcopy(payload)
            if self._approved and "_planning_state" not in candidate:
                candidate["_planning_state"] = deepcopy(
                    self._approved.get("_planning_state", {})
                )
            previous_revision = None
            if self._approved:
                previous_revision = (
                    self._approved.get("approved_snapshot") or {}
                ).get("revision")
            state = candidate.get("_planning_state") or {}
            existing_context = deepcopy(candidate.get("revision_context") or {})
            candidate["revision_context"] = {
                "previous_approved_revision": previous_revision,
                "closed_sprint": int(state.get("completed_through_sprint") or 0),
                "actual_result_sprint": int(state.get("completed_through_sprint") or 0),
                "trigger": "CLOSE_SPRINT" if state.get("completed_through_sprint") else "REPLACE_APPROVED",
                **existing_context,
            }
            self._approved = self._persist(
                _with_official_week0_kpi_denominator(candidate)
            )
            return deepcopy(self._approved)


plan_drafts = PlanDraftStore(repository_provider=lambda: get_catalog_repository())


class PreparedUploadStore:
    """Keep the already parsed snapshot while the user reviews warnings."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._uploads: dict[str, PreparedConstructorUpload] = {}

    def create(self, upload: PreparedConstructorUpload) -> str:
        upload_id = uuid4().hex
        with self._lock:
            self._uploads = {upload_id: upload}
        return upload_id

    def take(self, upload_id: str) -> PreparedConstructorUpload:
        with self._lock:
            upload = self._uploads.pop(upload_id, None)
        if upload is None:
            raise KeyError(upload_id)
        return upload

    def get(self, upload_id: str) -> PreparedConstructorUpload:
        with self._lock:
            upload = self._uploads.get(upload_id)
        if upload is None:
            raise KeyError(upload_id)
        return upload

    def discard(self, upload_id: str) -> None:
        with self._lock:
            self._uploads.pop(upload_id, None)

    def reset(self) -> None:
        with self._lock:
            self._uploads = {}


prepared_uploads = PreparedUploadStore()


def _current_plan_without_historical_done(payload: dict) -> dict:
    """Keep historical completions in planning input, not operational DTOs."""

    current = deepcopy(payload)
    source_tasks = current.get("tasks", [])
    done_ids = {
        str(task.get("task_id"))
        for task in source_tasks
        if task.get("task_id") and is_historical_done(task)
    }
    if not done_ids:
        return current

    def active_rows(rows: list[dict]) -> list[dict]:
        return [
            row for row in rows
            if row.get("task_id") not in done_ids and not is_historical_done(row)
        ]

    current["tasks"] = active_rows(source_tasks)
    current["risks"] = [
        row for row in current.get("risks", [])
        if row.get("task_id") not in done_ids
    ]
    for collection in ("initiatives",):
        for initiative in current.get(collection, []):
            if isinstance(initiative.get("task_ids"), list):
                initiative["task_ids"] = [
                    task_id for task_id in initiative["task_ids"]
                    if task_id not in done_ids
                ]

    for collection in ("teams", "engineers"):
        for entity in current.get(collection, []):
            if isinstance(entity.get("allocations"), list):
                entity["allocations"] = active_rows(entity["allocations"])

    plan = current.get("plan")
    if isinstance(plan, dict):
        plan["tasks"] = active_rows(plan.get("tasks", []))
        plan["allocations"] = active_rows(plan.get("allocations", []))
        for sprint in plan.get("sprints", []):
            sprint["tasks"] = active_rows(sprint.get("tasks", []))
        for initiative in plan.get("initiatives", []):
            if isinstance(initiative.get("task_ids"), list):
                initiative["task_ids"] = [
                    task_id for task_id in initiative["task_ids"]
                    if task_id not in done_ids
                ]
        if isinstance(plan.get("summary"), dict):
            plan["summary"]["total_tasks"] = len(current["tasks"])

    if isinstance(current.get("summary"), dict):
        current["summary"]["total_tasks"] = len(current["tasks"])
    return current


def _service():
    try:
        return get_planning_service()
    except ServiceDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _catalog_service() -> PlanningAPIService:
    repository = get_catalog_repository()
    return PlanningAPIService.from_tables(
        repository.active_planning_tables(), plan_version="working-catalog"
    )


def _catalog_snapshot() -> dict:
    repository = get_catalog_repository()
    return build_catalog_snapshot(
        repository.active_planning_tables(),
        archived_task_ids=repository.archived_ids("task"),
        revision=repository.revision(),
    )


def _overlay_catalog_entities(payload: dict, catalog: dict) -> dict:
    """Overlay current master data while preserving approved allocations."""

    approved_engineers = {item["engineer_id"]: item for item in payload.get("engineers", [])}
    engineers = []
    for profile in catalog.get("engineers", []):
        engineer_id = profile["engineer_id"]
        current = deepcopy(approved_engineers.get(engineer_id) or {})
        assignments = list(profile.get("assignments") or [])
        free = profile.get("free_assignment")
        buckets = [
            {
                "team_id": item.get("team_id"),
                "capacity_rate": float(item.get("capacity_rate") or 0),
                "capacity_hours": 80.0 * float(item.get("capacity_rate") or 0),
            }
            for item in assignments
        ]
        if free:
            buckets.append({
                "team_id": None,
                "capacity_rate": float(free.get("capacity_rate") or 0),
                "capacity_hours": 80.0 * float(free.get("capacity_rate") or 0),
            })
        roles = sorted({
            item.get("role") for item in [*assignments, *([free] if free else [])]
            if item.get("role")
        }, key=str.casefold)
        capacity = sum(float(item["capacity_hours"]) for item in buckets)
        used = current.get("used_hours_by_sprint") or {str(sprint): 0.0 for sprint in range(1, 7)}
        engineers.append({
            **current,
            "engineer_id": engineer_id,
            "name": profile.get("name") or engineer_id,
            "roles": roles,
            "grade": ", ".join(sorted({
                str(item.get("grade")) for item in [*assignments, *([free] if free else [])]
                if item.get("grade")
            })) or None,
            "team_ids": sorted({item["team_id"] for item in assignments if item.get("team_id")}),
            "capacity_buckets": buckets,
            "capacity_hours": capacity,
            "used_hours_by_sprint": used,
            "free_hours_by_sprint": {
                str(sprint): max(capacity - float(used.get(str(sprint), 0)), 0.0)
                for sprint in range(1, 7)
            },
            "skills": list(profile.get("skills") or []),
            "allocations": list(current.get("allocations") or []),
        })

    approved_teams = {item["team_id"]: item for item in payload.get("teams", [])}
    assignments_by_engineer = {
        item["engineer_id"]: item.get("assignments") or []
        for item in catalog.get("engineers", [])
    }
    teams = []
    for team in catalog.get("teams", []):
        team_id = team["team_id"]
        current = deepcopy(approved_teams.get(team_id) or {})
        members = [item for item in engineers if team_id in item.get("team_ids", [])]
        catalog_capacity = team.get("capacity_sp")
        current_capacity = current.get("capacity_sp")
        effective_capacity = current_capacity if current_capacity is not None else catalog_capacity
        # Plans created before manual-capacity support may contain a persisted
        # zero for a newly added team. Prefer the catalog bootstrap value in
        # that case so reopening the team does not erase its entered capacity.
        if (
            catalog_capacity is not None
            and float(catalog_capacity or 0) > 0
            and float(effective_capacity or 0) <= 0
        ):
            effective_capacity = catalog_capacity
        empty_sprints = {
            str(sprint): {
                "capacity": float(effective_capacity or 0),
                "used": 0.0,
                "remaining": float(effective_capacity or 0),
            }
            for sprint in range(1, 7)
        }
        current_sprints = current.get("sp_by_sprint") or {}
        has_current_capacity = any(
            float((resource or {}).get("capacity") or 0) > 0
            for resource in current_sprints.values()
        )
        teams.append({
            **current,
            "team_id": team_id,
            "name": team.get("name") or team_id,
            "member_ids": sorted(item["engineer_id"] for item in members),
            "roles": sorted({
                assignment["role"]
                for item in members
                for assignment in assignments_by_engineer.get(item["engineer_id"], [])
                if assignment.get("team_id") == team_id and assignment.get("role")
            }, key=str.casefold),
            "technologies": sorted({skill for item in members for skill in item.get("skills", [])}, key=str.casefold),
            "average_velocity_sp": team.get("expected_velocity_sp"),
            "capacity_sp": effective_capacity,
            "sp_by_sprint": (
                current_sprints
                if current_sprints and (has_current_capacity or catalog_capacity is None)
                else empty_sprints
            ),
            "member_capacity_hours": sum(
                bucket["capacity_hours"]
                for item in members
                for bucket in item.get("capacity_buckets", [])
                if bucket.get("team_id") == team_id
            ),
        })

    approved_tasks = {item["task_id"]: item for item in payload.get("tasks", [])}
    tasks = []
    for item in catalog.get("tasks", []):
        task_id = item["task_id"]
        current = deepcopy(approved_tasks.get(task_id) or {})
        requirements = {
            row["role"]: float(row.get("required_hours") or 0)
            for row in item.get("role_requirements", [])
            if row.get("role")
        }
        if current:
            tasks.append({
                **current,
                "title": item.get("title") or current.get("title"),
                "source_status": item.get("status"),
                "original_team_id": item.get("team_id"),
                "role_requirements": requirements or current.get("role_requirements", {}),
                "required_skills": list(item.get("required_skills") or []),
            })
            continue
        tasks.append({
            "task_id": task_id,
            "title": item.get("title") or task_id,
            "description": item.get("title") or task_id,
            "initiative_id": item.get("initiative_id"),
            "status": "CATALOG",
            "status_label": "Не включена в утверждённый план",
            "source_status": item.get("status"),
            "rung": item.get("rung"),
            "priority_label": f"Rung {item.get('rung', 0):g}",
            "story_points": item.get("story_points"),
            "remaining_story_points": item.get("story_points"),
            "original_team_id": item.get("team_id"),
            "assigned_team_id": None,
            "team_reassigned": False,
            "planned_sprints": [],
            "start_sprint": None,
            "completion_sprint": None,
            "multi_sprint": False,
            "sp_load_by_sprint": {},
            "hours_by_sprint": {},
            "role_hours_by_sprint": {},
            "role_requirements": requirements,
            "required_skills": list(item.get("required_skills") or []),
            "dependencies": {"incoming": [], "outgoing": []},
            "allocations": [],
            "ownership": None,
            "reasons": [],
            "reason_blocks": [],
            "risks": [],
            "recommendations": [],
            "summary": "Сущность каталога; в утверждённый план не включена.",
            "outside_quarter": True,
            "is_placeholder": False,
        })
    payload["engineers"] = engineers
    payload["teams"] = teams
    payload["tasks"] = tasks
    payload["catalog_revision"] = catalog.get("catalog_revision", 0)
    return payload


def _catalog_data_state() -> str:
    tables = get_catalog_repository().tables()
    return "READY" if any(tables.values()) else "UNINITIALIZED"


def _with_lifecycle(payload: dict, entity_type: str, entity_id: str) -> dict:
    return {**payload, **get_catalog_repository().lifecycle(entity_type, entity_id)}


def _empty_current_dashboard() -> dict:
    return {
        "status": "EMPTY",
        "data_state": _catalog_data_state(),
        "message": "Квартальный план ещё не построен",
        "plan": {
            "quarter": {"sprint_count": 6, "current_sprint": 1, "plan_version": None},
            "summary": {"total_tasks": 0},
            "sprints": [
                {"sprint_number": sprint, "status": "PLANNED", "tasks": [], "teams": [], "role_resources": []}
                for sprint in range(1, 7)
            ],
            "tasks": [], "allocations": [], "initiatives": [],
        },
        "summary": {"total_tasks": 0, "plan_version": None},
        "tasks": [], "initiatives": [], "risks": [], "teams": [], "engineers": [],
        "star_map": {"current_sprint": 1, "teams": [], "engineers": [], "skills": [], "roles": []},
        "kpis": {},
        "features": {
            "plan_constructor": "AVAILABLE_EPHEMERAL",
            "replanning": "AVAILABLE",
            "bus_factor": "AVAILABLE",
            "initial_excel_upload": "AVAILABLE_PERSISTENT_CATALOG",
        },
    }


def _current_dashboard() -> dict:
    # The persistent working catalog is the source of the current plan after a
    # restart as well; an approved in-memory draft still takes precedence.
    approved = plan_drafts.approved()
    current = _current_plan_without_historical_done(
        approved or _empty_current_dashboard()
    )
    history = plan_drafts.history() if approved is not None else []
    current = project_lifecycle(current, history)
    current = _apply_canonical_expected_initiatives(current)
    current = project_management_kpis(current)
    current.setdefault("features", {})["replanning"] = "AVAILABLE"
    return current


def _canonical_expected_initiatives(payload: dict) -> dict[str, object]:
    """Build one expected-initiative result for KPI and initiative lists.

    The Week-0 task membership is immutable.  A task is expected to complete
    when it is factually Done or still has a valid plan completion inside the
    six-sprint quarter.  This keeps historical Done tasks and moved/carry-over
    tasks in the same calculation as the current plan.
    """

    state = payload.get("_planning_state") or {}
    baseline_tables = state.get("baseline_tables") or state.get("tables") or {}
    baseline_by_initiative: dict[str, set[str]] = {}
    for task in baseline_tables.get("tasks") or []:
        task_id = str(task.get("task_id") or "").strip()
        initiative_id = str(task.get("initiative_id") or "").strip()
        status = str(task.get("status") or "").strip().casefold()
        if (
            task_id
            and initiative_id
            and status in {"todo", "inprogress"}
            and not bool(task.get("is_placeholder"))
        ):
            baseline_by_initiative.setdefault(initiative_id, set()).add(task_id)

    current_tasks = {
        str(task.get("task_id")): task
        for task in payload.get("tasks") or []
        if task.get("task_id")
    }
    actual_done = {
        str(task_id)
        for task_id in (state.get("actual_completed_tasks") or {})
        if task_id
    }

    def task_is_expected(task_id: str) -> bool:
        if task_id in actual_done:
            return True
        task = current_tasks.get(task_id)
        if not task:
            return False
        current_status = str(task.get("current_status") or "").strip().upper()
        if current_status == "DONE" or is_historical_done(task):
            return True
        status = str(task.get("status") or "").strip().upper()
        completion = task.get("completion_sprint")
        try:
            completion_sprint = int(completion)
        except (TypeError, ValueError):
            return False
        return status in {"PLANNED", "MOVED", "COMPLETED", "IN_PROGRESS"} and (
            completion_sprint <= 6
        )

    completed_ids = {
        initiative_id
        for initiative_id, task_ids in baseline_by_initiative.items()
        if task_ids and all(task_is_expected(task_id) for task_id in task_ids)
    }
    incomplete_ids = set(baseline_by_initiative) - completed_ids
    return {
        "week0_total_initiatives": len(baseline_by_initiative),
        "completed_initiative_ids": tuple(sorted(completed_ids)),
        "incomplete_initiative_ids": tuple(sorted(incomplete_ids)),
    }


def _apply_canonical_expected_initiatives(payload: dict) -> dict:
    """Align expected KPI and both initiative lists to one canonical result."""

    result = _canonical_expected_initiatives(payload)
    completed_ids = set(result["completed_initiative_ids"])
    incomplete_ids = set(result["incomplete_initiative_ids"])
    denominator = int(result["week0_total_initiatives"])
    if denominator == 0:
        return payload
    initiatives_by_id = {
        str(row.get("initiative_id")): row
        for row in payload.get("initiatives") or []
        if row.get("initiative_id")
    }
    baseline_tables = (payload.get("_planning_state") or {}).get("baseline_tables") or {}
    current_tasks = {
        str(task.get("task_id")): task
        for task in payload.get("tasks") or []
        if task.get("task_id")
    }
    actual_done = {
        str(task_id)
        for task_id in ((payload.get("_planning_state") or {}).get("actual_completed_tasks") or {})
        if task_id
    }

    def is_expected_task(task_id: str) -> bool:
        if task_id in actual_done:
            return True
        task = current_tasks.get(task_id)
        if not task:
            return False
        if str(task.get("current_status") or "").upper() == "DONE" or is_historical_done(task):
            return True
        try:
            completion_sprint = int(task.get("completion_sprint"))
        except (TypeError, ValueError):
            return False
        return (
            completion_sprint <= 6
            and str(task.get("status") or "").upper()
            in {"PLANNED", "MOVED", "COMPLETED", "IN_PROGRESS"}
        )

    baseline_tasks_by_initiative: dict[str, list[str]] = {}
    for task in baseline_tables.get("tasks") or []:
        initiative_id = str(task.get("initiative_id") or "").strip()
        task_id = str(task.get("task_id") or "").strip()
        if initiative_id and task_id and initiative_id in set(completed_ids) | incomplete_ids:
            baseline_tasks_by_initiative.setdefault(initiative_id, []).append(task_id)

    normalized_initiatives = []
    for initiative_id in sorted(set(baseline_tasks_by_initiative)):
        row = deepcopy(initiatives_by_id.get(initiative_id) or {
            "initiative_id": initiative_id,
            "selection_reasons": [],
            "summary": None,
            "selected_for_plan": False,
        })
        row["task_ids"] = sorted(baseline_tasks_by_initiative[initiative_id])
        row["task_count"] = len(row["task_ids"])
        row["completed_in_quarter"] = initiative_id in completed_ids
        row["planned_task_count"] = sum(is_expected_task(task_id) for task_id in row["task_ids"])
        normalized_initiatives.append(row)

    payload["initiatives"] = normalized_initiatives
    if isinstance(payload.get("plan"), dict):
        payload["plan"]["initiatives"] = deepcopy(normalized_initiatives)
    kpis = payload.setdefault("kpis", {})
    expected = kpis.setdefault("expected_kpi", {})
    expected.update({
        "completed_initiatives": float(len(completed_ids)),
        "baseline_initiatives": float(denominator),
        "incomplete_initiatives": float(len(incomplete_ids)),
        "completion_percent": (
            len(completed_ids) / denominator * 100 if denominator else None
        ),
    })
    return payload


def _company_star_map_team(engineers: list[dict], teams: list[dict]) -> dict:
    """Build a company view from already calculated active Star Map data."""

    engineer_ids = sorted({str(item.get("engineer_id")) for item in engineers if item.get("engineer_id")})

    def aggregate(kind: str, quarter: bool = False) -> list[dict]:
        field = "roles" if kind == "role" else "technologies"
        label_field = "role" if kind == "role" else "technology"
        holders: dict[str, set[str]] = {}
        if quarter:
            for team in teams:
                mode = (team.get("competency_modes") or {}).get("quarter") or {}
                for item in mode.get(field, []):
                    label = item.get(label_field) or item.get("competency")
                    if label:
                        holders.setdefault(str(label), set()).update(item.get("engineer_ids") or [])
        else:
            source_field = "roles" if kind == "role" else "skills"
            for engineer in engineers:
                engineer_id = engineer.get("engineer_id")
                if not engineer_id:
                    continue
                for label in engineer.get(source_field) or []:
                    if label:
                        holders.setdefault(str(label), set()).add(str(engineer_id))

        result = []
        for label, ids in sorted(holders.items(), key=lambda pair: pair[0].casefold()):
            unique_ids = sorted({str(value) for value in ids if str(value) in engineer_ids})
            if not unique_ids and not quarter:
                continue
            result.append({
                label_field: label,
                **({"competency": label} if kind == "role" else {}),
                "team_bus_factor": len(unique_ids),
                "organization_bus_factor": len(unique_ids),
                "engineer_ids": unique_ids,
                "engineer_count": len(unique_ids),
                "quarter_relevant": quarter,
                "quarter_engineer_ids": unique_ids,
                "helpers": [],
            })
        return result

    def mode(roles: list[dict], technologies: list[dict]) -> dict:
        entries = [*roles, *technologies]
        return {
            "roles": roles,
            "technologies": technologies,
            "entries": entries,
            "zone_counts": {
                "critical": sum(1 for item in entries if item["team_bus_factor"] <= 1),
                "limited": sum(1 for item in entries if item["team_bus_factor"] == 2),
                "stable": sum(1 for item in entries if item["team_bus_factor"] >= 3),
                "total": len(entries),
            },
        }

    all_technologies = aggregate("technology")
    all_roles = aggregate("role")
    quarter_technologies = aggregate("technology", quarter=True)
    quarter_roles = aggregate("role", quarter=True)
    modes = {
        "all": mode(all_roles, all_technologies),
        "quarter": mode(quarter_roles, quarter_technologies),
    }
    technology_counts = modes["all"]["zone_counts"]
    engineer_by_id = {item.get("engineer_id"): item for item in engineers}
    competencies = [
        {
            "skill": item["technology"],
            "bus_factor": item["team_bus_factor"],
            "engineers": [engineer_by_id[value] for value in item["engineer_ids"] if value in engineer_by_id],
            "external_engineers": [],
        }
        for item in all_technologies
    ]
    available = bool(all_technologies or all_roles)
    return {
        "team_id": "__company__",
        "team_name": "Вся компания",
        "scope": "company",
        "member_ids": engineer_ids,
        "member_count": len(engineer_ids),
        "engineer_count": len(engineer_ids),
        "competencies": competencies,
        "competency_count": len(all_technologies),
        "bus_factor_available": available,
        "summary": {
            "single_owner_count": technology_counts["critical"],
            "warning_count": technology_counts["limited"],
            "healthy_count": technology_counts["stable"],
        },
        "status": "AVAILABLE" if available else "INSUFFICIENT_DATA",
        "message": None if available else "Для компании пока нет данных о компетенциях.",
        "competency_modes": modes,
        "graph_competencies": quarter_technologies or all_technologies,
    }


def _active_star_map(
    active_tables: dict[str, list[dict]],
    catalog: dict,
    approved_star: dict | None,
    approved_tasks: list[dict],
) -> dict:
    """Build current Star Map/BF from catalog without running the planner."""

    approved_star = approved_star or {}
    current_sprint = int(approved_star.get("current_sprint") or 1)
    base = StarMapService(
        engineers=active_tables.get("engineers", []),
        engineer_skills=active_tables.get("engineer_skills", []),
        tasks=approved_tasks,
        current_sprint=current_sprint,
    ).payload()
    bus = build_bus_factor_payload(active_tables)
    bus_by_team = {item.get("team_id"): item for item in bus.get("teams", [])}
    profiles = {item["engineer_id"]: item for item in catalog.get("engineers", [])}
    engineer_by_id = {item["engineer_id"]: item for item in base.get("engineers", [])}
    for engineer_id, profile in profiles.items():
        target = engineer_by_id.get(engineer_id)
        if target is None:
            free = profile.get("free_assignment") or {}
            target = {
                "engineer_id": engineer_id,
                "roles": [free.get("role")] if free.get("role") else [],
                "grade": free.get("grade"),
                "team_ids": [],
                "skills": list(profile.get("skills") or []),
                "capacity_buckets": [],
                "capacity_hours": 0.0,
                "used_hours": 0.0,
                "free_hours": 0.0,
                "allocations": [],
                "quarter_allocations": [],
                "reserve_candidates": [],
            }
            base.setdefault("engineers", []).append(target)
            engineer_by_id[engineer_id] = target
        target["name"] = profile.get("name") or engineer_id
        target["skills"] = list(profile.get("skills") or [])
        target["memberships"] = list(profile.get("assignments") or [])

    old_teams = {item.get("team_id"): item for item in approved_star.get("teams", [])}
    base_teams = {item.get("team_id"): item for item in base.get("teams", [])}
    for team_row in catalog.get("teams", []):
        team_id = team_row["team_id"]
        team = base_teams.setdefault(team_id, {
            "team_id": team_id, "member_ids": [], "member_count": 0,
            "capacity_hours": 0.0, "used_hours": 0.0, "free_hours": 0.0,
            "skill_coverage": [], "role_coverage": [], "cross_team_links": [],
        })
        team["team_name"] = team_row.get("name") or team_id
        bus_team = bus_by_team.get(team_id, {})
        technologies = []
        for item in bus_team.get("competencies", []):
            holders = sorted({
                str(holder.get("engineer_id")) for holder in item.get("engineers", [])
                if holder.get("engineer_id")
            })
            technologies.append({
                "technology": item.get("skill"),
                "team_bus_factor": int(item.get("bus_factor") or len(holders)),
                "organization_bus_factor": int(item.get("organization_bus_factor") or len(holders)),
                "engineer_ids": holders,
                "engineer_count": len(holders),
                "quarter_relevant": False,
                "quarter_engineer_ids": [],
                "helpers": [],
            })
        roles: dict[str, set[str]] = {}
        for row in active_tables.get("engineers", []):
            if str(row.get("team_id") or "") != team_id or not row.get("role"):
                continue
            roles.setdefault(str(row["role"]), set()).add(str(row.get("engineer_id")))
        role_entries = [{
            "competency": role,
            "role": role,
            "team_bus_factor": len(ids),
            "organization_bus_factor": len({
                str(row.get("engineer_id")) for row in active_tables.get("engineers", [])
                if str(row.get("role") or "") == role
            }),
            "engineer_ids": sorted(ids),
            "engineer_count": len(ids),
            "quarter_relevant": False,
            "quarter_engineer_ids": [],
            "helpers": [],
        } for role, ids in sorted(roles.items())]
        old_quarter = ((old_teams.get(team_id, {}).get("competency_modes") or {}).get("quarter") or {})
        required_skills = {item.get("technology") for item in old_quarter.get("technologies", [])}
        required_roles = {item.get("role") or item.get("competency") for item in old_quarter.get("roles", [])}
        for item in technologies:
            item["quarter_relevant"] = item.get("technology") in required_skills
            item["quarter_engineer_ids"] = item["engineer_ids"] if item["quarter_relevant"] else []
        for item in role_entries:
            item["quarter_relevant"] = item.get("role") in required_roles
            item["quarter_engineer_ids"] = item["engineer_ids"] if item["quarter_relevant"] else []

        def mode(rows_roles, rows_skills):
            entries = [*rows_roles, *rows_skills]
            return {"roles": rows_roles, "technologies": rows_skills, "entries": entries, "zone_counts": {
                "critical": sum(1 for item in entries if int(item.get("team_bus_factor") or 0) <= 1),
                "limited": sum(1 for item in entries if int(item.get("team_bus_factor") or 0) == 2),
                "stable": sum(1 for item in entries if int(item.get("team_bus_factor") or 0) >= 3),
                "total": len(entries),
            }}
        modes = {
            "all": mode(role_entries, technologies),
            "quarter": mode(
                [item for item in role_entries if item["quarter_relevant"]],
                [item for item in technologies if item["quarter_relevant"]],
            ),
        }
        team["competencies"] = bus_team.get("competencies", [])
        team["competency_count"] = len(modes["all"]["entries"])
        team["bus_factor_available"] = bool(modes["all"]["entries"])
        team["summary"] = bus_team.get("summary", {})
        team["status"] = bus_team.get("status", "INSUFFICIENT_DATA")
        team["message"] = bus_team.get("message")
        team["competency_modes"] = modes
        team["graph_competencies"] = modes["quarter"]["technologies"] or technologies
    base["teams"] = [base_teams[key] for key in sorted(base_teams)]
    base["engineers"] = sorted(base.get("engineers", []), key=lambda item: item.get("engineer_id", ""))
    base["bus_factor"] = {"available": bus.get("status") == "AVAILABLE", "method": bus.get("method")}
    base["status"] = bus.get("status", "INSUFFICIENT_DATA")
    base["method"] = bus.get("method", "existing_bus_factor_service")
    base["bus_factor_status"] = base["status"]
    base["company"] = _company_star_map_team(base["engineers"], base["teams"])
    return base


def _dashboard_with_lifecycle() -> dict:
    payload = _current_dashboard()
    approved_tasks = payload.get("tasks", []) if plan_drafts.approved() is not None else []
    repository = get_catalog_repository()
    catalog = _catalog_snapshot()
    payload = _overlay_catalog_entities(payload, catalog)
    for collection, entity_type, id_field in (
        ("tasks", "task", "task_id"),
        ("teams", "team", "team_id"),
        ("engineers", "engineer", "engineer_id"),
    ):
        payload[collection] = [
            {**item, **repository.lifecycle(entity_type, item[id_field])}
            for item in payload.get(collection, [])
        ]
    if isinstance(payload.get("plan"), dict):
        lifecycle_by_task = {
            item["task_id"]: repository.lifecycle("task", item["task_id"])
            for item in payload["plan"].get("tasks", [])
        }
        payload["plan"]["tasks"] = [
            {**item, **lifecycle_by_task[item["task_id"]]}
            for item in payload["plan"].get("tasks", [])
        ]
    # Keep the immutable current plan untouched while making the Star Map use
    # the active catalog for current Bus Factor membership.
    if isinstance(payload.get("star_map"), dict):
        payload["star_map"] = _active_star_map(
            repository.active_planning_tables(), catalog, payload.get("star_map"), approved_tasks
        )
        has_approved_plan = plan_drafts.approved() is not None
        payload["star_map"]["quarter_demand_status"] = (
            "AVAILABLE" if has_approved_plan else "NO_APPROVED_PLAN"
        )
        if not has_approved_plan:
            for team in payload["star_map"].get("teams", []):
                team["competency_modes"]["quarter"] = {
                    "roles": [],
                    "technologies": [],
                    "entries": [],
                    "zone_counts": {"critical": 0, "limited": 0, "stable": 0, "total": 0},
                }
                team["quarter_demand_status"] = "NO_APPROVED_PLAN"
                team["quarter_demand_message"] = "Утверждённый квартальный план ещё не построен."
            payload["star_map"]["company"] = _company_star_map_team(
                payload["star_map"].get("engineers", []),
                payload["star_map"]["teams"],
            )
    return payload


@app.exception_handler(ServiceDataError)
async def service_data_error_handler(_, error: ServiceDataError):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "data_state": _catalog_data_state()}


@app.post("/api/admin/reset-data")
def reset_all_data() -> dict:
    """Clear the working catalog, plan history and transient server drafts."""

    deleted = get_catalog_repository().reset_all_data()
    plan_drafts.reset()
    prepared_uploads.reset()
    return {
        "status": "reset",
        "deleted": deleted,
        "message": "Все данные сайта удалены.",
    }


@app.get("/api/dashboard")
def dashboard() -> dict:
    return _dashboard_with_lifecycle()


@app.get("/api/plan")
def plan() -> dict:
    return _current_dashboard()["plan"]


@app.get("/api/current-sprint")
def current_sprint() -> dict:
    """Authoritative current sprint view used by the operational page."""

    dashboard = _current_dashboard()
    quarter = dashboard.get("plan", {}).get("quarter", {})
    sprint_number = int(quarter.get("current_sprint") or 1)
    sprint = next(
        (item for item in dashboard.get("plan", {}).get("sprints", [])
         if int(item.get("sprint_number") or 0) == sprint_number),
        {"sprint_number": sprint_number, "tasks": [], "teams": [], "role_resources": []},
    )
    task_ids = {str(item.get("task_id")) for item in sprint.get("tasks", [])}
    tasks = [item for item in dashboard.get("tasks", []) if item.get("task_id") in task_ids]
    return {
        "sprint_number": sprint_number,
        "quarter_status": (dashboard.get("replanning") or {}).get("quarter_status")
            or (dashboard.get("_planning_state") or {}).get("quarter_status")
            or "IN_PROGRESS",
        "sprint": sprint,
        "tasks": tasks,
        "actual_results": [
            row for row in (dashboard.get("_planning_state") or {}).get("actual_results", [])
            if int(row.get("sprint_number") or 0) == sprint_number
        ],
        "can_close": not bool((dashboard.get("_planning_state") or {}).get("quarter_status") == "COMPLETED"),
    }


@app.get("/api/summary")
def summary() -> dict:
    return _current_dashboard()["summary"]


@app.get("/api/kpis")
def kpis() -> dict:
    return _current_dashboard()["kpis"]


@app.post("/api/replanning/close-sprint")
def close_sprint(request: SprintActualRequest) -> dict:
    try:
        return plan_drafts.close_sprint(
            request.sprint_number,
            [row.model_dump() for row in request.results],
            all_completed=request.all_completed,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/replanning/draft")
def current_replan_draft() -> dict:
    workflow = plan_drafts.replan_workflow()
    if not workflow or workflow.get("status") not in {
        "ACTUAL_SAVED", "READY_FOR_REVIEW", "REPLANNING_FAILED"
    }:
        empty_state = (
            {"data_state": "UNINITIALIZED"}
            if _catalog_data_state() == "UNINITIALIZED"
            and plan_drafts.approved() is None
            else {}
        )
        return {"status": "NONE", **empty_state}
    return workflow


@app.post("/api/replanning/drafts/{workflow_id}/retry")
def retry_replan_draft(workflow_id: str) -> dict:
    try:
        return plan_drafts.retry_replanning(workflow_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Черновик пересчёта не найден.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/replanning/drafts/{workflow_id}/approve")
def approve_replan_draft(workflow_id: str) -> dict:
    try:
        return plan_drafts.approve_replanning(workflow_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Черновик пересчёта не найден.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/replanning/drafts/{workflow_id}/cancel")
def cancel_replan_draft(workflow_id: str) -> dict:
    try:
        return plan_drafts.cancel_replanning(workflow_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Черновик пересчёта не найден.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/tasks")
def tasks() -> list[dict]:
    return _dashboard_with_lifecycle()["tasks"]


def _catalog_mutation_error(error: Exception) -> HTTPException:
    if isinstance(error, CatalogNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=409, detail=str(error))


@app.post("/api/tasks", status_code=201)
def create_task(request: CatalogTaskCreateRequest) -> dict:
    repository = get_catalog_repository()
    try:
        explicit_skills = [dict(item) for item in request.required_skills]
        for requirement in request.role_requirements:
            explicit_skills.extend({
                "technology": skill,
                "role": requirement.get("role"),
                "source": "explicit",
            } for skill in requirement.get("skills", []) if skill)
        required_skills = merge_task_skill_requirements(
            task_id=request.task_id,
            title=request.title,
            explicit=[item for item in explicit_skills if not item.get("rejected")],
            roles=[str(item.get("role")) for item in request.role_requirements if item.get("role")],
            rejected=[str(item.get("technology")) for item in explicit_skills if item.get("rejected")],
        )
        repository.create_task(
            task={
                "task_id": request.task_id,
                "summary": request.title,
                "initiative_id": request.initiative_id,
                "team_id": request.team_id,
                "status": request.status,
                "rung": request.rung,
                "story_points": request.story_points,
                "planned_start": request.planned_start,
                "deadline": request.deadline,
                "is_placeholder": False,
            },
            role_requirements=request.role_requirements,
            skill_requirements=required_skills,
            depends_on=request.depends_on,
        )
    except (CatalogConflictError, CatalogNotFoundError, ValueError) as error:
        raise _catalog_mutation_error(error) from error
    return next(item for item in _catalog_snapshot()["tasks"] if item["task_id"] == request.task_id)


@app.get("/api/tasks/{task_id}")
def task(task_id: str) -> dict:
    payload = next(
        (item for item in _dashboard_with_lifecycle()["tasks"] if item["task_id"] == task_id),
        None,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown task_id: {task_id}")
    return _with_lifecycle(payload, "task", task_id)


@app.get("/api/tasks/{task_id}/history")
def task_history(task_id: str) -> dict:
    """Return original/current/history projection, including completed tasks."""

    approved = plan_drafts.approved()
    if approved is None:
        raise HTTPException(status_code=404, detail=f"Unknown task_id: {task_id}")
    repository = get_catalog_repository()
    try:
        snapshots = plan_drafts.history()
    except Exception:
        snapshots = []
    if not snapshots:
        snapshots = [approved]
    snapshots = sorted(snapshots, key=lambda item: int((item.get("approved_snapshot") or {}).get("revision") or 0))
    first = _task_map(snapshots[0]) if snapshots else {}
    latest = _task_map(approved)
    original = first.get(task_id) or latest.get(task_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"Unknown task_id: {task_id}")
    state = approved.get("_planning_state") or {}
    current = latest.get(task_id)
    projected = next(
        (
            row for row in _current_dashboard().get("tasks", [])
            if str(row.get("task_id")) == task_id
        ),
        None,
    )
    current_hours = {}
    for row in (state.get("tables") or {}).get("task_role_effort", []):
        if str(row.get("task_id")) == task_id:
            role = str(row.get("role") or "")
            if role:
                current_hours[role] = current_hours.get(role, 0.0) + float(row.get("required_hours") or 0.0)
    original_hours = {
        str(role): float(value or 0.0)
        for role, value in (original.get("role_requirements") or {}).items()
    }
    baseline_meta = (state.get("original_task_baseline") or {}).get(task_id) or {}
    if not original_hours:
        original_hours = {
            str(role): float(value or 0.0)
            for role, value in (baseline_meta.get("original_role_requirements") or {}).items()
        }
    original_total = float(
        baseline_meta.get("original_total_required_hours")
        or sum(original_hours.values())
    )
    original_sp = float(
        baseline_meta.get("original_story_points")
        if baseline_meta.get("original_story_points") is not None
        else (original.get("story_points") or 0.0)
    )
    remaining_sp = (state.get("remaining_story_points") or {}).get(task_id)
    if remaining_sp is None:
        remaining_sp = original_sp * sum(current_hours.values()) / original_total if original_total else original_sp
    history = []
    for snapshot in snapshots:
        item = _task_map(snapshot).get(task_id)
        actual_rows = [
            row for row in (snapshot.get("_planning_state") or {}).get("actual_results", [])
            if str(row.get("task_id")) == task_id
        ]
        if item is None and not actual_rows:
            continue
        history.append({
            "revision": (snapshot.get("approved_snapshot") or {}).get("revision"),
            "revision_context": snapshot.get("revision_context"),
            "planned_sprints": (item or {}).get("planned_sprints") or [],
            "assigned_team_id": (item or {}).get("assigned_team_id"),
            "allocations": (item or {}).get("allocations") or [],
            "actual_results": actual_rows,
        })
    completed_sprint = (state.get("actual_completed_tasks") or {}).get(task_id)
    return _with_lifecycle({
        "task_id": task_id,
        "original": {
            "story_points": original_sp,
            "role_requirements": original_hours,
            "total_required_hours": original_total,
            "owner_team_id": original.get("assigned_team_id") or original.get("original_team_id"),
            "initial_sprint": min(original.get("planned_sprints") or [], default=None),
        },
        "current": {
            "present_in_current_plan": current is not None,
            "story_points": original_sp,
            "remaining_story_points": float(remaining_sp),
            "remaining_role_hours": current_hours,
            "planned_sprints": (current or {}).get("planned_sprints") or [],
            "current_sprint_placement": (current or {}).get("planned_sprints") or [],
            "status": (current or {}).get("status"),
            "current_status": (projected or {}).get("current_status"),
            "current_status_label": (projected or {}).get("current_status_label"),
        },
        "completed": completed_sprint is not None,
        "completion_sprint": completed_sprint,
        "history": history,
    }, "task", task_id)


@app.get("/api/initiatives")
def initiatives() -> list[dict]:
    return _current_dashboard()["initiatives"]


@app.get("/api/risks")
def risks() -> list[dict]:
    return _current_dashboard()["risks"]


@app.get("/api/development/recommendations")
def development_recommendations() -> dict:
    """Lazily calculate read-only recommendations for the approved plan."""

    approved = plan_drafts.approved()
    if approved is None:
        return {
            "status": "NO_APPROVED_PLAN",
            "source": None,
            "scope": "FULL_QUARTER",
            "message": "Сначала утвердите квартальный план.",
            "summary": {"total": 0, "plan_critical": 0, "team_weakness": 0, "roles": 0, "skills": 0},
            "filters": {"teams": [], "roles": [], "skills": [], "categories": []},
            "recommendations": [],
            "read_only": True,
        }
    dashboard = _dashboard_with_lifecycle()
    tables = get_catalog_repository().active_planning_tables()
    return DevelopmentRecommendationService(tables, dashboard).calculate()


@app.get("/api/teams")
def teams() -> list[dict]:
    return _dashboard_with_lifecycle()["teams"]


@app.post("/api/teams", status_code=201)
def create_team(request: CatalogTeamCreateRequest) -> dict:
    repository = get_catalog_repository()
    try:
        repository.create_team(
            team_id=request.team_id,
            name=request.name,
            engineer_ids=request.engineer_ids,
            capacity_sp=request.capacity_sp,
        )
    except (CatalogConflictError, CatalogNotFoundError, ValueError) as error:
        raise _catalog_mutation_error(error) from error
    return next(item for item in _catalog_snapshot()["teams"] if item["team_id"] == request.team_id)


@app.get("/api/teams/{team_id}")
def team(team_id: str) -> dict:
    dashboard = _dashboard_with_lifecycle()
    payload = build_team_projection(
        dashboard, plan_drafts.history(), team_id
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown team_id: {team_id}")
    return _with_lifecycle(payload, "team", team_id)


@app.get("/api/teams/{team_id}/history")
def team_history(team_id: str) -> dict:
    if build_team_projection(_dashboard_with_lifecycle(), plan_drafts.history(), team_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown team_id: {team_id}")
    return build_team_history(plan_drafts.history(), team_id)


@app.get("/api/engineers")
def engineers() -> list[dict]:
    return _dashboard_with_lifecycle()["engineers"]


@app.post("/api/engineers", status_code=201)
def create_engineer(request: CatalogEngineerCreateRequest) -> dict:
    repository = get_catalog_repository()
    try:
        repository.create_engineer(
            engineer_id=request.engineer_id,
            name=request.name,
            role=request.role,
            grade=request.grade,
            capacity_rate=request.capacity_rate,
            team_id=request.team_id,
            skills=request.skills,
        )
    except (CatalogConflictError, CatalogNotFoundError, ValueError) as error:
        raise _catalog_mutation_error(error) from error
    return next(item for item in _catalog_snapshot()["engineers"] if item["engineer_id"] == request.engineer_id)


@app.post("/api/engineers/{engineer_id}/memberships", status_code=201)
def add_engineer_membership(engineer_id: str, request: CatalogMembershipRequest) -> dict:
    repository = get_catalog_repository()
    try:
        repository.add_engineer_membership(
            engineer_id=engineer_id,
            team_id=request.team_id,
            capacity_rate=request.capacity_rate,
            role=request.role,
            grade=request.grade,
        )
    except (CatalogConflictError, CatalogNotFoundError, ValueError) as error:
        raise _catalog_mutation_error(error) from error
    return next(item for item in _catalog_snapshot()["engineers"] if item["engineer_id"] == engineer_id)


@app.get("/api/engineers/{engineer_id}")
def engineer(engineer_id: str) -> dict:
    dashboard = _dashboard_with_lifecycle()
    payload = build_engineer_projection(
        dashboard, plan_drafts.history(), engineer_id
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown engineer_id: {engineer_id}")
    return _with_lifecycle(payload, "engineer", engineer_id)


@app.get("/api/engineers/{engineer_id}/history")
def engineer_history(engineer_id: str) -> dict:
    if build_engineer_projection(_dashboard_with_lifecycle(), plan_drafts.history(), engineer_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown engineer_id: {engineer_id}")
    return build_engineer_history(plan_drafts.history(), engineer_id)


@app.get("/api/engineer-workspace/{engineer_id}")
def engineer_workspace(engineer_id: str) -> dict:
    """Personal projection of the approved plan; drafts are never a fallback."""

    catalog_engineer = next((
        item for item in _overlay_catalog_entities({"engineers": [], "teams": [], "tasks": []}, _catalog_snapshot())["engineers"]
        if item.get("engineer_id") == engineer_id
    ), None)
    if catalog_engineer is None:
        raise HTTPException(status_code=404, detail=f"Unknown active engineer_id: {engineer_id}")
    return build_engineer_workspace(
        plan_drafts.approved(), engineer_id, catalog_engineer=catalog_engineer
    )


@app.get("/api/star-map")
def star_map() -> dict:
    return _dashboard_with_lifecycle()["star_map"]


@app.get("/api/plan-constructor/catalog")
def plan_constructor_catalog() -> dict:
    """Return active entities from the persistent working catalog."""

    repository = get_catalog_repository()
    return _catalog_snapshot()


@app.post("/api/skills/extract")
def extract_task_skills(request: SkillExtractionRequest) -> dict:
    """Return deterministic backend recognition for constructor live preview."""

    requirements = merge_task_skill_requirements(
        task_id=request.task_id or "preview",
        title=request.title,
        explicit=request.explicit,
        roles=request.roles,
        rejected=request.rejected,
    )
    registry = get_skill_registry()
    return {
        "requirements": requirements,
        "registry_version": "1.0",
        "options": registry.options(),
    }


@app.post("/api/plan-constructor/plan/upload")
def plan_constructor_plan_upload(
    filename: str,
    content: bytes = Body(media_type="application/octet-stream"),
) -> dict:
    """Audit an Excel upload before any catalog merge or planner execution."""

    try:
        prepared = prepare_constructor_workbook(
            filename, content, get_catalog_repository()
        )
    except ConstructorDatasetUploadError as error:
        if Path(filename).suffix.lower() not in {".xlsx", ".xlsm"}:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Не удалось использовать загруженный датасет.",
                    "errors": [str(error)],
                },
            ) from error
        return {
            "data_preflight": {
                "status": "blocked",
                "upload_id": None,
                "file": {"name": Path(filename).name, "size_bytes": len(content)},
                "audit": structural_preflight(
                    "Проверьте, что файл не повреждён и содержит все обязательные таблицы."
                ),
            }
        }

    preflight = {
        "status": "blocked" if prepared.audit["has_critical_errors"] else "review_required",
        "upload_id": None,
        "file": {"name": prepared.filename, "size_bytes": prepared.size_bytes},
        "audit": prepared.audit,
    }
    if prepared.audit["has_critical_errors"]:
        return {"data_preflight": preflight}
    if prepared.audit["show_dialog"]:
        preflight["upload_id"] = prepared_uploads.create(prepared)
        return {"data_preflight": preflight}

    result = plan_drafts.create(
        build_constructor_plan_from_prepared(prepared, get_catalog_repository())
    )
    return {
        **result,
        "data_preflight": {**preflight, "status": "clear"},
    }


@app.post("/api/plan-constructor/plan/upload/{upload_id}/continue")
def continue_constructor_plan_upload(
    upload_id: str,
    request: ConstructorUploadDecisionRequest | None = Body(default=None),
) -> dict:
    """Build from the exact audited snapshot accepted by the user."""

    try:
        prepared = prepared_uploads.get(upload_id)
    except KeyError as error:
        raise HTTPException(
            status_code=409,
            detail="Проверенные данные больше недоступны. Загрузите Excel ещё раз.",
        ) from error
    try:
        result = plan_drafts.create(
            build_constructor_plan_from_prepared(
                prepared, get_catalog_repository(),
                archive_decisions=(request.decisions if request else None),
                existing_decisions=(request.existing_decisions if request else None),
            )
        )
    except ConstructorDatasetUploadError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Не удалось построить план по проверенным данным.",
                "errors": [str(error)],
            },
        ) from error
    prepared_uploads.discard(upload_id)
    return {
        **result,
        "data_preflight": {
            "status": "accepted",
            "upload_id": upload_id,
            "file": {"name": prepared.filename, "size_bytes": prepared.size_bytes},
            "audit": prepared.audit,
        },
    }


@app.post("/api/plan-constructor/plan")
def plan_constructor(scenario: ConstructorScenario) -> dict:
    """Build a six-sprint plan in memory without changing source data."""

    try:
        return plan_drafts.create(
            build_constructor_plan(
                scenario, catalog_tables=get_catalog_repository().tables()
            )
        )
    except ScenarioValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Исправьте ошибки в черновике плана.",
                "errors": list(error.errors),
            },
        ) from error


@app.post("/api/plan-constructor/drafts/approve")
def approve_constructor_plan(request: DraftApprovalRequest) -> dict:
    """Promote the reviewed draft without invoking the planner again."""

    try:
        approved = plan_drafts.approve(request.draft_id)
        # Return the catalog after the approval merge as part of the same
        # response.  Manual entities are persisted by PlanDraftStore before
        # this point; exposing the freshly rebuilt catalog prevents the UI
        # from briefly showing the stale pre-approval catalog (and avoids a
        # second request racing the approval response).
        approved["catalog"] = _catalog_snapshot()
        star_map = approved.get("star_map")
        if isinstance(star_map, dict):
            star_map["company"] = _company_star_map_team(
                star_map.get("engineers", []),
                star_map.get("teams", []),
            )
        return approved
    except KeyError as error:
        raise HTTPException(
            status_code=409,
            detail="Предварительный план больше недоступен. Постройте его заново.",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _draft_action(action, draft_id: str, entity_id: str) -> dict:
    try:
        return action(draft_id, entity_id)
    except KeyError as error:
        raise HTTPException(
            status_code=409,
            detail="Рекомендация устарела. Черновик обновлён, выберите актуальный вариант.",
        ) from error


@app.get("/api/plan-constructor/drafts/{draft_id}")
def constructor_draft(draft_id: str) -> dict:
    try:
        return plan_drafts.snapshot(draft_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Черновик не найден.") from error


@app.post("/api/plan-constructor/drafts/{draft_id}/recommendations/apply")
def apply_recommendation(draft_id: str, request: RecommendationRequest) -> dict:
    return _draft_action(plan_drafts.apply, draft_id, request.recommendation_id)


@app.post("/api/plan-constructor/drafts/{draft_id}/recommendations/reject")
def reject_recommendation(draft_id: str, request: RecommendationRequest) -> dict:
    return _draft_action(plan_drafts.reject, draft_id, request.recommendation_id)


@app.post("/api/plan-constructor/drafts/{draft_id}/recommendations/apply-all")
def apply_all_recommendations(draft_id: str) -> dict:
    try:
        return plan_drafts.apply_all(draft_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Черновик не найден.") from error


@app.post("/api/plan-constructor/drafts/{draft_id}/recommendations/refresh")
def refresh_recommendations(
    draft_id: str, request: RecommendationRefreshRequest
) -> dict:
    try:
        return plan_drafts.refresh_recommendations(draft_id, request.revision)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Черновик не найден.") from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="Черновик уже изменился. Запросите актуальные рекомендации.",
        ) from error


@app.post("/api/plan-constructor/drafts/{draft_id}/changes/undo")
def undo_accepted_change(draft_id: str, request: AcceptedChangeRequest) -> dict:
    return _draft_action(plan_drafts.undo, draft_id, request.change_id)


@app.post("/api/plan-constructor/drafts/{draft_id}/reset")
def reset_constructor_draft(draft_id: str) -> dict:
    try:
        return plan_drafts.reset_to_base(draft_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Черновик не найден.") from error


@app.get("/api/catalog/archive-reasons")
def archive_reasons() -> dict:
    return ARCHIVE_REASON_LABELS


def _current_plan_reference_count(entity_type: str, entity_id: str) -> int:
    current_tasks = _current_dashboard()["tasks"]
    if entity_type == "task":
        task_payload = next(
            (item for item in current_tasks if item["task_id"] == entity_id), None
        )
        return int(bool(task_payload and task_payload.get("status") == "PLANNED"))
    if entity_type == "engineer":
        return sum(
            1
            for task_payload in current_tasks
            for allocation in task_payload.get("allocations", [])
            if allocation.get("engineer_id") == entity_id
        )
    if entity_type == "team":
        return sum(
            1
            for task_payload in current_tasks
            if task_payload.get("status") == "PLANNED"
            and (
                task_payload.get("assigned_team_id") == entity_id
                or any(
                    allocation.get("source_team_id") == entity_id
                    for allocation in task_payload.get("allocations", [])
                )
            )
        )
    return 0


def _tables_after_deletion(
    entity_type: str,
    entity_id: str,
    *,
    team_engineer_action: str | None = None,
    team_task_action: str | None = None,
) -> dict[str, list[dict]]:
    """Prepare an in-memory catalog snapshot for the existing planner."""

    tables = deepcopy(get_catalog_repository().active_planning_tables())
    if entity_type == "task":
        removed_tasks = {entity_id}
    elif entity_type == "team" and team_task_action == "REMOVE":
        removed_tasks = {
            str(row.get("task_id") or "").strip()
            for row in tables["tasks"]
            if str(row.get("team_id") or "").strip() == entity_id
            and str(row.get("status") or "").strip().casefold() != "done"
        }
    else:
        removed_tasks = set()

    if removed_tasks:
        tables["tasks"] = [
            row for row in tables["tasks"]
            if str(row.get("task_id") or "").strip() not in removed_tasks
        ]
        for table_name in ("task_role_effort", "task_skill_requirements"):
            tables[table_name] = [
                row for row in tables[table_name]
                if str(row.get("task_id") or "").strip() not in removed_tasks
            ]
        tables["task_dependencies"] = [
            row for row in tables["task_dependencies"]
            if str(row.get("blocking_task_id") or "").strip() not in removed_tasks
            and str(row.get("blocked_task_id") or "").strip() not in removed_tasks
        ]

    if entity_type == "engineer":
        removed_engineers = {entity_id}
    elif entity_type == "team" and team_engineer_action == "DEACTIVATE":
        removed_engineers = {
            str(row.get("engineer_id") or "").strip()
            for row in tables["engineers"]
            if str(row.get("team_id") or "").strip() == entity_id
        }
    else:
        removed_engineers = set()
    if removed_engineers:
        tables["engineers"] = [
            row for row in tables["engineers"]
            if str(row.get("engineer_id") or "").strip() not in removed_engineers
        ]
        tables["engineer_skills"] = [
            row for row in tables["engineer_skills"]
            if str(row.get("engineer_id") or "").strip() not in removed_engineers
        ]

    if entity_type == "team":
        tables["team_history"] = [
            row for row in tables["team_history"]
            if str(row.get("team_id") or "").strip() != entity_id
        ]
        if team_engineer_action == "UNASSIGN":
            tables["engineers"] = [
                ({**row, "team_id": None}
                 if str(row.get("team_id") or "").strip() == entity_id else row)
                for row in tables["engineers"]
            ]
        if team_task_action == "REDISTRIBUTE":
            tables["tasks"] = [
                ({**row, "team_id": None}
                 if str(row.get("team_id") or "").strip() == entity_id
                 and str(row.get("status") or "").strip().casefold() != "done"
                 else row)
                for row in tables["tasks"]
            ]
    return tables


def _preserve_kpi_baseline(candidate: dict, current: dict) -> None:
    current_kpis = current.get("kpis") or {}
    candidate_kpis = candidate.get("kpis") or {}
    expected = candidate_kpis.get("expected_kpi") or {}
    old_expected = current_kpis.get("expected_kpi") or {}
    baseline = old_expected.get("baseline_initiatives")
    completed = expected.get("completed_initiatives")
    if baseline is not None and completed is not None:
        expected["baseline_initiatives"] = baseline
        expected["incomplete_initiatives"] = max(float(baseline) - float(completed), 0.0)
        expected["completion_percent"] = (
            float(completed) / float(baseline) * 100 if float(baseline) else None
        )
    if "quarter_kpi" in current_kpis:
        candidate_kpis["quarter_kpi"] = deepcopy(current_kpis["quarter_kpi"])
    if "sprint_kpis" in current_kpis:
        candidate_kpis["sprint_kpis"] = deepcopy(current_kpis["sprint_kpis"])


def _build_deletion_candidate(
    entity_type: str,
    entity_id: str,
    *,
    team_engineer_action: str | None = None,
    team_task_action: str | None = None,
) -> dict:
    current = _current_dashboard()
    tables = _tables_after_deletion(
        entity_type,
        entity_id,
        team_engineer_action=team_engineer_action,
        team_task_action=team_task_action,
    )
    candidate = deepcopy(
        PlanningAPIService.from_tables(
            tables, plan_version="catalog-after-deletion"
        ).dashboard
    )
    _preserve_kpi_baseline(candidate, current)
    planning_state = deepcopy(current.get("_planning_state") or {})
    if planning_state:
        planning_state["tables"] = deepcopy(tables)
        candidate["_planning_state"] = planning_state
    return candidate


def _deletion_impact(entity_type: str, entity_id: str) -> dict:
    repository = get_catalog_repository()
    try:
        catalog_impact = repository.deletion_impact(entity_type, entity_id)
    except CatalogNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    current = _current_dashboard()
    affected_allocations = [
        {"task_id": task["task_id"], **allocation}
        for task in current.get("tasks", [])
        for allocation in task.get("allocations", [])
        if (
            entity_type == "task" and task["task_id"] == entity_id
        ) or (
            entity_type == "engineer" and allocation.get("engineer_id") == entity_id
        ) or (
            entity_type == "team"
            and (
                task.get("assigned_team_id") == entity_id
                or allocation.get("source_team_id") == entity_id
            )
        )
    ]
    impact = {
        **catalog_impact,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "allocation_count": len(affected_allocations),
        "allocations": affected_allocations,
        "requires_replan": bool(affected_allocations),
    }
    simulation_engineer_action = "UNASSIGN" if entity_type == "team" else None
    simulation_task_action = "REDISTRIBUTE" if entity_type == "team" else None
    try:
        candidate = _build_deletion_candidate(
            entity_type,
            entity_id,
            team_engineer_action=simulation_engineer_action,
            team_task_action=simulation_task_action,
        )
        old_tasks = {item["task_id"]: item for item in current.get("tasks", [])}
        new_tasks = {item["task_id"]: item for item in candidate.get("tasks", [])}
        improved = sorted(
            task_id for task_id, task in new_tasks.items()
            if task.get("status") == "PLANNED"
            and (
                old_tasks.get(task_id, {}).get("status") != "PLANNED"
                or (task.get("start_sprint") or 99)
                < (old_tasks.get(task_id, {}).get("start_sprint") or 99)
            )
        )
        affected_task_ids = {
            row["task_id"] for row in affected_allocations
            if row.get("task_id")
        }
        replacements = []
        if entity_type == "engineer":
            for task_id in sorted(affected_task_ids):
                old_rows = [
                    row for row in affected_allocations if row["task_id"] == task_id
                ]
                affected_roles = {
                    row.get("role") for row in old_rows if row.get("role")
                }
                replacement_ids = sorted({
                    row.get("engineer_id")
                    for row in new_tasks.get(task_id, {}).get("allocations", [])
                    if row.get("engineer_id") and row.get("engineer_id") != entity_id
                    and row.get("role") in affected_roles
                })
                replacements.append({
                    "task_id": task_id,
                    "roles": sorted(affected_roles),
                    "engineer_ids": replacement_ids,
                    "possible": bool(replacement_ids)
                    and new_tasks.get(task_id, {}).get("status") == "PLANNED",
                })
        impact.update({
            "optimization_available": bool(improved),
            "improved_task_ids": improved,
            "replacement_options": replacements,
            "unplannable_task_ids": sorted(
                task_id for task_id in affected_task_ids
                if not (entity_type == "task" and task_id == entity_id)
                if new_tasks.get(task_id, {}).get("status") != "PLANNED"
            ),
        })
    except (ServiceDataError, ValueError) as error:
        impact.update({
            "simulation_error": str(error),
            "optimization_available": False,
            "replacement_options": [],
            "unplannable_task_ids": [],
        })
    return impact


@app.get("/api/tasks/{task_id}/deletion-impact")
def task_deletion_impact(task_id: str) -> dict:
    return _deletion_impact("task", task_id)


@app.get("/api/engineers/{engineer_id}/deletion-impact")
def engineer_deletion_impact(engineer_id: str) -> dict:
    return _deletion_impact("engineer", engineer_id)


@app.get("/api/teams/{team_id}/deletion-impact")
def team_deletion_impact(team_id: str) -> dict:
    return _deletion_impact("team", team_id)


def _archive_entity(
    entity_type: str, entity_id: str, request: ArchiveRequest
) -> dict:
    references = _current_plan_reference_count(entity_type, entity_id)
    current_dashboard = _current_dashboard()
    current_affected_task_ids = {
        task["task_id"]
        for task in current_dashboard.get("tasks", [])
        if (
            entity_type == "task" and task["task_id"] == entity_id
        ) or (
            entity_type == "engineer"
            and any(
                allocation.get("engineer_id") == entity_id
                for allocation in task.get("allocations", [])
            )
        ) or (
            entity_type == "team"
            and (
                task.get("assigned_team_id") == entity_id
                or any(
                    allocation.get("source_team_id") == entity_id
                    for allocation in task.get("allocations", [])
                )
            )
        )
    }
    repository = get_catalog_repository()
    allowed_reasons = ARCHIVE_REASON_LABELS.get(entity_type, {})
    if request.reason_code not in allowed_reasons:
        raise HTTPException(status_code=422, detail="Выберите допустимую причину удаления.")
    if request.reason_code == "OTHER" and not str(request.reason_text or "").strip():
        raise HTTPException(status_code=422, detail="Для другой причины укажите пояснение.")
    try:
        impact = repository.deletion_impact(entity_type, entity_id)
    except CatalogNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if entity_type == "team":
        if impact.get("engineer_ids") and request.team_engineer_action is None:
            raise HTTPException(
                status_code=422,
                detail="Выберите действие для сотрудников удаляемой команды.",
            )
        if impact.get("task_ids") and request.team_task_action is None:
            raise HTTPException(
                status_code=422,
                detail="Выберите действие для задач удаляемой команды.",
            )
    if entity_type == "engineer" and references and request.engineer_allocation_action is None:
        raise HTTPException(
            status_code=422,
            detail="Выберите предложенную замену или пересчёт без замены.",
        )
    if plan_drafts.approved() is None:
        try:
            operation = repository.archive_with_relations(
                entity_type,
                entity_id,
                request.reason_code,
                request.reason_text,
                team_engineer_action=request.team_engineer_action,
                team_task_action=request.team_task_action,
            )
        except CatalogNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ArchiveValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ArchiveConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            **operation,
            "message": "Сущность архивирована в каталоге. Утверждённый план отсутствует.",
            "current_plan_unchanged": True,
            "dashboard": _dashboard_with_lifecycle(),
            "unplannable_task_ids": [],
            "requires_plan_change": False,
            "warning": None,
        }
    # The planner runs before the transaction. A planning failure therefore
    # cannot leave the catalog changed while the current plan is stale.
    try:
        candidate = _build_deletion_candidate(
            entity_type,
            entity_id,
            team_engineer_action=request.team_engineer_action,
            team_task_action=request.team_task_action,
        )
    except (ServiceDataError, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail=f"Не удалось построить согласованный план после удаления: {error}",
        ) from error
    current_tasks = {
        item["task_id"]: item for item in current_dashboard.get("tasks", [])
    }
    candidate_tasks = {
        item["task_id"]: item for item in candidate.get("tasks", [])
    }
    optimization_available = any(
        task.get("status") == "PLANNED"
        and (
            current_tasks.get(task_id, {}).get("status") != "PLANNED"
            or (task.get("start_sprint") or 99)
            < (current_tasks.get(task_id, {}).get("start_sprint") or 99)
        )
        for task_id, task in candidate_tasks.items()
    )
    collection_name = {"task": "tasks", "engineer": "engineers", "team": "teams"}[entity_type]
    id_field = f"{entity_type}_id"
    entity_is_in_current_dashboard = any(
        item.get(id_field) == entity_id
        for item in _current_dashboard().get(collection_name, [])
    )
    if (
        references
        or (entity_type in {"engineer", "team"} and entity_is_in_current_dashboard)
        or optimization_available
    ) and not request.confirm_replan:
        raise HTTPException(
            status_code=409,
            detail=(
                "Удаление требует согласованного пересчёта текущего плана. "
                "Подтвердите пересчёт в окне удаления."
            ),
        )
    try:
        operation = repository.archive_with_relations(
            entity_type,
            entity_id,
            request.reason_code,
            request.reason_text,
            team_engineer_action=request.team_engineer_action,
            team_task_action=request.team_task_action,
        )
    except CatalogNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ArchiveConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    plan_drafts.replace_approved(candidate)
    messages = {
        "task": "Задача удалена из активного каталога.",
        "engineer": "Инженер сделан неактивным и исключён из планирования.",
        "team": "Команда удалена из активного каталога.",
    }
    affected_task_ids = set(operation.get("affected_task_ids", [])) | current_affected_task_ids
    unplannable = sorted(
        task_id for task_id in affected_task_ids
        if not (entity_type == "task" and task_id == entity_id)
        if candidate_tasks.get(task_id, {}).get("status") != "PLANNED"
        and request.team_task_action != "REMOVE"
    )
    return {
        **operation,
        "message": messages[entity_type],
        "current_plan_unchanged": False,
        "dashboard": _dashboard_with_lifecycle(),
        "unplannable_task_ids": unplannable,
        "requires_plan_change": bool(unplannable),
        "warning": (
            "Не удалось перераспределить задачи: " + ", ".join(unplannable)
            + ". Измените состав или ограничения плана."
            if unplannable
            else None
        ),
    }


@app.post("/api/tasks/{task_id}/archive")
def archive_task(task_id: str, request: ArchiveRequest) -> dict:
    return _archive_entity("task", task_id, request)


@app.post("/api/engineers/{engineer_id}/archive")
def archive_engineer(engineer_id: str, request: ArchiveRequest) -> dict:
    return _archive_entity("engineer", engineer_id, request)


@app.post("/api/teams/{team_id}/archive")
def archive_team(team_id: str, request: ArchiveRequest) -> dict:
    return _archive_entity("team", team_id, request)


def _delete_entity_permanently(entity_type: str, entity_id: str) -> dict:
    """Permanently remove a catalog record after explicit UI confirmation."""

    try:
        result = get_catalog_repository().delete_entity(entity_type, entity_id)
    except CatalogNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**result, "dashboard": _dashboard_with_lifecycle()}


@app.delete("/api/tasks/{task_id}/permanent")
def permanently_delete_task(task_id: str) -> dict:
    return _delete_entity_permanently("task", task_id)


@app.delete("/api/engineers/{engineer_id}/permanent")
def permanently_delete_engineer(engineer_id: str) -> dict:
    return _delete_entity_permanently("engineer", engineer_id)


@app.delete("/api/teams/{team_id}/permanent")
def permanently_delete_team(team_id: str) -> dict:
    return _delete_entity_permanently("team", team_id)


if not FRONTEND_DIR.is_dir():
    raise RuntimeError(f"Frontend directory does not exist: {FRONTEND_DIR}")


@app.get("/tasks", include_in_schema=False)
@app.get("/tasks/{task_id}", include_in_schema=False)
def task_frontend_route(task_id: str | None = None) -> FileResponse:
    """Serve the SPA shell for shareable task catalog and detail URLs."""

    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/teams", include_in_schema=False)
@app.get("/teams/{team_id}", include_in_schema=False)
def team_frontend_route(team_id: str | None = None) -> FileResponse:
    """Serve the SPA shell for stable team catalog and detail URLs."""

    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/engineers", include_in_schema=False)
@app.get("/engineers/{engineer_id}", include_in_schema=False)
def engineer_frontend_route(engineer_id: str | None = None) -> FileResponse:
    """Serve the SPA shell for stable engineer catalog and detail URLs."""

    return FileResponse(FRONTEND_DIR / "index.html")

# Registered last so /api/* routes always win over the SPA/static fallback.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
