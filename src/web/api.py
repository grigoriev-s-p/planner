"""FastAPI application serving the product UI and planner API."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
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
    build_constructor_plan,
    build_constructor_plan_from_prepared,
    prepare_constructor_workbook,
)
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
    get_catalog_repository,
)
from .recommendations import RecommendationDraft
from .engineer_workspace import build_engineer_workspace
from src.planning.task_states import is_historical_done


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(title="PostCode Quarter Planning API", version="1.0.0")


class ArchiveRequest(BaseModel):
    reason_code: str
    reason_text: str | None = None


class DraftApprovalRequest(BaseModel):
    draft_id: str


class RecommendationRequest(BaseModel):
    recommendation_id: str


class AcceptedChangeRequest(BaseModel):
    change_id: str


class RecommendationRefreshRequest(BaseModel):
    revision: int


class ConstructorUploadDecisionRequest(BaseModel):
    decisions: dict[str, Literal["KEEP_ARCHIVED", "RESTORE", "RESTORE_UPDATE"]] = Field(default_factory=dict)


class PlanDraftStore:
    """Keep one reviewable plan draft and the last approved plan in memory."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._drafts: dict[str, RecommendationDraft] = {}
        self._approved: dict | None = None

    def create(self, payload: dict) -> dict:
        draft_id = uuid4().hex
        draft = RecommendationDraft(payload)
        with self._lock:
            self._drafts = {draft_id: draft}
        return draft.snapshot(draft_id)

    def snapshot(self, draft_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            return draft.snapshot(draft_id)

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
            approved = draft.snapshot(draft_id)
            approved["draft"] = {
                **approved.get("draft", {}),
                "approved": True,
            }
            approved = _current_plan_without_historical_done(approved)
            self._approved = approved
            return deepcopy(approved)

    def approved(self) -> dict | None:
        with self._lock:
            return deepcopy(self._approved) if self._approved is not None else None

    def reset(self) -> None:
        with self._lock:
            self._drafts = {}
            self._approved = None


plan_drafts = PlanDraftStore()


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


def _with_lifecycle(payload: dict, entity_type: str, entity_id: str) -> dict:
    return {**payload, **get_catalog_repository().lifecycle(entity_type, entity_id)}


def _current_dashboard() -> dict:
    return _current_plan_without_historical_done(
        plan_drafts.approved() or deepcopy(_service().dashboard)
    )


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
            if not unique_ids:
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


def _dashboard_with_lifecycle() -> dict:
    payload = _current_dashboard()
    repository = get_catalog_repository()
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
        active_tables = repository.active_planning_tables()
        active_bus = build_bus_factor_payload(active_tables)
        payload["star_map"]["status"] = active_bus.get("status", payload["star_map"].get("status"))
        payload["star_map"]["bus_factor_status"] = active_bus.get(
            "status", payload["star_map"].get("bus_factor_status")
        )
        payload["star_map"]["bus_factor"] = {
            **(payload["star_map"].get("bus_factor") or {}),
            "available": active_bus.get("status") == "AVAILABLE",
            "method": active_bus.get("method"),
        }
        active_team_by_id = {item.get("team_id"): item for item in active_bus.get("teams", [])}
        active_engineer_ids = {
            str(row.get("engineer_id"))
            for row in active_tables.get("engineers", [])
            if row.get("engineer_id")
        }
        payload["star_map"]["engineers"] = [
            item for item in payload["star_map"].get("engineers", [])
            if item.get("engineer_id") in active_engineer_ids
        ]
        filtered_teams = []
        for team in payload["star_map"].get("teams", []):
            active_team = active_team_by_id.get(team.get("team_id"))
            if active_team is None:
                continue
            team["member_ids"] = [
                engineer_id
                for engineer_id in team.get("member_ids", [])
                if engineer_id in active_engineer_ids
            ]
            team["member_count"] = len(set(team["member_ids"]))
            team["engineer_count"] = team["member_count"]
            team["competencies"] = active_team.get("competencies", [])
            team["competency_count"] = active_team.get("competency_count", len(team["competencies"]))
            team["bus_factor_available"] = active_team.get("bus_factor_available", False)
            team["summary"] = active_team.get("summary", {})
            team["status"] = active_team.get("status", team.get("status"))
            team["message"] = active_team.get("message")
            active_skills = {
                item.get("skill"): item for item in team["competencies"]
            }
            for mode in (team.get("competency_modes") or {}).values():
                if not isinstance(mode, dict):
                    continue
                for item in mode.get("technologies", []):
                    source = active_skills.get(item.get("technology"))
                    if source is None:
                        item["engineer_ids"] = []
                        item["team_bus_factor"] = 0
                    else:
                        item["engineer_ids"] = [
                            holder.get("engineer_id")
                            for holder in source.get("engineers", [])
                            if holder.get("engineer_id") in active_engineer_ids
                        ]
                        item["team_bus_factor"] = len(set(item["engineer_ids"]))
                for item in mode.get("roles", []):
                    item["engineer_ids"] = [
                        engineer_id
                        for engineer_id in item.get("engineer_ids", [])
                        if engineer_id in active_engineer_ids
                    ]
                    item["team_bus_factor"] = len(set(item["engineer_ids"]))
            filtered_teams.append(team)
        payload["star_map"]["teams"] = filtered_teams
        payload["star_map"]["company"] = _company_star_map_team(
            payload["star_map"].get("engineers", []),
            filtered_teams,
        )
    return payload


@app.exception_handler(ServiceDataError)
async def service_data_error_handler(_, error: ServiceDataError):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/api/health")
def health() -> dict[str, str]:
    _service()
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard() -> dict:
    return _dashboard_with_lifecycle()


@app.get("/api/plan")
def plan() -> dict:
    return _current_dashboard()["plan"]


@app.get("/api/summary")
def summary() -> dict:
    return _current_dashboard()["summary"]


@app.get("/api/kpis")
def kpis() -> dict:
    return _current_dashboard()["kpis"]


@app.get("/api/tasks")
def tasks() -> list[dict]:
    return [
        _with_lifecycle(item, "task", item["task_id"])
        for item in _current_dashboard()["tasks"]
    ]


@app.get("/api/tasks/{task_id}")
def task(task_id: str) -> dict:
    payload = next(
        (item for item in _current_dashboard()["tasks"] if item["task_id"] == task_id),
        None,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown task_id: {task_id}")
    return _with_lifecycle(payload, "task", task_id)


@app.get("/api/initiatives")
def initiatives() -> list[dict]:
    return _current_dashboard()["initiatives"]


@app.get("/api/risks")
def risks() -> list[dict]:
    return _current_dashboard()["risks"]


@app.get("/api/teams")
def teams() -> list[dict]:
    return [
        _with_lifecycle(item, "team", item["team_id"])
        for item in _current_dashboard()["teams"]
    ]


@app.get("/api/teams/{team_id}")
def team(team_id: str) -> dict:
    payload = next(
        (item for item in _current_dashboard()["teams"] if item["team_id"] == team_id),
        None,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown team_id: {team_id}")
    return _with_lifecycle(payload, "team", team_id)


@app.get("/api/engineers")
def engineers() -> list[dict]:
    return [
        _with_lifecycle(item, "engineer", item["engineer_id"])
        for item in _current_dashboard()["engineers"]
    ]


@app.get("/api/engineers/{engineer_id}")
def engineer(engineer_id: str) -> dict:
    payload = next(
        (
            item
            for item in _current_dashboard()["engineers"]
            if item["engineer_id"] == engineer_id
        ),
        None,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown engineer_id: {engineer_id}")
    return _with_lifecycle(payload, "engineer", engineer_id)


@app.get("/api/engineer-workspace/{engineer_id}")
def engineer_workspace(engineer_id: str) -> dict:
    """Personal projection of the approved plan; drafts are never a fallback."""

    catalog_engineer = next(
        (item for item in _catalog_service().engineers if item.get("engineer_id") == engineer_id),
        None,
    )
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
    return build_constructor_catalog(
        _catalog_service(), archived_task_ids=repository.archived_ids("task")
    )


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
        return plan_drafts.approve(request.draft_id)
    except KeyError as error:
        raise HTTPException(
            status_code=409,
            detail="Предварительный план больше недоступен. Постройте его заново.",
        ) from error


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
            if task_payload.get("assigned_team_id") == entity_id
            and task_payload.get("status") == "PLANNED"
        )
    return 0


def _archive_entity(
    entity_type: str, entity_id: str, request: ArchiveRequest
) -> dict:
    references = _current_plan_reference_count(entity_type, entity_id)
    try:
        lifecycle = get_catalog_repository().archive(
            entity_type,
            entity_id,
            request.reason_code,
            request.reason_text,
            current_plan_references=references,
        )
    except CatalogNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ArchiveConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    messages = {
        "task": "Задача удалена из активного каталога.",
        "engineer": "Инженер удалён из активного каталога.",
        "team": "Команда удалена из активного каталога.",
    }
    return {
        "entity": lifecycle,
        "message": messages[entity_type],
        "current_plan_unchanged": True,
        "warning": (
            "Объект участвует в текущем плане. Он исключён из будущих "
            "построений, но текущий план автоматически не пересчитан."
            if references
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


if not FRONTEND_DIR.is_dir():
    raise RuntimeError(f"Frontend directory does not exist: {FRONTEND_DIR}")


@app.get("/tasks", include_in_schema=False)
@app.get("/tasks/{task_id}", include_in_schema=False)
def task_frontend_route(task_id: str | None = None) -> FileResponse:
    """Serve the SPA shell for shareable task catalog and detail URLs."""

    return FileResponse(FRONTEND_DIR / "index.html")

# Registered last so /api/* routes always win over the SPA/static fallback.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
