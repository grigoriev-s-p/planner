"""FastAPI application serving the product UI and planner API."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .constructor import (
    ConstructorDatasetUploadError,
    ConstructorScenario,
    ScenarioValidationError,
    build_constructor_catalog,
    build_constructor_plan,
    build_constructor_plan_from_workbook,
)
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


class PlanDraftStore:
    """Keep one reviewable plan draft and the last approved plan in memory."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._drafts: dict[str, dict] = {}
        self._approved: dict | None = None

    def create(self, payload: dict) -> dict:
        draft_id = uuid4().hex
        draft = deepcopy(payload)
        draft["draft"] = {
            **draft.get("draft", {}),
            "id": draft_id,
            "storage": "server_memory",
            "server_persisted": True,
            "approved": False,
        }
        with self._lock:
            self._drafts = {draft_id: deepcopy(draft)}
        return draft

    def approve(self, draft_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            approved = deepcopy(draft)
            approved["draft"] = {
                **approved.get("draft", {}),
                "approved": True,
            }
            self._approved = approved
            return deepcopy(approved)

    def approved(self) -> dict | None:
        with self._lock:
            return deepcopy(self._approved) if self._approved is not None else None

    def reset(self) -> None:
        with self._lock:
            self._drafts = {}
            self._approved = None

    def replace_approved(self, payload: dict) -> dict:
        with self._lock:
            self._approved = deepcopy(payload)
            return deepcopy(self._approved)


plan_drafts = PlanDraftStore()


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
    # The persistent working catalog is the source of the current plan after a
    # restart as well; an approved in-memory draft still takes precedence.
    return plan_drafts.approved() or deepcopy(_catalog_service().dashboard)


def _dashboard_with_lifecycle() -> dict:
    payload = _current_dashboard()
    repository = get_catalog_repository()
    # Current Bus Factor follows the active catalog even when an approved plan
    # remains historically unchanged after an archive action. This calls only
    # the existing analytics service and never launches the quarter planner.
    payload["star_map"] = build_bus_factor_payload(repository.active_planning_tables())
    payload.setdefault("features", {})["bus_factor"] = "AVAILABLE"
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


@app.get("/api/star-map")
def star_map() -> dict:
    return build_bus_factor_payload(get_catalog_repository().active_planning_tables())


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
    """Build a complete six-sprint plan directly from an uploaded workbook."""

    try:
        return plan_drafts.create(
            build_constructor_plan_from_workbook(
                filename, content, get_catalog_repository()
            )
        )
    except ConstructorDatasetUploadError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Не удалось использовать загруженный датасет.",
                "errors": [str(error)],
            },
        ) from error


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


if not FRONTEND_DIR.is_dir():
    raise RuntimeError(f"Frontend directory does not exist: {FRONTEND_DIR}")


@app.get("/tasks", include_in_schema=False)
@app.get("/tasks/{task_id}", include_in_schema=False)
def task_frontend_route(task_id: str | None = None) -> FileResponse:
    """Serve the SPA shell for shareable task catalog and detail URLs."""

    return FileResponse(FRONTEND_DIR / "index.html")

# Registered last so /api/* routes always win over the SPA/static fallback.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
